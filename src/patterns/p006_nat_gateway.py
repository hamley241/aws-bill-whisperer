"""
Pattern 006: NAT Gateway Optimization — first agent-native pattern.

Detection
  Scans every region for available NAT Gateways. Captures the NAT itself,
  baseline cost (hourly), VPC/route-table topology, existing VPC
  endpoints, and a deterministic set of endpoint candidates (S3 and
  DynamoDB Gateway endpoints today). Findings are structured so the
  SavingsPlanner can reason about endpoint choices and migration
  sequencing across deterministic evidence, not free-form prose.

Cost model
  AWS NAT Gateway billing has two parts: an hourly charge and a
  per-GB "data processing" charge. The CloudWatch metrics
  (BytesInFromSource, BytesInFromDestination, BytesOutToSource,
  BytesOutToDestination) decompose traffic into four directions, but
  AWS docs do not currently identify a single metric (or sum of
  metrics) as the billable processed-byte proxy. Naively summing
  BytesOutToDestination + BytesOutToSource is one common convention
  but is not validated by AWS docs we could cite at implementation
  time.

  Per the p006 contract decision, this v1 ships with the conservative
  "hourly_only" cost model: monthly_processing_cost_usd = 0,
  gb_processed_30d = 0, cost_source = "hourly_only". Findings are
  still emitted — a NAT costing $32/mo for nothing is still waste —
  but every endpoint candidate's est_monthly_savings_usd is 0.0.
  A follow-up PR can verify metric semantics and add a
  "cloudwatch_derived" cost source.

Evidence layout (see also: the p006 contract proposal)

    evidence = {
      "nat_gateway":  {...identity, tags, age...},
      "cost":         {"cost_source": "hourly_only", ...},
      "topology":     {route tables, private subnets, existing VPCEs},
      "inferred":     {"endpoint_candidates": [...]},
      "gates":        {has_baseline_cost, has_any_candidate,
                       observed_supports_top_candidate},
      # "observed":   only when VPC Flow Logs are present
      #               (not produced by v1 scanner — fixture-only).
    }

Observed vs inferred is *structurally* separated. The planner's prompt
and the rationale_hedges_inferred rubric warning rely on this split;
inventing observed evidence from inferred candidates is forbidden.

Remediation modes
  DRY_RUN  — present the inferred candidates (no AWS calls).
  COMMAND  — emit `aws ec2 create-vpc-endpoint ...` ONLY when the top
             candidate is observed-tier. Inferred-only findings get
             a non-success result with reason `insufficient_evidence_for_command`.
  PR       — deferred. Terraform diff for endpoint + route-table
             association is materially more complex than p001's delete.
  API_CALL — forbidden in this milestone. Returns a non-success
             result with reason `not_supported_in_oss_milestone`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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
# Cost constants. Region-aware pricing is deferred (see contract §1).
# us-east-1 list price is used as a single global constant; the cost is
# under-stated in pricier regions and over-stated nowhere meaningful at
# the scale a finding triggers on.
# ---------------------------------------------------------------------------
NAT_HOURLY_USD = 0.045
HOURS_PER_MONTH = 24 * 30  # 720; same convention as p001
NAT_PROCESSING_USD_PER_GB = 0.045  # carried for future cloudwatch_derived path

# ---------------------------------------------------------------------------
# Risk thresholds. Initial values; tune later. Constants because the
# rubric and the agentic spec both reference them.
# ---------------------------------------------------------------------------
HIGH_RISK_ROUTE_COUNT = 4          # > 4 affected route tables → HIGH
MEDIUM_RISK_ROUTE_COUNT_MIN = 2    # 2..4 → MEDIUM (lower bound inclusive)
HIGH_RISK_SUBNET_COUNT = 4         # > 4 private subnets → HIGH
MEDIUM_RISK_GB_30D = 1000          # > 1000 GB / 30d → MEDIUM
PROD_TAG_VALUES = frozenset({"prod", "production"})

# Closed enum of cost sources persisted into evidence.
COST_SOURCE_HOURLY_ONLY = "hourly_only"
COST_SOURCE_CLOUDWATCH = "cloudwatch_derived"

# Closed enum for the per-candidate evidence tier.
EVIDENCE_TIER_OBSERVED = "observed"
EVIDENCE_TIER_INFERRED = "inferred"

# Service descriptors for the two Gateway-endpoint candidates we always
# enumerate. Stable candidate_id ensures planner sub-actions can
# reference them across runs.
_GATEWAY_CANDIDATES = (
    ("s3", "cand-gateway-s3"),
    ("dynamodb", "cand-gateway-ddb"),
)


class NatGatewayPattern(BasePattern):
    PATTERN_ID = "006"
    NAME = "NAT Gateway Optimization"
    DESCRIPTION = (
        "NAT Gateways with evidence sufficient for the planner to "
        "reason about VPC-endpoint migration."
    )
    CATEGORY = Category.NETWORK
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["ec2"]
    REQUIRED_IAM = [
        "ec2:DescribeNatGateways",
        "ec2:DescribeRouteTables",
        "ec2:DescribeVpcEndpoints",
        "ec2:DescribeSubnets",
        "ec2:DescribeRegions",
        # GetMetricStatistics is listed for forward-compatibility with the
        # cloudwatch_derived path; v1 emits hourly_only and does not call
        # CloudWatch.
        "cloudwatch:GetMetricStatistics",
    ]

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------
    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                self._findings.extend(self._scan_region(region))
            except Exception:  # pragma: no cover — surface in caller
                # Structured surfacing so log aggregators see the region and
                # stack trace; never swallow into stdout.
                logger.exception(
                    "p006 scan failed for region %s; continuing", region,
                )
                continue
        return self._findings

    def _scan_region(self, region: str) -> list[Finding]:
        ec2 = self.session.client("ec2", region_name=region)
        nat_gateways = ec2.describe_nat_gateways(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )["NatGateways"]
        if not nat_gateways:
            return []

        # One DescribeRouteTables + DescribeVpcEndpoints per region — the
        # NAT-to-rtb mapping is cheap to compute locally.
        route_tables = ec2.describe_route_tables()["RouteTables"]
        vpc_endpoints = ec2.describe_vpc_endpoints()["VpcEndpoints"]

        return [
            self._build_finding(region, nat_gw, route_tables, vpc_endpoints)
            for nat_gw in nat_gateways
        ]

    # ------------------------------------------------------------------
    # finding construction (deterministic; never calls an LLM)
    # ------------------------------------------------------------------
    def _build_finding(
        self,
        region: str,
        nat_gw: dict,
        all_route_tables: list[dict],
        all_vpc_endpoints: list[dict],
    ) -> Finding:
        nat_gw_id = nat_gw["NatGatewayId"]
        vpc_id = nat_gw["VpcId"]
        subnet_id = nat_gw["SubnetId"]
        create_time = nat_gw.get("CreateTime")
        age_days = (
            (datetime.now(timezone.utc) - create_time).days
            if create_time else 0
        )
        tags = {t["Key"]: t["Value"] for t in nat_gw.get("Tags", [])}

        # ----- cost (hourly_only — see module docstring) -----
        monthly_hours_cost_usd = round(NAT_HOURLY_USD * HOURS_PER_MONTH, 2)
        cost = {
            "monthly_hours_cost_usd": monthly_hours_cost_usd,
            "monthly_processing_cost_usd": 0.0,
            # field name spells out that this is bidirectional processed
            # bytes (sum across both NAT directions), not egress-only.
            # See module docstring for why this is 0 in v1.
            "gb_processed_30d_bidirectional": 0.0,
            "cost_source": COST_SOURCE_HOURLY_ONLY,
            "confidence": "low",
        }

        # ----- topology -----
        topology = self._topology(nat_gw_id, vpc_id, all_route_tables,
                                   all_vpc_endpoints)

        # ----- inferred candidates -----
        # topology["existing_vpc_endpoints"] uses our flattened dict
        # shape: {"vpce_id", "service", "type"} where `service` is the
        # raw AWS service name ("com.amazonaws.<region>.<svc>"). The
        # last dot-segment is the comparable suffix.
        existing_services = {
            (vpce.get("service") or "").split(".")[-1]
            for vpce in topology["existing_vpc_endpoints"]
        }
        candidates = self._candidates(region, existing_services)

        # ----- gates -----
        has_observed_top = False  # v1 never observes (hourly_only)
        gates = {
            "has_baseline_cost": monthly_hours_cost_usd > 0,
            "has_any_candidate": bool(candidates),
            "observed_supports_top_candidate": has_observed_top,
        }

        # ----- risk -----
        risk = self._risk_tier(
            tags=tags,
            affected_route_count=topology["affected_route_count"],
            private_subnet_count=len(topology["private_subnets_using_nat"]),
            gb_30d=cost["gb_processed_30d_bidirectional"],
        )

        monthly_impact = round(
            cost["monthly_hours_cost_usd"]
            + cost["monthly_processing_cost_usd"],
            2,
        )

        evidence: dict[str, Any] = {
            "nat_gateway": {
                "nat_gateway_id": nat_gw_id,
                "vpc_id": vpc_id,
                "subnet_id": subnet_id,
                "create_time": create_time.isoformat() if create_time else None,
                "age_days": age_days,
                "state": nat_gw.get("State", "available"),
                "tags": tags,
            },
            "cost": cost,
            "topology": topology,
            "inferred": {"endpoint_candidates": candidates},
            "gates": gates,
        }

        summary = (
            f"NAT Gateway {nat_gw_id} costs ~${monthly_impact:.2f}/mo "
            f"(hourly only; processed-byte cost not measured in v1). "
            f"{len(candidates)} deterministic endpoint candidate(s) "
            f"available for planner sub-action reasoning."
        )

        return Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=nat_gw_id,
            resource_type="NAT Gateway",
            resource_arn=f"arn:aws:ec2:{region}::natgateway/{nat_gw_id}",
            region=region,
            monthly_impact_usd=monthly_impact,
            summary=summary,
            risk_tier=risk,
            # 0.6: conservative under the hourly_only cost model — we know
            # the NAT exists and what the hourly charge is, but processed-
            # byte cost (the larger half) is unmeasured. Bump in the
            # cloudwatch_derived path once metric semantics are verified.
            confidence=0.6,
            # NAT changes are never safe-to-auto-fix in OSS this milestone:
            # api_call is forbidden and pr is deferred.
            safe_to_fix=False,
            fix_command=None,  # sub-action commands live in remediate()
            evidence=evidence,
            metadata={
                "vpc_id": vpc_id,
                "subnet_id": subnet_id,
                "age_days": age_days,
                "cost_source": cost["cost_source"],
            },
        )

    # ------------------------------------------------------------------
    # topology helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _topology(
        nat_gw_id: str,
        vpc_id: str,
        all_route_tables: list[dict],
        all_vpc_endpoints: list[dict],
    ) -> dict[str, Any]:
        affected_rtbs: list[dict] = []
        for rtb in all_route_tables:
            if rtb.get("VpcId") != vpc_id:
                continue
            nat_targeting_routes = [
                r for r in rtb.get("Routes", [])
                if r.get("NatGatewayId") == nat_gw_id
            ]
            if not nat_targeting_routes:
                continue
            subnet_ids = sorted({
                assoc.get("SubnetId")
                for assoc in rtb.get("Associations", [])
                if assoc.get("SubnetId")
            })
            affected_rtbs.append({
                "rtb_id": rtb.get("RouteTableId"),
                "subnets": subnet_ids,
                "default_route_target": nat_gw_id,
            })

        private_subnets = sorted({
            sn for rtb in affected_rtbs for sn in rtb["subnets"]
        })

        existing = [
            {
                "vpce_id": v.get("VpcEndpointId"),
                "service": v.get("ServiceName"),
                "type": v.get("VpcEndpointType", "Gateway"),
            }
            for v in all_vpc_endpoints
            if v.get("VpcId") == vpc_id
        ]

        return {
            "private_subnets_using_nat": private_subnets,
            "route_tables": affected_rtbs,
            "affected_route_count": len(affected_rtbs),
            "existing_vpc_endpoints": existing,
        }

    # ------------------------------------------------------------------
    # candidate enumeration — deterministic, S3 + DynamoDB Gateway only.
    # ------------------------------------------------------------------
    @staticmethod
    def _candidates(
        region: str,
        existing_service_suffixes: set[str],
    ) -> list[dict[str, Any]]:
        out = []
        for service, candidate_id in _GATEWAY_CANDIDATES:
            already_present = service in existing_service_suffixes
            if already_present:
                continue
            # All v1 candidates are inferred-tier because we have no
            # observed evidence (hourly_only). Savings are 0.0 by rule.
            out.append({
                "candidate_id": candidate_id,
                "service": service,
                "endpoint_type": "Gateway",
                "evidence_tier": EVIDENCE_TIER_INFERRED,
                "supporting_observed_share": None,
                "supporting_inference_reason": "service_endpoint_supported_by_aws",
                "est_monthly_savings_usd": 0.0,
                "blast_radius": "low",
                "deterministic_command_hint": (
                    f"aws ec2 create-vpc-endpoint "
                    f"--vpc-id <vpc-id> "
                    f"--service-name com.amazonaws.{region}.{service} "
                    f"--route-table-ids <rtb-ids> "
                    f"--region {region}"
                ),
            })
        return out

    @staticmethod
    def _risk_tier(
        *,
        tags: dict[str, str],
        affected_route_count: int,
        private_subnet_count: int,
        gb_30d: float,
    ) -> RiskTier:
        env_value = tags.get("Env", "").lower()
        is_prod = env_value in PROD_TAG_VALUES
        if (
            is_prod
            or affected_route_count > HIGH_RISK_ROUTE_COUNT
            or private_subnet_count > HIGH_RISK_SUBNET_COUNT
        ):
            return RiskTier.HIGH
        if (
            gb_30d > MEDIUM_RISK_GB_30D
            or MEDIUM_RISK_ROUTE_COUNT_MIN <= affected_route_count <= HIGH_RISK_ROUTE_COUNT
        ):
            return RiskTier.MEDIUM
        return RiskTier.LOW

    # ------------------------------------------------------------------
    # remediate
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
                    "pr mode not supported for p006 in this milestone; "
                    "Terraform diff for VPC endpoints is deferred."
                ),
            )
        if mode == RemediationMode.API_CALL:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message="not_supported_in_oss_milestone",
            )
        return super().remediate(finding, mode)

    def _remediate_dry_run(self, finding: Finding) -> RemediationResult:
        candidates = (finding.evidence.get("inferred") or {}).get(
            "endpoint_candidates", []
        )
        lines = [
            f"# Dry-run — NAT Gateway {finding.resource_id} ({finding.region})",
            f"# Monthly cost: ${finding.monthly_impact_usd:.2f} "
            f"(source: {finding.evidence.get('cost', {}).get('cost_source', '?')})",
            f"# {len(candidates)} candidate(s) available for planner reasoning:",
        ]
        for c in candidates:
            lines.append(
                f"#   - {c['candidate_id']} ({c['service']} Gateway, "
                f"tier={c['evidence_tier']}, est ${c['est_monthly_savings_usd']:.2f}/mo)"
            )
        if not candidates:
            lines.append("#   (no candidates — this NAT may need manual review)")
        return RemediationResult(
            finding_id=finding.id,
            pattern_id=self.PATTERN_ID,
            mode=RemediationMode.DRY_RUN,
            success=True,
            message="dry-run plan emitted",
            output="\n".join(lines),
        )

    def _remediate_command(self, finding: Finding) -> RemediationResult:
        candidates = (finding.evidence.get("inferred") or {}).get(
            "endpoint_candidates", []
        )
        # Only observed-tier candidates earn a real `aws` command.
        observed = [
            c for c in candidates
            if c.get("evidence_tier") == EVIDENCE_TIER_OBSERVED
        ]
        if not observed:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.COMMAND,
                success=False,
                message="insufficient_evidence_for_command",
            )
        top = observed[0]
        return RemediationResult(
            finding_id=finding.id,
            pattern_id=self.PATTERN_ID,
            mode=RemediationMode.COMMAND,
            success=True,
            message=f"create-vpc-endpoint suggestion for {top['service']}",
            output=top.get("deterministic_command_hint"),
            evidence={"candidate_id": top["candidate_id"]},
        )
