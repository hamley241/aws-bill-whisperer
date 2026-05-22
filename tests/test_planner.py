"""
Tests for src/agent/planner.py — the orchestration layer.

A stub LLM lets us pin down the planner's behaviour without network or
parse ambiguity. The validators have their own deep tests in
test_validators.py; here we verify the planner wires findings →
prompt → parse → validate → PlanResult correctly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.planner import SavingsPlanner
from agent.schemas import DropReason
from llm import LLMClient
from llm.base import LLMResponse, Message
from patterns.base import Finding, RemediationMode, RiskTier
from storage import SqliteBackend, WhisperRepository


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        safe_to_fix=True,
        evidence={"terraform_managed": True, "size_gb": 100, "age_days": 30},
    )
    defaults.update(overrides)
    return Finding(**defaults)


class _StubLLM(LLMClient):
    """LLM that returns scripted responses. The script is consumed
    in order; out-of-script calls raise."""
    provider = "stub"
    boundary_crossed = False

    def __init__(self, *responses: str):
        self._script = list(responses)
        self.calls: list[list[Message]] = []

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        self.calls.append(messages)
        if not self._script:
            raise AssertionError("LLM called more times than the script supports")
        text = self._script.pop(0)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model="stub-model",
            boundary_crossed=False,
            input_tokens=10,
            output_tokens=20,
        )

    @property
    def default_model(self) -> str:
        return "stub-model"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_returns_ok_plan_with_one_step(self):
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "trim the volume.",
            "steps": [{
                "finding_id": f.id,
                "suggested_mode": "pr",
                "monthly_impact_usd": 42.5,
                "rationale": "tagged and old",
                "order_rank": 1,
            }],
        }))
        planner = SavingsPlanner(llm=llm)
        result = planner.plan([f], goal="cut 20%")

        assert result.status == "ok"
        assert len(result.steps) == 1
        assert result.steps[0].finding_id == f.id
        assert result.steps[0].monthly_impact_usd == 42.5
        assert result.dropped_steps == []
        assert result.parse_retry_count == 0
        assert result.total_monthly_impact_usd == 42.5
        assert result.summary == "trim the volume."
        assert result.goal == "cut 20%"
        assert result.prompt_template == "savings_plan"
        assert result.prompt_template_version == "v2"
        assert result.provider == "stub"
        assert result.boundary_crossed is False
        assert result.input_finding_ids == [f.id]

    def test_steps_sorted_by_order_rank(self):
        a = _finding(resource_id="a", monthly_impact_usd=10.0)
        b = _finding(resource_id="b", monthly_impact_usd=20.0)
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [
                {"finding_id": a.id, "suggested_mode": "pr",
                 "monthly_impact_usd": 10.0, "rationale": "r", "order_rank": 3},
                {"finding_id": b.id, "suggested_mode": "pr",
                 "monthly_impact_usd": 20.0, "rationale": "r", "order_rank": 1},
            ],
        }))
        planner = SavingsPlanner(llm=llm)
        result = planner.plan([a, b])
        # b should come first (lower order_rank), even though emitted second.
        assert [s.finding_id for s in result.steps] == [b.id, a.id]

    def test_confidence_grows_with_coverage(self):
        f1 = _finding(resource_id="f1", monthly_impact_usd=100.0)
        f2 = _finding(resource_id="f2", monthly_impact_usd=100.0)

        # Plan covers only f1
        llm_partial = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f1.id, "suggested_mode": "pr",
                "monthly_impact_usd": 100.0, "rationale": "r", "order_rank": 1,
            }],
        }))
        partial = SavingsPlanner(llm=llm_partial).plan([f1, f2])

        # Plan covers both
        llm_full = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [
                {"finding_id": f1.id, "suggested_mode": "pr",
                 "monthly_impact_usd": 100.0, "rationale": "r", "order_rank": 1},
                {"finding_id": f2.id, "suggested_mode": "pr",
                 "monthly_impact_usd": 100.0, "rationale": "r", "order_rank": 2},
            ],
        }))
        full = SavingsPlanner(llm=llm_full).plan([f1, f2])

        assert partial.confidence < full.confidence


# ---------------------------------------------------------------------------
# Drops surfaced in the plan
# ---------------------------------------------------------------------------

class TestPartialDrops:
    def test_some_steps_dropped_still_ok_status(self):
        # One valid step + one with a ghost finding_id.
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [
                {"finding_id": f.id, "suggested_mode": "pr",
                 "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1},
                {"finding_id": "ghost", "suggested_mode": "pr",
                 "monthly_impact_usd": 99.0, "rationale": "r", "order_rank": 2},
            ],
        }))
        result = SavingsPlanner(llm=llm).plan([f])
        assert result.status == "ok"
        assert len(result.steps) == 1
        assert len(result.dropped_steps) == 1
        assert result.dropped_steps[0].reason == DropReason.UNKNOWN_FINDING_ID.value

    def test_all_dropped_signals_validation_failed(self):
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [
                {"finding_id": "ghost-1", "suggested_mode": "pr",
                 "monthly_impact_usd": 1.0, "rationale": "r", "order_rank": 1},
            ],
        }))
        result = SavingsPlanner(llm=llm).plan([f])
        assert result.status == "validation_failed"
        assert result.steps == []
        assert len(result.dropped_steps) == 1
        assert result.total_monthly_impact_usd == 0.0


# ---------------------------------------------------------------------------
# Parse retry behaviour
# ---------------------------------------------------------------------------

class TestParseRetry:
    def test_parse_succeeds_first_try(self):
        f = _finding()
        good = json.dumps({"summary": "s", "steps": [{
            "finding_id": f.id, "suggested_mode": "pr",
            "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
        }]})
        llm = _StubLLM(good)
        result = SavingsPlanner(llm=llm).plan([f])
        assert result.parse_retry_count == 0
        assert len(llm.calls) == 1

    def test_parse_succeeds_on_retry(self):
        f = _finding()
        bad = "not even close to JSON"
        good = json.dumps({"summary": "s", "steps": [{
            "finding_id": f.id, "suggested_mode": "pr",
            "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
        }]})
        llm = _StubLLM(bad, good)
        result = SavingsPlanner(llm=llm).plan([f])
        assert result.parse_retry_count == 1
        assert result.status == "ok"
        assert len(llm.calls) == 2
        # The repair prompt is appended to the second call.
        retry_messages = llm.calls[1]
        assert any("JSON only" in m.content or "JSON object" in m.content
                   for m in retry_messages)

    def test_parse_fails_both_attempts(self):
        f = _finding()
        llm = _StubLLM("garbage", "still garbage")
        result = SavingsPlanner(llm=llm).plan([f])
        assert result.parse_retry_count == 1
        assert result.status == "validation_failed"
        assert result.steps == []
        assert len(result.dropped_steps) == 1
        assert result.dropped_steps[0].reason == DropReason.SCHEMA_INVALID.value
        assert "parser" in result.dropped_steps[0].validator


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_plan_written_when_repository_provided(self, tmp_path):
        f = _finding()
        repo = WhisperRepository(backend=SqliteBackend(db_path=tmp_path / "p.db"))
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        result = SavingsPlanner(llm=llm, repository=repo).plan(
            [f], scan_id="scan-X", actor="U-test",
        )
        rows = repo.list_plans(scan_id="scan-X")
        assert len(rows) == 1
        assert rows[0].id == result.plan_id
        assert rows[0].actor == "U-test"
        assert rows[0].status == "ok"
        assert rows[0].parse_retry_count == 0
        assert rows[0].prompt_template_version == "v2"

    def test_no_repository_means_no_write(self, tmp_path):
        # Just verify nothing blows up; we can't assert "no write"
        # without a real DB to inspect, but the planner should
        # complete cleanly when repository=None.
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        result = SavingsPlanner(llm=llm, repository=None).plan([f])
        assert result.status == "ok"

    def test_repository_failure_does_not_break_plan(self, tmp_path):
        from unittest.mock import MagicMock
        bad_repo = MagicMock()
        bad_repo.record_plan.side_effect = RuntimeError("disk full")
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        # Should not raise.
        result = SavingsPlanner(llm=llm, repository=bad_repo).plan([f])
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

class TestPromptRendering:
    def _capture_prompt(self, finding: Finding) -> str:
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": finding.id, "suggested_mode": "pr",
                "monthly_impact_usd": finding.monthly_impact_usd,
                "rationale": "r", "order_rank": 1,
            }],
        }))
        SavingsPlanner(llm=llm).plan([finding])
        return llm.calls[0][0].content

    def test_prompt_includes_finding_id(self):
        f = _finding()
        assert f.id in self._capture_prompt(f)

    def test_prompt_includes_canonical_dollar_value(self):
        f = _finding(monthly_impact_usd=137.42)
        assert "137.42" in self._capture_prompt(f)

    def test_prompt_includes_available_modes(self):
        f = _finding(safe_to_fix=True, evidence={"terraform_managed": True})
        prompt = self._capture_prompt(f)
        assert "api_call" in prompt
        assert "pr" in prompt
        assert "dry_run" in prompt
        assert "command" in prompt

    def test_prompt_omits_modes_pattern_does_not_expose(self):
        f = _finding(safe_to_fix=False, evidence={"terraform_managed": False})
        prompt = self._capture_prompt(f)
        # available_modes section should NOT list api_call or pr.
        # We look at the available_modes line specifically.
        modes_line = next(
            line for line in prompt.splitlines()
            if "available_modes" in line
        )
        assert "api_call" not in modes_line
        assert "pr" not in modes_line


# ---------------------------------------------------------------------------
# Goal handling
# ---------------------------------------------------------------------------

class TestGoalHandling:
    def test_goal_string_makes_it_into_the_prompt(self):
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        SavingsPlanner(llm=llm).plan([f], goal="cut 20% by next month")
        assert "cut 20% by next month" in llm.calls[0][0].content

    def test_no_goal_uses_default_phrasing(self):
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        SavingsPlanner(llm=llm).plan([f])
        from agent.planner import DEFAULT_GOAL
        assert DEFAULT_GOAL in llm.calls[0][0].content


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

class TestEmptyFindings:
    def test_empty_finding_list_returns_validation_failed(self):
        llm = _StubLLM(json.dumps({"summary": "nothing to do.", "steps": []}))
        result = SavingsPlanner(llm=llm).plan([])
        # Empty input → zero steps → validation_failed (the LLM correctly
        # planned nothing; the planner's status semantics treat zero
        # accepted steps as failed regardless of cause).
        assert result.status == "validation_failed"
        assert result.steps == []
        assert result.input_finding_ids == []


# ---------------------------------------------------------------------------
# Trace fields
# ---------------------------------------------------------------------------

class TestTraceFields:
    def test_all_trace_fields_populated(self):
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        result = SavingsPlanner(llm=llm).plan([f])
        d = result.to_dict()
        for field in ("plan_id", "schema_version", "goal", "status",
                      "prompt_template", "prompt_template_version",
                      "model", "provider", "boundary_crossed",
                      "parse_retry_count", "input_finding_ids",
                      "total_monthly_impact_usd", "confidence"):
            assert field in d, f"missing trace field {field}"

    def test_plan_id_is_uuid_shape(self):
        f = _finding()
        llm = _StubLLM(json.dumps({
            "summary": "s",
            "steps": [{
                "finding_id": f.id, "suggested_mode": "pr",
                "monthly_impact_usd": 42.5, "rationale": "r", "order_rank": 1,
            }],
        }))
        result = SavingsPlanner(llm=llm).plan([f])
        assert len(result.plan_id) == 36
        assert result.plan_id.count("-") == 4
