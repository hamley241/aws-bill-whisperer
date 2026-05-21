"""
Tests for Pattern 009: Cross-AZ Data Transfer
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p009_cross_az_transfer import CrossAZTransferPattern
from patterns.base import RiskTier


class TestCrossAZTransferPattern:
    """Tests for P009: Cross-AZ Data Transfer"""

    def test_finds_rds_multi_az_with_high_traffic(self):
        """
        GIVEN: An RDS Multi-AZ instance with high write throughput
        WHEN: The pattern scans for cross-AZ transfer
        THEN: It returns a finding with cost estimate
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {
            'DBInstances': [{
                'DBInstanceIdentifier': 'prod-db',
                'MultiAZ': True,
                'DBInstanceClass': 'db.r5.large',
                'Engine': 'postgres',
                'AllocatedStorage': 500,
            }]
        }

        # 200GB write throughput in 30 days
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Sum': 200 * 1024**3 / 30}  # Per day
                for _ in range(30)
            ]
        }

        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': []
        }
        mock_ec2.describe_instances.return_value = {'Reservations': []}

        pattern = CrossAZTransferPattern(session=mock_session, monthly_threshold_gb=50.0)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'prod-db'
        assert findings[0].resource_type == 'RDS Multi-AZ Instance'
        assert findings[0].metadata['multi_az'] is True
        assert 'Cross-AZ replication' in findings[0].summary

    def test_skips_single_az_rds(self):
        """
        GIVEN: An RDS single-AZ instance
        WHEN: The pattern scans
        THEN: It skips the instance (no finding)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {
            'DBInstances': [{
                'DBInstanceIdentifier': 'dev-db',
                'MultiAZ': False,  # Single AZ
                'DBInstanceClass': 'db.t3.medium',
                'Engine': 'mysql',
            }]
        }

        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': []
        }
        mock_ec2.describe_instances.return_value = {'Reservations': []}

        pattern = CrossAZTransferPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0

    def test_finds_elasticache_cross_az(self):
        """
        GIVEN: An ElastiCache cluster spanning multiple AZs with high traffic
        WHEN: The pattern scans
        THEN: It returns a finding with cross-AZ cost estimate
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {'DBInstances': []}

        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': [{
                'ReplicationGroupId': 'prod-redis',
                'NodeGroups': [{
                    'NodeGroupMembers': [
                        {
                            'CacheClusterId': 'prod-redis-001',
                            'PreferredAvailabilityZone': 'us-east-1a',
                        },
                        {
                            'CacheClusterId': 'prod-redis-002',
                            'PreferredAvailabilityZone': 'us-east-1b',
                        },
                    ]
                }]
            }]
        }

        # High network traffic - 500GB
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Sum': 250 * 1024**3}]
        }

        mock_ec2.describe_instances.return_value = {'Reservations': []}

        pattern = CrossAZTransferPattern(session=mock_session, monthly_threshold_gb=50.0)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'prod-redis'
        assert findings[0].resource_type == 'ElastiCache Replication Group'
        assert len(findings[0].metadata['azs_used']) == 2

    def test_skips_single_az_elasticache(self):
        """
        GIVEN: An ElastiCache cluster in a single AZ
        WHEN: The pattern scans
        THEN: It skips the cluster (no cross-AZ traffic)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {'DBInstances': []}

        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': [{
                'ReplicationGroupId': 'dev-redis',
                'NodeGroups': [{
                    'NodeGroupMembers': [
                        {
                            'CacheClusterId': 'dev-redis-001',
                            'PreferredAvailabilityZone': 'us-east-1a',
                        },
                        {
                            'CacheClusterId': 'dev-redis-002',
                            'PreferredAvailabilityZone': 'us-east-1a',  # Same AZ
                        },
                    ]
                }]
            }]
        }

        mock_ec2.describe_instances.return_value = {'Reservations': []}

        pattern = CrossAZTransferPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0

    def test_finds_ec2_cross_az_traffic(self):
        """
        GIVEN: EC2 instances in a multi-AZ VPC with high network traffic
        WHEN: The pattern scans
        THEN: It returns findings for high-traffic instances
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {'DBInstances': []}
        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': []
        }

        mock_ec2.describe_instances.return_value = {
            'Reservations': [
                {
                    'Instances': [
                        {
                            'InstanceId': 'i-high-traffic',
                            'VpcId': 'vpc-123',
                            'Placement': {'AvailabilityZone': 'us-east-1a'},
                            'InstanceType': 'm5.xlarge',
                        },
                    ]
                },
                {
                    'Instances': [
                        {
                            'InstanceId': 'i-other-az',
                            'VpcId': 'vpc-123',
                            'Placement': {'AvailabilityZone': 'us-east-1b'},
                            'InstanceType': 'm5.large',
                        },
                    ]
                },
            ]
        }

        # High network traffic for high-traffic instance
        # EC2 threshold is monthly_threshold_gb * 2, and it checks NetworkOut directly as bytes
        # Cost calculation: network_gb * cross_az_ratio * 0.5 * 0.01 * 2 must exceed $10
        # For 2 AZs: cross_az_ratio = 0.5, so need network_gb * 0.25 * 0.02 > 10
        # That means network_gb > 2000GB to exceed $10 threshold
        def get_metric_stats(**kwargs):
            if kwargs.get('MetricName') == 'NetworkOut':
                dimensions = kwargs.get('Dimensions', [])
                for dim in dimensions:
                    if dim.get('Value') == 'i-high-traffic':
                        # 5000GB (5TB) total over 30 days to exceed $10 cross-AZ cost
                        return {'Datapoints': [{'Sum': 5000 * 1024**3}]}
                    if dim.get('Value') == 'i-other-az':
                        # Low traffic
                        return {'Datapoints': [{'Sum': 1 * 1024**3}]}
            return {'Datapoints': [{'Sum': 0}]}

        mock_cloudwatch.get_metric_statistics.side_effect = get_metric_stats

        # Use lower threshold to catch 500GB traffic (threshold * 2 = 200GB for EC2)
        pattern = CrossAZTransferPattern(session=mock_session, monthly_threshold_gb=50.0)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        # Should find the high-traffic EC2 instance
        ec2_findings = [f for f in findings if f.resource_type == 'EC2 Instance (Cross-AZ)']
        assert len(ec2_findings) >= 1
        assert any(f.resource_id == 'i-high-traffic' for f in ec2_findings)

    def test_high_severity_for_expensive_cross_az(self):
        """
        GIVEN: A resource with >$100/month cross-AZ cost
        WHEN: The pattern scans
        THEN: Finding has HIGH risk_tier
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {
            'DBInstances': [{
                'DBInstanceIdentifier': 'expensive-db',
                'MultiAZ': True,
                'DBInstanceClass': 'db.r5.4xlarge',
                'Engine': 'oracle-ee',
                'AllocatedStorage': 2000,
            }]
        }

        # Very high write throughput - 10TB
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Sum': 10000 * 1024**3 / 30}
                for _ in range(30)
            ]
        }

        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': []
        }
        mock_ec2.describe_instances.return_value = {'Reservations': []}

        pattern = CrossAZTransferPattern(session=mock_session, monthly_threshold_gb=50.0)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].risk_tier == RiskTier.HIGH
        assert findings[0].monthly_impact_usd > 100

    def test_skips_low_traffic_resources(self):
        """
        GIVEN: Resources with traffic below threshold
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_ec2 = MagicMock()
        mock_elasticache = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'rds': mock_rds,
                'cloudwatch': mock_cloudwatch,
                'ec2': mock_ec2,
                'elasticache': mock_elasticache,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_rds.describe_db_instances.return_value = {
            'DBInstances': [{
                'DBInstanceIdentifier': 'low-traffic-db',
                'MultiAZ': True,
                'DBInstanceClass': 'db.t3.micro',
                'Engine': 'mysql',
            }]
        }

        # Very low traffic - 10GB total (WriteThroughput is bytes/sec, multiplied by 86400 per day)
        # So we need Sum to be small: 10GB / 30 days / 86400 seconds = ~3858 bytes/sec per datapoint
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Sum': 3858} for _ in range(30)]  # ~10GB total
        }

        mock_elasticache.describe_replication_groups.return_value = {
            'ReplicationGroups': []
        }
        mock_ec2.describe_instances.return_value = {'Reservations': []}

        pattern = CrossAZTransferPattern(session=mock_session, monthly_threshold_gb=100.0)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0
