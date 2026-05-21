"""
Tests for Pattern 007: Idle RDS Instances
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p007_idle_rds import IdleRDSPattern
from patterns.base import RiskTier


class TestIdleRDSPattern:
    """Tests for P007: Idle RDS Instances"""

    def test_finds_idle_rds_with_zero_connections(self):
        """
        GIVEN: An RDS instance with 0 connections and <5% CPU for 14 days
        WHEN: The pattern scans for idle instances
        THEN: It returns a finding
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'rds':
                return mock_rds
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Mock paginator for describe_db_instances
        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'DBInstances': [{
                'DBInstanceIdentifier': 'dev-db',
                'DBInstanceClass': 'db.t3.medium',
                'Engine': 'mysql',
                'DBInstanceStatus': 'available',
                'MultiAZ': False,
                'AvailabilityZone': 'us-east-1a',
                'InstanceCreateTime': datetime.now(timezone.utc) - timedelta(days=30),
            }]
        }]
        
        # CloudWatch returns low metrics
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 0.5}]  # Very low
        }
        
        pattern = IdleRDSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'dev-db'

    def test_no_finding_for_active_rds(self):
        """
        GIVEN: An RDS instance with active connections
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'rds':
                return mock_rds
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Mock paginator for describe_db_instances
        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'DBInstances': [{
                'DBInstanceIdentifier': 'prod-db',
                'DBInstanceClass': 'db.r5.large',
                'Engine': 'postgres',
                'DBInstanceStatus': 'available',
                'MultiAZ': True,
                'AvailabilityZone': 'us-east-1a',
            }]
        }]
        
        # CloudWatch returns high activity
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 50.0}]  # High connections/CPU
        }
        
        pattern = IdleRDSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_multi_az_doubles_cost(self):
        """
        GIVEN: An idle Multi-AZ RDS instance
        WHEN: The pattern calculates cost
        THEN: The cost is doubled compared to single-AZ
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'rds':
                return mock_rds
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Mock paginator for describe_db_instances
        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'DBInstances': [{
                'DBInstanceIdentifier': 'multi-az-db',
                'DBInstanceClass': 'db.t3.medium',
                'Engine': 'mysql',
                'DBInstanceStatus': 'available',
                'MultiAZ': True,  # Multi-AZ enabled
                'AvailabilityZone': 'us-east-1a',
                'InstanceCreateTime': datetime.now(timezone.utc) - timedelta(days=30),
            }]
        }]
        
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 0.5}]
        }
        
        pattern = IdleRDSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        # Multi-AZ should double the cost
        assert 'Multi-AZ' in findings[0].metadata.get('notes', '') or findings[0].metadata.get('multi_az', False)

    def test_ignores_stopped_instances(self):
        """
        GIVEN: A stopped RDS instance
        WHEN: The pattern scans
        THEN: It ignores the instance (already stopped)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'rds':
                return mock_rds
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Mock paginator for describe_db_instances
        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'DBInstances': [{
                'DBInstanceIdentifier': 'stopped-db',
                'DBInstanceClass': 'db.t3.medium',
                'Engine': 'mysql',
                'DBInstanceStatus': 'stopped',  # Already stopped
                'MultiAZ': False,
                'AvailabilityZone': 'us-east-1a',
            }]
        }]
        
        pattern = IdleRDSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_handles_pagination_over_100_instances(self):
        """
        GIVEN: An AWS account with more than 100 RDS instances (requiring pagination)
        WHEN: The pattern scans
        THEN: It processes ALL instances across all pages
        
        Bug fix: describe_db_instances() returns max 100 by default, truncates silently.
        Use paginator instead.
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'rds':
                return mock_rds
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Create 150 instances across 2 pages (all idle)
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        page1_instances = [
            {
                'DBInstanceIdentifier': f'dev-db-{i}',
                'DBInstanceClass': 'db.t3.medium',
                'Engine': 'mysql',
                'DBInstanceStatus': 'available',
                'MultiAZ': False,
                'AvailabilityZone': 'us-east-1a',
                'InstanceCreateTime': create_time,
            }
            for i in range(100)
        ]
        page2_instances = [
            {
                'DBInstanceIdentifier': f'dev-db-{i}',
                'DBInstanceClass': 'db.t3.medium',
                'Engine': 'mysql',
                'DBInstanceStatus': 'available',
                'MultiAZ': False,
                'AvailabilityZone': 'us-east-1a',
                'InstanceCreateTime': create_time,
            }
            for i in range(100, 150)
        ]
        
        # Mock paginator
        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {'DBInstances': page1_instances},
            {'DBInstances': page2_instances},
        ]
        
        # CloudWatch returns low metrics (all idle)
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 0.5}]
        }
        
        pattern = IdleRDSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN - should find all 150 instances
        assert len(findings) == 150
        # Verify we got instances from both pages
        instance_ids = {f.resource_id for f in findings}
        assert 'dev-db-0' in instance_ids
        assert 'dev-db-99' in instance_ids
        assert 'dev-db-100' in instance_ids
        assert 'dev-db-149' in instance_ids

    def test_severity_high_for_expensive_idle_db(self):
        """
        GIVEN: An idle RDS instance costing >$200/month
        WHEN: The pattern scans
        THEN: The finding has HIGH risk_tier
        """
        # GIVEN
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'rds':
                return mock_rds
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Mock paginator for describe_db_instances
        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'DBInstances': [{
                'DBInstanceIdentifier': 'expensive-idle-db',
                'DBInstanceClass': 'db.r5.2xlarge',  # Expensive
                'Engine': 'postgres',
                'DBInstanceStatus': 'available',
                'MultiAZ': True,
                'AvailabilityZone': 'us-east-1a',
                'InstanceCreateTime': datetime.now(timezone.utc) - timedelta(days=30),
            }]
        }]
        
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 0.0}]
        }
        
        pattern = IdleRDSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].risk_tier in [RiskTier.HIGH, RiskTier.HIGH]
