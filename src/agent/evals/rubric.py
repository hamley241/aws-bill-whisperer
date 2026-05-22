"""
Rubric assertion vocabulary.

Each assertion is a small class with `check(plan, findings) -> CheckResult`.
The vocabulary is intentionally tiny — add new types only when a real
fixture needs one.

Supported numeric operators on dropped_steps_count and similar counters:
  equals, min, max

Adversarial fixtures (planner stress tests with deliberately-broken
LLM responses) use `min: 1` to assert the validator *did* drop bad
emissions. Normal fixtures use `equals: 0` to assert clean output.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SRC = Path(__file__).parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.schemas import PlanResult

if False:  # TYPE_CHECKING — avoid runtime import cycle
    from patterns.base import Finding


# Warning-level rubric checks surface in eval output and the aggregate
# summary but do NOT affect process exit code. Promote to a CI gate
# (level="gate") in a later PR once the check is empirically reliable.
LEVEL_GATE = "gate"
LEVEL_WARNING = "warning"

# Verbs forbidden in rationales of sub-actions whose evidence_tier is
# "inferred". Intentionally short — promote a verb once the warning
# fires reliably on real model output.
CONFIDENT_VERBS_INFERRED: tuple[str, ...] = (
    "shows",
    "confirmed",
    "measured",
)


@dataclass
class CheckResult:
    assertion_type: str
    ok: bool
    detail: str = ""
    level: str = LEVEL_GATE   # LEVEL_GATE | LEVEL_WARNING


def load_rubric(path: Path) -> list[dict[str, Any]]:
    """Load an assertions YAML file. Returns a list of assertion dicts."""
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: rubric must be a list, got {type(data).__name__}")
    for item in data:
        if not isinstance(item, dict) or "type" not in item:
            raise ValueError(f"{path}: every assertion must be a dict with a 'type' key")
    return data


def run_rubric(
    assertions: list[dict[str, Any]],
    plan: PlanResult,
    findings: list["Finding"],
) -> list[CheckResult]:
    """Apply every assertion in order. Returns one CheckResult per assertion."""
    findings_by_id = {f.id: f for f in findings}
    return [_apply(a, plan, findings_by_id) for a in assertions]


def _apply(assertion: dict[str, Any], plan: PlanResult,
           findings_by_id: dict[str, "Finding"]) -> CheckResult:
    atype = assertion["type"]
    handler = _HANDLERS.get(atype)
    level = _WARNING_TYPES.get(atype, LEVEL_GATE)
    if handler is None:
        return CheckResult(atype, False, f"unknown assertion type: {atype}",
                           level=level)
    try:
        ok, detail = handler(assertion, plan, findings_by_id)
    except Exception as e:  # surface as a failure, not a crash
        return CheckResult(atype, False, f"assertion raised: {e}", level=level)
    return CheckResult(atype, ok, detail, level=level)


# ---------------------------------------------------------------------------
# Assertion implementations — small, no shared state.
# ---------------------------------------------------------------------------

def _check_structural_valid_json(_a, plan: PlanResult, _f) -> tuple[bool, str]:
    if plan.status == "validation_failed" and not plan.steps:
        return False, f"plan.status={plan.status}; no steps survived"
    return True, "plan is structurally valid"


def _check_status(a: dict, plan: PlanResult, _f) -> tuple[bool, str]:
    want = a["equals"]
    if plan.status != want:
        return False, f"status={plan.status!r} expected {want!r}"
    return True, f"status={plan.status!r}"


def _check_dropped_steps_count(a: dict, plan: PlanResult, _f) -> tuple[bool, str]:
    return _numeric_check(len(plan.dropped_steps), a, "dropped_steps_count")


def _check_steps_count(a: dict, plan: PlanResult, _f) -> tuple[bool, str]:
    return _numeric_check(len(plan.steps), a, "steps_count")


def _check_parse_retry_count(a: dict, plan: PlanResult, _f) -> tuple[bool, str]:
    return _numeric_check(plan.parse_retry_count, a, "parse_retry_count")


def _check_total_impact_within_input_sum(_a, plan: PlanResult,
                                         findings_by_id: dict) -> tuple[bool, str]:
    """The sum of planned impact never exceeds the sum of input impact.
    (If it does, the planner has been overcounting or accepting
    duplicates — both safety failures.)"""
    cap = sum(f.monthly_impact_usd for f in findings_by_id.values())
    if plan.total_monthly_impact_usd > cap + 0.01:
        return False, (f"plan total {plan.total_monthly_impact_usd:.2f} "
                       f"> input sum {cap:.2f}")
    return True, f"{plan.total_monthly_impact_usd:.2f} ≤ {cap:.2f}"


def _check_includes_finding_with_evidence(a: dict, plan: PlanResult,
                                          findings_by_id: dict) -> tuple[bool, str]:
    """Assert the plan includes any finding whose evidence matches.

    YAML:
      - type: includes_finding
        finding_id_evidence: { terraform_managed: true }
    """
    want = a.get("finding_id_evidence") or {}
    matching_ids = {
        fid for fid, f in findings_by_id.items()
        if all(f.evidence.get(k) == v for k, v in want.items())
    }
    if not matching_ids:
        return False, f"no input finding matches evidence {want!r}"
    planned_ids = {s.finding_id for s in plan.steps}
    if matching_ids & planned_ids:
        return True, f"plan includes one of {sorted(matching_ids)}"
    return False, (f"none of {sorted(matching_ids)} appear in plan "
                   f"(planned: {sorted(planned_ids)})")


def _check_never_recommends_mode(a: dict, plan: PlanResult,
                                 findings_by_id: dict) -> tuple[bool, str]:
    """Assert no step suggests `mode` for any finding matching the filters.

    Filters apply **conjunctively** — every filter present on the
    assertion must match for a finding to be counted as a candidate
    offender. With no filters set, every finding matches (i.e., the
    assertion forbids the mode globally for every step in the plan).

    Supported filters:
      - `for_pattern_id`: optional string. The finding's `pattern_id`
        must equal this value exactly.
      - `for_finding_evidence`: optional dict. Each key must appear as
        a top-level key in `finding.evidence`, and its value must
        equal the finding's value under that key (shallow equality;
        nested dict matching is NOT supported — see the rubric
        vacuous-pass follow-up).

    Caveat — silent vacuous pass: a filter that matches zero findings
    produces no offenders and returns True. A typo in `for_pattern_id`
    ("04" vs "004") or a nested-dict in `for_finding_evidence` will
    silently neuter the assertion. The broader fix (fail loud on
    zero-match) is tracked as a deferred rubric follow-up
    (`project_rubric_vacuous_pass`).
    """
    mode = a["mode"]
    want = a.get("for_finding_evidence") or {}
    want_pattern_id = a.get("for_pattern_id")
    offenders = []
    for step in plan.steps:
        if step.suggested_mode != mode:
            continue
        f = findings_by_id.get(step.finding_id)
        if f is None:
            continue
        if want_pattern_id is not None and f.pattern_id != want_pattern_id:
            continue
        if all(f.evidence.get(k) == v for k, v in want.items()):
            offenders.append(step.finding_id)
    if offenders:
        return False, f"steps for findings {offenders} use forbidden mode {mode!r}"
    return True, f"no step uses {mode!r} for matching findings"


def _check_order_rank_unique(_a, plan: PlanResult, _f) -> tuple[bool, str]:
    ranks = [s.order_rank for s in plan.steps]
    if len(ranks) != len(set(ranks)):
        return False, f"duplicate order_ranks: {ranks}"
    return True, "ranks unique"


def _check_dropped_reason_present(a: dict, plan: PlanResult, _f) -> tuple[bool, str]:
    """Adversarial fixtures use this to assert a specific drop reason fired."""
    want = a["reason"]
    found = {d.reason for d in plan.dropped_steps}
    if want in found:
        return True, f"reason {want!r} present"
    return False, f"reason {want!r} not in drops: {sorted(found)}"


def _check_rationale_hedges_inferred(_a, plan: PlanResult, _f) -> tuple[bool, str]:
    """Warning: scan rationales of `evidence_tier="inferred"` sub-actions
    for confident verbs. Pure warning — only the rationales of inferred
    sub-actions are scanned (not free-form plan text), to keep the false-
    positive rate manageable while the heuristic matures.
    """
    offenders: list[str] = []
    for step in plan.steps:
        if not step.recommended_sequence:
            continue
        for sub in step.recommended_sequence:
            if sub.evidence_tier != "inferred":
                continue
            text = (sub.rationale or "").lower()
            for verb in CONFIDENT_VERBS_INFERRED:
                if verb in text:
                    offenders.append(
                        f"{step.finding_id}/{sub.candidate_id}: {verb!r}"
                    )
                    break
    if offenders:
        return False, "confident verbs in inferred rationales: " + "; ".join(offenders)
    return True, "no confident verbs in inferred rationales"


_HANDLERS = {
    "structural_valid_json": _check_structural_valid_json,
    "status": _check_status,
    "dropped_steps_count": _check_dropped_steps_count,
    "steps_count": _check_steps_count,
    "parse_retry_count": _check_parse_retry_count,
    "total_impact_within_input_sum": _check_total_impact_within_input_sum,
    "includes_finding": _check_includes_finding_with_evidence,
    "never_recommends_mode": _check_never_recommends_mode,
    "order_rank_unique": _check_order_rank_unique,
    "dropped_reason_present": _check_dropped_reason_present,
    "rationale_hedges_inferred": _check_rationale_hedges_inferred,
}

# Assertion types that surface as warnings rather than CI gates. The
# rubric still records the result; the runner excludes warnings from
# the failing set used for exit code.
_WARNING_TYPES: dict[str, str] = {
    "rationale_hedges_inferred": LEVEL_WARNING,
}


def _numeric_check(actual: int, a: dict, label: str) -> tuple[bool, str]:
    """Shared logic for assertions with equals/min/max operators."""
    if "equals" in a:
        target = a["equals"]
        return actual == target, f"{label}={actual} (want {target})"
    if "min" in a:
        target = a["min"]
        return actual >= target, f"{label}={actual} (min {target})"
    if "max" in a:
        target = a["max"]
        return actual <= target, f"{label}={actual} (max {target})"
    return False, f"{label} assertion missing equals/min/max operator"
