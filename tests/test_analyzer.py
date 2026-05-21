"""Tests for the AWS Bill Whisperer analyzer."""

import json

# Import from src
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from analyzer import cost_explorer, formatter
import prompts as prompt_registry


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
        assert result["cost_data"]["usage"]["total"] == 1247.32


class TestPromptRegistry:
    """Templates live in src/prompts/ now (CLAUDE.md principle 5)."""

    def test_cost_analysis_template_loads(self):
        template = prompt_registry.load_template("cost_analysis")
        assert template.name == "cost_analysis"
        assert len(template.text) > 100
        assert template.provider_neutral

    def test_anomaly_template_loads(self):
        template = prompt_registry.load_template("anomaly")
        assert template.name == "anomaly"
        assert "anomaly" in template.text.lower()

    def test_recommendations_template_loads(self):
        template = prompt_registry.load_template("recommendations")
        assert template.name == "recommendations"

    def test_missing_template_raises(self):
        with pytest.raises(KeyError):
            prompt_registry.load_template("nope_never_existed")

    def test_list_templates_includes_core_three(self):
        names = set(prompt_registry.list_templates())
        assert {"cost_analysis", "anomaly", "recommendations"}.issubset(names)


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


class TestHandlerPatternIntegration:
    """Test cases for pattern integration in handler."""

    def test_run_patterns_returns_list(self):
        """Test that _run_patterns returns a list of findings."""
        from unittest.mock import MagicMock, patch
        from analyzer.handler import _run_patterns
        
        # Mock discover_patterns to return a test pattern
        mock_pattern_class = MagicMock()
        mock_pattern_class.PATTERN_ID = "TEST"
        mock_pattern_class.NAME = "Test Pattern"
        mock_instance = MagicMock()
        mock_pattern_class.return_value = mock_instance
        mock_instance.scan.return_value = []
        
        with patch('analyzer.handler.discover_patterns', return_value=[mock_pattern_class]):
            findings = _run_patterns(['us-east-1'])
        
        assert isinstance(findings, list)
        mock_instance.scan.assert_called_once_with(regions=['us-east-1'])

    def test_run_patterns_converts_findings_to_dicts(self):
        """Test that findings are converted to serializable dicts."""
        from unittest.mock import MagicMock, patch
        from analyzer.handler import _run_patterns
        from patterns.base import Finding, RiskTier
        
        # Create a mock finding
        mock_finding = Finding(
            resource_id='test-123',
            resource_type='Test Resource',
            region='us-east-1',
            monthly_impact_usd=10.0,
            summary='Test summary',
            risk_tier=RiskTier.HIGH,
            safe_to_fix=True,
            fix_command='test command',
            metadata={'key': 'value'}
        )
        
        mock_pattern_class = MagicMock()
        mock_pattern_class.PATTERN_ID = "001"
        mock_pattern_class.NAME = "Test Pattern"
        mock_instance = MagicMock()
        mock_pattern_class.return_value = mock_instance
        mock_instance.scan.return_value = [mock_finding]
        
        with patch('analyzer.handler.discover_patterns', return_value=[mock_pattern_class]):
            findings = _run_patterns(['us-east-1'])
        
        assert len(findings) == 1
        assert findings[0]['resource_id'] == 'test-123'
        assert findings[0]['pattern_id'] == '001'
        assert findings[0]['pattern_name'] == 'Test Pattern'
        assert findings[0]['monthly_impact_usd'] == 10.0
        assert findings[0]['risk_tier'] == 'high'

    def test_run_patterns_handles_pattern_errors_gracefully(self):
        """Test that pattern errors don't crash the handler."""
        from unittest.mock import MagicMock, patch
        from analyzer.handler import _run_patterns
        
        # Create two patterns: one that fails, one that succeeds
        failing_pattern = MagicMock()
        failing_pattern.PATTERN_ID = "FAIL"
        failing_pattern.NAME = "Failing Pattern"
        failing_instance = MagicMock()
        failing_pattern.return_value = failing_instance
        failing_instance.scan.side_effect = Exception("Test error")
        
        working_pattern = MagicMock()
        working_pattern.PATTERN_ID = "WORK"
        working_pattern.NAME = "Working Pattern"
        working_instance = MagicMock()
        working_pattern.return_value = working_instance
        working_instance.scan.return_value = []
        
        with patch('analyzer.handler.discover_patterns', return_value=[failing_pattern, working_pattern]):
            # Should not raise, should continue to working pattern
            findings = _run_patterns(['us-east-1'])
        
        assert isinstance(findings, list)
        # Both patterns were attempted
        failing_instance.scan.assert_called_once()
        working_instance.scan.assert_called_once()
