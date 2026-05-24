"""
Tests for the `/whisper plan` slash command.

Covers:
  - Dispatch from /whisper plan (case-insensitive subcommand).
  - Goal extraction via `goal:` prefix.
  - Parent + threaded message flow (parity with scan).
  - Scan / planner failures land in-thread, not ephemeral.
  - Thread-store handoff so the Open-PR button keeps working.
  - Failure path renders the clean Slack failure message (no provider
    internals).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent.schemas import DropReason, DroppedStep, PlanResult, PlanStep
from config import WhisperConfig
from patterns.base import Finding, RiskTier
from presenters import ScanResult
from slack.handlers import plan as plan_handler
from slack.handlers import scan as scan_handler
from slack.handlers.plan import (
    PLAN_FAILURE_FALLBACK,
    PLAN_STARTED_TEXT,
    set_background_runner as set_plan_background_runner,
    set_planner_factory,
    set_scan_runner as set_plan_scan_runner,
)
from slack.handlers.scan import (
    USAGE_TEXT,
    register,
    set_background_runner as set_scan_background_runner,
    set_explainer,
)
from slack.thread_store import get_store


def _valid_config(**overrides) -> WhisperConfig:
    defaults = dict(
        slack_bot_token="xoxb-test-bot-token",
        slack_signing_secret="test-signing-secret",
    )
    defaults.update(overrides)
    return WhisperConfig(**defaults)


def _safe_p001_finding() -> Finding:
    return Finding(
        id="10000000-0001-4000-8000-000000000aaa",
        resource_id="vol-tf",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=80.0,
        summary="Delete 1000GB gp2 vol-tf (terraform-managed)",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.9,
        safe_to_fix=True,
        evidence={"terraform_managed": True},
    )


def _sample_scan_result() -> ScanResult:
    return ScanResult.from_findings([_safe_p001_finding()])


def _ok_plan(findings: list[Finding]) -> PlanResult:
    f = findings[0]
    step = PlanStep(
        finding_id=f.id, pattern_id=f.pattern_id, suggested_mode="pr",
        monthly_impact_usd=f.monthly_impact_usd,
        rationale="terraform-managed; land deletion through IaC review.",
        order_rank=1,
    )
    return PlanResult(
        plan_id="aaaaaaaa-1111-4000-8000-bbbbbbbbbbbb",
        goal=None,
        status="ok",
        steps=[step],
        dropped_steps=[],
        total_monthly_impact_usd=f.monthly_impact_usd,
        summary="One step.",
        confidence=0.8,
        prompt_template="savings_plan",
        prompt_template_version="v2",
        model="test-model",
        provider="test",
        boundary_crossed=False,
        parse_retry_count=0,
        input_finding_ids=[f.id],
    )


def _failed_plan(findings: list[Finding]) -> PlanResult:
    return PlanResult(
        plan_id="ffffffff-1111-4000-8000-cccccccccccc",
        goal=None,
        status="validation_failed",
        steps=[],
        dropped_steps=[DroppedStep(
            raw_emission={"raw_response": "garbage that must not leak"},
            reason=DropReason.SCHEMA_INVALID.value,
            validator="parser",
            detail="parse failure",
        )],
        total_monthly_impact_usd=0.0,
        summary="The model did not return a parseable JSON plan.",
        confidence=0.0,
        prompt_template="savings_plan",
        prompt_template_version="v2",
        model="claude-test",
        provider="bedrock",
        boundary_crossed=False,
        parse_retry_count=1,
        input_finding_ids=[],
    )


class _StubApp:
    def __init__(self, config=None):
        self._whisper_config = config or _valid_config()
        self.commands: dict = {}
        self.actions: dict = {}
        self.events: dict = {}

    def command(self, name):
        def deco(fn):
            self.commands[name] = fn
            return fn
        return deco

    def action(self, name):
        def deco(fn):
            self.actions[name] = fn
            return fn
        return deco

    def event(self, name):
        def deco(fn):
            self.events[name] = fn
            return fn
        return deco


class _StubPlanner:
    """Tiny stand-in for SavingsPlanner whose .plan() returns a canned PlanResult."""

    def __init__(self, plan_result: PlanResult):
        self._plan = plan_result
        self.calls: list[dict] = []

    def plan(self, findings, *, goal=None, scan_id=None, actor=None):
        self.calls.append({
            "findings": findings, "goal": goal,
            "scan_id": scan_id, "actor": actor,
        })
        return self._plan


@pytest.fixture(autouse=True)
def _isolate_handler_globals():
    """Reset module-level injection points between tests."""
    set_explainer(lambda findings, **kwargs: None)
    set_scan_background_runner(lambda fn: fn())  # inline
    set_plan_background_runner(lambda fn: fn())  # inline
    get_store().clear()
    yield
    set_scan_background_runner(lambda fn: fn())
    set_plan_background_runner(lambda fn: fn())
    set_planner_factory(None)
    get_store().clear()


def _invoke_plan(text: str, *, scan_runner=None, planner=None,
                 parent_ts: str = "1700000000.001"):
    """Drive the dispatcher with `/whisper {text}`."""
    if scan_runner is None:
        scan_runner = lambda config=None: _sample_scan_result()
    set_plan_scan_runner(scan_runner)

    if planner is None:
        # Default OK planner over a synthetic safe finding. We do NOT
        # invoke the supplied scan_runner here because some tests pass
        # a runner that intentionally raises.
        planner = _StubPlanner(_ok_plan([_safe_p001_finding()]))
    set_planner_factory(lambda config: planner)

    ack = MagicMock()
    respond = MagicMock()
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": parent_ts, "ok": True}
    logger = MagicMock()
    command = {
        "text": text,
        "user_id": "U123",
        "channel_id": "C456",
        "team_id": "T789",
    }
    stub = _StubApp()
    register(stub)
    stub.commands["/whisper"](
        ack=ack, respond=respond, command=command,
        client=client, logger=logger,
    )
    return ack, respond, client, logger, planner


class TestUsageMentionsPlan:
    def test_usage_text_mentions_plan_subcommand(self):
        # The help/empty path must mention the new subcommand so users
        # can discover it without reading docs.
        assert "/whisper plan" in USAGE_TEXT
        assert "goal:" in USAGE_TEXT


class TestPlanDispatch:

    def test_acknowledges_immediately(self):
        ack, _, _, _, _ = _invoke_plan("plan")
        ack.assert_called_once()

    def test_plan_posts_parent_then_threaded_blocks(self):
        _, _, client, _, _ = _invoke_plan("plan")
        assert client.chat_postMessage.call_count == 2

        parent_call = client.chat_postMessage.call_args_list[0].kwargs
        assert parent_call["channel"] == "C456"
        assert parent_call["text"] == PLAN_STARTED_TEXT
        assert "thread_ts" not in parent_call

        plan_call = client.chat_postMessage.call_args_list[1].kwargs
        assert plan_call["channel"] == "C456"
        assert plan_call["thread_ts"] == "1700000000.001"
        assert "blocks" in plan_call
        # Fallback text mentions step count + canonical total.
        assert "$80.00" in plan_call["text"]

    def test_plan_blocks_include_finding_and_pr_button(self):
        _, _, client, _, _ = _invoke_plan("plan")
        blocks = client.chat_postMessage.call_args_list[1].kwargs["blocks"]
        text_blob = json.dumps(blocks)
        assert "vol-tf" in text_blob
        assert "[pr]" in text_blob
        # PR-only button — Open-PR action_id, value=finding_id.
        action_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert len(action_blocks) == 1
        elem = action_blocks[0]["elements"][0]
        assert elem["action_id"] == "open_pr"
        assert elem["value"] == "10000000-0001-4000-8000-000000000aaa"

    def test_plan_case_insensitive_subcommand(self):
        _, _, client, _, _ = _invoke_plan("PLAN")
        # Parent post = PLAN_STARTED_TEXT (not SCAN_STARTED_TEXT).
        assert client.chat_postMessage.call_args_list[0].kwargs["text"] \
            == PLAN_STARTED_TEXT

    def test_plan_goal_extracted_and_passed_to_planner(self):
        _, _, _, _, planner = _invoke_plan("plan goal: cut NAT cost")
        assert len(planner.calls) == 1
        assert planner.calls[0]["goal"] == "cut NAT cost"
        # Actor passed through as the Slack user id.
        assert planner.calls[0]["actor"] == "U123"

    def test_plan_without_goal_passes_none(self):
        _, _, _, _, planner = _invoke_plan("plan")
        assert planner.calls[0]["goal"] is None

    def test_plan_empty_goal_passes_none(self):
        _, _, _, _, planner = _invoke_plan("plan goal:   ")
        assert planner.calls[0]["goal"] is None

    def test_thread_context_stored_for_open_pr_button(self):
        _invoke_plan("plan goal: foo", parent_ts="ts-plan-42")
        # The Open-PR action handler looks up findings via ScanResult.
        stored = get_store().get("ts-plan-42")
        assert stored is not None
        assert stored.findings[0].resource_id == "vol-tf"


class TestPlanFailureSurfaces:

    def test_scan_failure_posts_in_thread(self):
        def boom(config=None):
            raise RuntimeError("AWS exploded")

        _, _, client, _, _ = _invoke_plan("plan", scan_runner=boom)
        assert client.chat_postMessage.call_count == 2
        err = client.chat_postMessage.call_args_list[1].kwargs
        assert err["thread_ts"] == "1700000000.001"
        assert "Scan failed" in err["text"]
        assert "AWS exploded" in err["text"]

    def test_planner_failure_posts_in_thread(self):
        class _BoomPlanner:
            def plan(self, *args, **kwargs):
                raise RuntimeError("planner blew up")

        _, _, client, _, _ = _invoke_plan("plan", planner=_BoomPlanner())
        err = client.chat_postMessage.call_args_list[1].kwargs
        assert "Planner failed" in err["text"]
        assert "planner blew up" in err["text"]

    def test_validation_failed_plan_renders_clean_slack_message(self):
        findings = [_safe_p001_finding()]
        planner = _StubPlanner(_failed_plan(findings))
        _, _, client, _, _ = _invoke_plan("plan", planner=planner)

        plan_call = client.chat_postMessage.call_args_list[1].kwargs
        # Fallback text uses the clean failure copy, not the OK summary.
        assert plan_call["text"] == PLAN_FAILURE_FALLBACK

        blocks_text = json.dumps(plan_call["blocks"])
        # Slack must NOT surface provider internals or raw dropped content.
        assert "claude-test" not in blocks_text
        assert "bedrock" not in blocks_text
        assert "parse_retry_count" not in blocks_text
        assert "garbage" not in blocks_text
        # But the user-facing message must be present.
        assert "did not produce a usable plan" in blocks_text

    def test_parent_post_failure_replies_ephemeral(self):
        client = MagicMock()
        client.chat_postMessage.side_effect = RuntimeError("not_in_channel")
        ack = MagicMock()
        respond = MagicMock()
        logger = MagicMock()
        stub = _StubApp()
        set_plan_scan_runner(lambda config=None: _sample_scan_result())
        set_planner_factory(lambda config: _StubPlanner(
            _ok_plan([_safe_p001_finding()])
        ))

        register(stub)
        stub.commands["/whisper"](
            ack=ack, respond=respond,
            command={"text": "plan", "channel_id": "C", "user_id": "U"},
            client=client, logger=logger,
        )

        respond.assert_called_once()
        kwargs = respond.call_args.kwargs
        assert kwargs["response_type"] == "ephemeral"
        assert "not_in_channel" in kwargs["text"]


class TestUnknownSubcommandStillEphemeral:
    """Regression: the new dispatcher must keep rejecting unknown subcommands."""

    def test_unknown_subcommand(self):
        ack = MagicMock()
        respond = MagicMock()
        client = MagicMock()
        logger = MagicMock()
        stub = _StubApp()
        register(stub)
        stub.commands["/whisper"](
            ack=ack, respond=respond,
            command={"text": "destroy-everything", "channel_id": "C",
                     "user_id": "U"},
            client=client, logger=logger,
        )
        kwargs = respond.call_args.kwargs
        assert "Unknown subcommand" in kwargs["text"]
        assert kwargs["response_type"] == "ephemeral"
