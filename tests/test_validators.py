"""
Heavy tests for src/agent/validators.py — the safety boundary.

CLAUDE.md's "LLM proposes; framework disposes" rule is enforced here.
If a validator is wrong, the rest of the PR's safety story collapses,
so we over-test on purpose.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.modes import AvailableModesResolver
from agent.schemas import DropReason
from agent.validators import (
    MONTHLY_IMPACT_TOLERANCE,
    validate_step,
    validate_steps,
)
from patterns.base import Finding, RemediationMode, RiskTier


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.9,
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
        safe_to_fix=True,
        evidence={"terraform_managed": True, "size_gb": 100, "age_days": 30},
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _raw(**overrides) -> dict:
    defaults = dict(
        finding_id="placeholder",   # caller usually overrides with the real id
        suggested_mode="dry_run",
        monthly_impact_usd=42.5,
        rationale="why",
        order_rank=1,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# The 8 required test cases from the brief — explicit + named.
# ---------------------------------------------------------------------------

class TestRequiredCases:
    def test_unknown_finding_id_dropped(self):
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id="ghost-id")], findings=[f],
        )
        assert kept == []
        assert len(dropped) == 1
        assert dropped[0].reason == DropReason.UNKNOWN_FINDING_ID.value
        assert "ghost-id" in dropped[0].detail

    def test_unsupported_mode_dropped(self):
        # Make finding unsafe so api_call is NOT in the resolver's set.
        f = _finding(safe_to_fix=False,
                     evidence={"terraform_managed": False})
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, suggested_mode="api_call")],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.UNSUPPORTED_MODE.value
        assert "api_call" in dropped[0].detail

    def test_monthly_impact_mismatch_dropped(self):
        f = _finding(monthly_impact_usd=42.50)
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, monthly_impact_usd=99.99)],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.MONTHLY_IMPACT_MISMATCH.value
        assert "42.5" in dropped[0].detail
        assert "99.99" in dropped[0].detail

    def test_monthly_impact_missing_dropped(self):
        f = _finding()
        raw = _raw(finding_id=f.id)
        del raw["monthly_impact_usd"]
        kept, dropped = validate_steps([raw], findings=[f])
        # missing key is caught by the schema-required-fields check, since
        # monthly_impact_usd is in _REQUIRED_KEYS. The reason is SCHEMA_INVALID.
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_monthly_impact_present_but_null_dropped(self):
        # Distinct from "missing": LLM emits the key but with null.
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, monthly_impact_usd=None)],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.MONTHLY_IMPACT_MISSING.value

    def test_missing_required_field_dropped(self):
        f = _finding()
        raw = _raw(finding_id=f.id)
        del raw["order_rank"]
        kept, dropped = validate_steps([raw], findings=[f])
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value
        assert "order_rank" in dropped[0].detail

    def test_all_steps_dropped_signals_validation_failed(self):
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id="ghost-1"), _raw(finding_id="ghost-2")],
            findings=[f],
        )
        assert kept == []
        assert len(dropped) == 2
        # status is computed in the planner from `kept == []`; the
        # validators themselves don't compute status. Test the planner
        # status mapping in test_planner.

    def test_valid_p001_pr_step_accepted(self):
        f = _finding(safe_to_fix=True,
                     evidence={"terraform_managed": True, "size_gb": 100})
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, suggested_mode="pr")],
            findings=[f],
        )
        assert dropped == []
        assert len(kept) == 1
        step = kept[0]
        assert step.finding_id == f.id
        assert step.pattern_id == "001"
        assert step.suggested_mode == "pr"
        assert step.monthly_impact_usd == 42.5  # canonical value used

    def test_api_call_for_unsafe_finding_dropped(self):
        # The fixture for the safety boundary that matters most.
        f = _finding(safe_to_fix=False,
                     evidence={"terraform_managed": True})
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, suggested_mode="api_call")],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.UNSUPPORTED_MODE.value


# ---------------------------------------------------------------------------
# Additional edge cases the brief said to add if I thought of any.
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_monthly_impact_at_tolerance_boundary_accepted(self):
        f = _finding(monthly_impact_usd=42.50)
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id,
                  monthly_impact_usd=42.50 + MONTHLY_IMPACT_TOLERANCE)],
            findings=[f],
        )
        assert dropped == []
        assert len(kept) == 1

    def test_monthly_impact_just_past_tolerance_rejected(self):
        f = _finding(monthly_impact_usd=42.50)
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id,
                  monthly_impact_usd=42.50 + MONTHLY_IMPACT_TOLERANCE + 0.001)],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.MONTHLY_IMPACT_MISMATCH.value

    def test_canonical_dollar_value_overrides_emitted(self):
        f = _finding(monthly_impact_usd=42.50)
        kept, _ = validate_steps(
            [_raw(finding_id=f.id, monthly_impact_usd=42.501)],  # within tol
            findings=[f],
        )
        # We accept the emission, but the PlanStep carries the canonical value.
        assert kept[0].monthly_impact_usd == 42.50

    def test_string_dollar_value_coerced(self):
        f = _finding(monthly_impact_usd=42.50)
        kept, _ = validate_steps(
            [_raw(finding_id=f.id, monthly_impact_usd="42.50")],
            findings=[f],
        )
        assert len(kept) == 1

    def test_non_numeric_dollar_value_dropped(self):
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, monthly_impact_usd="lots")],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.MONTHLY_IMPACT_MISSING.value

    def test_duplicate_finding_id_second_one_dropped(self):
        f = _finding()
        kept, dropped = validate_steps(
            [
                _raw(finding_id=f.id, order_rank=1),
                _raw(finding_id=f.id, order_rank=2),
            ],
            findings=[f],
        )
        assert len(kept) == 1
        assert kept[0].order_rank == 1
        assert dropped[0].reason == DropReason.DUPLICATE_FINDING_ID.value

    def test_non_string_finding_id_dropped(self):
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id=12345)],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_non_string_mode_dropped(self):
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, suggested_mode=["dry_run"])],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_string_order_rank_coerced(self):
        f = _finding()
        kept, _ = validate_steps(
            [_raw(finding_id=f.id, order_rank="3")],
            findings=[f],
        )
        assert kept[0].order_rank == 3

    def test_non_int_order_rank_dropped(self):
        f = _finding()
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, order_rank="third")],
            findings=[f],
        )
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_raw_emissions_not_a_list(self):
        f = _finding()
        kept, dropped = validate_steps("steps", findings=[f])  # type: ignore[arg-type]
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value
        assert "expected list" in dropped[0].detail

    def test_individual_emission_not_a_dict(self):
        f = _finding()
        kept, dropped = validate_steps(["a string"], findings=[f])  # type: ignore[list-item]
        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_universal_modes_always_offered(self):
        # A pattern not in the resolver's map still allows dry_run / command.
        f = _finding(pattern_id="999")  # unknown pattern
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, suggested_mode="dry_run")],
            findings=[f],
        )
        assert dropped == []
        assert len(kept) == 1

    def test_drop_preserves_raw_emission(self):
        f = _finding()
        emission = _raw(finding_id="ghost", weird_extra_field="hi")
        _, dropped = validate_steps([emission], findings=[f])
        assert dropped[0].raw_emission == emission


# ---------------------------------------------------------------------------
# Custom resolver injection
# ---------------------------------------------------------------------------

class TestCustomResolver:
    def test_resolver_dict_injection_restricts_modes(self):
        f = _finding()
        resolver = AvailableModesResolver(resolvers={
            "001": lambda finding: {RemediationMode.DRY_RUN},
        })
        kept, dropped = validate_steps(
            [_raw(finding_id=f.id, suggested_mode="api_call")],
            findings=[f],
            resolver=resolver,
        )
        assert kept == []
        assert dropped[0].reason == DropReason.UNSUPPORTED_MODE.value


# ---------------------------------------------------------------------------
# Direct unit tests on validate_step (single emission)
# ---------------------------------------------------------------------------

class TestValidateSingleStep:
    def test_returns_validation_outcome(self):
        f = _finding()
        outcome = validate_step(
            _raw(finding_id=f.id),
            findings_by_id={f.id: f},
            resolver=AvailableModesResolver(),
            seen_finding_ids=set(),
        )
        assert outcome.is_kept
        assert outcome.kept is not None
        assert outcome.dropped is None

    def test_dropped_outcome_has_no_step(self):
        f = _finding()
        outcome = validate_step(
            _raw(finding_id="ghost"),
            findings_by_id={f.id: f},
            resolver=AvailableModesResolver(),
            seen_finding_ids=set(),
        )
        assert not outcome.is_kept
        assert outcome.kept is None
        assert outcome.dropped is not None


# ---------------------------------------------------------------------------
# Sub-action validation (p006 — recommended_sequence)
# ---------------------------------------------------------------------------

def _nat_finding(
    *,
    candidates=None,
    monthly_impact_usd=32.4,
    pattern_id="006",
) -> Finding:
    if candidates is None:
        candidates = [
            {
                "candidate_id": "cand-gateway-s3",
                "service": "s3",
                "endpoint_type": "Gateway",
                "evidence_tier": "inferred",
                "supporting_inference_reason": "service_endpoint_supported_by_aws",
                "est_monthly_savings_usd": 0.0,
                "blast_radius": "low",
            },
        ]
    return Finding(
        resource_id="nat-test",
        resource_type="NAT Gateway",
        region="us-east-1",
        monthly_impact_usd=monthly_impact_usd,
        summary="nat",
        pattern_id=pattern_id,
        risk_tier=RiskTier.MEDIUM,
        evidence={
            "cost": {"cost_source": "hourly_only"},
            "inferred": {"endpoint_candidates": candidates},
        },
    )


def _sub(**overrides) -> dict:
    defaults = dict(
        candidate_id="cand-gateway-s3",
        action_kind="add_vpc_endpoint",
        est_monthly_savings_usd=0.0,
        evidence_tier="inferred",
        rationale="candidate, may save",
    )
    defaults.update(overrides)
    return defaults


def _nat_raw(f: Finding, **overrides) -> dict:
    """Top-level step raw emission for a NAT finding (mode dry_run by
    default — exposed for both p001 and p006)."""
    defaults = dict(
        finding_id=f.id,
        suggested_mode="dry_run",
        monthly_impact_usd=f.monthly_impact_usd,
        rationale="r",
        order_rank=1,
    )
    defaults.update(overrides)
    return defaults


class TestSubActionValidation:
    def test_unknown_candidate_id_drops_whole_step(self):
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence=[
            _sub(candidate_id="cand-ghost"),
        ])
        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert len(dropped) == 1
        assert dropped[0].reason == DropReason.UNKNOWN_CANDIDATE_ID.value
        assert "cand-ghost" in dropped[0].detail

    def test_invalid_action_kind_drops_whole_step(self):
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence=[
            _sub(action_kind="remove_nat"),  # not in ALLOWED_ACTION_KINDS
        ])
        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.INVALID_ACTION_KIND.value
        assert "remove_nat" in dropped[0].detail

    def test_evidence_tier_mismatch_drops_whole_step(self):
        # Candidate is inferred; LLM says observed.
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence=[
            _sub(evidence_tier="observed"),
        ])
        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.EVIDENCE_TIER_MISMATCH.value

    def test_candidate_savings_mismatch_drops_whole_step(self):
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence=[
            _sub(est_monthly_savings_usd=99.0),  # canonical is 0.0
        ])
        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.CANDIDATE_SAVINGS_MISMATCH.value

    def test_sum_cap_exceeded_drops_whole_step(self):
        candidates = [
            {
                "candidate_id": "cand-gateway-s3", "service": "s3",
                "endpoint_type": "Gateway", "evidence_tier": "observed",
                "est_monthly_savings_usd": 30.0, "blast_radius": "low",
            },
            {
                "candidate_id": "cand-gateway-ddb", "service": "dynamodb",
                "endpoint_type": "Gateway", "evidence_tier": "observed",
                "est_monthly_savings_usd": 30.0, "blast_radius": "low",
            },
        ]
        # Sum 60 > finding 50 → reject.
        f = _nat_finding(candidates=candidates, monthly_impact_usd=50.0)
        raw = _nat_raw(f, recommended_sequence=[
            _sub(candidate_id="cand-gateway-s3",
                 evidence_tier="observed", est_monthly_savings_usd=30.0),
            _sub(candidate_id="cand-gateway-ddb",
                 evidence_tier="observed", est_monthly_savings_usd=30.0),
        ])
        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.CANDIDATE_SAVINGS_MISMATCH.value
        assert "exceeds" in dropped[0].detail

    def test_happy_path_canonicalises_sub_action_fields(self):
        candidates = [{
            "candidate_id": "cand-gateway-s3", "service": "s3",
            "endpoint_type": "Gateway", "evidence_tier": "observed",
            "est_monthly_savings_usd": 27.50, "blast_radius": "low",
        }]
        f = _nat_finding(candidates=candidates, monthly_impact_usd=32.40)
        raw = _nat_raw(f, recommended_sequence=[
            _sub(candidate_id="cand-gateway-s3",
                 evidence_tier="observed",
                 est_monthly_savings_usd=27.501),  # within tolerance
        ])

        kept, dropped = validate_steps([raw], findings=[f])

        assert dropped == []
        assert len(kept) == 1
        seq = kept[0].recommended_sequence
        assert seq is not None and len(seq) == 1
        # Canonical, not what the LLM emitted.
        assert seq[0].est_monthly_savings_usd == 27.50
        assert seq[0].evidence_tier == "observed"
        assert seq[0].candidate_id == "cand-gateway-s3"
        assert seq[0].action_kind == "add_vpc_endpoint"

    def test_missing_recommended_sequence_is_fine(self):
        # Most steps don't have one — backward compatible.
        f = _nat_finding()
        raw = _nat_raw(f)  # no recommended_sequence at all

        kept, dropped = validate_steps([raw], findings=[f])

        assert dropped == []
        assert len(kept) == 1
        assert kept[0].recommended_sequence is None

    def test_explicit_none_sequence_is_fine(self):
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence=None)

        kept, dropped = validate_steps([raw], findings=[f])

        assert dropped == []
        assert kept[0].recommended_sequence is None

    def test_sequence_must_be_list(self):
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence={"oops": "dict"})

        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_sub_action_missing_keys(self):
        f = _nat_finding()
        raw = _nat_raw(f, recommended_sequence=[
            {"candidate_id": "cand-gateway-s3"},  # everything else missing
        ])

        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.SCHEMA_INVALID.value

    def test_observe_and_reassess_must_have_zero_savings(self):
        # Even when the candidate's canonical savings is non-zero,
        # observe_and_reassess is by definition a wait-and-watch step.
        candidates = [{
            "candidate_id": "cand-gateway-s3", "service": "s3",
            "endpoint_type": "Gateway", "evidence_tier": "observed",
            "est_monthly_savings_usd": 270.0, "blast_radius": "low",
        }]
        f = _nat_finding(candidates=candidates, monthly_impact_usd=412.0)
        raw = _nat_raw(f, recommended_sequence=[
            _sub(candidate_id="cand-gateway-s3",
                 action_kind="observe_and_reassess",
                 evidence_tier="observed",
                 est_monthly_savings_usd=270.0),  # wrong — should be 0
        ])

        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == []
        assert dropped[0].reason == DropReason.CANDIDATE_SAVINGS_MISMATCH.value
        assert "action_kind=observe_and_reassess" in dropped[0].detail

    def test_mixed_sequence_one_bad_sub_action_drops_whole_step(self):
        # A valid add_vpc_endpoint sub-action followed by an unknown
        # candidate_id. The contract says: whole step drops; the good
        # sub-action does not survive on its own. (Partial salvage of a
        # corrupt sequence is worse than no plan.)
        candidates = [{
            "candidate_id": "cand-gateway-s3", "service": "s3",
            "endpoint_type": "Gateway", "evidence_tier": "observed",
            "est_monthly_savings_usd": 250.0, "blast_radius": "low",
        }]
        f = _nat_finding(candidates=candidates, monthly_impact_usd=412.0)
        raw = _nat_raw(f, recommended_sequence=[
            _sub(candidate_id="cand-gateway-s3",
                 action_kind="add_vpc_endpoint",
                 evidence_tier="observed",
                 est_monthly_savings_usd=250.0),
            _sub(candidate_id="cand-ghost-redshift"),  # invented
        ])

        kept, dropped = validate_steps([raw], findings=[f])

        assert kept == [], "valid sub-action must not survive on its own"
        assert len(dropped) == 1
        assert dropped[0].reason == DropReason.UNKNOWN_CANDIDATE_ID.value
        # The dropped raw emission must carry BOTH sub-actions — the
        # audit trail preserves the entire corrupt sequence, not just
        # the offending entry.
        seq = dropped[0].raw_emission["recommended_sequence"]
        assert len(seq) == 2
        assert seq[0]["candidate_id"] == "cand-gateway-s3"
        assert seq[1]["candidate_id"] == "cand-ghost-redshift"

    def test_observe_and_reassess_with_zero_savings_accepted(self):
        candidates = [{
            "candidate_id": "cand-gateway-s3", "service": "s3",
            "endpoint_type": "Gateway", "evidence_tier": "observed",
            "est_monthly_savings_usd": 270.0, "blast_radius": "low",
        }]
        f = _nat_finding(candidates=candidates, monthly_impact_usd=412.0)
        raw = _nat_raw(f, recommended_sequence=[
            _sub(candidate_id="cand-gateway-s3",
                 action_kind="add_vpc_endpoint",
                 evidence_tier="observed",
                 est_monthly_savings_usd=270.0),
            _sub(candidate_id="cand-gateway-s3",
                 action_kind="observe_and_reassess",
                 evidence_tier="observed",
                 est_monthly_savings_usd=0.0),
        ])

        kept, dropped = validate_steps([raw], findings=[f])

        assert dropped == []
        assert len(kept) == 1
        seq = kept[0].recommended_sequence
        assert [s.action_kind for s in seq] == [
            "add_vpc_endpoint", "observe_and_reassess",
        ]
        assert [s.est_monthly_savings_usd for s in seq] == [270.0, 0.0]
