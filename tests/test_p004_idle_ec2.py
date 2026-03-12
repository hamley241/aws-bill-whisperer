"""
Tests for Pattern 004: Idle EC2 Instances
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p004_idle_ec2 import IdleEC2Pattern
from patterns.base import Severity


class TestIdleEC2Pattern:
    """Tests for P004: Idle EC2 Instances"""

    def test_finds_idle_ec2_instance(self):
        """
        GIVEN: An EC2 instance with <5% CPU utilization over 14 days
        WHEN: The pattern scans for idle instances
        THEN: It returns a finding
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [{
                    'InstanceId': 'i-idle123',
                    'InstanceType': 't3.medium',
                    'Platform': 'Linux/UNIX',
                    'LaunchTime': launch_time,
                    'Tags': [{'Key': 'Name', 'Value': 'test-server'}]
                }]
            }]
        }]
        
        # CloudWatch returns low CPU
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Average': 2.0},
                {'Average': 1.5},
                {'Average': 3.0}
            ]
        }
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'i-idle123'
        assert findings[0].resource_type == 'EC2 Instance'
        assert findings[0].safe_to_fix is False  # Manual intervention required
        assert 't3.medium' in findings[0].recommendation
        assert 'test-server' in findings[0].recommendation

    def test_no_finding_for_active_instance(self):
        """
        GIVEN: An EC2 instance with >5% CPU utilization
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [{
                    'InstanceId': 'i-active123',
                    'InstanceType': 't3.medium',
                    'LaunchTime': launch_time,
                    'Tags': []
                }]
            }]
        }]
        
        # CloudWatch returns high CPU
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Average': 25.0},
                {'Average': 30.0},
                {'Average': 20.0}
            ]
        }
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_skips_recently_launched_instances(self):
        """
        GIVEN: An EC2 instance launched less than 14 days ago
        WHEN: The pattern scans
        THEN: It skips the instance to avoid false positives
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Recent launch time
        launch_time = datetime.now(timezone.utc) - timedelta(days=5)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [{
                    'InstanceId': 'i-new123',
                    'InstanceType': 't3.medium',
                    'LaunchTime': launch_time,
                    'Tags': []
                }]
            }]
        }]
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_severity_based_on_cpu_and_cost(self):
        """
        GIVEN: Idle instances with different CPU levels and costs
        WHEN: The pattern scans
        THEN: Severity is assigned based on CPU and monthly cost
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        # Launch time must be old enough (>14 days) to be considered
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [
                    # Very idle, expensive instance (should be CRITICAL)
                    {'InstanceId': 'i-critical', 'InstanceType': 'm5.2xlarge', 'Tags': []},
                    # Moderately idle instance (should be HIGH)
                    {'InstanceId': 'i-high', 'InstanceType': 't3.large', 'Tags': []},
                    # Less idle instance (should be MEDIUM)
                    {'InstanceId': 'i-medium', 'InstanceType': 't3.small', 'Tags': []},
                ]
            }]
        }]
        
        # Mock different CPU levels
        def mock_cpu_response(namespace, metric_name, dimensions, **kwargs):
            instance_id = dimensions[0]['Value']
            if instance_id == 'i-critical':
                return {'Datapoints': [{'Average': 0.5}]}  # <1% CPU
            elif instance_id == 'i-high':
                return {'Datapoints': [{'Average': 1.5}]}  # <2% CPU
            else:
                return {'Datapoints': [{'Average': 4.0}]}  # <5% CPU
        
        mock_cw.get_metric_statistics.side_effect = mock_cpu_response
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 3
        severities = {f.resource_id: f.severity for f in findings}
        
        # Very idle + expensive should be critical/high
        assert severities['i-critical'] in [Severity.CRITICAL, Severity.HIGH]
        # Other instances should have appropriate severities
        assert severities['i-high'] == Severity.HIGH
        assert severities['i-medium'] == Severity.MEDIUM

    def test_handles_missing_cloudwatch_data(self):
        """
        GIVEN: An EC2 instance without CloudWatch metrics
        WHEN: The pattern scans
        THEN: It skips the instance gracefully
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [{
                    'InstanceId': 'i-no-metrics',
                    'InstanceType': 't3.medium',
                    'LaunchTime': launch_time,
                    'Tags': []
                }]
            }]
        }]
        
        # No CloudWatch data
        mock_cw.get_metric_statistics.return_value = {'Datapoints': []}
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_detects_purpose_tags(self):
        """
        GIVEN: EC2 instances with and without purpose tags
        WHEN: The pattern scans
        THEN: It tracks whether instances have purpose tags in metadata
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [
                    {
                        'InstanceId': 'i-with-purpose',
                        'InstanceType': 't3.small',
                        'LaunchTime': launch_time,
                        'Tags': [{'Key': 'purpose', 'Value': 'web-server'}]
                    },
                    {
                        'InstanceId': 'i-no-purpose',
                        'InstanceType': 't3.small',
                        'LaunchTime': launch_time,
                        'Tags': [{'Key': 'Owner', 'Value': 'team-a'}]
                    }
                ]
            }]
        }]
        
        # Both instances are idle
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 2.0}]
        }
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 2
        
        purpose_status = {f.resource_id: f.metadata['has_purpose_tag'] for f in findings}
        assert purpose_status['i-with-purpose'] is True
        assert purpose_status['i-no-purpose'] is False

    def test_handles_windows_instances(self):
        """
        GIVEN: A Windows EC2 instance with low CPU
        WHEN: The pattern scans
        THEN: It detects the instance and notes Windows platform
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [{
                    'InstanceId': 'i-windows',
                    'InstanceType': 't3.medium',
                    'Platform': 'windows',  # Windows instance
                    'LaunchTime': launch_time,
                    'Tags': []
                }]
            }]
        }]
        
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 3.0}]
        }
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].metadata['platform'] == 'windows'

    def test_monthly_cost_calculation(self):
        """
        GIVEN: EC2 instances of different types
        WHEN: The pattern calculates costs
        THEN: It uses appropriate pricing for each instance type
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_cw = MagicMock()
        
        def mock_client(service, **kwargs):
            if service == 'ec2':
                return mock_ec2
            return mock_cw
        
        mock_session.client.side_effect = mock_client
        
        launch_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{
            'Reservations': [{
                'Instances': [
                    {'InstanceId': 'i-t3-micro', 'InstanceType': 't3.micro', 'LaunchTime': launch_time, 'Tags': []},
                    {'InstanceId': 'i-m5-large', 'InstanceType': 'm5.large', 'LaunchTime': launch_time, 'Tags': []},
                ]
            }]
        }]
        
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 2.0}]
        }
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 2
        
        # t3.micro should be much cheaper than m5.large
        costs = {f.resource_id: f.monthly_cost for f in findings}
        assert costs['i-t3-micro'] < costs['i-m5-large']
        assert costs['i-t3-micro'] == 0.0104  # t3.micro hourly cost
        assert costs['i-m5-large'] == 0.096  # m5.large hourly cost

    def test_handles_api_error_gracefully(self):
        """
        GIVEN: AWS API throws an error during scan
        WHEN: The pattern scans
        THEN: It continues and returns empty findings for that region
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.get_paginator.side_effect = Exception('API Error')
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0  # Graceful handling of error

    def test_no_findings_when_no_running_instances(self):
        """
        GIVEN: An AWS account with no running instances
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{'Reservations': []}]
        
        pattern = IdleEC2Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0