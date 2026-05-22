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
from .schemas import (
    ALLOWED_ACTION_KINDS,
    DropReason,
    DroppedStep,
    PlanStep,
    SubAction,
)

if TYPE_CHECKING:
    from patterns.base import Finding


# Required fields on every raw emission. order_rank can be 0 (zero is a
# valid emission); it just must be present and an int.
_REQUIRED_KEYS = ("finding_id", "suggested_mode", "monthly_impact_usd",
                  "rationale", "order_rank")

# Tolerance for the LLM's dollar emission vs the canonical Finding value.
# One cent — anything looser starts to mask LLM hallucination.
MONTHLY_IMPACT_TOLERANCE = 0.01

# Required fields on every sub-action emission inside a step's
# `recommended_sequence`.
_REQUIRED_SUBACTION_KEYS = (
    "candidate_id", "action_kind", "est_monthly_savings_usd",
    "evidence_tier", "rationale",
)


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

    # 8. recommended_sequence (optional) — only checked when the LLM
    #    chose to emit one. Whole-step drop on any sub-action failure;
    #    salvaging half a sub-action plan is worse than no plan.
    sub_actions: list[SubAction] | None = None
    if "recommended_sequence" in raw and raw["recommended_sequence"] is not None:
        sub_outcome = _validate_sub_actions(raw, finding)
        if sub_outcome.dropped is not None:
            return ValidationOutcome(kept=None, dropped=sub_outcome.dropped)
        sub_actions = sub_outcome.kept_sub_actions

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
        recommended_sequence=sub_actions,
    )
    return ValidationOutcome(kept=step, dropped=None)


# ---------------------------------------------------------------------------
# Sub-action validation (p006 — recommended_sequence)
# ---------------------------------------------------------------------------

@dataclass
class _SubActionOutcome:
    """Either kept_sub_actions is a list and dropped is None, or
    dropped carries a DroppedStep describing why the whole step fails."""
    kept_sub_actions: list[SubAction] | None
    dropped: DroppedStep | None


def _candidate_index(finding: "Finding") -> dict[str, dict[str, Any]]:
    """Pull `evidence.inferred.endpoint_candidates` into a dict keyed by
    candidate_id. Missing or malformed evidence is treated as an empty
    candidate set — every sub-action will then fail UNKNOWN_CANDIDATE_ID,
    which is the correct response for findings without candidates."""
    inferred = finding.evidence.get("inferred") if isinstance(finding.evidence, dict) else None
    candidates = inferred.get("endpoint_candidates", []) if isinstance(inferred, dict) else []
    if not isinstance(candidates, list):
        return {}
    return {
        c["candidate_id"]: c
        for c in candidates
        if isinstance(c, dict) and isinstance(c.get("candidate_id"), str)
    }


def _validate_sub_actions(raw: dict[str, Any], finding: "Finding") -> _SubActionOutcome:
    """Validate raw `recommended_sequence`. Returns either a list of
    canonical SubActions or a DroppedStep explaining the failure.

    Validator order is deliberate:
      1. shape (list, required keys, types)
      2. candidate_id known
      3. action_kind in closed enum
      4. evidence_tier matches the candidate's canonical tier
      5. est_monthly_savings_usd matches the candidate's canonical value
      6. sum cap — total sub-action savings ≤ finding's monthly_impact_usd
      7. canonicalisation (build SubAction from canonical candidate fields)
    """
    raw_sequence = raw["recommended_sequence"]
    if not isinstance(raw_sequence, list):
        return _SubActionOutcome(
            kept_sub_actions=None,
            dropped=_drop_step(
                raw, DropReason.SCHEMA_INVALID, "sub_actions",
                f"recommended_sequence must be a list, got {type(raw_sequence).__name__}",
            ),
        )

    candidates = _candidate_index(finding)
    canonical_actions: list[SubAction] = []
    running_total = 0.0

    for i, item in enumerate(raw_sequence):
        if not isinstance(item, dict):
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.SCHEMA_INVALID, "sub_actions",
                f"recommended_sequence[{i}] is not an object",
            ))
        missing = [k for k in _REQUIRED_SUBACTION_KEYS if k not in item]
        if missing:
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.SCHEMA_INVALID, "sub_actions",
                f"recommended_sequence[{i}] missing keys {missing}",
            ))

        candidate_id = item["candidate_id"]
        if not isinstance(candidate_id, str) or candidate_id not in candidates:
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.UNKNOWN_CANDIDATE_ID, "sub_actions",
                f"recommended_sequence[{i}] cites unknown candidate_id "
                f"{candidate_id!r}; available: {sorted(candidates)}",
            ))
        canonical_candidate = candidates[candidate_id]

        action_kind = item["action_kind"]
        if action_kind not in ALLOWED_ACTION_KINDS:
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.INVALID_ACTION_KIND, "sub_actions",
                f"recommended_sequence[{i}] action_kind {action_kind!r} "
                f"not in {sorted(ALLOWED_ACTION_KINDS)}",
            ))

        emitted_tier = item["evidence_tier"]
        canonical_tier = canonical_candidate.get("evidence_tier")
        if emitted_tier != canonical_tier:
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.EVIDENCE_TIER_MISMATCH, "sub_actions",
                f"recommended_sequence[{i}] evidence_tier {emitted_tier!r} "
                f"!= candidate canonical {canonical_tier!r}",
            ))

        try:
            emitted_savings = float(item["est_monthly_savings_usd"])
        except (TypeError, ValueError):
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.CANDIDATE_SAVINGS_MISMATCH, "sub_actions",
                f"recommended_sequence[{i}] est_monthly_savings_usd "
                f"is not numeric: {item['est_monthly_savings_usd']!r}",
            ))
        # Per-kind savings rule:
        #   add_vpc_endpoint      → savings == candidate canonical
        #   observe_and_reassess  → savings == 0 (observing doesn't save)
        # Both rules end with canonicalisation from a deterministic source.
        if action_kind == "observe_and_reassess":
            canonical_savings = 0.0
        else:
            canonical_savings = float(
                canonical_candidate.get("est_monthly_savings_usd", 0.0)
            )
        if abs(emitted_savings - canonical_savings) > MONTHLY_IMPACT_TOLERANCE:
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.CANDIDATE_SAVINGS_MISMATCH, "sub_actions",
                f"recommended_sequence[{i}] est_monthly_savings_usd "
                f"emitted={emitted_savings} canonical={canonical_savings} "
                f"(action_kind={action_kind})",
            ))

        running_total += canonical_savings
        if running_total > finding.monthly_impact_usd + MONTHLY_IMPACT_TOLERANCE:
            return _SubActionOutcome(None, _drop_step(
                raw, DropReason.CANDIDATE_SAVINGS_MISMATCH, "sub_actions",
                f"sub-action savings sum {running_total:.2f} exceeds "
                f"finding monthly_impact_usd {finding.monthly_impact_usd:.2f}",
            ))

        sub_rationale = item["rationale"]
        if not isinstance(sub_rationale, str):
            sub_rationale = str(sub_rationale)

        canonical_actions.append(SubAction(
            candidate_id=candidate_id,
            action_kind=action_kind,
            est_monthly_savings_usd=canonical_savings,
            evidence_tier=canonical_tier,
            rationale=sub_rationale,
        ))

    return _SubActionOutcome(kept_sub_actions=canonical_actions, dropped=None)


def _drop_step(raw: dict[str, Any], reason: DropReason, validator: str,
               detail: str) -> DroppedStep:
    """Build a DroppedStep — used directly by sub-action validation
    (which returns a DroppedStep rather than a ValidationOutcome)."""
    return DroppedStep(
        raw_emission=dict(raw),
        reason=reason.value,
        validator=validator,
        detail=detail,
    )


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
