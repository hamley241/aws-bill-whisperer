"""
Shared pytest fixtures for Bill Whisperer tests.
"""
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime, timedelta, timezone


@pytest.fixture
def mock_session():
    """Mock boto3 session with client factory."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_ec2_client():
    """Mock EC2 client."""
    return MagicMock()


@pytest.fixture
def mock_cloudwatch_client():
    """Mock CloudWatch client."""
    return MagicMock()


@pytest.fixture
def mock_rds_client():
    """Mock RDS client."""
    return MagicMock()


@pytest.fixture
def old_date():
    """Date 100 days ago (older than default 90-day threshold)."""
    return datetime.now(timezone.utc) - timedelta(days=100)


@pytest.fixture
def recent_date():
    """Date 30 days ago (within default thresholds)."""
    return datetime.now(timezone.utc) - timedelta(days=30)


@pytest.fixture
def two_weeks_ago():
    """Date 14 days ago (for CloudWatch metrics)."""
    return datetime.now(timezone.utc) - timedelta(days=14)
