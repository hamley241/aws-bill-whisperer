"""
Pattern 020: Unused Secrets Manager Secrets
Detects Secrets Manager secrets that are:
- Not accessed in 90+ days (potentially unused)
- Have no rotation configured (security risk + wasted secret)
"""

import logging
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class SecretsManagerPattern(BasePattern):
    PATTERN_ID = "020"
    NAME = "Unused Secrets Manager Secrets"
    DESCRIPTION = "Secrets Manager secrets not accessed in 90+ days or without rotation"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["secretsmanager"]
    CATEGORY = Category.SECURITY
    REQUIRED_IAM = ["secretsmanager:ListSecrets", "secretsmanager:DescribeSecret", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Pricing
    SECRET_PRICE_PER_MONTH = 0.40  # $0.40 per secret per month
    API_CALL_PRICE = 0.05 / 10000  # $0.05 per 10,000 API calls

    # Thresholds
    UNUSED_DAYS_THRESHOLD = 90  # Consider unused after 90 days
    WARNING_DAYS_THRESHOLD = 60  # Warn after 60 days

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                secretsmanager = self.session.client('secretsmanager', region_name=region)
                self._scan_secrets(secretsmanager, region)
            except Exception:
                logger.exception("p020 error scanning Secrets Manager in region %s", region)
                continue

        return self._findings

    def _scan_secrets(self, secretsmanager, region: str):
        """Scan all secrets in the region."""
        try:
            paginator = secretsmanager.get_paginator('list_secrets')

            for page in paginator.paginate():
                for secret_entry in page.get('SecretList', []):
                    self._analyze_secret(secretsmanager, secret_entry, region)

        except Exception:
            logger.exception("p020 error listing secrets in region %s", region)

    def _analyze_secret(self, secretsmanager, secret_entry: dict, region: str):
        """Analyze a single secret for usage and rotation status."""
        try:
            secret_arn = secret_entry['ARN']
            secret_name = secret_entry['Name']
            created_date = secret_entry.get('CreatedDate')
            last_accessed_date = secret_entry.get('LastAccessedDate')
            last_changed_date = secret_entry.get('LastChangedDate')
            last_rotated_date = secret_entry.get('LastRotatedDate')
            rotation_enabled = secret_entry.get('RotationEnabled', False)
            rotation_rules = secret_entry.get('RotationRules', {})

            now = datetime.now(timezone.utc)

            # Calculate days since last access
            if last_accessed_date:
                days_since_access = (now - last_accessed_date).days
            else:
                # If never accessed, use creation date
                days_since_access = (now - created_date).days if created_date else 999

            # Calculate days since last change/rotation
            last_modified = last_rotated_date or last_changed_date or created_date
            days_since_modified = (now - last_modified).days if last_modified else 999

            # Determine issues
            issues = []
            risk_tier= RiskTier.LOW
            monthly_impact_usd= self.SECRET_PRICE_PER_MONTH

            # Check for unused secret
            is_unused = days_since_access >= self.UNUSED_DAYS_THRESHOLD

            # Check for no rotation (security issue for long-lived secrets)
            no_rotation = not rotation_enabled and days_since_access < self.UNUSED_DAYS_THRESHOLD

            # Skip if no issues
            if not is_unused and not no_rotation:
                return

            # Build summary
            if is_unused:
                issues.append(f"not accessed in {days_since_access} days")
                if days_since_access > 180:
                    risk_tier= RiskTier.MEDIUM

            if no_rotation:
                issues.append("no rotation configured")

            # Check if secret is scheduled for deletion
            deleted_date = secret_entry.get('DeletedDate')
            if deleted_date:
                return  # Already scheduled for deletion

            # Get tags to help identify owner
            tags = {}
            try:
                tags_response = secretsmanager.describe_secret(SecretId=secret_arn)
                tags = {t['Key']: t['Value'] for t in tags_response.get('Tags', [])}
            except Exception:
                pass

            summary= f"Secret '{secret_name}' issues: {', '.join(issues)}. "
            if is_unused:
                summary += "Consider deleting if no longer needed."
            elif no_rotation:
                summary += "Enable rotation for security best practices."

            finding = Finding(
                pattern_id=self.PATTERN_ID,
                resource_id=secret_name,
                resource_type="Secrets Manager Secret",
                region=region,
                monthly_impact_usd=monthly_impact_usd,
                summary=summary,
                risk_tier=risk_tier,
                safe_to_fix=is_unused and days_since_access > 180,  # Only safe if very old
                fix_command=f"aws secretsmanager delete-secret --secret-id {secret_name} --recovery-window-in-days 30 --region {region}" if is_unused else None,
                metadata={
                    "secret_arn": secret_arn,
                    "days_since_access": days_since_access,
                    "days_since_modified": days_since_modified,
                    "rotation_enabled": rotation_enabled,
                    "rotation_rules": rotation_rules,
                    "created_date": created_date.isoformat() if created_date else None,
                    "last_accessed_date": last_accessed_date.isoformat() if last_accessed_date else None,
                    "last_changed_date": last_changed_date.isoformat() if last_changed_date else None,
                    "last_rotated_date": last_rotated_date.isoformat() if last_rotated_date else None,
                    "tags": tags,
                    "issues": issues,
                }
            )
            self._findings.append(finding)

        except Exception:
            logger.exception("p020 error analyzing secret %s", secret_entry.get("Name", "unknown"))

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"Secret {finding.resource_id} is not marked safe to delete",
            )
        try:
            """Delete an unused secret with recovery window."""
        


            secretsmanager = self.session.client('secretsmanager', region_name=finding.region)

            try:
                # Schedule deletion with recovery window (can be recovered within 30 days)
                secretsmanager.delete_secret(
                    SecretId=finding.resource_id,
                    RecoveryWindowInDays=30
                )
                logger.info("p020 scheduled deletion for secret %s (recovery possible for 30 days)", finding.resource_id)
            except Exception:
                logger.exception("p020 error deleting secret %s", finding.resource_id)
                return False
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="scheduled secret deletion (30-day recovery)",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
