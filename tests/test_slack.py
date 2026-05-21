"""
Tests for the Slack app skeleton.

PR 1 scope: /whisper scan is registered, ACKs, and replies with a
"scan started" message. Block Kit, LLM explanations, threads, and the
Lambda adapter land in later PRs.
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
from slack import make_app
from slack.handlers.scan import SCAN_STARTED_TEXT, USAGE_TEXT, register


def _valid_config(**overrides) -> WhisperConfig:
    defaults = dict(
        slack_bot_token="xoxb-test-bot-token",
        slack_signing_secret="test-signing-secret",
    )
    defaults.update(overrides)
    return WhisperConfig(**defaults)


class TestAppFactory:
    def test_builds_app_with_credentials(self):
        app = make_app(_valid_config())
        assert app is not None
        # Config attached for downstream handlers (PR 2+).
        assert app._whisper_config.slack_bot_token == "xoxb-test-bot-token"

    def test_missing_bot_token_raises(self):
        with pytest.raises(ValueError, match="slack_bot_token"):
            make_app(_valid_config(slack_bot_token=None))

    def test_missing_signing_secret_raises(self):
        with pytest.raises(ValueError, match="slack_signing_secret"):
            make_app(_valid_config(slack_signing_secret=None))


class TestScanCommand:
    """Exercise the handler directly with mocked Bolt context.

    Going through the full Bolt request pipeline requires HTTP signature
    verification; per Bolt's docs, unit-testing handlers in isolation
    with mocked ack/respond is the recommended pattern.
    """

    def _invoke(self, text: str):
        ack = MagicMock()
        respond = MagicMock()
        logger = MagicMock()
        command = {
            "text": text,
            "user_id": "U123",
            "channel_id": "C456",
            "team_id": "T789",
        }

        # Capture the registered handler by stubbing app.command()
        captured: dict = {}

        class _StubApp:
            def command(self, name):
                def decorator(fn):
                    captured["name"] = name
                    captured["fn"] = fn
                    return fn
                return decorator

        register(_StubApp())
        assert captured["name"] == "/whisper"
        captured["fn"](ack=ack, respond=respond, command=command, logger=logger)
        return ack, respond, logger

    def test_acknowledges_immediately(self):
        ack, _, _ = self._invoke("scan")
        ack.assert_called_once()

    def test_scan_posts_in_channel(self):
        _, respond, _ = self._invoke("scan")
        respond.assert_called_once()
        kwargs = respond.call_args.kwargs
        assert kwargs["text"] == SCAN_STARTED_TEXT
        assert kwargs["response_type"] == "in_channel"

    def test_empty_text_shows_usage(self):
        _, respond, _ = self._invoke("")
        kwargs = respond.call_args.kwargs
        assert kwargs["text"] == USAGE_TEXT
        assert kwargs["response_type"] == "ephemeral"

    def test_help_subcommand_shows_usage(self):
        _, respond, _ = self._invoke("help")
        assert respond.call_args.kwargs["text"] == USAGE_TEXT

    def test_unknown_subcommand_is_ephemeral(self):
        _, respond, _ = self._invoke("destroy-everything")
        kwargs = respond.call_args.kwargs
        assert "Unknown subcommand" in kwargs["text"]
        assert "destroy-everything" in kwargs["text"]
        assert kwargs["response_type"] == "ephemeral"

    def test_scan_text_is_case_insensitive(self):
        _, respond, _ = self._invoke("SCAN")
        assert respond.call_args.kwargs["text"] == SCAN_STARTED_TEXT

    def test_logs_caller(self):
        _, _, logger = self._invoke("scan")
        logger.info.assert_called_once()
        args, _ = logger.info.call_args
        # info("...user=%s channel=%s", user_id, channel_id)
        assert "U123" in args
        assert "C456" in args
