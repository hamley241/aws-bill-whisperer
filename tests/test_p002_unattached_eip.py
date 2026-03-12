"""
Tests for Pattern 002: Unattached Elastic IPs
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock

import sys
sys.path.insert(0, 'src')

from patterns.p002_unattached_eip import UnattachedEIPPattern
from patterns.base import Severity


class TestUnattachedEIPPattern:
    """Tests for P002: Unattached Elastic IPs"""

    def test_finds_unattached_eip(self):
        """
        GIVEN: An AWS account with an unattached Elastic IP
        WHEN: The pattern scans for unattached EIPs
        THEN: It returns a finding with cost estimate
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.describe_addresses.return_value = {
            'Addresses': [{
                'AllocationId': 'eipalloc-123',
                'PublicIp': '1.2.3.4',
                'Domain': 'vpc',
                # No InstanceId or NetworkInterfaceId = unattached
            }]
        }
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'eipalloc-123'
        assert abs(findings[0].monthly_cost - 3.6) < 0.01  # $0.005/hour * 24 * 30
        assert findings[0].severity == Severity.LOW
        assert findings[0].safe_to_fix is True
        assert '1.2.3.4' in findings[0].recommendation

    def test_no_finding_when_eip_attached_to_instance(self):
        """
        GIVEN: An AWS account with EIPs attached to instances
        WHEN: The pattern scans for unattached EIPs
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.describe_addresses.return_value = {
            'Addresses': [{
                'AllocationId': 'eipalloc-attached',
                'PublicIp': '1.2.3.5',
                'Domain': 'vpc',
                'InstanceId': 'i-123456',  # Attached to instance
            }]
        }
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_no_finding_when_eip_attached_to_network_interface(self):
        """
        GIVEN: An AWS account with EIPs attached to network interfaces
        WHEN: The pattern scans for unattached EIPs
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.describe_addresses.return_value = {
            'Addresses': [{
                'AllocationId': 'eipalloc-eni',
                'PublicIp': '1.2.3.6',
                'Domain': 'vpc',
                'NetworkInterfaceId': 'eni-123456',  # Attached to ENI
            }]
        }
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_handles_classic_domain_eips(self):
        """
        GIVEN: An unattached EC2-Classic EIP (no AllocationId)
        WHEN: The pattern scans
        THEN: It uses PublicIp as resource_id
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.describe_addresses.return_value = {
            'Addresses': [{
                'PublicIp': '2.3.4.5',
                'Domain': 'standard',  # EC2-Classic
                # No AllocationId for classic EIPs
            }]
        }
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == '2.3.4.5'
        assert findings[0].metadata['domain'] == 'standard'

    def test_multiple_unattached_eips(self):
        """
        GIVEN: Multiple unattached EIPs in the account
        WHEN: The pattern scans
        THEN: It finds all unattached EIPs
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.describe_addresses.return_value = {
            'Addresses': [
                {'AllocationId': 'eipalloc-1', 'PublicIp': '1.1.1.1', 'Domain': 'vpc'},
                {'AllocationId': 'eipalloc-2', 'PublicIp': '2.2.2.2', 'Domain': 'vpc'},
                {'AllocationId': 'eipalloc-attached', 'PublicIp': '3.3.3.3', 'Domain': 'vpc', 'InstanceId': 'i-123'},
            ]
        }
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 2
        resource_ids = {f.resource_id for f in findings}
        assert resource_ids == {'eipalloc-1', 'eipalloc-2'}

    def test_fix_dry_run(self):
        """
        GIVEN: An unattached EIP finding
        WHEN: The fix method is called with dry_run=True
        THEN: It returns success without making API calls
        """
        # GIVEN
        mock_session = MagicMock()
        pattern = UnattachedEIPPattern(session=mock_session)
        
        from patterns.base import Finding
        finding = Finding(
            resource_id='eipalloc-test',
            resource_type='Elastic IP',
            region='us-east-1',
            monthly_cost=3.6,
            recommendation='Test',
            severity=Severity.LOW,
            safe_to_fix=True,
            fix_command='test'
        )
        
        # WHEN
        result = pattern.fix(finding, dry_run=True)
        
        # THEN
        assert result is True
        mock_session.client.assert_not_called()

    def test_fix_actual_release(self):
        """
        GIVEN: An unattached EIP finding
        WHEN: The fix method is called with dry_run=False
        THEN: It releases the EIP via AWS API
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        pattern = UnattachedEIPPattern(session=mock_session)
        
        from patterns.base import Finding
        finding = Finding(
            resource_id='eipalloc-release',
            resource_type='Elastic IP',
            region='us-west-2',
            monthly_cost=3.6,
            recommendation='Test',
            severity=Severity.LOW,
            safe_to_fix=True,
            fix_command='test'
        )
        
        # WHEN
        result = pattern.fix(finding, dry_run=False)
        
        # THEN
        assert result is True
        mock_session.client.assert_called_with('ec2', region_name='us-west-2')
        mock_ec2.release_address.assert_called_with(AllocationId='eipalloc-release')

    def test_handles_empty_addresses_list(self):
        """
        GIVEN: An AWS account with no Elastic IPs
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_ec2.describe_addresses.return_value = {'Addresses': []}
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

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
        
        mock_ec2.describe_addresses.side_effect = Exception('API Error')
        
        pattern = UnattachedEIPPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0  # Graceful handling of error