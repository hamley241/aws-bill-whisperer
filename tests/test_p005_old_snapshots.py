"""
Tests for Pattern 005: Old EBS Snapshots
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p005_old_snapshots import OldSnapshotsPattern
from patterns.base import Severity


class TestOldSnapshotsPattern:
    """Tests for P005: Old EBS Snapshots"""

    def test_finds_snapshots_older_than_threshold(self):
        """
        GIVEN: An AWS account with a snapshot older than 90 days
        WHEN: The pattern scans for old snapshots
        THEN: It returns a finding for the old snapshot
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        mock_ec2.describe_snapshots.return_value = {
            'Snapshots': [{
                'SnapshotId': 'snap-old',
                'StartTime': old_date,
                'VolumeSize': 100,
                'Description': 'old backup',
                'VolumeId': 'vol-123',
            }]
        }
        mock_ec2.describe_images.return_value = {'Images': []}  # Not in any AMI
        
        pattern = OldSnapshotsPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'snap-old'
        assert findings[0].monthly_cost == 5.0  # 100GB * $0.05

    def test_ignores_recent_snapshots(self):
        """
        GIVEN: An AWS account with only recent snapshots (<90 days old)
        WHEN: The pattern scans for old snapshots
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        recent_date = datetime.now(timezone.utc) - timedelta(days=30)
        mock_ec2.describe_snapshots.return_value = {
            'Snapshots': [{
                'SnapshotId': 'snap-recent',
                'StartTime': recent_date,
                'VolumeSize': 100,
                'Description': 'recent backup',
                'VolumeId': 'vol-123',
            }]
        }
        
        pattern = OldSnapshotsPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 0

    def test_custom_threshold_days(self):
        """
        GIVEN: A custom threshold of 30 days
        WHEN: The pattern scans with a 45-day-old snapshot
        THEN: It returns a finding (45 > 30)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        mid_date = datetime.now(timezone.utc) - timedelta(days=45)
        mock_ec2.describe_snapshots.return_value = {
            'Snapshots': [{
                'SnapshotId': 'snap-mid',
                'StartTime': mid_date,
                'VolumeSize': 50,
                'Description': 'mid-age backup',
                'VolumeId': 'vol-123',
            }]
        }
        mock_ec2.describe_images.return_value = {'Images': []}
        
        pattern = OldSnapshotsPattern(session=mock_session, threshold_days=30)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1

    def test_unsafe_if_snapshot_used_by_ami(self):
        """
        GIVEN: An old snapshot that's used by an AMI
        WHEN: The pattern scans
        THEN: The finding is marked as NOT safe to fix
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        mock_ec2.describe_snapshots.return_value = {
            'Snapshots': [{
                'SnapshotId': 'snap-ami',
                'StartTime': old_date,
                'VolumeSize': 100,
                'Description': 'ami backup',
                'VolumeId': 'vol-123',
            }]
        }
        # Snapshot IS used by an AMI
        mock_ec2.describe_images.return_value = {
            'Images': [{'ImageId': 'ami-123', 'BlockDeviceMappings': [
                {'Ebs': {'SnapshotId': 'snap-ami'}}
            ]}]
        }
        
        pattern = OldSnapshotsPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 1
        assert findings[0].safe_to_fix is False

    def test_calculates_total_savings(self):
        """
        GIVEN: Multiple old snapshots totaling 500GB
        WHEN: The pattern scans
        THEN: Total monthly waste is $25 (500 * $0.05)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        
        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        mock_ec2.describe_snapshots.return_value = {
            'Snapshots': [
                {'SnapshotId': 'snap-1', 'StartTime': old_date, 'VolumeSize': 100, 'Description': '', 'VolumeId': 'vol-1'},
                {'SnapshotId': 'snap-2', 'StartTime': old_date, 'VolumeSize': 200, 'Description': '', 'VolumeId': 'vol-2'},
                {'SnapshotId': 'snap-3', 'StartTime': old_date, 'VolumeSize': 200, 'Description': '', 'VolumeId': 'vol-3'},
            ]
        }
        mock_ec2.describe_images.return_value = {'Images': []}
        
        pattern = OldSnapshotsPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']
        
        # WHEN
        findings = pattern.scan()
        
        # THEN
        assert len(findings) == 3
        assert pattern.total_monthly_waste == 25.0
