"""
Tests for the doctor CLI — --json, --check, --no-network flags.

The underlying check functions are covered in test_config.py; here we
exercise the wiring in cli/doctor.py.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent
for p in (_REPO, _REPO / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cli.doctor import _filter_checks, doctor, main
from config import CheckResult


def _stub_boto3_session(has_creds: bool = True):
    class _Creds: pass
    class _Session:
        def __init__(self, **kw): pass
        def get_credentials(self):
            return _Creds() if has_creds else None
    return _Session


class TestCheckFilter:
    def _checks(self):
        return [
            CheckResult("scan", True, "ok"),
            CheckResult("llm:bedrock", True, "ok"),
            CheckResult("llm:openai", False, "no key"),
            CheckResult("slack", False, "missing"),
            CheckResult("slack-webhook", True, "set"),
            CheckResult("prompt-log", True, "writable"),
        ]

    def test_no_selectors_keeps_all(self):
        assert len(_filter_checks(self._checks(), None)) == 6

    def test_exact_match(self):
        out = _filter_checks(self._checks(), ["scan"])
        assert [c.capability for c in out] == ["scan"]

    def test_prefix_match_keeps_subcaps(self):
        out = _filter_checks(self._checks(), ["llm"])
        assert {c.capability for c in out} == {"llm:bedrock", "llm:openai"}

    def test_slack_prefix_includes_webhook(self):
        out = _filter_checks(self._checks(), ["slack"])
        assert {c.capability for c in out} == {"slack", "slack-webhook"}

    def test_multiple_selectors(self):
        out = _filter_checks(self._checks(), ["scan", "prompt-log"])
        assert {c.capability for c in out} == {"scan", "prompt-log"}


class TestJsonOutput:
    def test_json_is_valid_and_complete(self, tmp_path: Path, capsys):
        cfg_path = tmp_path / "missing.toml"
        with patch("boto3.Session", new=_stub_boto3_session(True)):
            code = doctor(config_path=cfg_path, as_json=True, no_network=True)
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["config_path"] == str(cfg_path)
        assert payload["config_path_exists"] is False
        assert isinstance(payload["settings"], list)
        assert isinstance(payload["checks"], list)
        assert "ready" in payload
        assert "blocked" in payload
        # No ANSI escapes
        assert "\x1b[" not in captured.out
        # Exit code follows the usual rule
        assert code in (0, 1)

    def test_json_masks_secrets(self, tmp_path: Path, capsys, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecretvalue")
        with patch("boto3.Session", new=_stub_boto3_session(True)):
            doctor(config_path=tmp_path / "missing.toml",
                   as_json=True, no_network=True)
        payload = json.loads(capsys.readouterr().out)
        secret_value = next(
            s["value"] for s in payload["settings"] if s["name"] == "openai_api_key"
        )
        assert "sk-supersecretvalue" not in secret_value
        assert secret_value.endswith("alue")  # last 4 chars


class TestCheckSelector:
    def test_only_named_checks_run(self, tmp_path: Path, capsys):
        with patch("boto3.Session", new=_stub_boto3_session(True)):
            doctor(config_path=tmp_path / "missing.toml",
                   selectors=["scan"], as_json=True, no_network=True)
        payload = json.loads(capsys.readouterr().out)
        assert [c["capability"] for c in payload["checks"]] == ["scan"]

    def test_check_exit_code_zero_when_selected_ok(self, tmp_path: Path):
        with patch("boto3.Session", new=_stub_boto3_session(True)):
            code = doctor(config_path=tmp_path / "missing.toml",
                          selectors=["scan"], as_json=True, no_network=True)
        assert code == 0

    def test_check_exit_code_one_when_selected_blocked(self, tmp_path: Path):
        with patch("boto3.Session", new=_stub_boto3_session(False)):
            code = doctor(config_path=tmp_path / "missing.toml",
                          selectors=["scan"], as_json=True, no_network=True)
        assert code == 1


class TestNoNetworkFlag:
    def test_no_network_skips_slack_auth_test(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "sig")
        with patch("boto3.Session", new=_stub_boto3_session(True)), \
             patch("slack_sdk.WebClient") as web_client_class:
            doctor(config_path=tmp_path / "missing.toml",
                   as_json=True, no_network=True)
        web_client_class.assert_not_called()
        payload = json.loads(capsys.readouterr().out)
        slack = next(c for c in payload["checks"] if c["capability"] == "slack")
        assert slack["ok"]
        assert "skipped" in slack["message"]


class TestMainArgparse:
    def test_main_with_json_flag(self, tmp_path: Path, capsys):
        with patch("boto3.Session", new=_stub_boto3_session(True)):
            code = main(["doctor", "--config", str(tmp_path / "missing.toml"),
                         "--json", "--no-network"])
        assert code in (0, 1)
        # JSON should be the only thing on stdout
        json.loads(capsys.readouterr().out)

    def test_main_with_check_flag(self, tmp_path: Path, capsys):
        with patch("boto3.Session", new=_stub_boto3_session(True)):
            main(["doctor", "--config", str(tmp_path / "missing.toml"),
                  "--check", "scan", "--json", "--no-network"])
        payload = json.loads(capsys.readouterr().out)
        assert [c["capability"] for c in payload["checks"]] == ["scan"]
