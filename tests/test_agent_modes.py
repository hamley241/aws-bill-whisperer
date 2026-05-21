"""Tests for src/agent/modes.py — pattern-aware available modes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.modes import AvailableModesResolver
from patterns.base import Finding, RemediationMode, RiskTier


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="x",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        safe_to_fix=False,
        evidence={},
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestP001Resolver:
    def test_dry_run_and_command_always(self):
        modes = AvailableModesResolver().resolve(_finding(safe_to_fix=False))
        assert RemediationMode.DRY_RUN in modes
        assert RemediationMode.COMMAND in modes

    def test_pr_only_when_terraform_managed(self):
        no_tag = AvailableModesResolver().resolve(
            _finding(evidence={"terraform_managed": False}),
        )
        with_tag = AvailableModesResolver().resolve(
            _finding(evidence={"terraform_managed": True}),
        )
        assert RemediationMode.PR not in no_tag
        assert RemediationMode.PR in with_tag

    def test_api_call_only_when_safe_to_fix(self):
        unsafe = AvailableModesResolver().resolve(_finding(safe_to_fix=False))
        safe = AvailableModesResolver().resolve(_finding(safe_to_fix=True))
        assert RemediationMode.API_CALL not in unsafe
        assert RemediationMode.API_CALL in safe

    def test_full_set_when_tagged_and_safe(self):
        modes = AvailableModesResolver().resolve(_finding(
            safe_to_fix=True,
            evidence={"terraform_managed": True},
        ))
        assert modes == {
            RemediationMode.DRY_RUN, RemediationMode.COMMAND,
            RemediationMode.PR, RemediationMode.API_CALL,
        }


class TestUnknownPattern:
    def test_unknown_pattern_falls_back_to_universal(self):
        modes = AvailableModesResolver().resolve(_finding(pattern_id="042"))
        assert modes == {RemediationMode.DRY_RUN, RemediationMode.COMMAND}


class TestResolveValues:
    def test_returns_string_set(self):
        values = AvailableModesResolver().resolve_values(_finding())
        assert all(isinstance(v, str) for v in values)
        assert "dry_run" in values
        assert "command" in values


class TestCustomResolverInjection:
    def test_custom_resolver_overrides_default(self):
        custom = AvailableModesResolver(resolvers={
            "001": lambda f: {RemediationMode.PR},
        })
        modes = custom.resolve(_finding())
        assert modes == {RemediationMode.PR}
