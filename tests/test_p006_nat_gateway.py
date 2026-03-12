"""
Tests for Pattern 006: NAT Gateway Optimization
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p006_nat_gateway import NatGatewayOptimizationPattern
from patterns.base import Severity


class TestNatGatewayOptimizationPattern:
    """Tests for P006: NAT Gateway Optimization"""

    def test_finds_nat_gateway_with_high_transfer(self):
        """
        GIVEN: A NAT Gateway with data transfer exceeding threshold
        WHEN: The pattern scans for optimization opportunities
        THEN: It returns a finding with cost breakdown
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=60)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [{
                'NatGatewayId': 'nat-12345',
                'SubnetId': 'subnet-abc123',
                'VpcId': 'vpc-xyz789',
                'CreateTime': create_time,
                'State': 'available'
            }]
        }
        
        # Mock high data transfer (300GB = 300 * 1024^3 bytes)
        transfer_bytes = 300 * (1024**3)
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Sum': transfer_bytes / 2},  # BytesOutToDestination
            ]
        }
        
        # Called twice (BytesOutToDestination + BytesOutToSource)
        mock_cw.get_metric_statistics.side_effect = [
            {'Datapoints': [{'Sum': transfer_bytes / 2}]},  # First call
            {'Datapoints': [{'Sum': transfer_bytes / 2}]}   # Second call
        ]
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'nat-12345'
        assert findings[0].resource_type == 'NAT Gateway'
        assert findings[0].safe_to_fix is False  # Manual intervention required
        assert findings[0].metadata['total_gb_transferred'] == 300.0
        assert 'VPC endpoints' in findings[0].recommendation

    def test_no_finding_when_transfer_below_threshold(self):
        """
        GIVEN: A NAT Gateway with low data transfer
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [{
                'NatGatewayId': 'nat-lowtraffic',
                'SubnetId': 'subnet-abc123',
                'VpcId': 'vpc-xyz789',
                'CreateTime': create_time,
            }]
        }
        
        # Low transfer (50GB)
        transfer_bytes = 25 * (1024**3)  # 25GB each direction
        mock_cw.get_metric_statistics.side_effect = [
            {'Datapoints': [{'Sum': transfer_bytes}]},
            {'Datapoints': [{'Sum': transfer_bytes}]}
        ]
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_severity_based_on_transfer_volume(self):
        """
        GIVEN: NAT Gateways with different data transfer volumes
        WHEN: The pattern scans
        THEN: Higher transfer volumes get higher severity ratings
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [
                {'NatGatewayId': 'nat-medium', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1', 'CreateTime': create_time, 'State': 'available'},
                {'NatGatewayId': 'nat-high', 'SubnetId': 'subnet-2', 'VpcId': 'vpc-2', 'CreateTime': create_time, 'State': 'available'},
                {'NatGatewayId': 'nat-veryhigh', 'SubnetId': 'subnet-3', 'VpcId': 'vpc-3', 'CreateTime': create_time, 'State': 'available'},
            ]
        }
        
        # Mock different transfer levels
        def mock_transfer_response(**kwargs):
            dimensions = kwargs.get('Dimensions', [])
            if dimensions:
                nat_gw_id = dimensions[0]['Value']
                if nat_gw_id == 'nat-medium':
                    return {'Datapoints': [{'Sum': 200 * (1024**3)}]}  # 200GB each call
                elif nat_gw_id == 'nat-high':
                    return {'Datapoints': [{'Sum': 400 * (1024**3)}]}  # 400GB each call
                else:  # nat-veryhigh
                    return {'Datapoints': [{'Sum': 800 * (1024**3)}]}  # 800GB each call
            return {'Datapoints': []}
        
        mock_cw.get_metric_statistics.side_effect = mock_transfer_response
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 3
        severities = {f.resource_id: f.severity for f in findings}
        
        # 400GB total (200 each direction) = LOW (< 500GB)
        assert severities['nat-medium'] == Severity.LOW
        # 800GB total = HIGH (> 500GB)  
        assert severities['nat-high'] == Severity.MEDIUM
        # 1600GB total = HIGH (> 1000GB)
        assert severities['nat-veryhigh'] == Severity.HIGH

    def test_calculates_vpc_endpoint_savings(self):
        """
        GIVEN: A NAT Gateway with high data transfer
        WHEN: The pattern calculates potential savings
        THEN: It estimates VPC endpoint savings correctly
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [{
                'NatGatewayId': 'nat-savings',
                'SubnetId': 'subnet-abc123',
                'VpcId': 'vpc-xyz789',
                'CreateTime': create_time,
            }]
        }
        
        # High transfer (500GB total)
        transfer_bytes = 250 * (1024**3)  # 250GB each direction
        mock_cw.get_metric_statistics.side_effect = [
            {'Datapoints': [{'Sum': transfer_bytes}]},
            {'Datapoints': [{'Sum': transfer_bytes}]}
        ]
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        # VPC endpoint savings should be 30% of 500GB * $0.045/GB
        expected_savings = 500 * 0.3 * 0.045
        assert findings[0].metadata['estimated_vpc_endpoint_savings'] == expected_savings

    def test_suggests_nat_instance_for_dev_environments(self):
        """
        GIVEN: A NAT Gateway in a subnet/VPC with 'dev' in the name
        WHEN: The pattern scans
        THEN: It suggests NAT instance as an alternative
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [{
                'NatGatewayId': 'nat-dev',
                'SubnetId': 'subnet-dev-123',  # 'dev' in subnet name
                'VpcId': 'vpc-production',
                'CreateTime': create_time,
            }]
        }
        
        transfer_bytes = 150 * (1024**3)  # 150GB each direction
        mock_cw.get_metric_statistics.side_effect = [
            {'Datapoints': [{'Sum': transfer_bytes}]},
            {'Datapoints': [{'Sum': transfer_bytes}]}
        ]
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert 'NAT instance' in findings[0].recommendation
        assert 'dev/test' in findings[0].recommendation

    def test_calculates_total_monthly_cost(self):
        """
        GIVEN: A NAT Gateway with data transfer
        WHEN: The pattern calculates costs
        THEN: It includes both base cost and transfer cost
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [{
                'NatGatewayId': 'nat-cost',
                'SubnetId': 'subnet-abc123',
                'VpcId': 'vpc-xyz789',
                'CreateTime': create_time,
            }]
        }
        
        # 200GB total transfer
        transfer_bytes = 100 * (1024**3)  # 100GB each direction
        mock_cw.get_metric_statistics.side_effect = [
            {'Datapoints': [{'Sum': transfer_bytes}]},
            {'Datapoints': [{'Sum': transfer_bytes}]}
        ]
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        
        # Base cost: $0.045/hour * 24 * 30 = $32.4
        expected_base = 0.045 * 24 * 30
        # Transfer cost: 200GB * $0.045 = $9.0
        expected_transfer = 200 * 0.045
        expected_total = expected_base + expected_transfer
        
        assert findings[0].metadata['monthly_base_cost'] == expected_base
        assert findings[0].metadata['monthly_transfer_cost'] == expected_transfer
        assert findings[0].monthly_cost == expected_total

    def test_tracks_nat_gateway_age(self):
        """
        GIVEN: NAT Gateways of different ages
        WHEN: The pattern scans
        THEN: It tracks age in days in metadata
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
        
        # Different ages
        old_create_time = datetime.now(timezone.utc) - timedelta(days=90)
        new_create_time = datetime.now(timezone.utc) - timedelta(days=10)
        
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [
                {'NatGatewayId': 'nat-old', 'SubnetId': 'subnet-1', 'VpcId': 'vpc-1', 'CreateTime': old_create_time},
                {'NatGatewayId': 'nat-new', 'SubnetId': 'subnet-2', 'VpcId': 'vpc-2', 'CreateTime': new_create_time},
            ]
        }
        
        transfer_bytes = 150 * (1024**3)  # Above threshold
        mock_cw.get_metric_statistics.side_effect = [
            {'Datapoints': [{'Sum': transfer_bytes}]},  # nat-old out to dest
            {'Datapoints': [{'Sum': transfer_bytes}]},  # nat-old out to source
            {'Datapoints': [{'Sum': transfer_bytes}]},  # nat-new out to dest
            {'Datapoints': [{'Sum': transfer_bytes}]}   # nat-new out to source
        ]
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 2
        ages = {f.resource_id: f.metadata['age_days'] for f in findings}
        
        assert ages['nat-old'] == 90
        assert ages['nat-new'] == 10

    def test_handles_missing_cloudwatch_data(self):
        """
        GIVEN: A NAT Gateway with no CloudWatch metrics
        WHEN: The pattern scans
        THEN: It treats transfer as 0 and skips if below threshold
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
        
        create_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_nat_gateways.return_value = {
            'NatGateways': [{
                'NatGatewayId': 'nat-no-metrics',
                'SubnetId': 'subnet-abc123',
                'VpcId': 'vpc-xyz789',
                'CreateTime': create_time,
            }]
        }
        
        # No CloudWatch data
        mock_cw.get_metric_statistics.return_value = {'Datapoints': []}
        
        pattern = NatGatewayOptimizationPattern(session=mock_session, monthly_transfer_threshold_gb=100)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0  # 0 GB transfer < 100 GB threshold

    def test_fix_raises_not_implemented(self):
        """
        GIVEN: A NAT Gateway optimization finding
        WHEN: The fix method is called
        THEN: It raises NotImplementedError (manual intervention required)
        """
        # GIVEN
        mock_session = MagicMock()
        pattern = NatGatewayOptimizationPattern(session=mock_session)
        
        from patterns.base import Finding
        finding = Finding(
            resource_id='nat-test',
            resource_type='NAT Gateway',
            region='us-east-1',
            monthly_cost=100.0,
            recommendation='Test',
            severity=Severity.MEDIUM,
            safe_to_fix=False,
            fix_command=None
        )
        
        # WHEN/THEN
        with pytest.raises(NotImplementedError, match="manual review and planning"):
            pattern.fix(finding)

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
        
        mock_ec2.describe_nat_gateways.side_effect = Exception('API Error')
        
        pattern = NatGatewayOptimizationPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0  # Graceful handling of error

    def test_filters_only_available_nat_gateways(self):
        """
        GIVEN: NAT Gateways in various states
        WHEN: The pattern scans
        THEN: It only analyzes NAT Gateways in 'available' state
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        # Verify the filter is applied correctly
        mock_ec2.describe_nat_gateways.return_value = {'NatGateways': []}
        
        pattern = NatGatewayOptimizationPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        pattern.scan()
        
        # THEN
        mock_ec2.describe_nat_gateways.assert_called_with(
            Filters=[{'Name': 'state', 'Values': ['available']}]
        )