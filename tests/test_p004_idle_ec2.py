"""
Tests for Pattern 004: Idle EC2 Instances (v1 — third bulletproof pattern,
second planner-aware compute pattern).

Covers the scanner: signal collection, gate computation, risk tier,
"no data → no finding" precondition, and three invariant tests that
pin contract-level properties (TestNoUnattestedIdle,
TestSafeToFixImpliesAllGatesPass, TestResolverAndRemediatorAgreeOnEligibility).

Deeper remediation/mode tests live in `test_p004_bulletproof.py`.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.modes import AvailableModesResolver
from patterns.base import (
    Finding,
    RemediationMode,
    RiskTier,
)
from patterns.p004_idle_ec2 import (
    ASG_TAG_KEY,
    COST_SOURCE_STATIC_LIST_PRICE,
    EXPECTED_DATAPOINTS,
    GATE_NAMES,
    HOURLY_USD_US_EAST_1,
    HOURS_PER_MONTH,
    LOOKBACK_DAYS,
    MIN_CPU_DATAPOINT_COVERAGE,
    PRICING_REGION,
    IdleEC2Pattern,
)

# ---------------------------------------------------------------------------
# Mock helpers — keep these short. Tests should be readable without scrolling.
# ---------------------------------------------------------------------------

def _instance(
    *,
    instance_id="i-test1",
    instance_type="t3.medium",
    launch_time=None,
    tags=None,
    root_device_type="ebs",
    instance_lifecycle=None,           # None → on-demand
    network_interfaces=None,
    public_ip_address=None,
):
    """Build a describe_instances Instance dict."""
    if launch_time is None:
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
    out = {
        "InstanceId": instance_id,
        "InstanceType": instance_type,
        "LaunchTime": launch_time,
        "RootDeviceType": root_device_type,
        "Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()],
        "NetworkInterfaces": network_interfaces or [],
    }
    if instance_lifecycle is not None:
        out["InstanceLifecycle"] = instance_lifecycle
    if public_ip_address is not None:
        out["PublicIpAddress"] = public_ip_address
    return out


def _cpu_response(*, avg=2.0, max_value=10.0, datapoint_count=EXPECTED_DATAPOINTS):
    """Build a CPU get_metric_statistics response. The Avg and Max stats
    are returned at the same per-datapoint values for simplicity; the
    scanner aggregates avg-of-Average and max-of-Maximum."""
    dps = []
    for _ in range(datapoint_count):
        dps.append({"Average": avg, "Maximum": max_value})
    return {"Datapoints": dps}


def _bytes_response(*, bytes_per_hour=0.0, hours=EXPECTED_DATAPOINTS):
    """Single-metric Sum response. Each hourly datapoint carries
    bytes_per_hour. The scanner sums across metric names then divides
    by the representative hourly count."""
    return {
        "Datapoints": [{"Sum": bytes_per_hour} for _ in range(hours)],
    }


def _empty_response():
    return {"Datapoints": []}


def _cw_side_effect(*, cpu=None, network=None, disk=None):
    """Build a get_metric_statistics side_effect that dispatches on
    MetricName. Per-direction (NetworkIn/Out, DiskReadBytes/WriteBytes)
    each return half of `network` / `disk` so the sum lands where the
    test expects."""
    network = network if network is not None else _empty_response()
    disk = disk if disk is not None else _empty_response()
    cpu = cpu if cpu is not None else _empty_response()

    def _impl(**kwargs):
        metric = kwargs.get("MetricName")
        if metric == "CPUUtilization":
            return cpu
        if metric in ("NetworkIn", "NetworkOut"):
            # split bytes between in and out so the sum is `network`
            return _halve(network)
        if metric in ("DiskReadBytes", "DiskWriteBytes"):
            return _halve(disk)
        raise AssertionError(f"unexpected metric {metric!r}")
    return _impl


def _halve(resp):
    """Halve every Sum so two metrics summed reproduce the original total."""
    return {
        "Datapoints": [{"Sum": dp["Sum"] / 2} for dp in resp["Datapoints"]],
    }


def _mk_session(*, instances, elb_index=None, cw_side_effect=None):
    """Build a mock boto3 Session whose ec2/cloudwatch/elasticloadbalancing
    clients return the supplied data.

    `elb_index` is `{instance_id: [tg_arn, ...]}` — the helper builds the
    describe_target_groups / describe_target_health responses that would
    produce that index.
    """
    elb_index = elb_index or {}
    cw_side_effect = cw_side_effect or _cw_side_effect()

    ec2 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{
        "Reservations": [{"Instances": instances}]
    }]
    ec2.get_paginator.return_value = paginator

    cw = MagicMock()
    cw.get_metric_statistics.side_effect = lambda **kw: cw_side_effect(**kw)

    # Build a TG-arn → instance_ids inverse map.
    tg_to_instances: dict[str, list[str]] = {}
    for instance_id, tg_arns in elb_index.items():
        for tg_arn in tg_arns:
            tg_to_instances.setdefault(tg_arn, []).append(instance_id)

    elbv2 = MagicMock()
    elbv2_paginator = MagicMock()
    if tg_to_instances:
        tg_pages = [{
            "TargetGroups": [
                {"TargetGroupArn": tg_arn, "TargetType": "instance"}
                for tg_arn in tg_to_instances
            ]
        }]
    else:
        tg_pages = [{"TargetGroups": []}]
    elbv2_paginator.paginate.return_value = tg_pages
    elbv2.get_paginator.return_value = elbv2_paginator

    def _describe_target_health(TargetGroupArn=None, **_):  # noqa: N803 — boto3 kwarg
        ids = tg_to_instances.get(TargetGroupArn, [])
        return {
            "TargetHealthDescriptions": [
                {"Target": {"Id": i}} for i in ids
            ]
        }
    elbv2.describe_target_health.side_effect = _describe_target_health

    def _client(service, **_):
        if service == "ec2":
            return ec2
        if service == "cloudwatch":
            return cw
        if service == "elbv2":
            return elbv2
        raise AssertionError(f"unexpected service {service!r}")

    session = MagicMock()
    session.client.side_effect = _client
    return session, ec2, cw, elbv2


def _scan(session, *, region="us-east-1"):
    p = IdleEC2Pattern(session=session)
    p.get_all_regions = lambda: [region]
    return p.scan()


# ---------------------------------------------------------------------------
# Discovery + shape
# ---------------------------------------------------------------------------

class TestScanShape:
    def test_emits_one_finding_per_idle_instance(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_id="i-a"), _instance(instance_id="i-b")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        findings = _scan(session)
        assert {f.resource_id for f in findings} == {"i-a", "i-b"}
        assert all(f.pattern_id == "004" for f in findings)
        assert all(f.resource_type == "EC2 Instance" for f in findings)

    def test_finding_carries_arn(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_id="i-a")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.resource_arn == "arn:aws:ec2:us-east-1::instance/i-a"

    def test_filters_running_only(self):
        session, ec2, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        _scan(session)
        ec2.get_paginator.assert_called_with("describe_instances")
        ec2.get_paginator.return_value.paginate.assert_called_with(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )

    def test_handles_region_error_gracefully(self):
        session = MagicMock()
        ec2 = MagicMock()
        ec2.get_paginator.side_effect = Exception("boom")
        session.client.return_value = ec2
        p = IdleEC2Pattern(session=session)
        p.get_all_regions = lambda: ["us-east-1"]
        assert p.scan() == []

    def test_uses_elbv2_boto3_client_name(self):
        """boto3 has no 'elasticloadbalancing' service — only 'elb' (Classic
        v1) and 'elbv2' (ALB/NLB v2). The scanner needs 'elbv2' for
        describe_target_groups. Without this guard, a refactor that swaps
        to 'elasticloadbalancing' would crash at runtime with
        UnknownServiceError, the per-region exception handler would swallow
        it, and p004 would silently produce zero findings against any real
        AWS account.
        """
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        _scan(session)
        # All three boto3 service names the scanner uses must appear in
        # the recorded call list, with the load-balancing client being
        # 'elbv2' (not 'elasticloadbalancing', not 'elb').
        services_called = {
            call.args[0] if call.args else call.kwargs.get("service_name")
            for call in session.client.call_args_list
        }
        assert "elbv2" in services_called, (
            f"scanner did not request the 'elbv2' boto3 client; "
            f"saw: {sorted(services_called)}"
        )
        assert "elasticloadbalancing" not in services_called, (
            "scanner requested the invalid 'elasticloadbalancing' service "
            "name — that would crash with UnknownServiceError in production."
        )


# ---------------------------------------------------------------------------
# Detection preconditions — the "no data → no claim" rule
# ---------------------------------------------------------------------------

class TestDetectionPreconditions:
    def test_skips_instance_younger_than_lookback(self):
        young = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS - 1)
        session, *_ = _mk_session(
            instances=[_instance(launch_time=young)],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        assert _scan(session) == []

    def test_skips_instance_with_insufficient_cpu_coverage(self):
        # cpu coverage just below the minimum
        thin = _cpu_response(datapoint_count=MIN_CPU_DATAPOINT_COVERAGE - 1)
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=thin),
        )
        assert _scan(session) == []

    def test_skips_instance_with_no_cpu_metrics(self):
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=_empty_response()),
        )
        assert _scan(session) == []

    def test_skips_busy_avg_cpu(self):
        busy = _cpu_response(avg=12.0, max_value=15.0)
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=busy),
        )
        assert _scan(session) == []

    def test_skips_bursty_max_cpu(self):
        # avg is quiet but max indicates a burst
        bursty = _cpu_response(avg=2.0, max_value=55.0)
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=bursty),
        )
        assert _scan(session) == []

    def test_skips_network_heavy_instance(self):
        # 10 MiB/h sustained — well above NETWORK_IDLE_BYTES_PER_HOUR.
        network = _bytes_response(bytes_per_hour=10 * 1024 * 1024)
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(
                cpu=_cpu_response(), network=network,
            ),
        )
        assert _scan(session) == []

    def test_skips_disk_heavy_instance(self):
        disk = _bytes_response(bytes_per_hour=10 * 1024 * 1024)
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(
                cpu=_cpu_response(), disk=disk,
            ),
        )
        assert _scan(session) == []


# ---------------------------------------------------------------------------
# Gates — every named gate must be present and reflect reality
# ---------------------------------------------------------------------------

class TestGates:
    def test_all_gates_pass_for_safe_idle_instance(self):
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        gates = f.evidence["gates"]
        assert set(gates) == set(GATE_NAMES)
        assert all(gates.values()), gates
        assert f.safe_to_fix is True

    def test_asg_membership_fails_not_in_asg(self):
        session, *_ = _mk_session(
            instances=[_instance(tags={ASG_TAG_KEY: "my-asg"})],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["gates"]["not_in_asg"] is False
        assert f.safe_to_fix is False

    def test_elb_attachment_fails_no_alb_nlb_attachment(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_id="i-attached")],
            elb_index={"i-attached": ["arn:aws:elasticloadbalancing:us-east-1::targetgroup/tg-1"]},
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["gates"]["no_alb_nlb_attachment"] is False
        assert f.evidence["attachments"]["target_group_arns"]
        assert f.safe_to_fix is False

    @pytest.mark.parametrize("tag_key,tag_val", [
        ("Env", "prod"),
        ("Env", "production"),
        ("Environment", "Production"),
        ("env", "PROD"),
    ])
    def test_prod_tag_fails_not_prod(self, tag_key, tag_val):
        session, *_ = _mk_session(
            instances=[_instance(tags={tag_key: tag_val})],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["gates"]["not_prod"] is False
        assert f.safe_to_fix is False

    def test_instance_store_root_fails_ebs_root(self):
        session, *_ = _mk_session(
            instances=[_instance(root_device_type="instance-store")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["gates"]["ebs_root"] is False
        assert f.safe_to_fix is False

    def test_spot_lifecycle_fails_not_spot(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_lifecycle="spot")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["gates"]["not_spot"] is False
        assert f.safe_to_fix is False


# ---------------------------------------------------------------------------
# Cost — the static-list-price contract
# ---------------------------------------------------------------------------

class TestCost:
    def test_cost_block_uses_static_list_price(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_type="t3.medium")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        cost = f.evidence["cost"]

        assert cost["cost_source"] == COST_SOURCE_STATIC_LIST_PRICE
        assert cost["pricing_region"] == PRICING_REGION
        assert cost["confidence"] == "low"
        assert cost["hourly_usd"] == HOURLY_USD_US_EAST_1["t3.medium"]
        expected_monthly = round(HOURLY_USD_US_EAST_1["t3.medium"] * HOURS_PER_MONTH, 2)
        assert cost["monthly_cost_usd"] == expected_monthly
        assert f.monthly_impact_usd == expected_monthly

    def test_unknown_instance_type_falls_back_to_default(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_type="x999.huge")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["cost"]["hourly_usd"] == 0.10
        # confidence drops when the table missed.
        assert f.confidence == 0.85


# ---------------------------------------------------------------------------
# Risk tier
# ---------------------------------------------------------------------------

class TestRiskTier:
    def test_prod_tag_forces_high(self):
        session, *_ = _mk_session(
            instances=[_instance(tags={"Env": "prod"})],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        assert _scan(session)[0].risk_tier == RiskTier.HIGH

    def test_asg_membership_forces_high(self):
        session, *_ = _mk_session(
            instances=[_instance(tags={ASG_TAG_KEY: "my-asg"})],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        assert _scan(session)[0].risk_tier == RiskTier.HIGH

    def test_elb_attachment_forces_high(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_id="i-attached")],
            elb_index={"i-attached": ["arn:tg-1"]},
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        assert _scan(session)[0].risk_tier == RiskTier.HIGH

    def test_medium_for_production_family(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_type="m5.large")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        assert _scan(session)[0].risk_tier == RiskTier.MEDIUM

    def test_low_for_quiet_dev_t3(self):
        session, *_ = _mk_session(
            instances=[_instance(instance_type="t3.medium")],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        assert _scan(session)[0].risk_tier == RiskTier.LOW

    def test_public_ip_without_eip_bumps_low_to_medium(self):
        # t3.medium would be LOW; public IP w/o EIP should bump to MEDIUM.
        session, *_ = _mk_session(
            instances=[_instance(
                instance_type="t3.medium",
                public_ip_address="54.0.0.1",
                network_interfaces=[{"Association": {}}],  # no AllocationId
            )],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["attachments"]["public_ip_without_eip"] is True
        assert f.risk_tier == RiskTier.MEDIUM

    def test_public_ip_with_eip_does_not_bump(self):
        session, *_ = _mk_session(
            instances=[_instance(
                instance_type="t3.medium",
                public_ip_address="54.0.0.1",
                network_interfaces=[{"Association": {"AllocationId": "eipalloc-1"}}],
            )],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        f = _scan(session)[0]
        assert f.evidence["attachments"]["public_ip_without_eip"] is False
        assert f.risk_tier == RiskTier.LOW


# ---------------------------------------------------------------------------
# Invariant tests — the three contract-level guards
# ---------------------------------------------------------------------------

class TestNoUnattestedIdle:
    """Any emitted finding has cpu_datapoint_coverage >= MIN_CPU_DATAPOINT_COVERAGE.

    Trivially true today because `_maybe_finding()` returns None before
    constructing a Finding when coverage is below the threshold. The
    test exists to guard future refactoring that might relax that
    precondition — if someone moves the coverage check, they must
    actively confirm "yes, this finding is substantiated".
    """

    @pytest.mark.parametrize("coverage", [
        # full coverage
        EXPECTED_DATAPOINTS,
        # minimum allowed coverage
        MIN_CPU_DATAPOINT_COVERAGE,
        # an extra mid-window value
        (MIN_CPU_DATAPOINT_COVERAGE + EXPECTED_DATAPOINTS) // 2,
    ])
    def test_emitted_findings_have_sufficient_coverage(self, coverage):
        session, *_ = _mk_session(
            instances=[_instance()],
            cw_side_effect=_cw_side_effect(
                cpu=_cpu_response(datapoint_count=coverage),
            ),
        )
        for f in _scan(session):
            cov = f.evidence["utilization"]["cpu_datapoint_coverage"]
            assert cov >= MIN_CPU_DATAPOINT_COVERAGE


class TestSafeToFixImpliesAllGatesPass:
    """For any finding with safe_to_fix=True, every gate in evidence.gates
    must be True. Pins the contract: safe_to_fix is the AND of the gates,
    not a separate boolean some future code path could set independently.
    """

    @pytest.mark.parametrize("instance_kwargs", [
        {},  # vanilla idle instance — all gates pass
        {"instance_type": "m5.large"},  # production family, gates still pass
        {"tags": {"Owner": "platform", "Project": "p1"}},
    ])
    def test_safe_to_fix_implies_all_gates_pass(self, instance_kwargs):
        session, *_ = _mk_session(
            instances=[_instance(**instance_kwargs)],
            cw_side_effect=_cw_side_effect(cpu=_cpu_response()),
        )
        findings = _scan(session)
        assert findings, "expected an emitted finding for this scenario"
        for f in findings:
            if not f.safe_to_fix:
                continue
            gates = f.evidence["gates"]
            failures = [k for k in GATE_NAMES if not gates.get(k, False)]
            assert not failures, (
                f"safe_to_fix=True but failed gates: {failures}; "
                f"gates dict: {gates}"
            )


# Hand-built findings (no scanner) exercise both safe and unsafe cases
# in TestResolverAndRemediatorAgreeOnEligibility — we don't need the
# CloudWatch surface here.

def _hand_finding(*, safe_to_fix: bool, instance_id="i-eligibility") -> Finding:
    """Build a minimal p004-shaped Finding directly. The scanner
    invariant tests cover the gate→safe_to_fix derivation; this helper
    decouples the eligibility test from the scanner.
    """
    gates = dict.fromkeys(GATE_NAMES, safe_to_fix)
    return Finding(
        resource_id=instance_id,
        resource_type="EC2 Instance",
        region="us-east-1",
        monthly_impact_usd=30.0,
        summary="hand-built p004 finding for eligibility test",
        pattern_id="004",
        risk_tier=RiskTier.LOW,
        safe_to_fix=safe_to_fix,
        fix_command=(
            f"aws ec2 stop-instances --instance-ids {instance_id} "
            f"--region us-east-1"
        ),
        evidence={
            "gates": gates,
            "cost": {
                "monthly_cost_usd": 30.0,
                "hourly_usd": 0.0416,
                "cost_source": COST_SOURCE_STATIC_LIST_PRICE,
                "pricing_region": PRICING_REGION,
                "confidence": "low",
            },
            "utilization": {
                "avg_cpu_14d": 1.5,
                "max_cpu_14d": 8.0,
                "cpu_datapoint_coverage": EXPECTED_DATAPOINTS,
            },
        },
    )


class TestResolverAndRemediatorAgreeOnEligibility:
    """For every fixture finding, the set of modes the resolver returns
    must match the set of modes the remediator returns success=True for.

    Catches the failure mode where (a) the resolver offers a mode but
    the remediator refuses it, or (b) the remediator silently accepts a
    mode the resolver never offered. Single eligibility function is the
    intended design.
    """

    @pytest.mark.parametrize("safe", [True, False])
    def test_resolver_and_remediator_agree_for_p004(self, safe):
        finding = _hand_finding(safe_to_fix=safe)
        # Mock the EC2 client so API_CALL on a safe finding succeeds.
        session = MagicMock()
        ec2 = MagicMock()
        ec2.stop_instances.return_value = {
            "StoppingInstances": [{
                "PreviousState": {"Name": "running"},
                "CurrentState": {"Name": "stopping"},
            }]
        }
        session.client.return_value = ec2
        pattern = IdleEC2Pattern(session=session)

        resolver = AvailableModesResolver()
        offered = resolver.resolve(finding)

        # Probe every public mode; build the set the remediator accepts.
        accepted = set()
        for mode in (
            RemediationMode.DRY_RUN,
            RemediationMode.COMMAND,
            RemediationMode.PR,
            RemediationMode.API_CALL,
        ):
            result = pattern.remediate(finding, mode)
            if result.success:
                accepted.add(mode)

        assert offered == accepted, (
            f"resolver offered {sorted(m.value for m in offered)} but "
            f"remediator accepted {sorted(m.value for m in accepted)} "
            f"(safe_to_fix={safe})"
        )
