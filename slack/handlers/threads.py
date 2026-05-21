"""
Thread reply + @mention handlers.

- `message` event fires for every channel message. We filter to messages
  in threads we own (parent ts is in the thread store).
- `app_mention` fires when the bot is @-mentioned anywhere. Inside one
  of our threads we answer with scan context; elsewhere we offer a hint.
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

from ..thread_store import get_store  # noqa: E402


_MENTION_RE = re.compile(r"<@[UW][A-Z0-9]+>")

MENTION_OUT_OF_THREAD_HINT = (
    "Hi! Run `/whisper scan` in a channel and I'll find AWS cost waste. "
    "Once a scan is posted, you can @-mention me in that thread to ask "
    "follow-up questions."
)


# Override in tests.
_answerer: Callable[..., str] = answer_thread_question


def set_answerer(fn: Callable[..., str]) -> None:
    global _answerer
    _answerer = fn


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
        scan_result = get_store().get(thread_ts)
        if scan_result is None:
            return  # not one of our threads

        question = _strip_mentions(event.get("text", "")).strip()
        if not question:
            return
        logger.info("thread reply in %s by user=%s",
                    thread_ts, event.get("user"))
        answer = _answerer(question, scan_result=scan_result, config=config)
        _post(client, event["channel"], thread_ts, answer, logger)

    @app.event("app_mention")
    def handle_app_mention(event, client, logger):
        thread_ts = event.get("thread_ts") or event.get("ts")
        scan_result = get_store().get(thread_ts) if thread_ts else None
        question = _strip_mentions(event.get("text", "")).strip()

        if scan_result is None:
            _post(client, event["channel"], thread_ts,
                  MENTION_OUT_OF_THREAD_HINT, logger)
            return

        if not question:
            _post(client, event["channel"], thread_ts,
                  "What would you like to know about this scan?", logger)
            return

        logger.info("app_mention in %s by user=%s",
                    thread_ts, event.get("user"))
        answer = _answerer(question, scan_result=scan_result, config=config)
        _post(client, event["channel"], thread_ts, answer, logger)


def _strip_mentions(text: str) -> str:
    """Remove <@U…> tokens before sending the question to the LLM."""
    return _MENTION_RE.sub("", text)


def _post(client, channel, thread_ts, text, logger) -> None:
    try:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except Exception:
        logger.exception("chat.postMessage failed")
