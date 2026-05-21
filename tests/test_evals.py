"""
Tests for src/agent/evals/ — the rubric vocabulary and the eval runner's
record/replay safety.

The runner's load-bearing behaviour: replay is the default, re-record
needs BOTH `--re-record` AND `WHISPER_ALLOW_REAL_LLM=1`. CI MUST NOT be
able to accidentally call the live LLM.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC))

from agent.evals.rubric import CheckResult, load_rubric, run_rubric
from agent.evals.runner import (
    FIXTURES_DIR,
    EvalResult,
    load_fixture,
    run_fixture,
)
from agent.schemas import DroppedStep, PlanResult, PlanStep, new_plan_id
from patterns.base import Finding, RiskTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-x",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=10.0,
        summary="s",
        pattern_id="001",
        risk_tier=RiskTier.MEDIUM,
        evidence={},
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _plan(
    *,
    status="ok",
    steps: list[PlanStep] | None = None,
    dropped: list[DroppedStep] | None = None,
    parse_retry_count: int = 0,
) -> PlanResult:
    steps = steps or []
    dropped = dropped or []
    return PlanResult(
        plan_id=new_plan_id(),
        goal=None,
        status=status,
        steps=steps,
        dropped_steps=dropped,
        total_monthly_impact_usd=sum(s.monthly_impact_usd for s in steps),
        summary="s",
        confidence=0.5,
        prompt_template="savings_plan",
        prompt_template_version="v1",
        model="test",
        provider="test",
        boundary_crossed=False,
        parse_retry_count=parse_retry_count,
        input_finding_ids=[],
    )


def _step(**overrides) -> PlanStep:
    defaults = dict(
        finding_id="f1",
        pattern_id="001",
        suggested_mode="dry_run",
        monthly_impact_usd=10.0,
        rationale="r",
        order_rank=1,
    )
    defaults.update(overrides)
    return PlanStep(**defaults)


# ---------------------------------------------------------------------------
# Rubric loading
# ---------------------------------------------------------------------------

class TestLoadRubric:
    def test_loads_a_list(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("- type: status\n  equals: ok\n", encoding="utf-8")
        out = load_rubric(p)
        assert out == [{"type": "status", "equals": "ok"}]

    def test_empty_file_is_empty_list(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("", encoding="utf-8")
        assert load_rubric(p) == []

    def test_non_list_raises(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("status: ok\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a list"):
            load_rubric(p)

    def test_missing_type_raises(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("- equals: ok\n", encoding="utf-8")
        with pytest.raises(ValueError, match="'type' key"):
            load_rubric(p)


# ---------------------------------------------------------------------------
# Per-assertion behaviour
# ---------------------------------------------------------------------------

class TestRubricAssertions:
    def test_structural_valid_json_pass(self):
        results = run_rubric(
            [{"type": "structural_valid_json"}],
            _plan(status="ok", steps=[_step()]), [],
        )
        assert results[0].ok

    def test_structural_valid_json_fails_on_no_steps(self):
        results = run_rubric(
            [{"type": "structural_valid_json"}],
            _plan(status="validation_failed", steps=[]), [],
        )
        assert not results[0].ok

    def test_status_equals_pass(self):
        r = run_rubric(
            [{"type": "status", "equals": "ok"}],
            _plan(status="ok"), [],
        )
        assert r[0].ok

    def test_status_equals_fail(self):
        r = run_rubric(
            [{"type": "status", "equals": "ok"}],
            _plan(status="validation_failed"), [],
        )
        assert not r[0].ok

    def test_dropped_steps_count_equals_zero(self):
        r = run_rubric([{"type": "dropped_steps_count", "equals": 0}],
                       _plan(dropped=[]), [])
        assert r[0].ok

    def test_dropped_steps_count_min_one_for_adversarial(self):
        dropped = [DroppedStep(raw_emission={}, reason="x", validator="v")]
        r = run_rubric([{"type": "dropped_steps_count", "min": 1}],
                       _plan(dropped=dropped), [])
        assert r[0].ok

    def test_dropped_steps_count_missing_operator(self):
        r = run_rubric([{"type": "dropped_steps_count"}], _plan(), [])
        assert not r[0].ok
        assert "operator" in r[0].detail

    def test_parse_retry_count_max_one(self):
        r = run_rubric([{"type": "parse_retry_count", "max": 1}],
                       _plan(parse_retry_count=1), [])
        assert r[0].ok

    def test_total_impact_within_input_sum_pass(self):
        f = _finding(monthly_impact_usd=100.0)
        steps = [_step(monthly_impact_usd=50.0)]
        r = run_rubric(
            [{"type": "total_impact_within_input_sum"}],
            _plan(steps=steps), [f],
        )
        assert r[0].ok

    def test_total_impact_within_input_sum_fail(self):
        f = _finding(monthly_impact_usd=10.0)
        steps = [_step(monthly_impact_usd=100.0)]
        r = run_rubric(
            [{"type": "total_impact_within_input_sum"}],
            _plan(steps=steps), [f],
        )
        assert not r[0].ok

    def test_includes_finding_pass(self):
        f = _finding(evidence={"terraform_managed": True})
        r = run_rubric(
            [{"type": "includes_finding",
              "finding_id_evidence": {"terraform_managed": True}}],
            _plan(steps=[_step(finding_id=f.id)]),
            [f],
        )
        assert r[0].ok

    def test_includes_finding_fail_when_skipped(self):
        f = _finding(evidence={"terraform_managed": True})
        r = run_rubric(
            [{"type": "includes_finding",
              "finding_id_evidence": {"terraform_managed": True}}],
            _plan(steps=[]),
            [f],
        )
        assert not r[0].ok

    def test_never_recommends_mode_pass(self):
        f = _finding(safe_to_fix=False, evidence={})
        r = run_rubric(
            [{"type": "never_recommends_mode",
              "mode": "api_call",
              "for_finding_evidence": {}}],
            _plan(steps=[_step(finding_id=f.id, suggested_mode="dry_run")]),
            [f],
        )
        assert r[0].ok

    def test_never_recommends_mode_catches_violation(self):
        # We construct a plan that includes api_call on a finding we said
        # shouldn't get one. Validators would normally drop this, but the
        # rubric is the second line of defence.
        f = _finding(safe_to_fix=False, evidence={})
        r = run_rubric(
            [{"type": "never_recommends_mode",
              "mode": "api_call",
              "for_finding_evidence": {}}],
            _plan(steps=[_step(finding_id=f.id, suggested_mode="api_call")]),
            [f],
        )
        assert not r[0].ok

    def test_order_rank_unique_pass(self):
        r = run_rubric([{"type": "order_rank_unique"}],
                       _plan(steps=[_step(order_rank=1), _step(finding_id="f2", order_rank=2)]),
                       [])
        assert r[0].ok

    def test_order_rank_unique_fail(self):
        r = run_rubric([{"type": "order_rank_unique"}],
                       _plan(steps=[_step(order_rank=1), _step(finding_id="f2", order_rank=1)]),
                       [])
        assert not r[0].ok

    def test_dropped_reason_present(self):
        dropped = [DroppedStep(raw_emission={}, reason="unknown_finding_id", validator="v")]
        r = run_rubric([{"type": "dropped_reason_present",
                         "reason": "unknown_finding_id"}],
                       _plan(dropped=dropped), [])
        assert r[0].ok

    def test_unknown_assertion_type_fails(self):
        r = run_rubric([{"type": "definitely_not_a_real_check"}], _plan(), [])
        assert not r[0].ok
        assert "unknown" in r[0].detail


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

class TestLoadFixture:
    def test_loads_p001_only(self):
        fx = load_fixture("p001_only")
        assert fx.name == "p001_only"
        assert len(fx.findings) >= 1
        assert any(f.evidence.get("terraform_managed") for f in fx.findings)
        assert fx.recorded_responses, "fixture must ship a recorded response"
        assert fx.goal is not None

    def test_missing_fixture_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_fixture(tmp_path / "nope")


# ---------------------------------------------------------------------------
# Replay (the default path — green path of the harness)
# ---------------------------------------------------------------------------

class TestReplay:
    def test_p001_only_full_pass(self, monkeypatch):
        # Ensure WHISPER_ALLOW_REAL_LLM is unset so a misconfigured test
        # can't accidentally re-record.
        monkeypatch.delenv("WHISPER_ALLOW_REAL_LLM", raising=False)
        result = run_fixture("p001_only")
        assert isinstance(result, EvalResult)
        assert result.ok, [c for c in result.checks if not c.ok]
        assert result.plan.status == "ok"
        assert result.plan.dropped_steps == []

    def test_replay_never_writes_recording(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALLOW_REAL_LLM", raising=False)
        path = FIXTURES_DIR / "p001_only" / "recorded_response.json"
        before = path.read_bytes()
        run_fixture("p001_only")
        assert path.read_bytes() == before, \
            "replay must not modify recorded_response.json"


# ---------------------------------------------------------------------------
# Re-record protection (CI must not accidentally call live LLM)
# ---------------------------------------------------------------------------

class TestReRecordSafety:
    def test_rerecord_refuses_without_env_flag(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALLOW_REAL_LLM", raising=False)
        result = run_fixture("p001_only", re_record=True)
        assert not result.ok
        assert any("WHISPER_ALLOW_REAL_LLM" in err for err in result.errors)
        # Crucially: no LLM client was constructed, no network call attempted.

    def test_rerecord_with_env_flag_uses_provided_live_llm(
        self, monkeypatch, tmp_path
    ):
        # Build an isolated fixture in tmp_path so we don't mutate the
        # real recorded_response.json on disk.
        fix_dir = tmp_path / "tmp_fixture"
        fix_dir.mkdir()
        (fix_dir / "findings.json").write_text(json.dumps([{
            "id": "test-id", "schema_version": "1", "pattern_id": "001",
            "resource_id": "vol-x", "resource_type": "EBS Volume",
            "resource_arn": None, "account_id": None, "region": "us-east-1",
            "monthly_impact_usd": 10.0, "confidence": 0.5,
            "risk_tier": "low", "summary": "s", "explanation": None,
            "fix_command": "cmd", "fix_pr": None,
            "evidence": {}, "metadata": {}, "safe_to_fix": False,
        }]), encoding="utf-8")
        (fix_dir / "assertions.yaml").write_text(
            "- type: status\n  equals: ok\n", encoding="utf-8"
        )

        # A scripted "live" LLM we control — never network.
        from llm.base import LLMClient, LLMResponse

        class _Scripted(LLMClient):
            provider = "scripted-live"
            boundary_crossed = False
            def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
                return LLMResponse(
                    text=json.dumps({
                        "summary": "tiny plan",
                        "steps": [{
                            "finding_id": "test-id",
                            "suggested_mode": "dry_run",
                            "monthly_impact_usd": 10.0,
                            "rationale": "ok",
                            "order_rank": 1,
                        }],
                    }),
                    provider="scripted-live",
                    model="m",
                    boundary_crossed=False,
                )
            @property
            def default_model(self): return "m"

        monkeypatch.setenv("WHISPER_ALLOW_REAL_LLM", "1")
        result = run_fixture(fix_dir, re_record=True, live_llm=_Scripted())
        assert result.rerecorded
        assert (fix_dir / "recorded_response.json").exists()
        body = json.loads((fix_dir / "recorded_response.json").read_text())
        assert len(body["responses"]) == 1
        assert body["metadata"]["provider"] == "scripted-live"
