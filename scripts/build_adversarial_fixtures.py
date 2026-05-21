"""
One-shot generator for PR 3 adversarial fixtures.

This script is a build aid, not part of the runtime. Each fixture lives
under `src/agent/evals/fixtures/<scenario>/` and consists of:

  - findings.json         the input findings (canonical p001 shape)
  - goal                  one-line goal text
  - recorded_response.json envelope with the deliberately-broken LLM text
  - assertions.yaml       rubric asserting the expected drop reasons

Re-run with: `python scripts/build_adversarial_fixtures.py`
The files are committed; this script exists so the regeneration is
deterministic and the JSON-inside-JSON escaping isn't done by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "src" / "agent" / "evals" / "fixtures"


def _finding(
    *,
    id_: str,
    resource_id: str,
    monthly_impact_usd: float,
    terraform_managed: bool,
    safe_to_fix: bool,
    risk_tier: str = "medium",
    extra_evidence: dict | None = None,
) -> dict:
    """Build a p001-shaped finding."""
    evidence = {
        "size_gb": 200,
        "volume_type": "gp2",
        "age_days": 45,
        "has_snapshot": safe_to_fix,
        "latest_snapshot_age_days": 5 if safe_to_fix else None,
        "snapshot_count": 1 if safe_to_fix else 0,
        "terraform_managed": terraform_managed,
        "tags": {"Env": "prod"} | (
            {"managed-by-terraform": "true"} if terraform_managed else {}
        ),
    }
    if extra_evidence:
        evidence.update(extra_evidence)
    return {
        "id": id_,
        "schema_version": "1",
        "pattern_id": "001",
        "resource_id": resource_id,
        "resource_type": "EBS Volume",
        "resource_arn": f"arn:aws:ec2:us-east-1::volume/{resource_id}",
        "account_id": "123456789012",
        "region": "us-east-1",
        "monthly_impact_usd": monthly_impact_usd,
        "confidence": 0.85,
        "risk_tier": risk_tier,
        "summary": f"Delete {evidence['size_gb']}GB {evidence['volume_type']} volume",
        "explanation": None,
        "fix_command": f"aws ec2 delete-volume --volume-id {resource_id}",
        "fix_pr": None,
        "evidence": evidence,
        "metadata": {},
        "safe_to_fix": safe_to_fix,
    }


# Canonical IDs (UUID v4 form) so fixtures stay deterministic.
ID_A = "11111111-1111-4111-8111-111111111111"  # tf-managed, safe (modes: all four)
ID_B = "22222222-2222-4222-8222-222222222222"  # NOT tf-managed, NOT safe (dry_run, command)
ID_C = "33333333-3333-4333-8333-333333333333"  # NOT tf-managed, safe (dry_run, command, api_call)
ID_GHOST = "99999999-9999-4999-8999-999999999999"  # never in any fixture's input set

FINDING_A = _finding(
    id_=ID_A, resource_id="vol-aaaa", monthly_impact_usd=80.0,
    terraform_managed=True, safe_to_fix=True, risk_tier="high",
)
FINDING_B = _finding(
    id_=ID_B, resource_id="vol-bbbb", monthly_impact_usd=12.0,
    terraform_managed=False, safe_to_fix=False, risk_tier="low",
)
FINDING_C = _finding(
    id_=ID_C, resource_id="vol-cccc", monthly_impact_usd=32.0,
    terraform_managed=False, safe_to_fix=True, risk_tier="medium",
)


def _recording(response_obj: dict | str, *, drop_reason_under_test: str,
               note: str) -> dict:
    if isinstance(response_obj, dict):
        text = json.dumps(response_obj, indent=2)
    else:
        text = response_obj
    return {
        "responses": [text],
        "metadata": {
            "model": "hand-crafted-adversarial",
            "provider": "fixture",
            "boundary_crossed": False,
            "kind": "adversarial",
            "drop_reason_under_test": drop_reason_under_test,
            "prompt_template": "savings_plan",
            "prompt_template_version": "v1",
            "note": note,
        },
    }


def _write(scenario: str, *, findings: list[dict], goal: str,
           recording: dict, assertions: str) -> None:
    d = FIXTURES / scenario
    d.mkdir(parents=True, exist_ok=True)
    (d / "findings.json").write_text(
        json.dumps(findings, indent=2) + "\n", encoding="utf-8"
    )
    (d / "goal").write_text(goal.strip() + "\n", encoding="utf-8")
    (d / "recorded_response.json").write_text(
        json.dumps(recording, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (d / "assertions.yaml").write_text(
        dedent(assertions).lstrip() + "\n", encoding="utf-8"
    )
    print(f"wrote {scenario}/")


# ---------------------------------------------------------------------------
# 1. unknown_finding_id — emit a step citing a finding that wasn't in the input.
# ---------------------------------------------------------------------------

_write(
    "adversarial_unknown_finding_id",
    findings=[FINDING_A],
    goal=(
        "Adversarial test — the recorded LLM response cites a finding_id "
        "that does not appear in the input. Validator must drop it."
    ),
    recording=_recording(
        {
            "summary": "Plan that invents a finding_id not in the input set.",
            "steps": [
                {
                    "finding_id": ID_GHOST,
                    "suggested_mode": "pr",
                    "monthly_impact_usd": 80.0,
                    "rationale": "Pretending this volume exists.",
                    "order_rank": 1,
                },
            ],
        },
        drop_reason_under_test="unknown_finding_id",
        note=(
            "Hand-crafted: the LLM hallucinates a UUID. validator step 2 "
            "(finding_id lookup) must drop with DropReason.UNKNOWN_FINDING_ID."
        ),
    ),
    assertions="""
        # adversarial: unknown_finding_id
        # Validator must drop emissions whose finding_id is not in the input set.

        - type: status
          equals: validation_failed

        - type: steps_count
          equals: 0

        - type: dropped_steps_count
          equals: 1

        - type: parse_retry_count
          equals: 0

        - type: dropped_reason_present
          reason: unknown_finding_id

        - type: dropped_step_reasons
          reasons:
            - unknown_finding_id

        - type: total_impact_within_input_sum
    """,
)


# ---------------------------------------------------------------------------
# 2. unsupported_mode — emit a mode not in the resolver-exposed set for the
#    finding. vol-bbbb is NOT terraform-managed AND NOT safe_to_fix, so its
#    available_modes = {dry_run, command}. Suggesting "api_call" is unsupported.
# ---------------------------------------------------------------------------

_write(
    "adversarial_unsupported_mode",
    findings=[FINDING_B],
    goal=(
        "Adversarial test — the recorded response suggests api_call on a "
        "finding where the resolver does not expose api_call. Validator "
        "must drop with unsupported_mode."
    ),
    recording=_recording(
        {
            "summary": "Plan that picks a mode the pattern does not expose.",
            "steps": [
                {
                    "finding_id": ID_B,
                    "suggested_mode": "api_call",
                    "monthly_impact_usd": 12.0,
                    "rationale": (
                        "LLM ignored the available_modes list and picked "
                        "api_call on a finding with safe_to_fix=False."
                    ),
                    "order_rank": 1,
                },
            ],
        },
        drop_reason_under_test="unsupported_mode",
        note=(
            "vol-bbbb has safe_to_fix=False and terraform_managed=False, so "
            "_p001_modes returns {dry_run, command}. api_call must be dropped."
        ),
    ),
    assertions="""
        # adversarial: unsupported_mode
        # Validator must drop modes the resolver does not expose for the finding.

        - type: status
          equals: validation_failed

        - type: steps_count
          equals: 0

        - type: dropped_steps_count
          equals: 1

        - type: parse_retry_count
          equals: 0

        - type: dropped_reason_present
          reason: unsupported_mode

        - type: dropped_step_reasons
          reasons:
            - unsupported_mode
    """,
)


# ---------------------------------------------------------------------------
# 3. monthly_impact_missing — emit the key but with a null value. (Omitting
#    the key entirely fails the shape check and is logged as schema_invalid;
#    "missing" in the validator's vocabulary means present-but-unusable.)
# ---------------------------------------------------------------------------

_write(
    "adversarial_monthly_impact_missing",
    findings=[FINDING_A],
    goal=(
        "Adversarial test — the recorded response emits monthly_impact_usd: "
        "null. Validator must drop with monthly_impact_missing."
    ),
    recording=_recording(
        {
            "summary": "Plan with a null monthly_impact_usd.",
            "steps": [
                {
                    "finding_id": ID_A,
                    "suggested_mode": "pr",
                    "monthly_impact_usd": None,
                    "rationale": "LLM emitted the key but left the value null.",
                    "order_rank": 1,
                },
            ],
        },
        drop_reason_under_test="monthly_impact_missing",
        note=(
            "Key present, value null. Validator step 4 catches None and "
            "non-numeric values as MONTHLY_IMPACT_MISSING; omitting the key "
            "entirely would be SCHEMA_INVALID (shape check, step 1). The "
            "shape-vs-missing split is tracked in issue #3 — when that "
            "lands, this fixture should be migrated to emit an omitted "
            "key (the realistic production shape)."
        ),
    ),
    assertions="""
        # adversarial: monthly_impact_missing
        # Validator must drop emissions where monthly_impact_usd is null/non-numeric.

        - type: status
          equals: validation_failed

        - type: steps_count
          equals: 0

        - type: dropped_steps_count
          equals: 1

        - type: parse_retry_count
          equals: 0

        - type: dropped_reason_present
          reason: monthly_impact_missing

        - type: dropped_step_reasons
          reasons:
            - monthly_impact_missing
    """,
)


# ---------------------------------------------------------------------------
# 4. monthly_impact_mismatch — emit a numeric value that differs from the
#    canonical Finding.monthly_impact_usd by more than $0.01.
# ---------------------------------------------------------------------------

_write(
    "adversarial_monthly_impact_mismatch",
    findings=[FINDING_A],
    goal=(
        "Adversarial test — the recorded response emits monthly_impact_usd "
        "that disagrees with the canonical Finding value. Validator must "
        "drop with monthly_impact_mismatch."
    ),
    recording=_recording(
        {
            "summary": "Plan that lies about the dollar amount.",
            "steps": [
                {
                    "finding_id": ID_A,
                    "suggested_mode": "pr",
                    "monthly_impact_usd": 999.99,
                    "rationale": "LLM invented a dollar figure.",
                    "order_rank": 1,
                },
            ],
        },
        drop_reason_under_test="monthly_impact_mismatch",
        note=(
            "Canonical value is 80.0; emitted is 999.99. Difference >> $0.01 "
            "tolerance, so validator step 4 drops with MONTHLY_IMPACT_MISMATCH."
        ),
    ),
    assertions="""
        # adversarial: monthly_impact_mismatch
        # Validator must drop emissions whose dollar value disagrees with the
        # canonical Finding.monthly_impact_usd beyond the $0.01 tolerance.

        - type: status
          equals: validation_failed

        - type: steps_count
          equals: 0

        - type: dropped_steps_count
          equals: 1

        - type: parse_retry_count
          equals: 0

        - type: dropped_reason_present
          reason: monthly_impact_mismatch

        - type: dropped_step_reasons
          reasons:
            - monthly_impact_mismatch
    """,
)


# ---------------------------------------------------------------------------
# 5. schema_invalid — omit a required key (rationale) so the shape check
#    drops the emission.
# ---------------------------------------------------------------------------

_write(
    "adversarial_schema_invalid",
    findings=[FINDING_A],
    goal=(
        "Adversarial test — the recorded response emits a step missing the "
        "required `rationale` key. Validator must drop with schema_invalid."
    ),
    recording=_recording(
        {
            "summary": "Plan missing the rationale field.",
            "steps": [
                {
                    "finding_id": ID_A,
                    "suggested_mode": "pr",
                    "monthly_impact_usd": 80.0,
                    "order_rank": 1,
                    # rationale omitted on purpose
                },
            ],
        },
        drop_reason_under_test="schema_invalid",
        note=(
            "Step is missing the `rationale` key. Validator step 1 (_REQUIRED_KEYS "
            "shape check) drops with SCHEMA_INVALID."
        ),
    ),
    assertions="""
        # adversarial: schema_invalid
        # Validator's shape check must drop emissions missing required keys.

        - type: status
          equals: validation_failed

        - type: steps_count
          equals: 0

        - type: dropped_steps_count
          equals: 1

        - type: parse_retry_count
          equals: 0

        - type: dropped_reason_present
          reason: schema_invalid

        - type: dropped_step_reasons
          reasons:
            - schema_invalid
    """,
)


# ---------------------------------------------------------------------------
# 6. all_steps_dropped — three emissions, three different reasons, none
#    survive. End-to-end: status must be validation_failed.
# ---------------------------------------------------------------------------

_write(
    "all_steps_dropped",
    findings=[FINDING_A, FINDING_B, FINDING_C],
    goal=(
        "End-to-end adversarial test — every emission is broken in a "
        "different way. The planner must surface zero steps and "
        "status=validation_failed."
    ),
    recording=_recording(
        {
            "summary": "Three broken emissions, three different drop reasons.",
            "steps": [
                {
                    # unknown_finding_id
                    "finding_id": ID_GHOST,
                    "suggested_mode": "dry_run",
                    "monthly_impact_usd": 50.0,
                    "rationale": "Bogus finding id.",
                    "order_rank": 1,
                },
                {
                    # monthly_impact_mismatch (real finding, wrong dollar)
                    "finding_id": ID_A,
                    "suggested_mode": "pr",
                    "monthly_impact_usd": 1.0,
                    "rationale": "Real finding, wrong dollar.",
                    "order_rank": 2,
                },
                {
                    # unsupported_mode on vol-bbbb (no api_call)
                    "finding_id": ID_B,
                    "suggested_mode": "api_call",
                    "monthly_impact_usd": 12.0,
                    "rationale": "Real finding, mode not exposed.",
                    "order_rank": 3,
                },
            ],
        },
        drop_reason_under_test="multiple",
        note=(
            "Three emissions, three distinct drop reasons. status must be "
            "validation_failed because zero steps survive."
        ),
    ),
    assertions="""
        # adversarial end-to-end: all steps dropped
        # Three broken emissions, none survive. Plan status must be validation_failed.

        - type: status
          equals: validation_failed

        - type: steps_count
          equals: 0

        - type: dropped_steps_count
          equals: 3

        - type: parse_retry_count
          equals: 0

        - type: dropped_step_reasons
          reasons:
            - unknown_finding_id
            - monthly_impact_mismatch
            - unsupported_mode

        - type: total_impact_within_input_sum
    """,
)


# ---------------------------------------------------------------------------
# 7. mixed_valid_and_adversarial — one valid emission + two broken ones in
#    the same response. The valid one survives; the broken ones are dropped.
# ---------------------------------------------------------------------------

_write(
    "mixed_valid_and_adversarial",
    findings=[FINDING_A, FINDING_B, FINDING_C],
    goal=(
        "Mixed adversarial — one valid emission survives alongside two "
        "broken ones in the same response. Plan must surface only the "
        "valid step, with both drops recorded."
    ),
    recording=_recording(
        {
            "summary": (
                "Open a PR for the largest tagged volume. Two other "
                "emissions are deliberately broken to verify the validator "
                "drops them while the valid step survives."
            ),
            "steps": [
                {
                    # VALID — tf-managed, safe, pr mode is exposed.
                    "finding_id": ID_A,
                    "suggested_mode": "pr",
                    "monthly_impact_usd": 80.0,
                    "rationale": (
                        "Terraform-managed, snapshot exists, largest single "
                        "saving. PR-mode keeps the change auditable."
                    ),
                    "order_rank": 1,
                },
                {
                    # unknown_finding_id
                    "finding_id": ID_GHOST,
                    "suggested_mode": "dry_run",
                    "monthly_impact_usd": 5.0,
                    "rationale": "Invented finding.",
                    "order_rank": 2,
                },
                {
                    # unsupported_mode on vol-bbbb
                    "finding_id": ID_B,
                    "suggested_mode": "api_call",
                    "monthly_impact_usd": 12.0,
                    "rationale": "Mode not exposed for this finding.",
                    "order_rank": 3,
                },
            ],
        },
        drop_reason_under_test="mixed",
        note=(
            "One valid step + two adversarial drops. Status must be ok "
            "because the validator promoted at least one PlanStep."
        ),
    ),
    assertions="""
        # mixed adversarial — valid + bad emissions in the same response.
        # The valid step must survive; the bad emissions must be dropped.

        - type: structural_valid_json

        - type: status
          equals: ok

        - type: steps_count
          equals: 1

        - type: dropped_steps_count
          equals: 2

        - type: parse_retry_count
          equals: 0

        - type: dropped_step_reasons
          reasons:
            - unknown_finding_id
            - unsupported_mode

        - type: total_impact_within_input_sum

        - type: order_rank_unique

        # The tf-managed finding must be in the surviving plan.
        - type: includes_finding
          finding_id_evidence:
            terraform_managed: true
    """,
)


print("done.")
