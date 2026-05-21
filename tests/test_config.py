"""
Tests for src/config.py — the single source of truth for configuration.
Covers precedence (CLI > env > file > default), TOML parsing, and doctor checks.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import (
    CheckResult,
    OPTIONS,
    WhisperConfig,
    field_names,
    load_config,
    run_checks,
    settings_view,
)


class TestPrecedence:
    """CLI > env > file > default."""

    def test_defaults_when_nothing_set(self, tmp_path: Path):
        cfg = load_config(env={}, config_path=tmp_path / "missing.toml")
        assert cfg.llm_backend == "bedrock"
        assert cfg.analysis_days == 30
        assert cfg._sources["llm_backend"] == "default"

    def test_env_overrides_default(self, tmp_path: Path):
        cfg = load_config(
            env={"WHISPER_LLM_BACKEND": "openai"},
            config_path=tmp_path / "missing.toml",
        )
        assert cfg.llm_backend == "openai"
        assert cfg._sources["llm_backend"] == "env:WHISPER_LLM_BACKEND"

    def test_legacy_env_name_works(self, tmp_path: Path):
        cfg = load_config(
            env={"LLM_PROVIDER": "anthropic"},
            config_path=tmp_path / "missing.toml",
        )
        assert cfg.llm_backend == "anthropic"
        assert cfg._sources["llm_backend"] == "env:LLM_PROVIDER"

    def test_primary_env_beats_legacy(self, tmp_path: Path):
        cfg = load_config(
            env={"WHISPER_LLM_BACKEND": "openai", "LLM_PROVIDER": "anthropic"},
            config_path=tmp_path / "missing.toml",
        )
        assert cfg.llm_backend == "openai"

    def test_empty_env_string_is_ignored(self, tmp_path: Path):
        cfg = load_config(
            env={"WHISPER_LLM_BACKEND": ""},
            config_path=tmp_path / "missing.toml",
        )
        assert cfg.llm_backend == "bedrock"  # falls through to default
        assert cfg._sources["llm_backend"] == "default"

    def test_file_overrides_default(self, tmp_path: Path):
        toml = tmp_path / "config.toml"
        toml.write_text('llm_backend = "openai"\nanalysis_days = 7\n')
        cfg = load_config(env={}, config_path=toml)
        assert cfg.llm_backend == "openai"
        assert cfg.analysis_days == 7
        assert cfg._sources["llm_backend"].startswith("file:")

    def test_env_overrides_file(self, tmp_path: Path):
        toml = tmp_path / "config.toml"
        toml.write_text('llm_backend = "openai"\n')
        cfg = load_config(
            env={"WHISPER_LLM_BACKEND": "bedrock"},
            config_path=toml,
        )
        assert cfg.llm_backend == "bedrock"

    def test_cli_overrides_everything(self, tmp_path: Path):
        toml = tmp_path / "config.toml"
        toml.write_text('llm_backend = "openai"\n')
        cfg = load_config(
            cli_overrides={"llm_backend": "anthropic"},
            env={"WHISPER_LLM_BACKEND": "bedrock"},
            config_path=toml,
        )
        assert cfg.llm_backend == "anthropic"
        assert cfg._sources["llm_backend"] == "cli"

    def test_cli_none_does_not_override(self, tmp_path: Path):
        cfg = load_config(
            cli_overrides={"llm_backend": None},
            env={"WHISPER_LLM_BACKEND": "openai"},
            config_path=tmp_path / "missing.toml",
        )
        assert cfg.llm_backend == "openai"


class TestTomlFormat:
    def test_supports_whisper_table(self, tmp_path: Path):
        toml = tmp_path / "config.toml"
        toml.write_text('[whisper]\nllm_backend = "openai"\nanalysis_days = 14\n')
        cfg = load_config(env={}, config_path=toml)
        assert cfg.llm_backend == "openai"
        assert cfg.analysis_days == 14

    def test_supports_top_level_keys(self, tmp_path: Path):
        toml = tmp_path / "config.toml"
        toml.write_text('llm_backend = "openai"\n')
        cfg = load_config(env={}, config_path=toml)
        assert cfg.llm_backend == "openai"


class TestCoercion:
    def test_analysis_days_coerced_to_int(self, tmp_path: Path):
        cfg = load_config(
            env={"ANALYSIS_DAYS": "45"},
            config_path=tmp_path / "missing.toml",
        )
        assert cfg.analysis_days == 45
        assert isinstance(cfg.analysis_days, int)

    def test_invalid_int_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="analysis_days"):
            load_config(
                env={"WHISPER_ANALYSIS_DAYS": "many"},
                config_path=tmp_path / "missing.toml",
            )


class TestOptionsRegistry:
    """Every WhisperConfig field must have a matching ConfigOption."""

    def test_every_field_has_an_option(self):
        option_names = {opt.name for opt in OPTIONS}
        for name in field_names():
            assert name in option_names, f"no ConfigOption for field {name!r}"

    def test_every_option_has_a_field(self):
        names = set(field_names())
        for opt in OPTIONS:
            assert opt.name in names, f"ConfigOption {opt.name!r} has no matching field"


class TestDoctorChecks:
    def _stub_session(self, has_creds: bool):
        class _Creds: pass

        class _Session:
            def __init__(self, **kw): pass
            def get_credentials(self):
                return _Creds() if has_creds else None
        return _Session

    def test_scan_ok_with_creds(self, tmp_path: Path):
        cfg = load_config(env={}, config_path=tmp_path / "missing.toml")
        cfg.prompt_log_path = str(tmp_path / "prompts.log")
        with patch("boto3.Session", new=self._stub_session(True)):
            checks = run_checks(cfg)
        scan = next(c for c in checks if c.capability == "scan")
        assert scan.ok

    def test_scan_fails_without_creds(self, tmp_path: Path):
        cfg = load_config(env={}, config_path=tmp_path / "missing.toml")
        cfg.prompt_log_path = str(tmp_path / "prompts.log")
        with patch("boto3.Session", new=self._stub_session(False)):
            checks = run_checks(cfg)
        scan = next(c for c in checks if c.capability == "scan")
        assert not scan.ok
        assert "credentials" in scan.message.lower()

    def test_openai_backend_requires_key(self, tmp_path: Path):
        cfg = load_config(
            env={"WHISPER_LLM_BACKEND": "openai"},
            config_path=tmp_path / "missing.toml",
        )
        cfg.prompt_log_path = str(tmp_path / "prompts.log")
        with patch("boto3.Session", new=self._stub_session(True)):
            checks = run_checks(cfg)
        openai = next(c for c in checks if c.capability == "llm:openai")
        assert not openai.ok

    def test_openai_backend_ok_when_key_present(self, tmp_path: Path):
        cfg = load_config(
            env={
                "WHISPER_LLM_BACKEND": "openai",
                "OPENAI_API_KEY": "sk-test",
            },
            config_path=tmp_path / "missing.toml",
        )
        cfg.prompt_log_path = str(tmp_path / "prompts.log")
        with patch("boto3.Session", new=self._stub_session(True)):
            checks = run_checks(cfg)
        openai = next(c for c in checks if c.capability == "llm:openai")
        assert openai.ok

    def test_slack_check_present(self, tmp_path: Path):
        cfg = load_config(env={}, config_path=tmp_path / "missing.toml")
        cfg.prompt_log_path = str(tmp_path / "prompts.log")
        with patch("boto3.Session", new=self._stub_session(True)):
            checks = run_checks(cfg)
        slack = [c for c in checks if c.capability == "slack"]
        assert len(slack) == 1
        assert not slack[0].ok  # tokens unset by default

    def test_invalid_choice_flagged(self, tmp_path: Path):
        cfg = WhisperConfig(llm_backend="grok")
        cfg.prompt_log_path = str(tmp_path / "prompts.log")
        with patch("boto3.Session", new=self._stub_session(True)):
            checks = run_checks(cfg)
        bad = [c for c in checks if c.capability == "setting:llm_backend"]
        assert len(bad) == 1
        assert not bad[0].ok


class TestSettingsView:
    def test_yields_one_row_per_option(self, tmp_path: Path):
        cfg = load_config(env={}, config_path=tmp_path / "missing.toml")
        rows = settings_view(cfg)
        assert len(rows) == len(OPTIONS)
        for (opt, value, source) in rows:
            assert isinstance(opt.name, str)
            assert source in ("default", "cli") or source.startswith(("env:", "file:"))
