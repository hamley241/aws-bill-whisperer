"""
Thread reply + @mention handlers.

- `message` event fires for every channel message. We filter to messages
  in threads we own (parent ts is in the thread store).
- `app_mention` fires when the bot is @-mentioned anywhere. Inside one
  of our threads we answer with scan + plan context; elsewhere we offer
  a hint.

Routing: the thread store now returns a `ThreadContext` wrapping
`(ScanResult, optional PlanResult, created_at, turns)`. When
`plan_result is None` (scan-only thread), we delegate to the existing
plain-text `analyzer.conversation.answer_thread_question` — zero
behaviour change. When `plan_result` is present (plan thread), we
delegate to `analyzer.plan_conversation.answer_plan_thread_question`,
which runs through the freshness gate, pre-router, envelope validators,
and records a `ConversationTurn` on the context.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

_SRC = Path(__file__).parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from analyzer.conversation import answer_thread_question  # noqa: E402
from analyzer.plan_conversation import (  # noqa: E402
    answer_plan_thread_question,
)

from ..thread_store import ThreadContext, get_store  # noqa: E402


_MENTION_RE = re.compile(r"<@[UW][A-Z0-9]+>")

MENTION_OUT_OF_THREAD_HINT = (
    "Hi! Run `/whisper scan` in a channel and I'll find AWS cost waste. "
    "Once a scan is posted, you can @-mention me in that thread to ask "
    "follow-up questions."
)


# Override in tests. Two seams: one for scan-thread Q&A, one for plan-
# thread Q&A. Routing decides which to call.
_scan_answerer: Callable[..., str] = answer_thread_question
_plan_answerer: Callable[..., Any] = answer_plan_thread_question


def set_answerer(fn: Callable[..., str]) -> None:
    """Override the scan-thread answerer. Retained for compatibility
    with existing tests that swap the LLM call out."""
    global _scan_answerer
    _scan_answerer = fn


def set_plan_answerer(fn: Callable[..., Any]) -> None:
    """Override the plan-thread answerer. Returns a TurnOutcome so
    tests can inspect the validator path that fired."""
    global _plan_answerer
    _plan_answerer = fn


def register(app: Any) -> None:
    config = getattr(app, "_whisper_config", None)

    @app.event("message")
    def handle_thread_message(event, client, logger):
        # Skip bot's own messages and edits / system messages.
        if event.get("bot_id") or event.get("subtype"):
            return
        thread_ts = event.get("thread_ts")
        if not thread_ts:
            return  # not a thread reply
        context = get_store().get(thread_ts)
        if context is None:
            return  # not one of our threads

        question = _strip_mentions(event.get("text", "")).strip()
        if not question:
            return
        logger.info("thread reply in %s by user=%s plan_present=%s",
                    thread_ts, event.get("user"), context.plan_result is not None)
        text = _answer(question, context=context, config=config)
        _post(client, event["channel"], thread_ts, text, logger)

    @app.event("app_mention")
    def handle_app_mention(event, client, logger):
        thread_ts = event.get("thread_ts") or event.get("ts")
        context = get_store().get(thread_ts) if thread_ts else None
        question = _strip_mentions(event.get("text", "")).strip()

        if context is None:
            _post(client, event["channel"], thread_ts,
                  MENTION_OUT_OF_THREAD_HINT, logger)
            return

        if not question:
            _post(client, event["channel"], thread_ts,
                  "What would you like to know about this scan?", logger)
            return

        logger.info("app_mention in %s by user=%s plan_present=%s",
                    thread_ts, event.get("user"),
                    context.plan_result is not None)
        text = _answer(question, context=context, config=config)
        _post(client, event["channel"], thread_ts, text, logger)


def _answer(question: str, *, context: ThreadContext, config) -> str:
    """Route to the right answerer and unwrap to text for posting.

    Plan threads return a `TurnOutcome`; we record the turn on the
    context so subsequent follow-ups in the same thread see the prior
    exchange. Scan-only threads return plain text — no turn tracking
    (the existing path doesn't have it; introducing it for scan-only
    threads is out of scope for PR #9).
    """
    if context.plan_result is None:
        return _scan_answerer(
            question, scan_result=context.scan_result, config=config,
        )
    outcome = _plan_answerer(question, context=context, config=config)
    context.record_turn(outcome.turn)
    return outcome.surfaced_text


def _strip_mentions(text: str) -> str:
    """Remove <@U…> tokens before sending the question to the LLM."""
    return _MENTION_RE.sub("", text)


def _post(client, channel, thread_ts, text, logger) -> None:
    try:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except Exception:
        logger.exception("chat.postMessage failed")
