"""
Tests for the Slack app — factory, /whisper scan handler, action stubs.
"""
from __future__ import annotations

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
    set_scan_runner,
)


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
    """Captures app.command() and app.action() registrations."""

    def __init__(self, config=None):
        self._whisper_config = config or _valid_config()
        self.commands: dict = {}
        self.actions: dict = {}

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


@pytest.fixture(autouse=True)
def _isolate_handler_globals():
    """Reset module-level injection points after every test."""
    from slack.handlers.scan import _scan_runner as orig_runner  # noqa: F401
    yield
    # Default the background runner back to inline to keep other tests sane.
    set_background_runner(lambda fn: fn())


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
    """Exercise the handler directly with mocked Bolt context."""

    def _invoke(self, text: str, *, scan_runner=None, app=None):
        if scan_runner is not None:
            set_scan_runner(scan_runner)
        else:
            set_scan_runner(lambda config=None: _sample_result())
        set_background_runner(lambda fn: fn())  # inline

        ack = MagicMock()
        respond = MagicMock()
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
            ack=ack, respond=respond, command=command, logger=logger
        )
        return ack, respond, logger

    def test_acknowledges_immediately(self):
        ack, _, _ = self._invoke("scan")
        ack.assert_called_once()

    def test_scan_posts_started_then_findings(self):
        _, respond, _ = self._invoke("scan")
        # Two calls: "scan started" then the blocks payload
        assert respond.call_count == 2
        first = respond.call_args_list[0].kwargs
        second = respond.call_args_list[1].kwargs

        assert first["text"] == SCAN_STARTED_TEXT
        assert first["response_type"] == "in_channel"

        assert "blocks" in second
        assert second["response_type"] == "in_channel"
        assert second["replace_original"] is False
        # Fallback text mentions the totals
        assert "$42.50" in second["text"]

    def test_scan_blocks_include_finding_data(self):
        _, respond, _ = self._invoke("scan")
        blocks = respond.call_args_list[1].kwargs["blocks"]
        import json
        text_blob = json.dumps(blocks)
        assert "vol-abc" in text_blob
        assert "high" in text_blob.lower()

    def test_scan_failure_posts_error(self):
        def boom(config=None):
            raise RuntimeError("AWS exploded")

        _, respond, _ = self._invoke("scan", scan_runner=boom)
        # "scan started" + error message
        assert respond.call_count == 2
        error_call = respond.call_args_list[1].kwargs
        assert "Scan failed" in error_call["text"]
        assert "AWS exploded" in error_call["text"]

    def test_empty_text_shows_usage(self):
        _, respond, _ = self._invoke("")
        assert respond.call_args.kwargs["text"] == USAGE_TEXT
        assert respond.call_args.kwargs["response_type"] == "ephemeral"

    def test_help_subcommand_shows_usage(self):
        _, respond, _ = self._invoke("help")
        assert respond.call_args.kwargs["text"] == USAGE_TEXT

    def test_unknown_subcommand_is_ephemeral(self):
        _, respond, _ = self._invoke("destroy-everything")
        kwargs = respond.call_args.kwargs
        assert "Unknown subcommand" in kwargs["text"]
        assert kwargs["response_type"] == "ephemeral"

    def test_scan_text_is_case_insensitive(self):
        _, respond, _ = self._invoke("SCAN")
        assert respond.call_args_list[0].kwargs["text"] == SCAN_STARTED_TEXT

    def test_logs_caller(self):
        _, _, logger = self._invoke("scan")
        logger.info.assert_called()
        args, _ = logger.info.call_args_list[0]
        assert "U123" in args
        assert "C456" in args


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
