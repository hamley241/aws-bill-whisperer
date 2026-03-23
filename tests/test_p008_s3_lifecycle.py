"""
Tests for Pattern 008: S3 Lifecycle Rules
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from botocore.exceptions import ClientError

import sys
sys.path.insert(0, 'src')

from patterns.p008_s3_lifecycle import S3LifecyclePattern
from patterns.base import Severity


class TestS3LifecyclePattern:
    """Tests for P008: S3 Lifecycle Rules"""

    def test_finds_bucket_without_lifecycle(self):
        """
        GIVEN: An S3 bucket without lifecycle rules and sufficient size
        WHEN: The pattern scans for buckets without lifecycle policies
        THEN: It returns a finding with cost estimate
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        create_date = datetime.now(timezone.utc) - timedelta(days=90)
        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'my-data-bucket', 'CreationDate': create_date}]
        }

        # Raise NoSuchLifecycleConfiguration error
        error_response = {'Error': {'Code': 'NoSuchLifecycleConfiguration'}}
        mock_s3.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, 'GetBucketLifecycleConfiguration'
        )

        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Return 50GB bucket size
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 50 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'my-data-bucket'
        assert findings[0].region == 'us-west-2'
        assert findings[0].metadata['bucket_type'] == 'general'
        assert findings[0].metadata['bucket_size_gb'] == 50.0

    def test_skips_bucket_with_lifecycle(self):
        """
        GIVEN: An S3 bucket with existing lifecycle rules
        WHEN: The pattern scans
        THEN: It skips the bucket (no finding)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'bucket-with-lifecycle', 'CreationDate': datetime.now(timezone.utc)}]
        }

        # Has lifecycle rules
        mock_s3.get_bucket_lifecycle_configuration.return_value = {
            'Rules': [{'ID': 'rule1', 'Status': 'Enabled'}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0

    def test_skips_small_bucket(self):
        """
        GIVEN: An S3 bucket smaller than the threshold
        WHEN: The pattern scans
        THEN: It skips the bucket (no finding)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'small-bucket', 'CreationDate': datetime.now(timezone.utc)}]
        }

        error_response = {'Error': {'Code': 'NoSuchLifecycleConfiguration'}}
        mock_s3.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, 'GetBucketLifecycleConfiguration'
        )

        # Return 5GB bucket size (below default 10GB threshold)
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 5 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session, min_bucket_size_gb=10.0)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0

    def test_classifies_log_bucket(self):
        """
        GIVEN: A bucket with 'logs' in the name
        WHEN: The pattern scans
        THEN: It classifies as logs and recommends Glacier + expiration
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'app-access-logs', 'CreationDate': datetime.now(timezone.utc)}]
        }

        error_response = {'Error': {'Code': 'NoSuchLifecycleConfiguration'}}
        mock_s3.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, 'GetBucketLifecycleConfiguration'
        )
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': None}

        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 100 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].metadata['bucket_type'] == 'logs'
        assert 'Glacier' in findings[0].recommendation
        assert 'delete after 365 days' in findings[0].recommendation

    def test_classifies_backup_bucket(self):
        """
        GIVEN: A bucket with 'backup' in the name
        WHEN: The pattern scans
        THEN: It classifies as backup and recommends Glacier Instant Retrieval
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'prod-db-backup', 'CreationDate': datetime.now(timezone.utc)}]
        }

        error_response = {'Error': {'Code': 'NoSuchLifecycleConfiguration'}}
        mock_s3.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, 'GetBucketLifecycleConfiguration'
        )
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-east-2'}

        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 200 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].metadata['bucket_type'] == 'backup'
        assert 'Glacier Instant Retrieval' in findings[0].recommendation

    def test_classifies_temp_bucket(self):
        """
        GIVEN: A bucket with 'temp' in the name
        WHEN: The pattern scans
        THEN: It classifies as temporary and recommends expiration
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'staging-temp-files', 'CreationDate': datetime.now(timezone.utc)}]
        }

        error_response = {'Error': {'Code': 'NoSuchLifecycleConfiguration'}}
        mock_s3.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, 'GetBucketLifecycleConfiguration'
        )
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-1'}

        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 50 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].metadata['bucket_type'] == 'temporary'
        assert 'expire objects' in findings[0].recommendation

    def test_high_severity_for_large_potential_savings(self):
        """
        GIVEN: A bucket with potential savings > $100/month
        WHEN: The pattern scans
        THEN: Finding has HIGH severity
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'huge-backup-bucket', 'CreationDate': datetime.now(timezone.utc)}]
        }

        error_response = {'Error': {'Code': 'NoSuchLifecycleConfiguration'}}
        mock_s3.get_bucket_lifecycle_configuration.side_effect = ClientError(
            error_response, 'GetBucketLifecycleConfiguration'
        )
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': None}

        # 10TB bucket - high savings potential
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 10000 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].metadata['potential_savings'] > 100

    def test_handles_access_denied(self):
        """
        GIVEN: A bucket where we don't have permission to check lifecycle
        WHEN: The pattern scans
        THEN: It handles the error gracefully and continues
        """
        # GIVEN
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_session.client.side_effect = lambda service, **kwargs: {
            's3': mock_s3,
            'cloudwatch': mock_cloudwatch,
        }[service]

        mock_s3.list_buckets.return_value = {
            'Buckets': [
                {'Name': 'no-access-bucket', 'CreationDate': datetime.now(timezone.utc)},
                {'Name': 'accessible-bucket', 'CreationDate': datetime.now(timezone.utc)},
            ]
        }

        def lifecycle_side_effect(Bucket):
            if Bucket == 'no-access-bucket':
                raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetBucketLifecycleConfiguration')
            raise ClientError({'Error': {'Code': 'NoSuchLifecycleConfiguration'}}, 'GetBucketLifecycleConfiguration')

        mock_s3.get_bucket_lifecycle_configuration.side_effect = lifecycle_side_effect
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': None}

        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Average': 50 * 1024**3}]
        }

        pattern = S3LifecyclePattern(session=mock_session)

        # WHEN
        findings = pattern.scan()

        # THEN
        # Should only find the accessible bucket
        assert len(findings) == 1
        assert findings[0].resource_id == 'accessible-bucket'
