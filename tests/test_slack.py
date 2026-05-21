"""
Tests for the Slack app — factory, /whisper scan handler, action stubs.
Thread + app_mention handlers and the LLM Q&A live in tests/test_threads.py.
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

from config import WhisperConfig
from patterns.base import Finding, RiskTier
from presenters import ScanResult
from slack import make_app
from slack.handlers import actions as action_handlers
from slack.handlers.scan import (
    SCAN_STARTED_TEXT,
    USAGE_TEXT,
    register,
    set_background_runner,
    set_explainer,
    set_scan_runner,
)
from slack.thread_store import get_store


def _valid_config(**overrides) -> WhisperConfig:
    defaults = dict(
        slack_bot_token="xoxb-test-bot-token",
        slack_signing_secret="test-signing-secret",
    )
    defaults.update(overrides)
    return WhisperConfig(**defaults)


def _sample_result() -> ScanResult:
    finding = Finding(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.9,
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
    )
    return ScanResult.from_findings([finding])


class _StubApp:
    """Captures app.command/action/event registrations."""

    def __init__(self, config=None):
        self._whisper_config = config or _valid_config()
        self.commands: dict = {}
        self.actions: dict = {}
        self.events: dict = {}

    def command(self, name):
        def decorator(fn):
            self.commands[name] = fn
            return fn
        return decorator

    def action(self, name):
        def decorator(fn):
            self.actions[name] = fn
            return fn
        return decorator

    def event(self, name):
        def decorator(fn):
            self.events[name] = fn
            return fn
        return decorator


@pytest.fixture(autouse=True)
def _isolate_handler_globals():
    """Reset module-level injection points after every test."""
    set_explainer(lambda findings, **kwargs: None)
    get_store().clear()
    yield
    set_background_runner(lambda fn: fn())
    get_store().clear()


class TestAppFactory:
    def test_builds_app_with_credentials(self):
        app = make_app(_valid_config())
        assert app is not None
        assert app._whisper_config.slack_bot_token == "xoxb-test-bot-token"

    def test_missing_bot_token_raises(self):
        with pytest.raises(ValueError, match="slack_bot_token"):
            make_app(_valid_config(slack_bot_token=None))

    def test_missing_signing_secret_raises(self):
        with pytest.raises(ValueError, match="slack_signing_secret"):
            make_app(_valid_config(slack_signing_secret=None))


class TestScanCommand:
    """Direct handler invocation with mocked Bolt context.

    The handler now calls client.chat_postMessage twice (parent post +
    threaded findings). We assert against the client mock.
    """

    def _invoke(self, text: str, *, scan_runner=None, app=None,
                parent_ts: str = "1700000000.001"):
        if scan_runner is not None:
            set_scan_runner(scan_runner)
        else:
            set_scan_runner(lambda config=None: _sample_result())
        set_background_runner(lambda fn: fn())  # inline

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

        stub = app or _StubApp()
        register(stub)
        assert "/whisper" in stub.commands
        stub.commands["/whisper"](
            ack=ack, respond=respond, command=command, client=client, logger=logger
        )
        return ack, respond, client, logger

    def test_acknowledges_immediately(self):
        ack, _, _, _ = self._invoke("scan")
        ack.assert_called_once()

    def test_scan_posts_parent_then_threaded_findings(self):
        _, _, client, _ = self._invoke("scan")
        assert client.chat_postMessage.call_count == 2

        parent_call = client.chat_postMessage.call_args_list[0].kwargs
        assert parent_call["channel"] == "C456"
        assert parent_call["text"] == SCAN_STARTED_TEXT
        assert "thread_ts" not in parent_call  # parent is top-level

        findings_call = client.chat_postMessage.call_args_list[1].kwargs
        assert findings_call["channel"] == "C456"
        assert findings_call["thread_ts"] == "1700000000.001"
        assert "blocks" in findings_call
        assert "$42.50" in findings_call["text"]  # fallback

    def test_scan_findings_blocks_include_finding_data(self):
        _, _, client, _ = self._invoke("scan")
        blocks = client.chat_postMessage.call_args_list[1].kwargs["blocks"]
        text_blob = json.dumps(blocks)
        assert "vol-abc" in text_blob
        assert "high" in text_blob.lower()

    def test_thread_context_stored_after_scan(self):
        self._invoke("scan", parent_ts="ts-42")
        result = get_store().get("ts-42")
        assert result is not None
        assert result.findings[0].resource_id == "vol-abc"

    def test_scan_failure_posts_error_in_thread(self):
        def boom(config=None):
            raise RuntimeError("AWS exploded")

        _, _, client, _ = self._invoke("scan", scan_runner=boom)
        # parent + error message both posted; error lands in thread
        assert client.chat_postMessage.call_count == 2
        error_call = client.chat_postMessage.call_args_list[1].kwargs
        assert "Scan failed" in error_call["text"]
        assert "AWS exploded" in error_call["text"]
        assert error_call["thread_ts"] == "1700000000.001"

    def test_parent_post_failure_replies_ephemeral(self):
        client = MagicMock()
        client.chat_postMessage.side_effect = RuntimeError("not_in_channel")
        ack = MagicMock()
        respond = MagicMock()
        logger = MagicMock()
        stub = _StubApp()
        set_scan_runner(lambda config=None: _sample_result())
        set_background_runner(lambda fn: fn())

        register(stub)
        stub.commands["/whisper"](
            ack=ack, respond=respond,
            command={"text": "scan", "channel_id": "C", "user_id": "U"},
            client=client, logger=logger,
        )

        respond.assert_called_once()
        kwargs = respond.call_args.kwargs
        assert kwargs["response_type"] == "ephemeral"
        assert "not_in_channel" in kwargs["text"]

    def test_empty_text_shows_usage(self):
        _, respond, _, _ = self._invoke("")
        assert respond.call_args.kwargs["text"] == USAGE_TEXT
        assert respond.call_args.kwargs["response_type"] == "ephemeral"

    def test_help_subcommand_shows_usage(self):
        _, respond, _, _ = self._invoke("help")
        assert respond.call_args.kwargs["text"] == USAGE_TEXT

    def test_unknown_subcommand_is_ephemeral(self):
        _, respond, _, _ = self._invoke("destroy-everything")
        kwargs = respond.call_args.kwargs
        assert "Unknown subcommand" in kwargs["text"]
        assert kwargs["response_type"] == "ephemeral"

    def test_scan_text_is_case_insensitive(self):
        _, _, client, _ = self._invoke("SCAN")
        assert client.chat_postMessage.call_args_list[0].kwargs["text"] == SCAN_STARTED_TEXT

    def test_explainer_called_between_scan_and_post(self):
        explain_calls: list = []

        def fake_explain(findings, **kwargs):
            for f in findings:
                f.explanation = "Test explanation."
            explain_calls.append(len(findings))

        set_explainer(fake_explain)
        _, _, client, _ = self._invoke("scan")
        assert explain_calls == [1]

        blocks = client.chat_postMessage.call_args_list[1].kwargs["blocks"]
        assert "Test explanation." in json.dumps(blocks)

    def test_explainer_failure_does_not_block_post(self):
        def boom(findings, **kwargs):
            raise RuntimeError("LLM dead")

        set_explainer(boom)
        _, _, client, _ = self._invoke("scan")
        # The blocks were still posted despite the explainer crashing.
        assert client.chat_postMessage.call_count == 2
        assert "blocks" in client.chat_postMessage.call_args_list[1].kwargs


class TestActionHandlers:
    def _invoke(self, action_id: str, body: dict):
        ack = MagicMock()
        respond = MagicMock()
        logger = MagicMock()

        stub = _StubApp()
        action_handlers.register(stub)
        assert action_id in stub.actions
        stub.actions[action_id](
            ack=ack, body=body, respond=respond, logger=logger
        )
        return ack, respond, logger

    def test_open_pr_button_acknowledges_and_replies(self):
        body = {
            "user": {"id": "U999"},
            "actions": [{"value": "finding-uuid-123"}],
        }
        ack, respond, logger = self._invoke("open_pr", body)
        ack.assert_called_once()
        kwargs = respond.call_args.kwargs
        assert kwargs["response_type"] == "ephemeral"
        assert "Open PR" in kwargs["text"]
        logger.info.assert_called()

    def test_overflow_show_all(self):
        body = {
            "user": {"id": "U999"},
            "actions": [{"selected_option": {"value": "show_all"}}],
        }
        _, respond, _ = self._invoke("scan_overflow", body)
        assert "Show all findings" in respond.call_args.kwargs["text"]

    def test_overflow_download_json(self):
        body = {
            "user": {"id": "U999"},
            "actions": [{"selected_option": {"value": "download_json"}}],
        }
        _, respond, _ = self._invoke("scan_overflow", body)
        assert "Download JSON" in respond.call_args.kwargs["text"]
