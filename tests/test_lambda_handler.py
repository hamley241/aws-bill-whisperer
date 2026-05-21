"""
Tests for slack.lambda_handler — the AWS Lambda entry point.

We don't invoke real Lambda here; we exercise the module's import path,
its app-construction behaviour given environment configuration, and
the SlackRequestHandler indirection.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _reload_handler(monkeypatch):
    """Reload the module so the module-level make_app() call re-runs
    against the patched environment."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-bot")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing")
    monkeypatch.setenv("WHISPER_LLM_BACKEND", "bedrock")
    # Ensure we don't leak into other tests via sys.modules.
    if "slack.lambda_handler" in sys.modules:
        del sys.modules["slack.lambda_handler"]
    return importlib.import_module("slack.lambda_handler")


class TestLambdaHandler:
    def test_module_loads_with_credentials(self, monkeypatch):
        module = _reload_handler(monkeypatch)
        assert hasattr(module, "handler")
        assert callable(module.handler)
        # App was constructed at import time
        assert module._app is not None
        assert module._app._whisper_config.slack_bot_token == "xoxb-test-bot"

    def test_module_raises_without_credentials(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        if "slack.lambda_handler" in sys.modules:
            del sys.modules["slack.lambda_handler"]
        with pytest.raises(ValueError, match="slack_bot_token"):
            importlib.import_module("slack.lambda_handler")

    def test_handler_delegates_to_slack_request_handler(self, monkeypatch):
        module = _reload_handler(monkeypatch)

        fake_handler_instance = MagicMock()
        fake_handler_instance.handle.return_value = {"statusCode": 200}
        fake_handler_class = MagicMock(return_value=fake_handler_instance)

        with patch.dict(
            sys.modules,
            {"slack_bolt.adapter.aws_lambda": MagicMock(
                SlackRequestHandler=fake_handler_class
            )},
        ):
            result = module.handler({"body": "x"}, MagicMock())

        fake_handler_class.assert_called_once_with(module._app)
        fake_handler_instance.handle.assert_called_once()
        assert result == {"statusCode": 200}
