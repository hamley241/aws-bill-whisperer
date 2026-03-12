"""
Tests for Pattern 003: GP2 to GP3 Migration
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock

import sys
sys.path.insert(0, 'src')

from patterns.p003_gp2_to_gp3 import GP2ToGP3Pattern
from patterns.base import Severity


class TestGP2ToGP3Pattern:
    """Tests for P003: GP2 to GP3 Migration"""

    def test_finds_gp2_volumes_for_migration(self):
        """
        GIVEN: An AWS account with gp2 volumes
        WHEN: The pattern scans for migration opportunities
        THEN: It returns findings with potential savings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        
        mock_paginator.paginate.return_value = [{
            'Volumes': [{
                'VolumeId': 'vol-gp2-test',
                'Size': 100,
                'VolumeType': 'gp2',
                'State': 'in-use',
                'Iops': 300,
                'Attachments': [{'InstanceId': 'i-123'}]
            }]
        }]
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'vol-gp2-test'
        assert findings[0].monthly_cost == 1.0  # 20% savings on 100GB * $0.10 * 0.5
        assert findings[0].safe_to_fix is True
        assert 'gp3' in findings[0].recommendation
        assert findings[0].metadata['current_type'] == 'gp2'
        assert findings[0].metadata['proposed_type'] == 'gp3'

    def test_no_finding_when_no_gp2_volumes(self):
        """
        GIVEN: An AWS account with no gp2 volumes
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{'Volumes': []}]
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_severity_based_on_volume_size(self):
        """
        GIVEN: gp2 volumes of different sizes
        WHEN: The pattern scans
        THEN: Larger volumes get higher severity ratings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        
        mock_paginator.paginate.return_value = [{
            'Volumes': [
                {'VolumeId': 'vol-small', 'Size': 30, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 100, 'Attachments': []},
                {'VolumeId': 'vol-medium', 'Size': 200, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 600, 'Attachments': []},
                {'VolumeId': 'vol-large', 'Size': 800, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 2400, 'Attachments': []},
            ]
        }]
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 3
        severities = {f.resource_id: f.severity for f in findings}
        assert severities['vol-small'] == Severity.LOW  # < 50GB
        assert severities['vol-medium'] == Severity.HIGH  # > 100GB
        assert severities['vol-large'] == Severity.CRITICAL  # > 500GB

    def test_skips_volumes_that_cannot_migrate(self):
        """
        GIVEN: gp2 volumes that exceed gp3 limits
        WHEN: The pattern scans
        THEN: It skips volumes that can't be migrated
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        
        mock_paginator.paginate.return_value = [{
            'Volumes': [
                # Too large (> 16TB)
                {'VolumeId': 'vol-too-large', 'Size': 17000, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 16000, 'Attachments': []},
                # Too many IOPS (> 16000)
                {'VolumeId': 'vol-too-many-iops', 'Size': 100, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 20000, 'Attachments': []},
                # Valid volume
                {'VolumeId': 'vol-valid', 'Size': 100, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 300, 'Attachments': []},
            ]
        }]
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'vol-valid'

    def test_calculates_correct_iops_for_gp3(self):
        """
        GIVEN: gp2 volumes with different IOPS
        WHEN: The pattern scans
        THEN: It calculates appropriate gp3 IOPS (min 3000, max 16000)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        
        mock_paginator.paginate.return_value = [{
            'Volumes': [
                {'VolumeId': 'vol-low-iops', 'Size': 50, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 150, 'Attachments': []},
                {'VolumeId': 'vol-high-iops', 'Size': 1000, 'VolumeType': 'gp2', 'State': 'available', 'Iops': 8000, 'Attachments': []},
            ]
        }]
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 2
        iops_mapping = {f.resource_id: f.metadata['proposed_iops'] for f in findings}
        assert iops_mapping['vol-low-iops'] == 3000  # gp3 minimum
        assert iops_mapping['vol-high-iops'] == 8000  # Keep current IOPS

    def test_fix_dry_run(self):
        """
        GIVEN: A gp2 to gp3 migration finding
        WHEN: The fix method is called with dry_run=True
        THEN: It returns success without making API calls
        """
        # GIVEN
        mock_session = MagicMock()
        pattern = GP2ToGP3Pattern(session=mock_session)
        
        from patterns.base import Finding
        finding = Finding(
            resource_id='vol-test',
            resource_type='EBS Volume',
            region='us-east-1',
            monthly_cost=1.0,
            recommendation='Test',
            severity=Severity.LOW,
            safe_to_fix=True,
            fix_command='test',
            metadata={'proposed_iops': 3000}
        )
        
        # WHEN
        result = pattern.fix(finding, dry_run=True)
        
        # THEN
        assert result is True
        mock_session.client.assert_not_called()

    def test_fix_actual_migration(self):
        """
        GIVEN: A gp2 to gp3 migration finding
        WHEN: The fix method is called with dry_run=False
        THEN: It modifies the volume to gp3 via AWS API
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        mock_ec2.modify_volume.return_value = {
            'VolumeModification': {'Status': 'modifying'}
        }
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        
        from patterns.base import Finding
        finding = Finding(
            resource_id='vol-migrate',
            resource_type='EBS Volume',
            region='us-west-2',
            monthly_cost=2.0,
            recommendation='Test',
            severity=Severity.MEDIUM,
            safe_to_fix=True,
            fix_command='test',
            metadata={'proposed_iops': 4000}
        )
        
        # WHEN
        result = pattern.fix(finding, dry_run=False)
        
        # THEN
        assert result is True
        mock_session.client.assert_called_with('ec2', region_name='us-west-2')
        mock_ec2.modify_volume.assert_called_with(
            VolumeId='vol-migrate',
            VolumeType='gp3',
            Iops=4000
        )

    def test_fix_raises_error_for_unsafe_finding(self):
        """
        GIVEN: A finding marked as not safe to fix
        WHEN: The fix method is called
        THEN: It raises a ValueError
        """
        # GIVEN
        mock_session = MagicMock()
        pattern = GP2ToGP3Pattern(session=mock_session)
        
        from patterns.base import Finding
        finding = Finding(
            resource_id='vol-unsafe',
            resource_type='EBS Volume',
            region='us-east-1',
            monthly_cost=1.0,
            recommendation='Test',
            severity=Severity.LOW,
            safe_to_fix=False,  # Not safe to fix
            fix_command='test'
        )
        
        # WHEN/THEN
        with pytest.raises(ValueError, match="not marked safe to fix"):
            pattern.fix(finding, dry_run=False)

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
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0  # Graceful handling of error

    def test_metadata_contains_all_required_fields(self):
        """
        GIVEN: A gp2 volume with various properties
        WHEN: The pattern scans
        THEN: The finding metadata contains all expected fields
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        
        mock_paginator.paginate.return_value = [{
            'Volumes': [{
                'VolumeId': 'vol-metadata-test',
                'Size': 250,
                'VolumeType': 'gp2',
                'State': 'in-use',
                'Iops': 750,
                'Attachments': [{'InstanceId': 'i-123'}, {'InstanceId': 'i-456'}]
            }]
        }]
        
        pattern = GP2ToGP3Pattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        metadata = findings[0].metadata
        
        expected_fields = [
            'size_gb', 'current_type', 'proposed_type', 'current_monthly_cost',
            'potential_savings', 'state', 'current_iops', 'proposed_iops', 'attachment_count'
        ]
        
        for field in expected_fields:
            assert field in metadata
        
        assert metadata['size_gb'] == 250
        assert metadata['current_type'] == 'gp2'
        assert metadata['proposed_type'] == 'gp3'
        assert metadata['state'] == 'in-use'
        assert metadata['current_iops'] == 750
        assert metadata['proposed_iops'] == 3000
        assert metadata['attachment_count'] == 2