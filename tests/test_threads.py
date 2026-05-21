"""
Tests for thread context store, LLM Q&A (analyzer.conversation), and
the message + app_mention handlers (slack.handlers.threads).
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

from analyzer.conversation import answer_thread_question
from llm import LLMClient
from llm.base import LLMResponse, Message
from patterns.base import Finding, RiskTier
from presenters import ScanResult
from slack.handlers import threads as thread_handlers
from slack.handlers.threads import (
    MENTION_OUT_OF_THREAD_HINT,
    set_answerer,
)
from slack.thread_store import InMemoryThreadStore, get_store


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _sample_result() -> ScanResult:
    return ScanResult.from_findings([_finding()])


@pytest.fixture(autouse=True)
def _reset_store_and_answerer():
    get_store().clear()
    set_answerer(answer_thread_question)
    yield
    get_store().clear()


# ---------------------------------------------------------------------------
# Thread context store
# ---------------------------------------------------------------------------

class TestInMemoryThreadStore:
    def test_set_get_round_trip(self):
        store = InMemoryThreadStore()
        store.set("ts-1", _sample_result())
        assert store.get("ts-1").finding_count == 1

    def test_missing_returns_none(self):
        store = InMemoryThreadStore()
        assert store.get("missing") is None

    def test_has(self):
        store = InMemoryThreadStore()
        assert not store.has("ts-1")
        store.set("ts-1", _sample_result())
        assert store.has("ts-1")

    def test_clear(self):
        store = InMemoryThreadStore()
        store.set("ts-1", _sample_result())
        store.clear()
        assert store.get("ts-1") is None


# ---------------------------------------------------------------------------
# analyzer.conversation.answer_thread_question
# ---------------------------------------------------------------------------

class _StubLLM(LLMClient):
    provider = "stub"
    boundary_crossed = False

    def __init__(self, answer: str = "Because that EBS volume is unattached."):
        self.calls: list[list[Message]] = []
        self._answer = answer

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        self.calls.append(messages)
        return LLMResponse(
            text=self._answer,
            provider=self.provider,
            model="stub-model",
            boundary_crossed=False,
        )

    @property
    def default_model(self):
        return "stub-model"


class TestAnswerThreadQuestion:
    def test_returns_llm_text(self):
        client = _StubLLM("Volume is wasted because it's unattached.")
        ans = answer_thread_question(
            "Why is this expensive?",
            scan_result=_sample_result(),
            client=client,
        )
        assert ans == "Volume is wasted because it's unattached."

    def test_includes_scan_context_in_prompt(self):
        client = _StubLLM()
        answer_thread_question(
            "Tell me about vol-abc",
            scan_result=_sample_result(),
            client=client,
        )
        prompt = client.calls[0][0].content
        assert "vol-abc" in prompt
        assert "EBS Volume" in prompt
        assert "$42.50/mo" in prompt
        assert "aws ec2 delete-volume" in prompt
        assert "Tell me about vol-abc" in prompt

    def test_no_scan_uses_placeholder(self):
        client = _StubLLM()
        answer_thread_question(
            "What can you tell me?",
            scan_result=None,
            client=client,
        )
        prompt = client.calls[0][0].content
        assert "no recent scan" in prompt.lower()

    def test_no_config_no_client_returns_fallback(self):
        ans = answer_thread_question(
            "Why?", scan_result=_sample_result(),
        )
        assert "doctor" in ans
        assert "Why" in ans

    def test_llm_failure_returns_graceful_message(self):
        class _ExplodingLLM(LLMClient):
            provider = "boom"
            boundary_crossed = False
            def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
                raise RuntimeError("LLM down")
            @property
            def default_model(self):
                return "boom"
        ans = answer_thread_question(
            "Q?", scan_result=_sample_result(), client=_ExplodingLLM(),
        )
        assert "couldn't answer" in ans.lower() or "x:" in ans.lower()


# ---------------------------------------------------------------------------
# Thread + app_mention handlers
# ---------------------------------------------------------------------------

class _StubApp:
    def __init__(self):
        self._whisper_config = None
        self.events: dict = {}

    def event(self, name):
        def decorator(fn):
            self.events[name] = fn
            return fn
        return decorator


def _register_threads() -> _StubApp:
    stub = _StubApp()
    thread_handlers.register(stub)
    return stub


class TestMessageHandler:
    def test_ignores_message_outside_known_thread(self):
        client = MagicMock()
        captured: list = []
        set_answerer(lambda q, **kw: captured.append(q) or "answer")

        stub = _register_threads()
        stub.events["message"](
            event={"text": "hello", "channel": "C1", "user": "U1",
                   "thread_ts": "unknown-ts"},
            client=client,
            logger=MagicMock(),
        )
        client.chat_postMessage.assert_not_called()
        assert captured == []

    def test_ignores_non_thread_message(self):
        client = MagicMock()
        get_store().set("ts-1", _sample_result())

        stub = _register_threads()
        stub.events["message"](
            event={"text": "hi", "channel": "C1", "user": "U1"},  # no thread_ts
            client=client,
            logger=MagicMock(),
        )
        client.chat_postMessage.assert_not_called()

    def test_ignores_bot_messages(self):
        client = MagicMock()
        get_store().set("ts-1", _sample_result())

        stub = _register_threads()
        stub.events["message"](
            event={"text": "hi", "channel": "C1", "thread_ts": "ts-1",
                   "bot_id": "B999"},
            client=client,
            logger=MagicMock(),
        )
        client.chat_postMessage.assert_not_called()

    def test_answers_in_known_thread(self):
        client = MagicMock()
        get_store().set("ts-1", _sample_result())

        captured = []

        def fake(q, *, scan_result, config):
            captured.append((q, scan_result.finding_count))
            return "stubbed answer"

        set_answerer(fake)

        stub = _register_threads()
        stub.events["message"](
            event={"text": "<@U999> why is this expensive?", "channel": "C1",
                   "user": "U1", "thread_ts": "ts-1"},
            client=client,
            logger=MagicMock(),
        )

        # Mention token stripped
        assert captured == [("why is this expensive?", 1)]
        client.chat_postMessage.assert_called_once_with(
            channel="C1", thread_ts="ts-1", text="stubbed answer",
        )


class TestAppMentionHandler:
    def test_mention_outside_thread_returns_hint(self):
        client = MagicMock()
        stub = _register_threads()
        stub.events["app_mention"](
            event={"text": "<@U999> hi", "channel": "C1", "user": "U1",
                   "ts": "msg-ts"},
            client=client,
            logger=MagicMock(),
        )
        client.chat_postMessage.assert_called_once()
        call = client.chat_postMessage.call_args.kwargs
        assert call["text"] == MENTION_OUT_OF_THREAD_HINT

    def test_mention_in_known_thread_answers(self):
        client = MagicMock()
        get_store().set("ts-7", _sample_result())
        set_answerer(lambda q, **kw: "stubbed")

        stub = _register_threads()
        stub.events["app_mention"](
            event={"text": "<@U999> what about vol-abc?", "channel": "C1",
                   "user": "U1", "thread_ts": "ts-7", "ts": "msg-ts"},
            client=client,
            logger=MagicMock(),
        )
        client.chat_postMessage.assert_called_once_with(
            channel="C1", thread_ts="ts-7", text="stubbed",
        )

    def test_mention_with_empty_text_in_thread_prompts_for_question(self):
        client = MagicMock()
        get_store().set("ts-5", _sample_result())
        set_answerer(lambda q, **kw: "should not be called")

        stub = _register_threads()
        stub.events["app_mention"](
            event={"text": "<@U999>", "channel": "C1", "user": "U1",
                   "thread_ts": "ts-5", "ts": "msg-ts"},
            client=client,
            logger=MagicMock(),
        )
        call = client.chat_postMessage.call_args.kwargs
        assert "What would you like to know" in call["text"]
