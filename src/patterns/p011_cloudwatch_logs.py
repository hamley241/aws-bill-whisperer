"""
Pattern 011: CloudWatch Logs Retention & Storage Class
Detects CloudWatch Log Groups with excessive retention (>90 days) or using wrong storage class.

CloudWatch Logs charges:
- STANDARD class: ~$0.50/GB/month for storage
- Infrequent Access (IA): ~$0.25/GB/month (50% savings)
- Logs older than 30 days should typically use IA class

Best practice:
- Use IA class for logs >30 days old
- Set retention to match compliance needs (most don't need >90 days)
- Delete logs that serve no purpose
"""
import logging
from datetime import datetime, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class CloudWatchLogsRetentionPattern(BasePattern):
    PATTERN_ID = "011"
    NAME = "CloudWatch Logs Retention & Storage"
    DESCRIPTION = "Log groups with excessive retention (>90 days) or using wrong storage class"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["logs"]
    CATEGORY = Category.MONITORING
    REQUIRED_IAM = ["logs:DescribeLogGroups", "ec2:DescribeRegions"]

    # Thresholds
    EXCESSIVE_RETENTION_DAYS = 90  # Days before we flag as excessive
    IA_RECOMMENDED_DAYS = 30  # Days after which IA class is recommended
    
    # Pricing per GB per month
    STANDARD_PRICE_PER_GB = 0.50
    IA_PRICE_PER_GB = 0.25

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                logs_client = self.session.client("logs", region_name=region)
                
                # Paginate through all log groups
                paginator = logs_client.get_paginator("describe_log_groups")
                
                for page in paginator.paginate():
                    for log_group in page.get("logGroups", []):
                        findings = self._analyze_log_group(log_group, region)
                        self._findings.extend(findings)

            except Exception as exc:
                logger.exception(
                    "p011 error scanning region %s", region,
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

    def _analyze_log_group(self, log_group: dict, region: str) -> list[Finding]:
        """Analyze a single log group for optimization opportunities."""
        findings = []
        
        log_group_name = log_group["logGroupName"]
        stored_bytes = log_group.get("storedBytes", 0)
        retention_days = log_group.get("retentionInDays")  # None = never expire
        log_group_class = log_group.get("logGroupClass", "STANDARD")
        creation_time = log_group.get("creationTime", 0)
        
        # Convert bytes to GB
        stored_gb = stored_bytes / (1024 ** 3)
        
        # Skip very small log groups (< 100MB)
        if stored_gb < 0.1:
            return findings
        
        # Calculate current monthly cost
        if log_group_class == "INFREQUENT_ACCESS":
            current_monthly_cost = stored_gb * self.IA_PRICE_PER_GB
        else:
            current_monthly_cost = stored_gb * self.STANDARD_PRICE_PER_GB
        
        # Check 1: No retention set (logs kept forever)
        if retention_days is None:
            # Calculate age of log group
            if creation_time:
                age_days = (datetime.now(timezone.utc).timestamp() * 1000 - creation_time) / (1000 * 60 * 60 * 24)
            else:
                age_days = 365  # Assume old if we can't tell
            
            # Estimate savings if retention set to 90 days
            # Rough estimate: if log group is old, assume linear growth
            potential_savings = current_monthly_cost * 0.5  # Conservative 50% estimate
            
            risk_tier= RiskTier.MEDIUM if current_monthly_cost < 50 else RiskTier.HIGH
            
            finding = Finding(
                pattern_id=self.PATTERN_ID,
                resource_id=log_group_name,
                resource_type="CloudWatch Log Group",
                region=region,
                monthly_impact_usd=current_monthly_cost,
                summary=f"No retention policy set. Logs stored forever ({stored_gb:.2f} GB). "
                              f"Set retention to 90 days or less to reduce costs by ~${potential_savings:.2f}/mo.",
                risk_tier=risk_tier,
                safe_to_fix=False,  # Changing retention can delete logs
                fix_command=f"aws logs put-retention-policy --log-group-name '{log_group_name}' --retention-in-days 90 --region {region}",
                metadata={
                    "stored_gb": round(stored_gb, 2),
                    "retention_days": "never expire",
                    "log_group_class": log_group_class,
                    "estimated_age_days": round(age_days, 0),
                    "issue_type": "no_retention"
                }
            )
            findings.append(finding)
        
        # Check 2: Excessive retention (> 90 days) with significant storage
        elif retention_days > self.EXCESSIVE_RETENTION_DAYS and stored_gb > 1:
            potential_savings = current_monthly_cost * (1 - self.EXCESSIVE_RETENTION_DAYS / retention_days)
            
            risk_tier= RiskTier.LOW if current_monthly_cost < 20 else RiskTier.MEDIUM
            
            finding = Finding(
                pattern_id=self.PATTERN_ID,
                resource_id=log_group_name,
                resource_type="CloudWatch Log Group",
                region=region,
                monthly_impact_usd=current_monthly_cost,
                summary=f"Retention set to {retention_days} days ({stored_gb:.2f} GB stored). "
                              f"Consider reducing to 90 days to save ~${potential_savings:.2f}/mo.",
                risk_tier=risk_tier,
                safe_to_fix=False,
                fix_command=f"aws logs put-retention-policy --log-group-name '{log_group_name}' --retention-in-days 90 --region {region}",
                metadata={
                    "stored_gb": round(stored_gb, 2),
                    "retention_days": retention_days,
                    "log_group_class": log_group_class,
                    "issue_type": "excessive_retention"
                }
            )
            findings.append(finding)
        
        # Check 3: Using STANDARD class but should use IA
        # IA is better for logs accessed infrequently (older logs)
        if log_group_class == "STANDARD" and stored_gb > 5:
            # If retention is set, check if it's > 30 days (IA makes sense)
            # If no retention or > 30 days, recommend IA
            recommend_ia = retention_days is None or retention_days > self.IA_RECOMMENDED_DAYS
            
            if recommend_ia:
                potential_savings = stored_gb * (self.STANDARD_PRICE_PER_GB - self.IA_PRICE_PER_GB)
                
                # Only flag if savings are meaningful
                if potential_savings > 2:  # At least $2/month savings
                    finding = Finding(
                        pattern_id=self.PATTERN_ID,
                        resource_id=log_group_name,
                        resource_type="CloudWatch Log Group",
                        region=region,
                        monthly_impact_usd=potential_savings,  # Report potential savings as cost
                        summary=f"Using STANDARD class ({stored_gb:.2f} GB). "
                                      f"Switch to Infrequent Access class to save ~${potential_savings:.2f}/mo. "
                                      f"Note: IA class cannot be changed after creation - must recreate log group.",
                        risk_tier=RiskTier.LOW,
                        safe_to_fix=False,  # Cannot change class directly
                        fix_command=None,  # No direct fix - requires recreating log group
                        metadata={
                            "stored_gb": round(stored_gb, 2),
                            "retention_days": retention_days if retention_days else "never expire",
                            "log_group_class": log_group_class,
                            "issue_type": "wrong_storage_class",
                            "recommended_class": "INFREQUENT_ACCESS"
                        }
                    )
                    findings.append(finding)
        
        return findings

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"Cannot safely fix {finding.resource_id}. "
                f"Changing retention may delete logs. Manual review required.",
            )
        try:
            """Apply fix for retention policy issues."""
        
        
        
            # Execute the fix
            logs_client = self.session.client("logs", region_name=finding.region)
        
            try:
                logs_client.put_retention_policy(
                    logGroupName=finding.resource_id,
                    retentionInDays=90
                )
            except Exception as exc:
                logger.exception(
                    "p011 error setting retention policy",
                    extra={
                        "pattern_id": self.PATTERN_ID,
                        "region": finding.region,
                        "outcome": "failed",
                        "log_group_name": finding.resource_id,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                return False
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="set retention policy to 90 days",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
