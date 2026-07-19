"""
Rubric assertion vocabulary.

Two surfaces share this registry: the planner rubric (plan + findings
inputs) and the conversation rubric (TurnOutcome + scan + plan
inputs). `run_rubric` dispatches by surface; planner assertion types
and conversation assertion types live in separate handler tables but
share `_numeric_check` and the `CheckResult` / `LEVEL_*` vocabulary so
the runner only needs one assertion-loop loop.

Each assertion is a small handler with a signature scoped to its
surface. The vocabulary is intentionally tiny — add new types only
when a real fixture needs one.

Supported numeric operators on count-style assertions: equals, min, max.

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
    from analyzer.plan_conversation import TurnOutcome
    from patterns.base import Finding
    from presenters import ScanResult


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
    """Apply every planner assertion in order. Returns one CheckResult per assertion."""
    findings_by_id = {f.id: f for f in findings}
    return [_apply(a, plan, findings_by_id) for a in assertions]


def run_conversation_rubric(
    assertions: list[dict[str, Any]],
    outcome: "TurnOutcome",
    *,
    scan: "ScanResult",
    plan: PlanResult,
) -> list[CheckResult]:
    """Apply every conversation assertion in order.

    Conversation assertions check a `TurnOutcome` (the structured
    result of one Q&A cycle) against the scan/plan it was answered
    over. Mirrors the planner rubric's shape so the runner's
    pass/warn/fail loop is identical for both surfaces.
    """
    return [_apply_conversation(a, outcome, scan, plan) for a in assertions]


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


def _apply_conversation(
    assertion: dict[str, Any],
    outcome: "TurnOutcome",
    scan: "ScanResult",
    plan: PlanResult,
) -> CheckResult:
    atype = assertion["type"]
    handler = _CONVERSATION_HANDLERS.get(atype)
    level = _CONVERSATION_WARNING_TYPES.get(atype, LEVEL_GATE)
    if handler is None:
        return CheckResult(atype, False,
                           f"unknown conversation assertion type: {atype}",
                           level=level)
    try:
        ok, detail = handler(assertion, outcome, scan, plan)
    except Exception as e:
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

    Zero-match semantics: a non-empty `finding_id_evidence` filter that
    matches zero input findings is a malformed assertion (the author
    wrote a filter intending it to select something; selecting nothing
    is a typo, stale evidence key, or a nested-dict matcher that the
    shallow comparator cannot resolve). The check fails loud rather
    than passing vacuously. An empty filter `{}` still matches every
    finding by convention.
    """
    want = a.get("finding_id_evidence") or {}
    matching_ids = {
        fid for fid, f in findings_by_id.items()
        if all(f.evidence.get(k) == v for k, v in want.items())
    }
    if want and not matching_ids:
        return False, (
            f"malformed assertion: filter {want!r} matched zero of "
            f"{len(findings_by_id)} input findings"
        )
    if not matching_ids:
        return False, "no input findings provided"
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
        nested dict matching is NOT supported).

    Zero-match semantics: when any filter is present and the combined
    filter set matches zero input findings, the assertion is treated
    as malformed and fails loud. Previously this case silently passed
    ("no offenders found"), which masked typos in `for_pattern_id`
    (`"04"` vs `"004"`) and unresolvable nested matchers in
    `for_finding_evidence`. An assertion with both filters absent
    (forbidding the mode globally) retains its prior semantics.
    """
    mode = a["mode"]
    want = a.get("for_finding_evidence") or {}
    want_pattern_id = a.get("for_pattern_id")
    has_filter = bool(want) or want_pattern_id is not None

    if has_filter:
        matching_finding_ids = {
            fid for fid, f in findings_by_id.items()
            if (want_pattern_id is None or f.pattern_id == want_pattern_id)
            and all(f.evidence.get(k) == v for k, v in want.items())
        }
        if not matching_finding_ids:
            filter_repr = {
                **({"for_pattern_id": want_pattern_id}
                   if want_pattern_id is not None else {}),
                **({"for_finding_evidence": want} if want else {}),
            }
            return False, (
                f"malformed assertion: filter {filter_repr!r} matched zero "
                f"of {len(findings_by_id)} input findings"
            )
    else:
        matching_finding_ids = set(findings_by_id)

    offenders = []
    for step in plan.steps:
        if step.suggested_mode != mode:
            continue
        if step.finding_id not in matching_finding_ids:
            continue
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


# ---------------------------------------------------------------------------
# Conversation rubric (PR #9)
# ---------------------------------------------------------------------------

# Recommendation-language verbs flagged as warnings. Distinct from the
# action-verb HARD-DROP path in plan_conversation: that one catches
# past-tense execution implication ("I stopped", "I executed"); this
# one watches for present-tense recommendation strength so we can
# audit when "you should definitely" creeps into stale-tier answers.
# Pure warning per sign-off — promote to gate only after we have data.
STRONG_RECOMMENDATION_PATTERNS_STALE: tuple[str, ...] = (
    "you should definitely", "you must", "definitely", "absolutely",
    "right now", "immediately",
)


def _check_envelope_present(_a, outcome, _s, _p) -> tuple[bool, str]:
    if outcome.envelope is None:
        return False, "no envelope was produced (fell back before LLM path)"
    return True, "envelope parsed"


def _check_fallback_reason_present(a, outcome, _s, _p) -> tuple[bool, str]:
    """Adversarial fixtures use this to assert a specific fallback fired."""
    want = a["reason"]
    actual = outcome.fallback.value if outcome.fallback is not None else None
    if actual == want:
        return True, f"fallback={want!r}"
    return False, f"fallback={actual!r} expected {want!r}"


def _check_no_fallback(_a, outcome, _s, _p) -> tuple[bool, str]:
    if outcome.fallback is None:
        return True, "no fallback (validated answer surfaced)"
    return False, f"fallback fired: {outcome.fallback.value}"


def _check_turn_kind(a, outcome, _s, _p) -> tuple[bool, str]:
    want = a["equals"]
    if outcome.turn.turn_kind == want:
        return True, f"turn_kind={want!r}"
    return False, f"turn_kind={outcome.turn.turn_kind!r} expected {want!r}"


def _check_cited_finding_ids_subset(_a, outcome, scan, _p) -> tuple[bool, str]:
    """Floor invariant — every cited id must exist in the scan."""
    known = {f.id for f in scan.findings}
    unknown = [fid for fid in outcome.turn.cited_finding_ids if fid not in known]
    if unknown:
        return False, f"cited unknown finding ids: {unknown}"
    return True, "all cited ids known"


def _check_answer_cites_finding(a, outcome, _s, _p) -> tuple[bool, str]:
    want = a["finding_id"]
    if want in outcome.turn.cited_finding_ids:
        return True, f"cited {want!r}"
    return False, (f"cited {list(outcome.turn.cited_finding_ids)!r} "
                   f"missing required {want!r}")


def _check_answer_scope(a, outcome, _s, _p) -> tuple[bool, str]:
    want_scope = bool(a["in_scope"])
    env = outcome.envelope
    if env is None:
        # No envelope means we never reached the LLM. Pre-routed
        # refusals count as out-of-scope; expired/parse_failed/etc are
        # neither in-scope nor out-of-scope answers — they're
        # framework refusals and should fail this assertion to make
        # the author choose a different rubric.
        if outcome.pre_routed_category is not None:
            actual = False
            return (actual == want_scope,
                    f"pre-routed; in_scope=False (want {want_scope})")
        return False, "no envelope produced — cannot judge in_scope"
    return (env.is_in_scope == want_scope,
            f"in_scope={env.is_in_scope} expected {want_scope}")


def _check_answer_no_invented_dollar(_a, outcome, scan, plan) -> tuple[bool, str]:
    """Floor invariant — every inline `$N` in the SURFACED answer
    matches a canonical scan/plan value within $0.01.

    The runtime validator already enforces this for the LLM call;
    this assertion catches a future regression where the validator
    weakens. Under regex-strict-rules (PR #9) we scan the surfaced
    prose directly rather than a structured citation list — the
    answer IS the canonical citation surface.
    """
    if outcome.envelope is None:
        return True, "no envelope; nothing to check"
    from analyzer.plan_conversation import (
        DOLLAR_TOLERANCE,
        INLINE_DOLLAR_RE,
        _canonical_dollar_universe,
    )
    universe = _canonical_dollar_universe(scan, plan)
    offenders: list[float] = []
    for match in INLINE_DOLLAR_RE.finditer(outcome.surfaced_text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            offenders.append(float("nan"))
            continue
        if not any(abs(value - c) <= DOLLAR_TOLERANCE for c in universe):
            offenders.append(value)
    if offenders:
        return False, f"non-canonical dollars in answer: {offenders}"
    return True, "all inline dollars canonical"


def _check_answer_no_action_past_tense(_a, outcome, _s, _p) -> tuple[bool, str]:
    """Warning-level — past-tense action language in the SURFACED prose.

    The runtime validator drops the envelope, replacing it with a
    deterministic IMPLIED_ACTION_TEMPLATE. The surfaced text is then
    safe by construction. This warning audits whether the LLM keeps
    *trying* — high warning rates suggest the prompt isn't holding.
    """
    from analyzer.plan_conversation import PAST_TENSE_ACTION_PATTERNS
    text = outcome.surfaced_text
    offenders = [p.pattern for p in PAST_TENSE_ACTION_PATTERNS if p.search(text)]
    if offenders:
        return False, f"past-tense action language surfaced: {offenders}"
    return True, "no past-tense action language"


def _check_stale_tier_softens(_a, outcome, _s, _p) -> tuple[bool, str]:
    """Warning-level — stale-tier answers should use softening
    language, not strong recommendation. Active only on stale-tier
    outcomes; fresh/aging answers pass vacuously.
    """
    from analyzer.plan_conversation import FreshnessTier
    if outcome.freshness_tier != FreshnessTier.STALE:
        return True, f"tier={outcome.freshness_tier.value}; rule inactive"
    text = outcome.surfaced_text.lower()
    offenders = [p for p in STRONG_RECOMMENDATION_PATTERNS_STALE if p in text]
    if offenders:
        return False, f"stale-tier answer used strong language: {offenders}"
    return True, "stale-tier answer softened"


def _check_pre_routed(a, outcome, _s, _p) -> tuple[bool, str]:
    want = a.get("category")  # optional — assertion may just require any pre-route
    cat = outcome.pre_routed_category
    if cat is None:
        return False, "question was not pre-routed"
    if want is not None and cat.value != want:
        return False, f"pre-routed category={cat.value!r} expected {want!r}"
    return True, f"pre-routed category={cat.value!r}"


_CONVERSATION_HANDLERS = {
    "envelope_present": _check_envelope_present,
    "fallback_reason_present": _check_fallback_reason_present,
    "no_fallback": _check_no_fallback,
    "turn_kind": _check_turn_kind,
    "cited_finding_ids_subset": _check_cited_finding_ids_subset,
    "answer_cites_finding": _check_answer_cites_finding,
    "answer_scope": _check_answer_scope,
    "answer_no_invented_dollar": _check_answer_no_invented_dollar,
    "answer_no_action_past_tense": _check_answer_no_action_past_tense,
    "stale_tier_softens": _check_stale_tier_softens,
    "pre_routed": _check_pre_routed,
}


_CONVERSATION_WARNING_TYPES: dict[str, str] = {
    "answer_no_action_past_tense": LEVEL_WARNING,
    "stale_tier_softens": LEVEL_WARNING,
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
