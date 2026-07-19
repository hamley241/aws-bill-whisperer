"""
Pattern 004: Idle EC2 Instances — third bulletproof pattern, second
planner-aware compute pattern.

Detection
  Scans every region for running EC2 instances. For each instance ≥14
  days old, pulls 14d of hourly CloudWatch metrics for CPU (avg + max),
  network bytes (in + out), and disk bytes (read + write). Classifies as
  idle iff all four signals are below threshold AND CloudWatch returned
  enough datapoints to make that claim.

  An instance with insufficient CloudWatch coverage (<280 / 336 hourly
  datapoints over the 14d window) is NEVER emitted as a finding. This is
  the p004 analogue of p006's `hourly_only` conservatism: when the data
  isn't there, we don't make claims. The invariant is enforced by
  `TestNoUnattestedIdle` in the scanner tests.

Cost model
  Hardcoded us-east-1 list prices, looked up by InstanceType. The cost
  block carries `cost_source="static_list_price"` and a separate
  `pricing_region="us-east-1"` field. cost_source describes the kind of
  measurement (static lookup, not derived from CUR or the Pricing API);
  pricing_region is metadata about which region's prices were used. The
  enum value is intentionally not region-encoded — adding a new region's
  prices doesn't mean a new cost_source.

Evidence layout
    evidence = {
      "instance":    {...identity, tags, lifecycle, root_device_type...},
      "utilization": {avg_cpu, max_cpu, network/disk bytes per hour,
                      cpu_datapoint_coverage},
      "attachments": {asg_name, target_group_arns, eip_associated,
                      has_public_ip},
      "cost":        {monthly_cost_usd, hourly_usd, cost_source,
                      pricing_region, confidence},
      "gates":       {cpu_data_sufficient, age_ok, not_in_asg,
                      no_alb_nlb_attachment, not_prod, ebs_root, not_spot},
    }

  `safe_to_fix` is True iff every gate is True. The invariant is enforced
  by `TestSafeToFixImpliesAllGatesPass`.

  ASG membership subsumes warm-pool membership: warm pools are an ASG
  feature, so any warm-pool instance is by definition in an ASG and is
  already blocked by `not_in_asg`. No separate warm-pool gate.

  ELB attachment is detected via elbv2 (ALB / NLB target groups). Classic
  ELB (CLB) is NOT checked in v1; an instance attached only to a Classic
  ELB would falsely satisfy `no_alb_nlb_attachment=True`. The gate name
  reflects what it actually checks. Adding CLB support requires
  `elasticloadbalancing:DescribeInstanceHealth` and per-CLB enumeration.

Remediation modes
  DRY_RUN  — always available; renders evidence + gate results.
  COMMAND  — available iff `safe_to_fix=True`; emits
             `aws ec2 stop-instances --instance-ids <id> --region <r>`.
  PR       — deferred. Instance run-state isn't cleanly modelled in
             Terraform (stopping shows up as drift, not a config change).
  API_CALL — available iff `safe_to_fix=True`; calls
             ec2.stop_instances(). Refuses with `safety_gate_failed` if
             called against an unsafe finding (defensive path — the
             resolver doesn't offer it and the validator drops emissions
             that try).

The reversibility-blast-radius principle (see agentic doc): API_CALL is
allowed because `stop_instances` is fully reversible (EBS root preserved,
restart with `start_instances`), gates are deterministic, and the blast
radius is bounded to one instance. Terminate is OUT OF SCOPE for v1
because it fails the reversibility test.

Every remediate() call should be invoked through audit.audit_remediation
so the audit log captures the attempt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import (
    BasePattern,
    Category,
    Complexity,
    Finding,
    RemediationMode,
    RemediationResult,
    RiskTier,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants. Tunable; tests and the agentic doc reference these names.
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 14
EXPECTED_DATAPOINTS = 24 * LOOKBACK_DAYS          # 336 hourly samples
MIN_CPU_DATAPOINT_COVERAGE = 280                  # 83% — allows ~56h of gaps

CPU_AVG_THRESHOLD = 5.0                            # %
CPU_MAX_THRESHOLD = 20.0                           # %
NETWORK_IDLE_BYTES_PER_HOUR = 1_048_576            # 1 MiB/h
DISK_IDLE_BYTES_PER_HOUR = 1_048_576               # 1 MiB/h

HOURS_PER_MONTH = 24 * 30                          # 720 — matches p001/p006

# Closed enum of cost sources for p004. cost_source describes the kind of
# measurement, not the region. New regions add `pricing_region` values,
# not new cost_source values.
COST_SOURCE_STATIC_LIST_PRICE = "static_list_price"

PROD_TAG_VALUES = frozenset({"prod", "production"})
PROD_TAG_KEYS = ("Env", "Environment", "env", "environment")
ASG_TAG_KEY = "aws:autoscaling:groupName"

# Production-grade instance families. Used in MEDIUM-risk classification:
# m/c/r-family instances are usually doing something even at low CPU,
# so we lean conservative on ranking even when gates technically pass.
PRODUCTION_FAMILIES = frozenset({"m5", "m5a", "m6i", "c5", "c6i", "r5", "r6i"})

# Static list-price table (us-east-1, on-demand, Linux). Pricing_region
# is recorded separately so the customer knows the cost is approximate
# outside us-east-1. A future PR can swap this for the Pricing API.
HOURLY_USD_US_EAST_1 = {
    "t2.nano": 0.0058,
    "t2.micro": 0.0116,
    "t2.small": 0.023,
    "t2.medium": 0.0464,
    "t2.large": 0.0928,
    "t2.xlarge": 0.1856,
    "t2.2xlarge": 0.3712,
    "t3.nano": 0.0052,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    "t3a.nano": 0.0048,
    "t3a.micro": 0.0096,
    "t3a.small": 0.0188,
    "t3a.medium": 0.0376,
    "t3a.large": 0.0752,
    "t3a.xlarge": 0.1504,
    "t3a.2xlarge": 0.3008,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768,
    "m5.8xlarge": 1.536,
    "m5.12xlarge": 2.304,
    "m5.16xlarge": 3.072,
    "m5.24xlarge": 4.608,
    "m5a.large": 0.086,
    "m5a.xlarge": 0.172,
    "m5a.2xlarge": 0.344,
    "m6i.large": 0.0864,
    "m6i.xlarge": 0.1728,
    "m6i.2xlarge": 0.3456,
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
    "c5.2xlarge": 0.34,
    "c5.4xlarge": 0.68,
    "c5.9xlarge": 1.53,
    "c6i.large": 0.085,
    "c6i.xlarge": 0.17,
    "c6i.2xlarge": 0.34,
    "r5.large": 0.126,
    "r5.xlarge": 0.252,
    "r5.2xlarge": 0.504,
    "r6i.large": 0.128,
    "r6i.xlarge": 0.256,
}
DEFAULT_HOURLY_USD = 0.10
PRICING_REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Closed gate-name set. The agentic doc and the invariant test both
# reference these names. Adding a gate means updating the doc, the test,
# and the safe_to_fix predicate together.
# ---------------------------------------------------------------------------

GATE_NAMES = (
    "cpu_data_sufficient",
    "age_ok",
    "not_in_asg",
    "no_alb_nlb_attachment",
    "not_prod",
    "ebs_root",
    "not_spot",
)


class IdleEC2Pattern(BasePattern):
    PATTERN_ID = "004"
    NAME = "Idle EC2 Instances"
    DESCRIPTION = (
        "Running EC2 instances with low CPU, network, and disk activity "
        "over a 14-day window — candidates for stopping."
    )
    CATEGORY = Category.COMPUTE
    COMPLEXITY = Complexity.MEDIUM
    # SERVICES uses boto3 client names (what we pass to session.client()).
    # Note that ALB/NLB's boto3 client name is `"elbv2"`, even though the
    # IAM service prefix is `elasticloadbalancing` (see REQUIRED_IAM
    # below). `"elasticloadbalancing"` is NOT a valid boto3 service name;
    # using it would crash at runtime with UnknownServiceError.
    SERVICES = ["ec2", "cloudwatch", "elbv2"]
    REQUIRED_IAM = [
        "ec2:DescribeInstances",
        "ec2:DescribeRegions",
        "ec2:StopInstances",  # API_CALL mode only
        "cloudwatch:GetMetricStatistics",
        "elasticloadbalancing:DescribeTargetGroups",
        "elasticloadbalancing:DescribeTargetHealth",
    ]

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------
    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            # Each region emits a structured outcome event — success or
            # failure — so log aggregators can distinguish "scanned
            # cleanly, found nothing" from "scan exploded silently."
            # The latter is the failure mode that hid the
            # elasticloadbalancing boto3 client-name bug pre-merge. The
            # broader return-type refactor (per-region status surfaced
            # in scan()'s return value) is tracked in memory follow-up
            # `project_silent_region_failure_logging` — it lands before
            # the first paid-tier scheduled-scan PR.
            try:
                region_findings = self._scan_region(region)
                self._findings.extend(region_findings)
                logger.info(
                    "p004 scan region complete",
                    extra={
                        "pattern_id": self.PATTERN_ID,
                        "region": region,
                        "outcome": "ok",
                        "finding_count": len(region_findings),
                    },
                )
            except Exception as e:  # pragma: no cover — structured surface
                logger.exception(
                    "p004 scan failed for region %s; continuing", region,
                    extra={
                        "pattern_id": self.PATTERN_ID,
                        "region": region,
                        "outcome": "failed",
                        "exception_type": type(e).__name__,
                        "exception_message": str(e),
                    },
                )
                continue
        return self._findings

    def _scan_region(self, region: str) -> list[Finding]:
        ec2 = self.session.client("ec2", region_name=region)
        cw = self.session.client("cloudwatch", region_name=region)
        # boto3 client name is "elbv2" for ALB/NLB v2; "elasticloadbalancing"
        # is reserved for the unrelated v1 Classic ELB API (and even there
        # the canonical boto3 name is "elb"). Using "elasticloadbalancing"
        # raises UnknownServiceError at runtime.
        elbv2 = self.session.client("elbv2", region_name=region)

        instances = self._list_running_instances(ec2)
        if not instances:
            return []

        # One pass to build the ELB target-group index for the region.
        elb_index = self._build_elb_target_index(elbv2)

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=LOOKBACK_DAYS)

        findings: list[Finding] = []
        for instance in instances:
            f = self._maybe_finding(
                ec2_region=region,
                cw=cw,
                instance=instance,
                elb_index=elb_index,
                start_time=start_time,
                end_time=end_time,
            )
            if f is not None:
                findings.append(f)
        return findings

    # ------------------------------------------------------------------
    # boto3 helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _list_running_instances(ec2) -> list[dict]:
        paginator = ec2.get_paginator("describe_instances")
        out: list[dict] = []
        for page in paginator.paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        ):
            for reservation in page.get("Reservations", []):
                out.extend(reservation.get("Instances", []))
        return out

    @staticmethod
    def _build_elb_target_index(elbv2) -> dict[str, list[str]]:
        """Returns {instance_id: [tg_arn, ...]} for instance-type TGs only.

        Classic ELBs (CLB) are not checked — they live in a different
        API family. An instance attached only to a CLB will appear here
        as having no attachment. The gate is named no_alb_nlb_attachment
        to make that boundary visible.
        """
        index: dict[str, list[str]] = {}
        try:
            paginator = elbv2.get_paginator("describe_target_groups")
            pages = paginator.paginate()
        except Exception as exc:
            logger.exception(
                "p004: describe_target_groups paginator failed",
                extra={
                    "pattern_id": IdleEC2Pattern.PATTERN_ID,
                    "outcome": "failed",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            return index

        for page in pages:
            for tg in page.get("TargetGroups", []):
                if tg.get("TargetType") != "instance":
                    continue
                tg_arn = tg.get("TargetGroupArn")
                if not tg_arn:
                    continue
                try:
                    health = elbv2.describe_target_health(TargetGroupArn=tg_arn)
                except Exception as exc:
                    logger.exception(
                        "p004: describe_target_health failed for %s", tg_arn,
                        extra={
                            "pattern_id": IdleEC2Pattern.PATTERN_ID,
                            "target_group_arn": tg_arn,
                            "outcome": "failed",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                        },
                    )
                    continue
                for thd in health.get("TargetHealthDescriptions", []):
                    target_id = (thd.get("Target") or {}).get("Id")
                    if isinstance(target_id, str) and target_id.startswith("i-"):
                        index.setdefault(target_id, []).append(tg_arn)
        return index

    @staticmethod
    def _cpu_stats(cw, instance_id: str, start: datetime, end: datetime
                   ) -> tuple[float | None, float | None, int]:
        """Returns (avg, max, datapoint_count). avg/max are None if the
        metric returned no points."""
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=start,
                EndTime=end,
                Period=3600,
                Statistics=["Average", "Maximum"],
            )
        except Exception as exc:
            logger.exception(
                "p004: CPUUtilization fetch failed for %s", instance_id,
                extra={
                    "pattern_id": IdleEC2Pattern.PATTERN_ID,
                    "instance_id": instance_id,
                    "outcome": "failed",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            return None, None, 0
        dps = resp.get("Datapoints", []) or []
        if not dps:
            return None, None, 0
        avg = sum(dp["Average"] for dp in dps) / len(dps)
        mx = max(dp["Maximum"] for dp in dps)
        return avg, mx, len(dps)

    @staticmethod
    def _bytes_per_hour(cw, instance_id: str, start: datetime, end: datetime,
                        metric_names: tuple[str, ...]) -> float:
        """Hourly average of (sum of metric_names) over the window.

        Each metric is queried with Statistics=["Sum"] at Period=3600.
        We sum the Sums across all metrics, then divide by the
        representative hourly count (max datapoints seen across metrics)
        to get bytes/hour. Returns 0.0 if no metric returns any point —
        that's "no traffic recorded", which is consistent with the idle
        signal we're looking for.
        """
        grand_total = 0.0
        representative_count = 0
        for m in metric_names:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/EC2",
                    MetricName=m,
                    Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                    StartTime=start,
                    EndTime=end,
                    Period=3600,
                    Statistics=["Sum"],
                )
            except Exception as exc:
                logger.exception(
                    "p004: %s fetch failed for %s", m, instance_id,
                    extra={
                        "pattern_id": IdleEC2Pattern.PATTERN_ID,
                        "instance_id": instance_id,
                        "metric_name": m,
                        "outcome": "failed",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                continue
            dps = resp.get("Datapoints", []) or []
            grand_total += sum(dp.get("Sum", 0.0) for dp in dps)
            if len(dps) > representative_count:
                representative_count = len(dps)
        if representative_count == 0:
            return 0.0
        return grand_total / representative_count

    # ------------------------------------------------------------------
    # Finding construction
    # ------------------------------------------------------------------
    def _maybe_finding(
        self,
        *,
        ec2_region: str,
        cw,
        instance: dict,
        elb_index: dict[str, list[str]],
        start_time: datetime,
        end_time: datetime,
    ) -> Finding | None:
        instance_id = instance["InstanceId"]
        launch_time = instance.get("LaunchTime")
        if launch_time is None:
            return None

        launch_utc = (
            launch_time.astimezone(timezone.utc)
            if launch_time.tzinfo else launch_time.replace(tzinfo=timezone.utc)
        )
        instance_age_days = (datetime.now(timezone.utc) - launch_utc).days
        if instance_age_days < LOOKBACK_DAYS:
            # Detection precondition: too young to claim idle.
            return None

        # ----- utilization -----
        avg_cpu, max_cpu, cpu_count = self._cpu_stats(
            cw, instance_id, start_time, end_time,
        )
        if avg_cpu is None or max_cpu is None or cpu_count < MIN_CPU_DATAPOINT_COVERAGE:
            # Detection precondition: insufficient CloudWatch coverage.
            # The invariant test enforces this — never emit a finding we
            # can't substantiate.
            return None
        if avg_cpu >= CPU_AVG_THRESHOLD or max_cpu >= CPU_MAX_THRESHOLD:
            return None

        net_bph = self._bytes_per_hour(
            cw, instance_id, start_time, end_time,
            ("NetworkIn", "NetworkOut"),
        )
        if net_bph >= NETWORK_IDLE_BYTES_PER_HOUR:
            return None

        disk_bph = self._bytes_per_hour(
            cw, instance_id, start_time, end_time,
            ("DiskReadBytes", "DiskWriteBytes"),
        )
        if disk_bph >= DISK_IDLE_BYTES_PER_HOUR:
            return None

        # ----- identity / tags -----
        instance_type = instance.get("InstanceType", "unknown")
        platform = instance.get("Platform") or "Linux/UNIX"
        tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
        name = tags.get("Name", "N/A")
        root_device_type = instance.get("RootDeviceType")
        instance_lifecycle = instance.get("InstanceLifecycle")  # None = on-demand

        # ----- attachments -----
        asg_name = tags.get(ASG_TAG_KEY)
        target_group_arns = elb_index.get(instance_id, [])
        has_public_ip = bool(instance.get("PublicIpAddress"))
        eip_allocation_id = self._find_eip_allocation_id(instance)

        # ----- prod tag -----
        is_prod_tagged = self._is_prod_tagged(tags)

        # ----- gates -----
        gates = {
            "cpu_data_sufficient": cpu_count >= MIN_CPU_DATAPOINT_COVERAGE,
            "age_ok": instance_age_days >= LOOKBACK_DAYS,
            "not_in_asg": asg_name is None,
            "no_alb_nlb_attachment": not target_group_arns,
            "not_prod": not is_prod_tagged,
            "ebs_root": root_device_type == "ebs",
            "not_spot": instance_lifecycle != "spot",
        }
        # Belt-and-braces: the closed gate-name set must match what we
        # actually computed. If a future contributor adds a key here
        # without updating GATE_NAMES (or vice versa), bail loudly.
        # NOTE: explicit raise rather than assert — assertions are stripped
        # under `python -O`, and this invariant is load-bearing for
        # safe_to_fix correctness in production.
        if set(gates) != set(GATE_NAMES):
            raise RuntimeError(
                "p004 gate set drifted from GATE_NAMES; update both together. "
                f"got={sorted(gates)} expected={sorted(GATE_NAMES)}"
            )
        safe_to_fix = all(gates.values())

        # ----- cost -----
        hourly_usd = HOURLY_USD_US_EAST_1.get(instance_type, DEFAULT_HOURLY_USD)
        monthly_cost = round(hourly_usd * HOURS_PER_MONTH, 2)
        cost = {
            "monthly_cost_usd": monthly_cost,
            "hourly_usd": hourly_usd,
            "cost_source": COST_SOURCE_STATIC_LIST_PRICE,
            "pricing_region": PRICING_REGION,
            "confidence": "low",
        }

        # ----- risk -----
        public_ip_without_eip = has_public_ip and eip_allocation_id is None
        instance_family = instance_type.split(".")[0] if "." in instance_type else ""
        risk = self._risk_tier(
            is_prod_tagged=is_prod_tagged,
            is_in_asg=asg_name is not None,
            has_elb_attachment=bool(target_group_arns),
            monthly_impact_usd=monthly_cost,
            instance_family=instance_family,
            public_ip_without_eip=public_ip_without_eip,
        )

        evidence: dict[str, Any] = {
            "instance": {
                "instance_id": instance_id,
                "instance_type": instance_type,
                "instance_family": instance_family,
                "platform": platform,
                "launch_time": launch_utc.isoformat(),
                "age_days": instance_age_days,
                "root_device_type": root_device_type,
                "instance_lifecycle": instance_lifecycle or "on-demand",
                "name": name,
                "tags": tags,
            },
            "utilization": {
                "avg_cpu_14d": round(avg_cpu, 2),
                "max_cpu_14d": round(max_cpu, 2),
                "network_bytes_per_hour_14d": round(net_bph, 1),
                "disk_bytes_per_hour_14d": round(disk_bph, 1),
                "cpu_datapoint_coverage": cpu_count,
                "expected_datapoints": EXPECTED_DATAPOINTS,
            },
            "attachments": {
                "asg_name": asg_name,
                "target_group_arns": target_group_arns,
                "has_public_ip": has_public_ip,
                "eip_allocation_id": eip_allocation_id,
                "public_ip_without_eip": public_ip_without_eip,
            },
            "cost": cost,
            "gates": gates,
        }

        fix_command = (
            f"aws ec2 stop-instances --instance-ids {instance_id} "
            f"--region {ec2_region}"
        )
        summary = (
            f"Stop idle {instance_type} {instance_id} "
            f"(avg CPU {avg_cpu:.1f}% / max {max_cpu:.1f}% over {LOOKBACK_DAYS}d; "
            f"~${monthly_cost:.2f}/mo)"
        )

        return Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=instance_id,
            resource_type="EC2 Instance",
            resource_arn=(
                f"arn:aws:ec2:{ec2_region}::instance/{instance_id}"
            ),
            region=ec2_region,
            monthly_impact_usd=monthly_cost,
            summary=summary,
            risk_tier=risk,
            # confidence model: idle classification is strict (4 signals
            # must all be quiet AND data coverage adequate), so 0.85
            # baseline; bumped to 0.9 when the static-price hit was an
            # exact match in the table (more confident cost figure).
            confidence=0.9 if instance_type in HOURLY_USD_US_EAST_1 else 0.85,
            safe_to_fix=safe_to_fix,
            fix_command=fix_command,
            evidence=evidence,
            metadata={
                "instance_type": instance_type,
                "name": name,
                "avg_cpu_14d": round(avg_cpu, 2),
                "age_days": instance_age_days,
            },
        )

    # ------------------------------------------------------------------
    # Small pure helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _find_eip_allocation_id(instance: dict) -> str | None:
        for ni in instance.get("NetworkInterfaces", []) or []:
            assoc = ni.get("Association") or {}
            alloc = assoc.get("AllocationId")
            if alloc:
                return alloc
        return None

    @staticmethod
    def _is_prod_tagged(tags: dict[str, str]) -> bool:
        for key in PROD_TAG_KEYS:
            value = tags.get(key, "")
            if isinstance(value, str) and value.lower() in PROD_TAG_VALUES:
                return True
        return False

    @staticmethod
    def _risk_tier(
        *,
        is_prod_tagged: bool,
        is_in_asg: bool,
        has_elb_attachment: bool,
        monthly_impact_usd: float,
        instance_family: str,
        public_ip_without_eip: bool,
    ) -> RiskTier:
        if (
            is_prod_tagged
            or is_in_asg
            or has_elb_attachment
            or monthly_impact_usd > 500
        ):
            return RiskTier.HIGH
        if monthly_impact_usd > 100 or instance_family in PRODUCTION_FAMILIES:
            return RiskTier.MEDIUM
        if public_ip_without_eip:
            # Bump LOW to MEDIUM: stopping releases the ephemeral public
            # IP, which is the kind of surprise that breaks an unmanaged
            # DNS record. Worth a second look even if everything else is
            # quiet.
            return RiskTier.MEDIUM
        return RiskTier.LOW

    # ------------------------------------------------------------------
    # remediate — one entry point dispatching on mode (principle 4)
    # ------------------------------------------------------------------
    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode == RemediationMode.DRY_RUN:
            return self._remediate_dry_run(finding)
        if mode == RemediationMode.COMMAND:
            return self._remediate_command(finding)
        if mode == RemediationMode.PR:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=(
                    "pr mode not supported for p004 in this milestone; "
                    "instance run-state isn't cleanly modelled in Terraform "
                    "(a stopped instance shows up as drift, not as a config "
                    "change)."
                ),
            )
        if mode == RemediationMode.API_CALL:
            return self._remediate_api_call(finding)
        return super().remediate(finding, mode)

    def _remediate_dry_run(self, finding: Finding) -> RemediationResult:
        util = finding.evidence.get("utilization") or {}
        gates = finding.evidence.get("gates") or {}
        lines = [
            f"# Dry-run — EC2 instance {finding.resource_id} ({finding.region})",
            f"# Monthly cost: ${finding.monthly_impact_usd:.2f} "
            f"(source: {finding.evidence.get('cost', {}).get('cost_source', '?')}, "
            f"region: {finding.evidence.get('cost', {}).get('pricing_region', '?')})",
            f"# avg_cpu_14d: {util.get('avg_cpu_14d', '?')}%  "
            f"max_cpu_14d: {util.get('max_cpu_14d', '?')}%",
            f"# network_bytes_per_hour_14d: {util.get('network_bytes_per_hour_14d', '?')}",
            f"# disk_bytes_per_hour_14d: {util.get('disk_bytes_per_hour_14d', '?')}",
            f"# safe_to_fix: {finding.safe_to_fix}",
            "# Gates:",
        ]
        for k in GATE_NAMES:
            v = gates.get(k)
            marker = "[ok]" if v else "[FAIL]"
            lines.append(f"#   {marker} {k}={v}")
        if finding.safe_to_fix:
            lines.append(f"# Would run: {finding.fix_command}")
        else:
            lines.append(
                "# Refusing to suggest stop — at least one gate failed; "
                "review the gate list manually."
            )
        return RemediationResult(
            finding_id=finding.id,
            pattern_id=self.PATTERN_ID,
            mode=RemediationMode.DRY_RUN,
            success=True,
            message="dry-run plan emitted",
            output="\n".join(lines),
        )

    def _remediate_command(self, finding: Finding) -> RemediationResult:
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.COMMAND,
                success=False,
                message=self._safety_failure_message(finding),
            )
        return RemediationResult(
            finding_id=finding.id,
            pattern_id=self.PATTERN_ID,
            mode=RemediationMode.COMMAND,
            success=True,
            message="emitted stop-instance command",
            output=finding.fix_command,
        )

    def _remediate_api_call(self, finding: Finding) -> RemediationResult:
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.API_CALL,
                success=False,
                message=self._safety_failure_message(finding),
            )
        try:
            ec2 = self.session.client("ec2", region_name=finding.region)
            resp = ec2.stop_instances(InstanceIds=[finding.resource_id])
            states = resp.get("StoppingInstances", []) or []
            previous = (states[0].get("PreviousState") or {}).get("Name") if states else None
            current = (states[0].get("CurrentState") or {}).get("Name") if states else None
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.API_CALL,
                success=True,
                message=f"stopped instance {finding.resource_id}",
                output=f"StopInstances {finding.resource_id} ({finding.region})",
                evidence={
                    "previous_state": previous,
                    "current_state": current,
                },
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.API_CALL,
                success=False,
                message=str(e),
            )

    @staticmethod
    def _safety_failure_message(finding: Finding) -> str:
        gates = finding.evidence.get("gates") or {}
        failed = sorted(k for k in GATE_NAMES if not gates.get(k, False))
        if not failed:
            return f"refusing to stop {finding.resource_id}: not safe_to_fix"
        return (
            f"refusing to stop {finding.resource_id}: "
            f"failed gates: {failed}"
        )
