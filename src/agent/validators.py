"""
The safety boundary — CLAUDE.md's "LLM proposes; framework disposes".

Every raw step the LLM emits passes through `validate_step`. Steps
that survive become `PlanStep` instances; failures become
`DroppedStep` records with a `DropReason`. The planner never
surfaces a dropped step to the user.

Design notes:

  - This module imports nothing from `llm.*` on purpose. The validators
    don't know the model produced the emission and don't care; they
    only see structured input. That isolation is what lets the eval
    harness exercise them with hand-crafted bad inputs.

  - Every drop carries the *raw* emission (the dict that came back from
    the parser), the reason from the closed `DropReason` enum, the
    name of the validator that rejected it, and an optional `detail`
    string. The eval rubric asserts against `reason`; the `detail`
    field is for humans reading the audit log.

  - `monthly_impact_usd` follows the strict rule from PR review:
    the LLM MUST emit it. Missing → drop. Mismatched by >$0.01 → drop.
    Within tolerance → accept; final `PlanStep.monthly_impact_usd` is
    overwritten from the canonical Finding so downstream callers never
    see LLM-typed numbers.

  - `suggested_mode` is validated against the resolver, not against the
    full RemediationMode enum. A mode that exists in the enum but
    isn't exposed for this Finding is still "unsupported".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .modes import AvailableModesResolver
from .schemas import DropReason, DroppedStep, PlanStep

if TYPE_CHECKING:
    from patterns.base import Finding


# Required fields on every raw emission. order_rank can be 0 (zero is a
# valid emission); it just must be present and an int.
_REQUIRED_KEYS = ("finding_id", "suggested_mode", "monthly_impact_usd",
                  "rationale", "order_rank")

# Tolerance for the LLM's dollar emission vs the canonical Finding value.
# One cent — anything looser starts to mask LLM hallucination.
MONTHLY_IMPACT_TOLERANCE = 0.01


@dataclass
class ValidationOutcome:
    """Sum type: either a PlanStep or a DroppedStep. Tests + the planner
    pattern-match on `kept`."""
    kept: PlanStep | None
    dropped: DroppedStep | None

    @property
    def is_kept(self) -> bool:
        return self.kept is not None


def validate_step(
    raw: dict[str, Any],
    *,
    findings_by_id: dict[str, "Finding"],
    resolver: AvailableModesResolver,
    seen_finding_ids: set[str],
) -> ValidationOutcome:
    """Promote one raw LLM emission to PlanStep or reject it.

    `seen_finding_ids` is the caller-owned set of finding IDs already
    accepted in this plan; we use it to enforce "one step per finding"
    (duplicates are dropped). The caller mutates the set on acceptance.
    """
    # 1. Shape check — required keys present.
    missing = [k for k in _REQUIRED_KEYS if k not in raw]
    if missing:
        return _drop(raw, DropReason.SCHEMA_INVALID, "shape",
                     f"missing required keys: {missing}")

    # 2. finding_id must reference a known input finding.
    finding_id = raw["finding_id"]
    if not isinstance(finding_id, str):
        return _drop(raw, DropReason.SCHEMA_INVALID, "finding_id",
                     "finding_id must be a string")
    finding = findings_by_id.get(finding_id)
    if finding is None:
        return _drop(raw, DropReason.UNKNOWN_FINDING_ID, "finding_id",
                     f"finding_id {finding_id!r} is not in the input set")

    # 3. Duplicate suppression — only one step per finding.
    if finding_id in seen_finding_ids:
        return _drop(raw, DropReason.DUPLICATE_FINDING_ID, "finding_id",
                     f"finding_id {finding_id!r} was already accepted in this plan")

    # 4. monthly_impact_usd — required, numeric, within tolerance.
    emitted_impact = raw.get("monthly_impact_usd", _SENTINEL)
    if emitted_impact is _SENTINEL or emitted_impact is None:
        return _drop(raw, DropReason.MONTHLY_IMPACT_MISSING, "monthly_impact",
                     "LLM must emit monthly_impact_usd")
    try:
        emitted_value = float(emitted_impact)
    except (TypeError, ValueError):
        return _drop(raw, DropReason.MONTHLY_IMPACT_MISSING, "monthly_impact",
                     f"monthly_impact_usd is not numeric: {emitted_impact!r}")
    canonical = finding.monthly_impact_usd
    if abs(emitted_value - canonical) > MONTHLY_IMPACT_TOLERANCE:
        return _drop(raw, DropReason.MONTHLY_IMPACT_MISMATCH, "monthly_impact",
                     f"emitted={emitted_value} canonical={canonical}")

    # 5. suggested_mode must come from the pattern's exposed set.
    mode = raw["suggested_mode"]
    if not isinstance(mode, str):
        return _drop(raw, DropReason.SCHEMA_INVALID, "suggested_mode",
                     "suggested_mode must be a string")
    allowed = resolver.resolve_values(finding)
    if mode not in allowed:
        return _drop(raw, DropReason.UNSUPPORTED_MODE, "suggested_mode",
                     f"mode {mode!r} not in available modes {sorted(allowed)}")

    # 6. order_rank — required, int-coercible.
    order_rank = raw["order_rank"]
    try:
        order_rank_int = int(order_rank)
    except (TypeError, ValueError):
        return _drop(raw, DropReason.SCHEMA_INVALID, "order_rank",
                     f"order_rank must be int-coercible: {order_rank!r}")

    # 7. rationale — string, may be empty; we keep it whatever it is.
    rationale = raw["rationale"]
    if not isinstance(rationale, str):
        rationale = str(rationale)

    # All checks passed. Build the PlanStep using the *canonical*
    # monthly_impact_usd, not the LLM-emitted value — that's the
    # "framework disposes" half of the rule.
    step = PlanStep(
        finding_id=finding_id,
        pattern_id=finding.pattern_id,
        suggested_mode=mode,
        monthly_impact_usd=canonical,
        rationale=rationale,
        order_rank=order_rank_int,
    )
    return ValidationOutcome(kept=step, dropped=None)


def validate_steps(
    raw_emissions: list[dict[str, Any]],
    *,
    findings: list["Finding"],
    resolver: AvailableModesResolver | None = None,
) -> tuple[list[PlanStep], list[DroppedStep]]:
    """Validate a list of raw step emissions. Returns (kept, dropped).

    The order of `kept` matches the input order — the planner can then
    sort by `order_rank` or any other key if it wants.
    """
    if not isinstance(raw_emissions, list):
        # Defensive: a non-list emission means the parser handed us
        # something malformed. The planner should have caught it, but
        # we surface it as a schema-invalid drop rather than crashing.
        return [], [DroppedStep(
            raw_emission={"value": str(raw_emissions)},
            reason=DropReason.SCHEMA_INVALID.value,
            validator="validate_steps",
            detail=f"expected list of step dicts, got {type(raw_emissions).__name__}",
        )]

    findings_by_id = {f.id: f for f in findings}
    resolver = resolver or AvailableModesResolver()
    seen: set[str] = set()
    kept: list[PlanStep] = []
    dropped: list[DroppedStep] = []

    for raw in raw_emissions:
        if not isinstance(raw, dict):
            dropped.append(DroppedStep(
                raw_emission={"value": str(raw)},
                reason=DropReason.SCHEMA_INVALID.value,
                validator="validate_steps",
                detail=f"emission is not a dict: {type(raw).__name__}",
            ))
            continue
        outcome = validate_step(
            raw,
            findings_by_id=findings_by_id,
            resolver=resolver,
            seen_finding_ids=seen,
        )
        if outcome.is_kept:
            kept.append(outcome.kept)  # type: ignore[arg-type]
            seen.add(outcome.kept.finding_id)  # type: ignore[union-attr]
        else:
            dropped.append(outcome.dropped)  # type: ignore[arg-type]
    return kept, dropped


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _drop(raw: dict[str, Any], reason: DropReason, validator: str,
          detail: str) -> ValidationOutcome:
    return ValidationOutcome(
        kept=None,
        dropped=DroppedStep(
            raw_emission=dict(raw),
            reason=reason.value,
            validator=validator,
            detail=detail,
        ),
    )
