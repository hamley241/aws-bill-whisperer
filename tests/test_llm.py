"""
Tests for src/llm/ — provider clients, prompt logger, factory — and for
the analyzer.llm thin wrapper that delegates to them.

Per CLAUDE.md principle 5, the LLM layer is the single boundary every
prompt flows through. These tests pin down that contract.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import WhisperConfig
from llm import LLMClient, LoggedLLMClient, Message, PromptLogger, make_llm_client
from llm.base import LLMResponse
from llm.bedrock import BedrockClient


# ---------------------------------------------------------------------------
# Bedrock client — boto3 mocked
# ---------------------------------------------------------------------------

class TestBedrockClient:
    def _mock_session(self, response_text: str = "Hello", usage: dict | None = None):
        session = MagicMock()
        client = MagicMock()
        body_payload = {
            "content": [{"text": response_text}],
            "usage": usage or {"input_tokens": 12, "output_tokens": 34},
        }
        client.invoke_model.return_value = {
            "body": io.BytesIO(json.dumps(body_payload).encode())
        }
        session.client.return_value = client
        return session, client

    def test_returns_response_with_provider_metadata(self):
        session, _ = self._mock_session("Analysis text")
        bedrock = BedrockClient(session=session)
        resp = bedrock.complete([Message(role="user", content="Hi")])
        assert resp.provider == "bedrock"
        assert resp.boundary_crossed is False
        assert resp.text == "Analysis text"
        assert resp.input_tokens == 12
        assert resp.output_tokens == 34

    def test_system_message_moves_to_top_level(self):
        session, client = self._mock_session()
        bedrock = BedrockClient(session=session)
        bedrock.complete([
            Message(role="system", content="be terse"),
            Message(role="user", content="hello"),
        ])
        body = json.loads(client.invoke_model.call_args.kwargs["body"])
        assert body["system"] == "be terse"
        assert all(m["role"] != "system" for m in body["messages"])

    def test_model_override(self):
        session, client = self._mock_session()
        bedrock = BedrockClient(session=session, default_model="default-model")
        bedrock.complete([Message(role="user", content="x")], model="other-model")
        assert client.invoke_model.call_args.kwargs["modelId"] == "other-model"


# ---------------------------------------------------------------------------
# Prompt logger — JSONL contract
# ---------------------------------------------------------------------------

class _StubClient(LLMClient):
    """Minimal LLMClient that returns canned responses, for logger tests."""
    provider = "stub"
    boundary_crossed = False

    def __init__(self, response_text: str = "stub answer", *,
                 provider: str = "stub", boundary_crossed: bool = False):
        self._text = response_text
        self.provider = provider
        self.boundary_crossed = boundary_crossed

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        return LLMResponse(
            text=self._text,
            provider=self.provider,
            model=model or "stub-model",
            boundary_crossed=self.boundary_crossed,
            input_tokens=7,
            output_tokens=11,
        )

    @property
    def default_model(self):
        return "stub-model"


class TestPromptLogger:
    def test_writes_jsonl_record(self, tmp_path: Path):
        log_path = tmp_path / "prompts.log"
        logger = PromptLogger(log_path)
        client = LoggedLLMClient(_StubClient(), logger, prompt_template="cost_analysis")

        client.complete([Message(role="user", content="hello")])

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["provider"] == "stub"
        assert record["boundary_crossed"] is False
        assert record["input_tokens"] == 7
        assert record["output_tokens"] == 11
        assert record["prompt_template"] == "cost_analysis"
        assert record["messages"] == [{"role": "user", "content": "hello"}]
        assert record["response_text"] == "stub answer"
        assert "timestamp" in record

    def test_boundary_crossed_flag_recorded(self, tmp_path: Path):
        log_path = tmp_path / "prompts.log"
        logger = PromptLogger(log_path)
        client = LoggedLLMClient(
            _StubClient(provider="openai", boundary_crossed=True), logger
        )
        client.complete([Message(role="user", content="x")])

        record = json.loads(log_path.read_text().strip())
        assert record["provider"] == "openai"
        assert record["boundary_crossed"] is True

    def test_appends_each_call(self, tmp_path: Path):
        log_path = tmp_path / "prompts.log"
        logger = PromptLogger(log_path)
        client = LoggedLLMClient(_StubClient(), logger)
        client.complete([Message(role="user", content="one")])
        client.complete([Message(role="user", content="two")])

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_iter_records_round_trips(self, tmp_path: Path):
        log_path = tmp_path / "prompts.log"
        logger = PromptLogger(log_path)
        client = LoggedLLMClient(_StubClient(), logger)
        client.complete([Message(role="user", content="ping")])

        records = list(logger.iter_records())
        assert len(records) == 1
        assert records[0].response_text == "stub answer"


# ---------------------------------------------------------------------------
# Factory — pick a provider from WhisperConfig
# ---------------------------------------------------------------------------

class TestFactory:
    def test_bedrock_default(self, tmp_path: Path):
        cfg = WhisperConfig(prompt_log_path=str(tmp_path / "prompts.log"))
        with patch("llm.bedrock.BedrockClient.__init__", return_value=None):
            client = make_llm_client(cfg)
        assert isinstance(client, LoggedLLMClient)
        assert client.provider == "bedrock"
        assert client.boundary_crossed is False

    def test_openai_requires_api_key(self, tmp_path: Path):
        cfg = WhisperConfig(
            llm_backend="openai",
            prompt_log_path=str(tmp_path / "prompts.log"),
        )
        with pytest.raises(ValueError, match="openai_api_key"):
            make_llm_client(cfg)

    def test_anthropic_requires_api_key(self, tmp_path: Path):
        cfg = WhisperConfig(
            llm_backend="anthropic",
            prompt_log_path=str(tmp_path / "prompts.log"),
        )
        with pytest.raises(ValueError, match="anthropic_api_key"):
            make_llm_client(cfg)

    def test_unknown_backend_rejected(self, tmp_path: Path):
        cfg = WhisperConfig(
            llm_backend="palm",
            prompt_log_path=str(tmp_path / "prompts.log"),
        )
        with pytest.raises(ValueError, match="palm"):
            make_llm_client(cfg)


# ---------------------------------------------------------------------------
# analyzer.llm integration
# ---------------------------------------------------------------------------

class TestAnalyzeCostsIntegration:
    def test_uses_injected_client_and_template(self):
        from analyzer.llm import analyze_costs

        captured: dict = {}

        class _CaptureClient(LLMClient):
            provider = "stub"
            boundary_crossed = False

            def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
                captured["messages"] = messages
                return LLMResponse(
                    text="OK",
                    provider="stub",
                    model="stub-model",
                    boundary_crossed=False,
                )

            @property
            def default_model(self):
                return "stub-model"

        cost_data = {"usage": {
            "period": {"start": "2026-05-01", "end": "2026-05-17"},
            "total": 100.0,
            "services": [{"name": "EC2", "cost": 100.0, "percent": 100.0}],
        }}

        result = analyze_costs(cost_data, client=_CaptureClient())
        assert result == "OK"
        prompt = captured["messages"][0].content
        assert "Total Cost: $100.00" in prompt
        assert "Top Cost Drivers" in prompt  # marker from cost_analysis template


class TestFormatCostData:
    def test_format_with_usage(self):
        from analyzer.llm import _format_cost_data_for_llm
        cost_data = {
            "usage": {
                "period": {"start": "2026-01-01", "end": "2026-02-01"},
                "total": 100,
                "services": [
                    {"name": "EC2", "cost": 50, "percent": 50},
                    {"name": "RDS", "cost": 50, "percent": 50},
                ],
            }
        }
        result = _format_cost_data_for_llm(cost_data)
        assert "Total" in result
        assert "EC2" in result
        assert "RDS" in result

    def test_format_missing_usage(self):
        from analyzer.llm import _format_cost_data_for_llm
        assert _format_cost_data_for_llm({}) == ""
