"""
Tests for the BasePattern contract — CLAUDE.md principle 1.

Pins down the metadata every pattern must declare (category, IAM,
regions) and the remediate(finding, mode) entry-point contract
(principle 4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns import discover_patterns
from patterns.base import (
    BasePattern,
    Category,
    Complexity,
    Finding,
    RemediationMode,
    RemediationResult,
    RiskTier,
)


PATTERNS = discover_patterns()


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="rsrc-1",
        resource_type="X",
        region="us-east-1",
        monthly_impact_usd=1.0,
        summary="test",
        pattern_id="000",
        risk_tier=RiskTier.MEDIUM,
        fix_command="aws --do-the-thing",
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestPatternMetadata:
    """Every pattern declares the metadata principle 1 promises."""

    @pytest.mark.parametrize("cls", PATTERNS, ids=lambda p: p.PATTERN_ID)
    def test_has_three_digit_id(self, cls: type[BasePattern]):
        assert isinstance(cls.PATTERN_ID, str)
        assert cls.PATTERN_ID.isdigit() and len(cls.PATTERN_ID) == 3

    @pytest.mark.parametrize("cls", PATTERNS, ids=lambda p: p.PATTERN_ID)
    def test_has_name_and_description(self, cls: type[BasePattern]):
        assert cls.NAME and cls.NAME != "Base Pattern"
        assert cls.DESCRIPTION and cls.DESCRIPTION != "Override this description"

    @pytest.mark.parametrize("cls", PATTERNS, ids=lambda p: p.PATTERN_ID)
    def test_category_is_set(self, cls: type[BasePattern]):
        assert isinstance(cls.CATEGORY, Category)
        # General is the BasePattern default — every concrete pattern overrides it.
        assert cls.CATEGORY != Category.GENERAL, \
            f"pattern {cls.PATTERN_ID} didn't override CATEGORY"

    @pytest.mark.parametrize("cls", PATTERNS, ids=lambda p: p.PATTERN_ID)
    def test_complexity_is_an_enum_value(self, cls: type[BasePattern]):
        assert isinstance(cls.COMPLEXITY, Complexity)

    @pytest.mark.parametrize("cls", PATTERNS, ids=lambda p: p.PATTERN_ID)
    def test_required_iam_is_non_empty(self, cls: type[BasePattern]):
        assert cls.REQUIRED_IAM, f"pattern {cls.PATTERN_ID} declares no required IAM actions"
        # Every entry looks like "service:Action".
        for action in cls.REQUIRED_IAM:
            assert ":" in action, f"{action!r} is not a service:Action string"


class TestPatternUniqueness:
    def test_pattern_ids_are_unique(self):
        ids = [cls.PATTERN_ID for cls in PATTERNS]
        assert len(set(ids)) == len(ids)

    def test_count_matches_repo_inventory(self):
        # We currently ship 20 patterns. If you add another, bump this AND
        # add the corresponding agentic spec.
        assert len(PATTERNS) == 20


class TestRemediateContract:
    """BasePattern.remediate() default behaviour — for any pattern without an override."""

    class _Stub(BasePattern):
        PATTERN_ID = "999"
        CATEGORY = Category.GENERAL
        REQUIRED_IAM = ["sts:GetCallerIdentity"]

        def scan(self, regions=None):
            return []

    def _pattern(self):
        return self._Stub(session=object())

    def test_dry_run_uses_fix_command(self):
        p = self._pattern()
        r = p.remediate(_finding(), RemediationMode.DRY_RUN)
        assert isinstance(r, RemediationResult)
        assert r.success
        assert r.mode == RemediationMode.DRY_RUN
        assert "would execute" in r.message
        assert r.output == "aws --do-the-thing"

    def test_command_returns_fix_command(self):
        p = self._pattern()
        r = p.remediate(_finding(), RemediationMode.COMMAND)
        assert r.success
        assert r.output == "aws --do-the-thing"

    def test_dry_run_without_fix_command_fails_softly(self):
        p = self._pattern()
        r = p.remediate(_finding(fix_command=None), RemediationMode.DRY_RUN)
        assert not r.success
        assert "no fix command" in r.message

    def test_pr_mode_unsupported_by_default(self):
        p = self._pattern()
        r = p.remediate(_finding(), RemediationMode.PR)
        assert not r.success
        assert "pr not supported" in r.message

    def test_api_call_unsupported_by_default(self):
        p = self._pattern()
        r = p.remediate(_finding(), RemediationMode.API_CALL)
        assert not r.success
        assert "api_call not supported" in r.message

    def test_unknown_mode_raises(self):
        p = self._pattern()
        with pytest.raises(ValueError):
            p.remediate(_finding(), "freestyle")  # type: ignore[arg-type]


class TestRemediationResultSchema:
    def test_to_dict_round_trip(self):
        r = RemediationResult(
            finding_id="f-1", pattern_id="001",
            mode=RemediationMode.DRY_RUN,
            success=True, message="ok", output="aws ...",
            evidence={"k": "v"},
        )
        d = r.to_dict()
        assert d["schema_version"] == "1"
        assert d["mode"] == "dry_run"
        assert d["finding_id"] == "f-1"
        assert d["pattern_id"] == "001"
        assert d["success"] is True
        assert d["evidence"] == {"k": "v"}

    def test_id_auto_generated(self):
        r1 = RemediationResult(
            finding_id="f", pattern_id="p", mode=RemediationMode.DRY_RUN,
            success=True, message="",
        )
        r2 = RemediationResult(
            finding_id="f", pattern_id="p", mode=RemediationMode.DRY_RUN,
            success=True, message="",
        )
        assert r1.id != r2.id
        assert len(r1.id) == 36  # UUID
