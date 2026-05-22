"""
Tests for Pattern 006: NAT Gateway Optimization (v1 — first agent-native
pattern, hourly_only cost model).

These tests cover the scanner: NAT discovery, topology assembly,
candidate enumeration, risk tier, and the basic shape of each
Finding. Deeper remediation/mode tests live in
`test_p006_bulletproof.py`.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns.base import RiskTier
from patterns.p006_nat_gateway import (
    COST_SOURCE_HOURLY_ONLY,
    EVIDENCE_TIER_INFERRED,
    HOURS_PER_MONTH,
    NAT_HOURLY_USD,
    NatGatewayPattern,
)


def _mk_session(*, nat_gateways, route_tables=None, vpc_endpoints=None):
    """Build a MagicMock boto3 session whose EC2 client returns the
    supplied descriptions. All other clients raise on use."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_nat_gateways.return_value = {"NatGateways": nat_gateways}
    mock_ec2.describe_route_tables.return_value = {"RouteTables": route_tables or []}
    mock_ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": vpc_endpoints or []}

    session = MagicMock()
    def _client(service, **_):
        if service == "ec2":
            return mock_ec2
        raise AssertionError(f"unexpected client request for {service!r}")
    session.client.side_effect = _client
    return session, mock_ec2


def _nat(nat_id="nat-1", vpc_id="vpc-1", subnet_id="subnet-1",
         create_time=None, tags=None):
    return {
        "NatGatewayId": nat_id,
        "VpcId": vpc_id,
        "SubnetId": subnet_id,
        "CreateTime": create_time or datetime.now(timezone.utc) - timedelta(days=60),
        "State": "available",
        "Tags": tags or [],
    }


def _rtb(rtb_id, vpc_id, nat_id, subnets):
    return {
        "RouteTableId": rtb_id,
        "VpcId": vpc_id,
        "Routes": [{"NatGatewayId": nat_id}],
        "Associations": [{"SubnetId": s} for s in subnets],
    }


# ---------------------------------------------------------------------------
# Discovery + shape
# ---------------------------------------------------------------------------

class TestScanShape:
    def test_emits_one_finding_per_nat(self):
        session, _ = _mk_session(nat_gateways=[
            _nat("nat-a", "vpc-1", "subnet-a"),
            _nat("nat-b", "vpc-2", "subnet-b"),
        ])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        findings = p.scan()

        assert len(findings) == 2
        assert {f.resource_id for f in findings} == {"nat-a", "nat-b"}
        assert all(f.pattern_id == "006" for f in findings)
        assert all(f.resource_type == "NAT Gateway" for f in findings)
        assert all(f.region == "us-east-1" for f in findings)

    def test_finding_carries_arn(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-a")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]

        assert f.resource_arn == "arn:aws:ec2:us-east-1::natgateway/nat-a"

    def test_filters_only_available_nat_gateways(self):
        session, mock_ec2 = _mk_session(nat_gateways=[])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        p.scan()

        mock_ec2.describe_nat_gateways.assert_called_with(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )

    def test_handles_api_error_gracefully(self):
        session = MagicMock()
        ec2 = MagicMock()
        ec2.describe_nat_gateways.side_effect = Exception("boom")
        session.client.return_value = ec2

        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        assert p.scan() == []  # error swallowed per scan(), no findings


# ---------------------------------------------------------------------------
# Cost — hourly_only fallback
# ---------------------------------------------------------------------------

class TestCostHourlyOnly:
    def test_hourly_only_is_default(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-a")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]

        assert f.evidence["cost"]["cost_source"] == COST_SOURCE_HOURLY_ONLY
        assert f.evidence["cost"]["monthly_processing_cost_usd"] == 0.0
        assert f.evidence["cost"]["gb_processed_30d_bidirectional"] == 0.0
        assert f.evidence["cost"]["confidence"] == "low"

    def test_monthly_impact_equals_hourly_only(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-a")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]

        expected = round(NAT_HOURLY_USD * HOURS_PER_MONTH, 2)
        assert f.monthly_impact_usd == expected
        assert f.evidence["cost"]["monthly_hours_cost_usd"] == expected

    def test_finding_emitted_even_without_traffic(self):
        # A NAT costing $32/mo for nothing is still waste — must emit.
        session, _ = _mk_session(nat_gateways=[_nat("nat-idle")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        assert len(p.scan()) == 1


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

class TestTopology:
    def test_route_tables_targeting_nat_recorded(self):
        rtbs = [
            _rtb("rtb-priv-a", "vpc-1", "nat-1", ["subnet-priv-1", "subnet-priv-2"]),
            _rtb("rtb-priv-b", "vpc-1", "nat-1", ["subnet-priv-3"]),
            # different VPC — should be ignored
            _rtb("rtb-other", "vpc-99", "nat-1", ["subnet-other"]),
        ]
        session, _ = _mk_session(
            nat_gateways=[_nat("nat-1", vpc_id="vpc-1")],
            route_tables=rtbs,
        )
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]

        topo = f.evidence["topology"]
        assert topo["affected_route_count"] == 2
        assert sorted(r["rtb_id"] for r in topo["route_tables"]) == [
            "rtb-priv-a", "rtb-priv-b",
        ]
        assert "subnet-priv-1" in topo["private_subnets_using_nat"]
        assert "subnet-other" not in topo["private_subnets_using_nat"]

    def test_existing_vpc_endpoints_recorded(self):
        vpces = [
            {
                "VpcEndpointId": "vpce-1",
                "VpcId": "vpc-1",
                "ServiceName": "com.amazonaws.us-east-1.s3",
                "VpcEndpointType": "Gateway",
            },
            {  # different VPC — must be filtered out
                "VpcEndpointId": "vpce-other",
                "VpcId": "vpc-99",
                "ServiceName": "com.amazonaws.us-east-1.dynamodb",
                "VpcEndpointType": "Gateway",
            },
        ]
        session, _ = _mk_session(
            nat_gateways=[_nat("nat-1", vpc_id="vpc-1")],
            vpc_endpoints=vpces,
        )
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]

        existing = f.evidence["topology"]["existing_vpc_endpoints"]
        assert len(existing) == 1
        assert existing[0]["vpce_id"] == "vpce-1"


# ---------------------------------------------------------------------------
# Inferred candidates
# ---------------------------------------------------------------------------

class TestCandidates:
    def test_default_two_candidates_s3_and_dynamodb(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-1")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]
        candidates = f.evidence["inferred"]["endpoint_candidates"]

        assert {c["service"] for c in candidates} == {"s3", "dynamodb"}
        for c in candidates:
            assert c["evidence_tier"] == EVIDENCE_TIER_INFERRED
            assert c["est_monthly_savings_usd"] == 0.0
            assert c["blast_radius"] == "low"
            assert c["supporting_inference_reason"] == "service_endpoint_supported_by_aws"
            assert c["candidate_id"].startswith("cand-gateway-")

    def test_skips_candidate_already_present_in_vpc(self):
        # An existing S3 Gateway endpoint should drop S3 from the candidates.
        session, _ = _mk_session(
            nat_gateways=[_nat("nat-1", vpc_id="vpc-1")],
            vpc_endpoints=[{
                "VpcEndpointId": "vpce-1",
                "VpcId": "vpc-1",
                "ServiceName": "com.amazonaws.us-east-1.s3",
                "VpcEndpointType": "Gateway",
            }],
        )
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]
        services = {c["service"] for c in f.evidence["inferred"]["endpoint_candidates"]}

        assert services == {"dynamodb"}

    def test_command_hint_is_region_qualified(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-1")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["eu-west-1"]

        f = p.scan()[0]
        hints = [c["deterministic_command_hint"]
                 for c in f.evidence["inferred"]["endpoint_candidates"]]

        assert all("com.amazonaws.eu-west-1." in h for h in hints)
        assert all("--region eu-west-1" in h for h in hints)


# ---------------------------------------------------------------------------
# Risk tier
# ---------------------------------------------------------------------------

class TestRiskTier:
    def test_prod_tag_forces_high(self):
        session, _ = _mk_session(nat_gateways=[
            _nat("nat-prod", tags=[{"Key": "Env", "Value": "prod"}]),
        ])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        assert p.scan()[0].risk_tier == RiskTier.HIGH

    def test_many_route_tables_forces_high(self):
        rtbs = [
            _rtb(f"rtb-{i}", "vpc-1", "nat-many", [f"subnet-{i}"])
            for i in range(6)  # 6 > HIGH_RISK_ROUTE_COUNT (4)
        ]
        session, _ = _mk_session(
            nat_gateways=[_nat("nat-many", vpc_id="vpc-1")],
            route_tables=rtbs,
        )
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        assert p.scan()[0].risk_tier == RiskTier.HIGH

    def test_low_for_idle_isolated_nat(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-low")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        assert p.scan()[0].risk_tier == RiskTier.LOW


# ---------------------------------------------------------------------------
# Gates & evidence completeness
# ---------------------------------------------------------------------------

class TestGates:
    def test_no_observed_evidence_in_v1(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-1")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        f = p.scan()[0]

        assert "observed" not in f.evidence  # v1 never emits observed
        assert f.evidence["gates"]["observed_supports_top_candidate"] is False

    def test_has_baseline_cost_true_always(self):
        session, _ = _mk_session(nat_gateways=[_nat("nat-1")])
        p = NatGatewayPattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]

        assert p.scan()[0].evidence["gates"]["has_baseline_cost"] is True


# ---------------------------------------------------------------------------
# Production safety invariant — never present fabricated endpoint savings
# ---------------------------------------------------------------------------

class TestNoFabricatedSavings:
    """When cost_source == "hourly_only", every endpoint candidate's
    est_monthly_savings_usd must be 0.0, regardless of evidence_tier.

    This is the load-bearing safety invariant: the scanner today is
    structurally incapable of producing non-zero candidate savings (it
    has no `observed` codepath), but this test pins the property so it
    stays true once Flow Logs / CUR ingestion land in a follow-up.
    """

    def test_hourly_only_implies_zero_candidate_savings(self):
        # Vary the inputs that could plausibly influence savings down the
        # line: NAT with route tables, NAT idle, NAT with existing
        # endpoints. All must produce 0.0 savings while hourly_only is
        # the active cost source.
        scenarios = [
            ("idle", [_nat("nat-idle")], [], []),
            ("with-rtbs", [_nat("nat-r", vpc_id="vpc-1")],
             [_rtb("rtb-1", "vpc-1", "nat-r", ["subnet-1", "subnet-2"])], []),
            ("with-existing-ddb", [_nat("nat-x", vpc_id="vpc-1")], [],
             [{"VpcEndpointId": "vpce-1", "VpcId": "vpc-1",
               "ServiceName": "com.amazonaws.us-east-1.dynamodb",
               "VpcEndpointType": "Gateway"}]),
        ]
        for label, nats, rtbs, vpces in scenarios:
            session, _ = _mk_session(
                nat_gateways=nats, route_tables=rtbs, vpc_endpoints=vpces,
            )
            p = NatGatewayPattern(session=session)
            p.get_all_regions = lambda: ["us-east-1"]
            findings = p.scan()
            for f in findings:
                assert f.evidence["cost"]["cost_source"] == "hourly_only", \
                    f"{label}: scanner emitted unexpected cost_source"
                for c in f.evidence["inferred"]["endpoint_candidates"]:
                    assert c["est_monthly_savings_usd"] == 0.0, (
                        f"{label}: candidate {c['candidate_id']} "
                        f"emitted non-zero savings under hourly_only"
                    )
