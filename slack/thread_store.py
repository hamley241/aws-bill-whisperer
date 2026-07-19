"""
Thread-context storage.

When `/whisper scan` or `/whisper plan` posts findings as a threaded
message, we remember a `ThreadContext` keyed by the parent message ts.
Subsequent thread replies look up the context so the LLM has scan +
plan state for Q&A.

PR #9 wraps the prior raw-`ScanResult` value in `ThreadContext` so the
store also carries plan state, a creation timestamp (used by the
plan-thread freshness contract), and a bounded ring of conversation
turns. The wrapper itself lives in `src/analyzer/thread_context.py`
to keep the import direction one-way (slack depends on src, never the
other way round); this module only owns the slack-side state holder
keyed by Slack thread ts strings, plus the legacy re-exports of the
value types for backwards-compat with existing import sites.

This module ships only the in-memory implementation. Per CLAUDE.md
principle 8 (state owned by customer), persistent storage — SQLite
locally, DynamoDB in the paid tier — slots in behind the same
ThreadContextStore interface later. PR #9 deliberately does NOT
persist conversation turns to the audit log; that's its own design
(PII, retention, prompt-log overlap) deferred to a follow-up.
"""

from __future__ import annotations

import sys
from pathlib import Path
from threading import RLock
from typing import Protocol

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Re-export the value types so existing call sites that imported from
# slack.thread_store keep working. The canonical home is
# src/analyzer/thread_context.py.
from analyzer.thread_context import (  # noqa: E402, F401
    DEFAULT_TURN_RING_MAXLEN,
    ConversationTurn,
    ThreadContext,
    new_thread_context,
)


class ThreadContextStore(Protocol):
    """Pluggable thread-context backend."""

    def set(self, thread_ts: str, context: ThreadContext) -> None: ...
    def get(self, thread_ts: str) -> ThreadContext | None: ...
    def has(self, thread_ts: str) -> bool: ...


class InMemoryThreadStore:
    """Process-local dict. Fine for the self-hosted single-process default.

    Multi-process / multi-instance deployments (paid tier) will swap
    this for a persistent backend without changing call sites.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._contexts: dict[str, ThreadContext] = {}

    def set(self, thread_ts: str, context: ThreadContext) -> None:
        with self._lock:
            self._contexts[thread_ts] = context

    def get(self, thread_ts: str) -> ThreadContext | None:
        with self._lock:
            return self._contexts.get(thread_ts)

    def has(self, thread_ts: str) -> bool:
        with self._lock:
            return thread_ts in self._contexts

    def clear(self) -> None:
        with self._lock:
            self._contexts.clear()


# Single module-level default. Production swaps via set_store(); tests
# call .clear() between cases.
_default_store = InMemoryThreadStore()


def get_store() -> ThreadContextStore:
    return _default_store


def set_store(store: ThreadContextStore) -> None:
    global _default_store
    _default_store = store  # type: ignore[assignment]
