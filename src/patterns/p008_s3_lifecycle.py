"""
Pattern 008: S3 Lifecycle Rules
Detects S3 buckets without lifecycle policies that could save money with
Intelligent-Tiering, Glacier transitions, or object expiration.
"""

from datetime import datetime, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


class S3LifecyclePattern(BasePattern):
    PATTERN_ID = "008"
    NAME = "S3 Lifecycle Rules"
    DESCRIPTION = "S3 buckets without lifecycle policies that could save money"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["s3", "cloudwatch"]
    CATEGORY = Category.STORAGE
    REQUIRED_IAM = ["s3:ListAllMyBuckets", "s3:GetBucketLifecycleConfiguration", "s3:GetBucketLocation"]

    # S3 pricing (approximate, varies by region)
    STANDARD_PRICE_PER_GB = 0.023  # Standard storage
    IA_PRICE_PER_GB = 0.0125  # Infrequent Access
    INTELLIGENT_TIERING_PRICE_PER_GB = 0.023  # Same as standard for frequent tier
    GLACIER_INSTANT_PRICE_PER_GB = 0.004  # Glacier Instant Retrieval
    GLACIER_FLEXIBLE_PRICE_PER_GB = 0.0036  # Glacier Flexible Retrieval

    # Keywords suggesting bucket purpose
    LOG_BUCKET_KEYWORDS = ['log', 'logs', 'logging', 'audit', 'trail', 'access-log']
    BACKUP_BUCKET_KEYWORDS = ['backup', 'backups', 'bkp', 'archive', 'dr-']
    TEMP_BUCKET_KEYWORDS = ['temp', 'tmp', 'staging', 'cache', 'scratch']

    def __init__(self, session=None, min_bucket_size_gb: float = 10.0):
        super().__init__(session)
        self.min_bucket_size_gb = min_bucket_size_gb

    def scan(self, regions: list[str] = None) -> list[Finding]:
        # S3 is a global service, but we use us-east-1 for API calls
        self._findings = []

        try:
            s3 = self.session.client('s3', region_name='us-east-1')
            cloudwatch = self.session.client('cloudwatch', region_name='us-east-1')

            # List all buckets
            buckets = s3.list_buckets()['Buckets']

            for bucket in buckets:
                bucket_name = bucket['Name']
                create_date = bucket['CreationDate']

                try:
                    # Check if bucket has lifecycle configuration
                    has_lifecycle = self._has_lifecycle_rules(s3, bucket_name)
                    
                    if has_lifecycle:
                        continue  # Skip buckets with lifecycle rules

                    # Get bucket size from CloudWatch metrics
                    bucket_size_gb = self._get_bucket_size_gb(cloudwatch, bucket_name)
                    
                    if bucket_size_gb < self.min_bucket_size_gb:
                        continue  # Skip small buckets

                    # Get bucket region for accurate recommendations
                    bucket_region = self._get_bucket_region(s3, bucket_name)

                    # Classify bucket by name
                    bucket_type = self._classify_bucket(bucket_name)

                    # Calculate potential savings based on bucket type
                    monthly_impact_usd= bucket_size_gb * self.STANDARD_PRICE_PER_GB
                    potential_savings = self._calculate_potential_savings(
                        bucket_type, bucket_size_gb
                    )

                    # Build summary
                    summary= self._build_recommendation(
                        bucket_name, bucket_type, bucket_size_gb, potential_savings
                    )

                    # Determine risk_tier
                    if potential_savings > 100:
                        risk_tier= RiskTier.HIGH
                    elif potential_savings > 25:
                        risk_tier= RiskTier.MEDIUM
                    else:
                        risk_tier= RiskTier.LOW

                    # Age in days
                    age_days = (datetime.now(timezone.utc) - create_date).days

                    finding = Finding(
                        pattern_id=self.PATTERN_ID,
                        resource_id=bucket_name,
                        resource_type="S3 Bucket",
                        region=bucket_region or 'us-east-1',
                        monthly_impact_usd=potential_savings,  # Report potential savings
                        summary=summary,
                        risk_tier=risk_tier,
                        safe_to_fix=False,  # Lifecycle changes need review
                        fix_command=None,
                        metadata={
                            "bucket_size_gb": round(bucket_size_gb, 2),
                            "current_monthly_cost": round(monthly_impact_usd, 2),
                            "potential_savings": round(potential_savings, 2),
                            "bucket_type": bucket_type,
                            "has_lifecycle": False,
                            "age_days": age_days,
                            "create_date": create_date.isoformat(),
                        }
                    )
                    self._findings.append(finding)

                except Exception as e:
                    # Handle specific error cases
                    error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', '')
                    if error_code == 'NoSuchBucket':
                        continue  # Bucket was deleted during scan
                    if error_code == 'AccessDenied' or 'AccessDenied' in str(e):
                        continue  # Skip buckets we can't access
                    print(f"Error analyzing bucket {bucket_name}: {e}")
                    continue

        except Exception as e:
            print(f"Error scanning S3 buckets: {e}")

        return self._findings

    def _has_lifecycle_rules(self, s3, bucket_name: str) -> bool:
        """Check if bucket has any lifecycle configuration"""
        try:
            s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            return True
        except Exception as e:
            error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', '')
            if error_code == 'NoSuchLifecycleConfiguration' or 'NoSuchLifecycleConfiguration' in str(e):
                return False
            raise

    def _get_bucket_size_gb(self, cloudwatch, bucket_name: str) -> float:
        """Get bucket size from CloudWatch metrics"""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/S3',
                MetricName='BucketSizeBytes',
                Dimensions=[
                    {'Name': 'BucketName', 'Value': bucket_name},
                    {'Name': 'StorageType', 'Value': 'StandardStorage'}
                ],
                StartTime=datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
                EndTime=datetime.now(timezone.utc),
                Period=86400,
                Statistics=['Average']
            )

            if response['Datapoints']:
                bytes_size = max(dp['Average'] for dp in response['Datapoints'])
                return bytes_size / (1024**3)
            return 0.0

        except Exception:
            return 0.0

    def _get_bucket_region(self, s3, bucket_name: str) -> str | None:
        """Get bucket region"""
        try:
            response = s3.get_bucket_location(Bucket=bucket_name)
            # None means us-east-1
            return response['LocationConstraint'] or 'us-east-1'
        except Exception:
            return None

    def _classify_bucket(self, bucket_name: str) -> str:
        """Classify bucket type based on name"""
        name_lower = bucket_name.lower()

        for keyword in self.LOG_BUCKET_KEYWORDS:
            if keyword in name_lower:
                return 'logs'

        for keyword in self.BACKUP_BUCKET_KEYWORDS:
            if keyword in name_lower:
                return 'backup'

        for keyword in self.TEMP_BUCKET_KEYWORDS:
            if keyword in name_lower:
                return 'temporary'

        return 'general'

    def _calculate_potential_savings(
        self, bucket_type: str, bucket_size_gb: float
    ) -> float:
        """Calculate potential monthly savings based on bucket type"""
        current_cost = bucket_size_gb * self.STANDARD_PRICE_PER_GB

        if bucket_type == 'logs':
            # Logs older than 30 days can go to Glacier, 90+ days can be deleted
            # Assume 70% of data is old logs
            glacier_cost = bucket_size_gb * 0.7 * self.GLACIER_FLEXIBLE_PRICE_PER_GB
            recent_cost = bucket_size_gb * 0.3 * self.STANDARD_PRICE_PER_GB
            optimized_cost = glacier_cost + recent_cost
            return current_cost - optimized_cost

        elif bucket_type == 'backup':
            # Backups benefit from Glacier Instant Retrieval
            optimized_cost = bucket_size_gb * self.GLACIER_INSTANT_PRICE_PER_GB
            return current_cost - optimized_cost

        elif bucket_type == 'temporary':
            # Temp data could be auto-deleted, assume 50% savings
            return current_cost * 0.5

        else:
            # General buckets benefit from Intelligent-Tiering
            # Assume 30% of data is infrequently accessed
            ia_portion = bucket_size_gb * 0.3 * self.IA_PRICE_PER_GB
            active_portion = bucket_size_gb * 0.7 * self.STANDARD_PRICE_PER_GB
            optimized_cost = ia_portion + active_portion
            return current_cost - optimized_cost

    def _build_recommendation(
        self,
        bucket_name: str,
        bucket_type: str,
        bucket_size_gb: float,
        potential_savings: float,
    ) -> str:
        """Build actionable summary based on bucket type"""
        if bucket_type == 'logs':
            return (
                f"Add lifecycle rule: transition logs to Glacier after 30 days, "
                f"delete after 365 days. Bucket size: {bucket_size_gb:.1f}GB. "
                f"Potential savings: ${potential_savings:.2f}/month"
            )
        elif bucket_type == 'backup':
            return (
                f"Add lifecycle rule: transition to Glacier Instant Retrieval after 7 days. "
                f"Bucket size: {bucket_size_gb:.1f}GB. "
                f"Potential savings: ${potential_savings:.2f}/month"
            )
        elif bucket_type == 'temporary':
            return (
                f"Add lifecycle rule: expire objects after 7-30 days. "
                f"Bucket size: {bucket_size_gb:.1f}GB. "
                f"Potential savings: ${potential_savings:.2f}/month"
            )
        else:
            return (
                f"Enable S3 Intelligent-Tiering for automatic cost optimization. "
                f"Bucket size: {bucket_size_gb:.1f}GB. "
                f"Potential savings: ${potential_savings:.2f}/month"
            )

