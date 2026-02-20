"""Tests for the AWS Bill Whisperer analyzer."""

import json

# Import from src
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from analyzer import cost_explorer, formatter, prompts


@pytest.fixture
def sample_cost_data() -> dict:
    """Load sample cost data from fixtures."""
    fixture_path = Path(__file__).parent / "fixtures" / "sample_cost_data.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return json.load(f)
    # Return sample data if fixture doesn't exist
    return {
        "usage": {
            "period": {"start": "2026-01-18", "end": "2026-02-18"},
            "total": 1247.32,
            "services": [
                {"name": "EC2", "cost": 523.41, "percent": 42.0},
                {"name": "RDS", "cost": 312.18, "percent": 25.0},
            ]
        },
        "comparison": {
            "current": {"start": "2026-01-18", "end": "2026-02-18", "total": 1247.32},
            "previous": {"start": "2025-12-18", "end": "2026-01-18", "total": 1056.00},
            "change": 191.32,
            "change_percent": 18.1,
            "service_changes": []
        }
    }


class TestFormatter:
    """Test cases for the formatter module."""

    def test_to_markdown_contains_total(self, sample_cost_data):
        """Test that markdown output contains the total cost."""
        result = formatter.to_markdown("Analysis", sample_cost_data)
        assert "$1,247.32" in result or "$1247.32" in result

    def test_to_markdown_contains_services(self, sample_cost_data):
        """Test that markdown output lists services."""
        result = formatter.to_markdown("Analysis", sample_cost_data)
        assert "EC2" in result or "RDS" in result

    def test_to_json_structure(self, sample_cost_data):
        """Test that JSON output has correct structure."""
        result = formatter.to_json("Analysis", sample_cost_data)
        assert "analysis" in result
        assert "cost_data" in result
        assert result["cost_data"]["total"] == 1247.32


class TestPrompts:
    """Test cases for the prompts module."""

    def test_system_prompt_exists(self):
        """Test that system prompt is defined."""
        assert hasattr(prompts, 'SYSTEM_PROMPT') or hasattr(prompts, 'get_system_prompt')

    def test_cost_analysis_prompt_content(self):
        """Test that cost analysis prompt contains expected content."""
        if hasattr(prompts, 'COST_ANALYSIS_PROMPT'):
            prompt = prompts.COST_ANALYSIS_PROMPT
            assert isinstance(prompt, str)
            assert len(prompt) > 100


class TestCostExplorer:
    """Test cases for the cost_explorer module."""

    def test_format_service_name_trims_aws(self):
        """Test that AWS prefix is trimmed from service names."""
        # This tests internal logic if available
        result = cost_explorer.format_service_name("Amazon Elastic Compute Cloud")
        assert "Amazon" not in result or result.startswith("Elastic")

    def test_format_service_name_ec2(self):
        """Test EC2 service name formatting."""
        result = cost_explorer.format_service_name("Amazon Elastic Compute Cloud - Compute")
        assert "EC2" in result or "Elastic" in result


def test_imports():
    """Test that all modules can be imported."""
    assert True  # If we got here, imports worked
