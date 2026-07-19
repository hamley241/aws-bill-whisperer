"""
Pattern 010: Idle Load Balancers
Detects ELBs, ALBs, and NLBs with no targets or zero traffic.
"""

import logging
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class IdleLoadBalancerPattern(BasePattern):
    PATTERN_ID = "010"
    NAME = "Idle Load Balancers"
    DESCRIPTION = "Load balancers with no targets or zero traffic"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["elbv2", "elb", "cloudwatch"]
    CATEGORY = Category.NETWORK
    REQUIRED_IAM = ["elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTargetHealth", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Load Balancer pricing (approximate, varies by region)
    ALB_HOURLY_COST = 0.0225  # $0.0225/hour (~$16.20/month)
    NLB_HOURLY_COST = 0.0225  # $0.0225/hour (~$16.20/month)
    CLB_HOURLY_COST = 0.025   # $0.025/hour (~$18/month)

    def __init__(self, session=None, idle_days: int = 30):
        super().__init__(session)
        self.idle_days = idle_days
        self.end_time = datetime.now(timezone.utc)
        self.start_time = self.end_time - timedelta(days=idle_days)

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                # Check ALBs and NLBs (ELBv2)
                self._scan_elbv2(region)

                # Check Classic Load Balancers
                self._scan_classic_elb(region)

            except Exception as exc:
                logger.exception(
                    "p010 error scanning load balancers in region %s", region,
                    extra={
                        "pattern_id": self.PATTERN_ID,
                        "region": region,
                        "outcome": "failed",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                continue

        return self._findings

    def _scan_elbv2(self, region: str) -> None:
        """Scan Application Load Balancers and Network Load Balancers"""
        try:
            elbv2 = self.session.client('elbv2', region_name=region)
            cloudwatch = self.session.client('cloudwatch', region_name=region)

            # Get all load balancers
            load_balancers = elbv2.describe_load_balancers()['LoadBalancers']

            for lb in load_balancers:
                lb_arn = lb['LoadBalancerArn']
                lb_name = lb['LoadBalancerName']
                lb_type = lb['Type']  # 'application' or 'network'
                state = lb['State']['Code']
                created_time = lb['CreatedTime']

                if state != 'active':
                    continue  # Skip provisioning/failed LBs

                # Calculate monthly cost
                if lb_type == 'application':
                    monthly_impact_usd= self.ALB_HOURLY_COST * 24 * 30
                    metric_namespace = 'AWS/ApplicationELB'
                    request_metric = 'RequestCount'
                else:  # network
                    monthly_impact_usd= self.NLB_HOURLY_COST * 24 * 30
                    metric_namespace = 'AWS/NetworkELB'
                    request_metric = 'ProcessedBytes'

                # Check for targets
                target_groups = elbv2.describe_target_groups(
                    LoadBalancerArn=lb_arn
                )['TargetGroups']

                has_targets = False
                healthy_targets = 0

                for tg in target_groups:
                    tg_arn = tg['TargetGroupArn']
                    targets = elbv2.describe_target_health(
                        TargetGroupArn=tg_arn
                    )['TargetHealthDescriptions']

                    if targets:
                        has_targets = True
                        healthy_targets += sum(
                            1 for t in targets
                            if t['TargetHealth']['State'] == 'healthy'
                        )

                # Get request count from CloudWatch
                request_count = self._get_lb_request_count(
                    cloudwatch, lb_arn, lb_name, metric_namespace, request_metric
                )

                # Determine if idle
                is_idle = False
                idle_reason = []

                if not has_targets:
                    is_idle = True
                    idle_reason.append("no targets registered")
                elif healthy_targets == 0:
                    is_idle = True
                    idle_reason.append("no healthy targets")

                if request_count == 0:
                    is_idle = True
                    idle_reason.append(f"zero requests in {self.idle_days} days")

                if not is_idle:
                    continue

                # Calculate age
                age_days = (datetime.now(timezone.utc) - created_time).days

                # Determine risk_tier
                if not has_targets and age_days > 30:
                    risk_tier= RiskTier.HIGH
                elif request_count == 0 and age_days > 14:
                    risk_tier= RiskTier.HIGH
                elif not has_targets or request_count == 0:
                    risk_tier= RiskTier.MEDIUM
                else:
                    risk_tier= RiskTier.LOW

                lb_type_display = "ALB" if lb_type == 'application' else "NLB"
                summary= (
                    f"Idle {lb_type_display}: {', '.join(idle_reason)}. "
                    f"Age: {age_days} days. "
                    f"Monthly cost: ${monthly_impact_usd:.2f}. "
                    "Consider deleting if no longer needed."
                )

                finding = Finding(
                    pattern_id=self.PATTERN_ID,
                    resource_id=lb_name,
                    resource_type=f"{lb_type_display} Load Balancer",
                    region=region,
                    monthly_impact_usd=monthly_impact_usd,
                    summary=summary,
                    risk_tier=risk_tier,
                    safe_to_fix=not has_targets and age_days > 7,
                    fix_command=f"aws elbv2 delete-load-balancer --load-balancer-arn {lb_arn} --region {region}",
                    metadata={
                        "lb_arn": lb_arn,
                        "lb_type": lb_type,
                        "has_targets": has_targets,
                        "healthy_targets": healthy_targets,
                        "request_count": request_count,
                        "age_days": age_days,
                        "idle_reason": idle_reason,
                        "created_time": created_time.isoformat(),
                    }
                )
                self._findings.append(finding)

        except Exception as exc:
            logger.exception(
                "p010 error scanning ELBv2 in region %s", region,
                extra={
                    "pattern_id": self.PATTERN_ID,
                    "region": region,
                    "outcome": "failed",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )

    def _scan_classic_elb(self, region: str) -> None:
        """Scan Classic Load Balancers"""
        try:
            elb = self.session.client('elb', region_name=region)
            cloudwatch = self.session.client('cloudwatch', region_name=region)

            # Get all classic load balancers
            load_balancers = elb.describe_load_balancers()['LoadBalancerDescriptions']

            for lb in load_balancers:
                lb_name = lb['LoadBalancerName']
                created_time = lb['CreatedTime']
                instances = lb.get('Instances', [])

                # Calculate monthly cost
                monthly_impact_usd= self.CLB_HOURLY_COST * 24 * 30

                # Check instance health
                has_targets = len(instances) > 0
                healthy_targets = 0

                if has_targets:
                    try:
                        health = elb.describe_instance_health(
                            LoadBalancerName=lb_name
                        )['InstanceStates']
                        healthy_targets = sum(
                            1 for h in health if h['State'] == 'InService'
                        )
                    except Exception:
                        pass

                # Get request count
                request_count = self._get_classic_lb_request_count(
                    cloudwatch, lb_name
                )

                # Determine if idle
                is_idle = False
                idle_reason = []

                if not has_targets:
                    is_idle = True
                    idle_reason.append("no instances registered")
                elif healthy_targets == 0:
                    is_idle = True
                    idle_reason.append("no healthy instances")

                if request_count == 0:
                    is_idle = True
                    idle_reason.append(f"zero requests in {self.idle_days} days")

                if not is_idle:
                    continue

                # Calculate age
                age_days = (datetime.now(timezone.utc) - created_time).days

                # Determine risk_tier (CLBs are also deprecated, so higher urgency)
                if not has_targets and age_days > 30:
                    risk_tier= RiskTier.HIGH
                elif request_count == 0 and age_days > 14:
                    risk_tier= RiskTier.HIGH
                elif not has_targets or request_count == 0:
                    risk_tier= RiskTier.MEDIUM
                else:
                    risk_tier= RiskTier.LOW

                summary= (
                    f"Idle Classic ELB: {', '.join(idle_reason)}. "
                    f"Age: {age_days} days. "
                    f"Monthly cost: ${monthly_impact_usd:.2f}. "
                    "Classic ELBs are deprecated - migrate to ALB/NLB or delete."
                )

                finding = Finding(
                    pattern_id=self.PATTERN_ID,
                    resource_id=lb_name,
                    resource_type="Classic Load Balancer",
                    region=region,
                    monthly_impact_usd=monthly_impact_usd,
                    summary=summary,
                    risk_tier=risk_tier,
                    safe_to_fix=not has_targets and age_days > 7,
                    fix_command=f"aws elb delete-load-balancer --load-balancer-name {lb_name} --region {region}",
                    metadata={
                        "lb_type": "classic",
                        "has_targets": has_targets,
                        "num_instances": len(instances),
                        "healthy_targets": healthy_targets,
                        "request_count": request_count,
                        "age_days": age_days,
                        "idle_reason": idle_reason,
                        "created_time": created_time.isoformat(),
                        "is_deprecated": True,
                    }
                )
                self._findings.append(finding)

        except Exception as exc:
            logger.exception(
                "p010 error scanning Classic ELB in region %s", region,
                extra={
                    "pattern_id": self.PATTERN_ID,
                    "region": region,
                    "outcome": "failed",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )

    def _get_lb_request_count(
        self,
        cloudwatch,
        lb_arn: str,
        lb_name: str,
        namespace: str,
        metric_name: str,
    ) -> int:
        """Get load balancer request count from CloudWatch"""
        try:
            # Extract the ALB/NLB name from ARN for CloudWatch dimension
            # ARN format: arn:aws:elasticloadbalancing:region:account:loadbalancer/app|net/name/id
            arn_parts = lb_arn.split('/')
            if len(arn_parts) >= 3:
                dimension_value = '/'.join(arn_parts[-3:])
            else:
                dimension_value = lb_name

            response = cloudwatch.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=[
                    {'Name': 'LoadBalancer', 'Value': dimension_value}
                ],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=86400,
                Statistics=['Sum']
            )

            return int(sum(dp['Sum'] for dp in response['Datapoints']))

        except Exception:
            return 0

    def _get_classic_lb_request_count(self, cloudwatch, lb_name: str) -> int:
        """Get Classic ELB request count from CloudWatch"""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/ELB',
                MetricName='RequestCount',
                Dimensions=[
                    {'Name': 'LoadBalancerName', 'Value': lb_name}
                ],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=86400,
                Statistics=['Sum']
            )

            return int(sum(dp['Sum'] for dp in response['Datapoints']))

        except Exception:
            return 0

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"Load balancer {finding.resource_id} has targets or is too new. "
                "Manual review required before deletion.",
            )
        try:
            """Delete idle load balancer"""
        

            lb_type = finding.metadata.get('lb_type', '')


            if lb_type == 'classic':
                elb = self.session.client('elb', region_name=finding.region)
                elb.delete_load_balancer(LoadBalancerName=finding.resource_id)
            else:
                elbv2 = self.session.client('elbv2', region_name=finding.region)
                lb_arn = finding.metadata.get('lb_arn')
                if lb_arn:
                    elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
                else:
                    raise ValueError(f"Missing ARN for {finding.resource_id}")

            logger.info(
                "p010 deleted %s %s", finding.resource_type, finding.resource_id,
                extra={
                    "pattern_id": self.PATTERN_ID,
                    "region": finding.region,
                    "outcome": "ok",
                    "resource_type": finding.resource_type,
                    "load_balancer_id": finding.resource_id,
                },
            )
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="deleted load balancer",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
