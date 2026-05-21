"""
SavingsPlanner — the OSS agent loop (single shot, single tool set).

Flow (one shot, with one retry on parse failure):

    findings → render findings block →
    LLM.complete(prompt) → parse JSON →
    validate emissions → build PlanResult →
    (optional) record PlanRecord through repository

Everything LLM-produced flows through the validators in `validators.py`
before it reaches the user. The planner never reasons over LLM output
directly; it pattern-matches on `ValidationOutcome` and computes
deterministic fields (confidence, totals) itself.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm import LLMClient, Message  # noqa: E402
from prompts import load_template  # noqa: E402
from prompts.savings_plan import REPAIR_INSTRUCTION  # noqa: E402

from .modes import AvailableModesResolver
from .parser import ParseError, ParsedPlan, parse_plan
from .schemas import PlanResult, new_plan_id
from .trace import write_plan
from .validators import validate_steps

if TYPE_CHECKING:
    from patterns.base import Finding
    from storage import WhisperRepository


logger = logging.getLogger(__name__)

DEFAULT_GOAL = "Rank by impact and risk; recommend the safest mode for each."
TEMPLATE_NAME = "savings_plan"


class SavingsPlanner:
    """Single-loop planner. Hold one instance per process.

    Use `plan(findings, goal=..., actor=..., scan_id=...)`.
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        repository: "WhisperRepository | None" = None,
        resolver: AvailableModesResolver | None = None,
    ):
        self._llm = llm
        self._repository = repository
        self._resolver = resolver or AvailableModesResolver()

    def plan(
        self,
        findings: list["Finding"],
        *,
        goal: str | None = None,
        scan_id: str | None = None,
        actor: str | None = None,
    ) -> PlanResult:
        template = load_template(TEMPLATE_NAME)
        findings_block = self._render_findings_block(findings)
        effective_goal = (goal or DEFAULT_GOAL).strip()

        prompt = (
            template.text
            .replace("<<GOAL>>", effective_goal)
            .replace("<<FINDINGS_BLOCK>>", findings_block)
        )

        # First attempt + one repair retry.
        parsed, raw_text, retries = self._call_with_retry(prompt)

        result = self._build_result(
            parsed=parsed,
            findings=findings,
            template_name=TEMPLATE_NAME,
            template_version=template.version,
            goal=goal,
            scan_id=scan_id,
            actor=actor,
            parse_retry_count=retries,
            raw_text=raw_text,
        )
        write_plan(result, self._repository)
        return result

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _call_with_retry(self, prompt: str) -> tuple[ParsedPlan | None, str, int]:
        """Call LLM, parse. One repair retry on ParseError.

        Returns (parsed_or_None, last_raw_text, parse_retry_count).
        If parsing still fails after the retry, parsed is None and the
        planner builds a `validation_failed` PlanResult — no exception.
        """
        messages = [Message(role="user", content=prompt)]
        response = self._llm.complete(messages)
        try:
            return parse_plan(response.text), response.text, 0
        except ParseError as e:
            logger.info("first parse failed (%s); retrying with repair instruction", e)

        repair_messages = [
            Message(role="user", content=prompt),
            Message(role="assistant", content=response.text),
            Message(role="user", content=REPAIR_INSTRUCTION),
        ]
        retry_response = self._llm.complete(repair_messages)
        try:
            return parse_plan(retry_response.text), retry_response.text, 1
        except ParseError as e:
            logger.warning("repair retry also failed to parse: %s", e)
            return None, retry_response.text, 1

    def _render_findings_block(self, findings: list["Finding"]) -> str:
        """Render findings as deterministic text for the prompt.

        Each finding gets a one-line header + a JSON block of evidence.
        `available_modes` is the gated list from the resolver — the LLM
        sees only modes the pattern will actually accept.
        """
        if not findings:
            return "(no findings provided)"

        chunks: list[str] = []
        for f in findings:
            available = sorted(self._resolver.resolve_values(f))
            evidence_blob = json.dumps(f.evidence, default=str, indent=2)
            chunks.append(
                f"### finding `{f.id}`\n"
                f"- pattern_id: {f.pattern_id}\n"
                f"- resource: `{f.resource_id}` ({f.resource_type}) in {f.region}\n"
                f"- monthly_impact_usd: {f.monthly_impact_usd:.2f}\n"
                f"- risk_tier: {f.risk_tier.value}\n"
                f"- safe_to_fix: {f.safe_to_fix}\n"
                f"- available_modes: {available}\n"
                f"- summary: {f.summary}\n"
                f"- evidence:\n```json\n{evidence_blob}\n```"
            )
        return "\n\n".join(chunks)

    def _build_result(
        self,
        *,
        parsed: ParsedPlan | None,
        findings: list["Finding"],
        template_name: str,
        template_version: str,
        goal: str | None,
        scan_id: str | None,
        actor: str | None,
        parse_retry_count: int,
        raw_text: str,
    ) -> PlanResult:
        # Trace fields independent of parse success.
        provider = self._llm.provider
        boundary_crossed = self._llm.boundary_crossed
        try:
            model = self._llm.default_model
        except Exception:  # pragma: no cover — provider clients implement this
            model = "unknown"
        input_finding_ids = [f.id for f in findings]

        if parsed is None:
            # Both attempts failed to parse. Build a validation_failed
            # PlanResult with the raw response captured as a dropped step.
            from .schemas import DropReason, DroppedStep
            return PlanResult(
                plan_id=new_plan_id(),
                goal=goal,
                status="validation_failed",
                steps=[],
                dropped_steps=[DroppedStep(
                    raw_emission={"raw_response": raw_text},
                    reason=DropReason.SCHEMA_INVALID.value,
                    validator="parser",
                    detail="LLM response failed to parse after one retry",
                )],
                total_monthly_impact_usd=0.0,
                summary="The model did not return a parseable JSON plan.",
                confidence=0.0,
                prompt_template=template_name,
                prompt_template_version=template_version,
                model=model,
                provider=provider,
                boundary_crossed=boundary_crossed,
                parse_retry_count=parse_retry_count,
                input_finding_ids=input_finding_ids,
                scan_id=scan_id,
                actor=actor,
            )

        kept, dropped = validate_steps(
            parsed.steps,
            findings=findings,
            resolver=self._resolver,
        )

        kept_sorted = sorted(kept, key=lambda s: s.order_rank)
        status = "ok" if kept_sorted else "validation_failed"
        total = sum(s.monthly_impact_usd for s in kept_sorted)
        confidence = _confidence(kept_sorted, findings)
        summary = parsed.summary.strip() or (
            f"{len(kept_sorted)} step(s) planned."
        )

        return PlanResult(
            plan_id=new_plan_id(),
            goal=goal,
            status=status,
            steps=kept_sorted,
            dropped_steps=dropped,
            total_monthly_impact_usd=total,
            summary=summary,
            confidence=confidence,
            prompt_template=template_name,
            prompt_template_version=template_version,
            model=model,
            provider=provider,
            boundary_crossed=boundary_crossed,
            parse_retry_count=parse_retry_count,
            input_finding_ids=input_finding_ids,
            scan_id=scan_id,
            actor=actor,
        )


def _confidence(steps: list, findings: list) -> float:
    """Deterministic confidence — proportional to coverage of high-impact
    findings, capped at 0.95. Cheap and not LLM-derived (principle:
    confidence is planner-computed)."""
    if not steps or not findings:
        return 0.0
    impact_total = sum(f.monthly_impact_usd for f in findings)
    if impact_total <= 0:
        return 0.5
    plan_impact = sum(s.monthly_impact_usd for s in steps)
    coverage = plan_impact / impact_total
    return round(min(0.95, 0.5 + 0.45 * coverage), 3)
