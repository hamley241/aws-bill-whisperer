"""
Tests for Pattern 001: Unattached EBS Volumes
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p001_unattached_ebs import UnattachedEBSPattern
from patterns.base import Severity


class TestUnattachedEBSPattern:
    """Tests for P001: Unattached EBS Volumes"""

    def test_finds_unattached_volume(self):
        """
        GIVEN: An AWS account with an unattached EBS volume
        WHEN: The pattern scans for unattached volumes
        THEN: It returns a finding with cost estimate
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        create_time = datetime.now(timezone.utc) - timedelta(days=10)
        mock_ec2.describe_volumes.return_value = {
            'Volumes': [{
                'VolumeId': 'vol-123',
                'Size': 100,
                'VolumeType': 'gp2',
                'CreateTime': create_time,
            }]
        }
        mock_ec2.describe_snapshots.return_value = {'Snapshots': [{'SnapshotId': 'snap-1'}]}
        
        pattern = UnattachedEBSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'vol-123'
        assert findings[0].monthly_cost == 10.0  # 100GB * $0.10
        assert findings[0].safe_to_fix is True  # Has snapshot and >7 days old

    def test_no_finding_when_all_volumes_attached(self):
        """
        GIVEN: An AWS account with only attached volumes
        WHEN: The pattern scans for unattached volumes
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        mock_ec2.describe_volumes.return_value = {'Volumes': []}
        
        pattern = UnattachedEBSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_severity_high_for_old_expensive_volume(self):
        """
        GIVEN: An unattached volume older than 30 days costing >$50/month
        WHEN: The pattern scans
        THEN: The finding has HIGH severity
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        create_time = datetime.now(timezone.utc) - timedelta(days=60)
        mock_ec2.describe_volumes.return_value = {
            'Volumes': [{
                'VolumeId': 'vol-expensive',
                'Size': 1000,  # $100/month for gp2
                'VolumeType': 'gp2',
                'CreateTime': create_time,
            }]
        }
        mock_ec2.describe_snapshots.return_value = {'Snapshots': []}
        
        pattern = UnattachedEBSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].monthly_cost == 100.0

    def test_unsafe_to_delete_without_snapshot(self):
        """
        GIVEN: An unattached volume with no snapshot
        WHEN: The pattern scans
        THEN: The finding is marked as NOT safe to fix
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        create_time = datetime.now(timezone.utc) - timedelta(days=10)
        mock_ec2.describe_volumes.return_value = {
            'Volumes': [{
                'VolumeId': 'vol-nosnap',
                'Size': 50,
                'VolumeType': 'gp3',
                'CreateTime': create_time,
            }]
        }
        mock_ec2.describe_snapshots.return_value = {'Snapshots': []}  # No snapshots
        
        pattern = UnattachedEBSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].safe_to_fix is False

    def test_calculates_different_volume_types(self):
        """
        GIVEN: Unattached volumes of different types (gp2, gp3, io1)
        WHEN: The pattern scans
        THEN: Each has correct pricing applied
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        create_time = datetime.now(timezone.utc) - timedelta(days=10)
        mock_ec2.describe_volumes.return_value = {
            'Volumes': [
                {'VolumeId': 'vol-gp2', 'Size': 100, 'VolumeType': 'gp2', 'CreateTime': create_time},
                {'VolumeId': 'vol-gp3', 'Size': 100, 'VolumeType': 'gp3', 'CreateTime': create_time},
                {'VolumeId': 'vol-io1', 'Size': 100, 'VolumeType': 'io1', 'CreateTime': create_time},
            ]
        }
        mock_ec2.describe_snapshots.return_value = {'Snapshots': []}
        
        pattern = UnattachedEBSPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 3
        costs = {f.resource_id: f.monthly_cost for f in findings}
        assert costs['vol-gp2'] == 10.0   # $0.10/GB
        assert costs['vol-gp3'] == 8.0    # $0.08/GB
        assert costs['vol-io1'] == 12.5   # $0.125/GB
