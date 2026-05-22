"""
Tests for src/schemas/ and src/storage/ — the persistence layer.

Per CLAUDE.md principle 8: we define the schema, the customer holds the
bytes. Every persisted record carries schema_version and migrators
apply forward when older rows are read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns.base import (
    Finding,
    RemediationMode,
    RemediationResult,
    RiskTier,
)
from schemas import (
    CURRENT_SCHEMA_VERSION,
    FindingRecord,
    RemediationRecord,
)
from schemas.records import migrate, register_migrator
from storage import SqliteBackend, WhisperRepository


@pytest.fixture
def repo(tmp_path):
    return WhisperRepository(backend=SqliteBackend(db_path=tmp_path / "w.db"))


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.9,
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
        safe_to_fix=True,
        evidence={"size_gb": 100, "age_days": 47},
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestFindingPersistence:
    def test_round_trip(self, repo: WhisperRepository):
        f = _finding()
        repo.record_finding(f, scan_id="scan-1")
        out = repo.list_findings(scan_id="scan-1")
        assert len(out) == 1
        assert out[0].id == f.id
        assert out[0].resource_id == "vol-abc"
        assert out[0].risk_tier == "high"
        assert out[0].evidence == {"size_gb": 100, "age_days": 47}
        assert out[0].schema_version == CURRENT_SCHEMA_VERSION

    def test_scan_id_grouping(self, repo: WhisperRepository):
        repo.record_finding(_finding(resource_id="a"), scan_id="scan-A")
        repo.record_finding(_finding(resource_id="b"), scan_id="scan-A")
        repo.record_finding(_finding(resource_id="c"), scan_id="scan-B")
        a_findings = repo.list_findings(scan_id="scan-A")
        b_findings = repo.list_findings(scan_id="scan-B")
        assert {f.resource_id for f in a_findings} == {"a", "b"}
        assert {f.resource_id for f in b_findings} == {"c"}

    def test_bulk_record_scan(self, repo: WhisperRepository):
        findings = [_finding(resource_id=f"vol-{i}") for i in range(3)]
        repo.record_scan(findings, scan_id="scan-bulk")
        assert len(repo.list_findings(scan_id="scan-bulk")) == 3


class TestRemediationAuditLog:
    def test_record_and_list(self, repo: WhisperRepository):
        result = RemediationResult(
            finding_id="finding-1",
            pattern_id="001",
            mode=RemediationMode.API_CALL,
            success=True,
            message="deleted volume",
            output=None,
            evidence={"region": "us-east-1"},
        )
        repo.record_remediation(result, actor="U-slack-user")
        rows = repo.list_remediations()
        assert len(rows) == 1
        assert rows[0].mode == "api_call"
        assert rows[0].success is True
        assert rows[0].actor == "U-slack-user"
        assert rows[0].evidence == {"region": "us-east-1"}
        assert rows[0].schema_version == CURRENT_SCHEMA_VERSION

    def test_filter_by_finding_id(self, repo: WhisperRepository):
        repo.record_remediation(RemediationResult(
            finding_id="A", pattern_id="001", mode=RemediationMode.DRY_RUN,
            success=True, message="ok",
        ))
        repo.record_remediation(RemediationResult(
            finding_id="B", pattern_id="002", mode=RemediationMode.API_CALL,
            success=False, message="boom",
        ))
        only_a = repo.list_remediations(finding_id="A")
        assert len(only_a) == 1
        assert only_a[0].pattern_id == "001"

    def test_distinct_record_ids(self, repo: WhisperRepository):
        for _ in range(3):
            repo.record_remediation(RemediationResult(
                finding_id="F", pattern_id="P", mode=RemediationMode.DRY_RUN,
                success=True, message="ok",
            ))
        rows = repo.list_remediations(finding_id="F")
        assert len({r.id for r in rows}) == 3  # unique audit IDs


class TestPlanPersistence:
    def _record(self, **overrides) -> "PlanRecord":  # noqa: F821
        from schemas import PlanRecord
        import uuid
        defaults = dict(
            id=str(uuid.uuid4()),
            scan_id="scan-x",
            goal="cut 20%",
            status="ok",
            steps_json='[{"finding_id":"f1","order_rank":1}]',
            dropped_steps_json="[]",
            total_monthly_impact_usd=42.5,
            summary="trim EBS first.",
            confidence=0.7,
            prompt_template="savings_plan",
            prompt_template_version="v1",
            model="stub-model",
            provider="stub",
            boundary_crossed=False,
            parse_retry_count=0,
            input_finding_ids_json='["f1"]',
            actor="U-test",
        )
        defaults.update(overrides)
        return PlanRecord(**defaults)

    def test_round_trip(self, repo: WhisperRepository):
        rec = self._record()
        repo.record_plan(rec)
        out = repo.get_plan(rec.id)
        assert out is not None
        assert out.id == rec.id
        assert out.status == "ok"
        assert out.prompt_template_version == "v1"
        assert out.parse_retry_count == 0
        assert out.boundary_crossed is False
        assert out.schema_version == CURRENT_SCHEMA_VERSION

    def test_list_by_scan_id(self, repo: WhisperRepository):
        repo.record_plan(self._record(scan_id="A"))
        repo.record_plan(self._record(scan_id="A"))
        repo.record_plan(self._record(scan_id="B"))
        a = repo.list_plans(scan_id="A")
        b = repo.list_plans(scan_id="B")
        assert len(a) == 2
        assert len(b) == 1

    def test_unknown_id_returns_none(self, repo: WhisperRepository):
        assert repo.get_plan("does-not-exist") is None

    def test_validation_failed_status_persists(self, repo: WhisperRepository):
        rec = self._record(status="validation_failed",
                           steps_json="[]",
                           total_monthly_impact_usd=0.0)
        repo.record_plan(rec)
        out = repo.get_plan(rec.id)
        assert out.status == "validation_failed"


class TestPromptTemplateVersion:
    def test_default_is_v1(self):
        from prompts import PromptTemplate
        t = PromptTemplate(name="x", text="hi", description="d")
        assert t.version == "v1"

    def test_existing_templates_have_v1(self):
        # As templates evolve they're allowed to bump version; this test
        # only catches *accidental* version loss. Intentional bumps are
        # listed here so a reader can see the audit trail at a glance.
        from prompts import list_templates, load_template
        expected_versions = {
            "savings_plan": "v2",  # p006 — added recommended_sequence
        }
        for name in list_templates():
            want = expected_versions.get(name, "v1")
            assert load_template(name).version == want, \
                f"template {name} expected {want!r}, " \
                f"got {load_template(name).version!r}"


class TestPromptPersistence:
    def test_record_and_list(self, repo: WhisperRepository):
        repo.record_prompt(
            provider="bedrock",
            model="claude-sonnet",
            boundary_crossed=False,
            prompt_template="cost_analysis",
            messages=[{"role": "user", "content": "hello"}],
            response_text="hi",
            input_tokens=12,
            output_tokens=34,
        )
        rows = repo.list_prompts()
        assert len(rows) == 1
        assert rows[0].provider == "bedrock"
        assert rows[0].boundary_crossed is False
        assert rows[0].input_tokens == 12

    def test_boundary_crossed_flag(self, repo: WhisperRepository):
        repo.record_prompt(
            provider="openai", model="gpt-4o", boundary_crossed=True,
            prompt_template=None,
            messages=[{"role": "user", "content": "x"}],
            response_text="y",
        )
        rows = repo.list_prompts()
        assert rows[0].boundary_crossed is True


class TestMigrationFramework:
    def test_current_version_short_circuits(self):
        raw = {"schema_version": CURRENT_SCHEMA_VERSION, "id": "x"}
        assert migrate("finding", raw) == raw

    def test_missing_migrator_raises(self):
        with pytest.raises(ValueError, match="no migrator"):
            migrate("finding", {"schema_version": "999"})

    def test_registered_migrator_applies(self):
        # Pretend we shipped a v0 → v1 migrator for a record type called "demo".
        # The fixture name "demo" avoids colliding with real types.
        @register_migrator("demo", "0", CURRENT_SCHEMA_VERSION)
        def _v0_to_v1(raw):
            raw["renamed_field"] = raw.pop("old_field")
            return raw

        out = migrate("demo", {"schema_version": "0", "old_field": "v"})
        assert out["schema_version"] == CURRENT_SCHEMA_VERSION
        assert out["renamed_field"] == "v"


class TestDefaultRepository:
    def test_set_and_reset(self, tmp_path):
        from storage import default_repository, set_default_repository
        custom = WhisperRepository(backend=SqliteBackend(db_path=tmp_path / "x.db"))
        set_default_repository(custom)
        assert default_repository() is custom
        set_default_repository(None)
        # Next access creates a fresh real one; check it isn't the same instance.
        assert default_repository() is not custom


class TestSchemaPersistence:
    def test_findings_and_remediations_tables_exist(self, tmp_path):
        backend = SqliteBackend(db_path=tmp_path / "schema.db")
        # Insert + read works → tables exist with the right shape.
        backend.insert_finding({
            "id": "f1", "schema_version": "1", "scan_id": "s",
            "pattern_id": "001", "resource_id": "r", "resource_type": "X",
            "resource_arn": None, "account_id": None, "region": "us-east-1",
            "monthly_impact_usd": 1.0, "risk_tier": "low", "confidence": 0.5,
            "summary": "s", "explanation": None, "fix_command": None,
            "fix_pr": None, "safe_to_fix": False,
            "evidence": {}, "metadata": {}, "observed_at": "2026-05-20T00:00:00",
        })
        assert backend.list_findings()[0]["resource_id"] == "r"
