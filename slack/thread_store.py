"""
Thread-context storage.

When `/whisper scan` posts findings as a threaded message, we remember
the ScanResult keyed by the parent message ts. Subsequent thread
replies look up the result so the LLM has scan context for Q&A.

This module ships only the in-memory implementation. Per CLAUDE.md
principle 8 (state owned by customer), persistent storage — SQLite
locally, DynamoDB in the paid tier — slots in behind the same
ThreadContextStore interface later.
"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from presenters import ScanResult


class ThreadContextStore(Protocol):
    """Pluggable thread-context backend."""

    def set(self, thread_ts: str, result: "ScanResult") -> None: ...
    def get(self, thread_ts: str) -> "ScanResult | None": ...
    def has(self, thread_ts: str) -> bool: ...


class InMemoryThreadStore:
    """Process-local dict. Fine for the self-hosted single-process default.

    Multi-process / multi-instance deployments (paid tier) will swap
    this for a persistent backend without changing call sites.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._results: dict[str, "ScanResult"] = {}

    def set(self, thread_ts: str, result: "ScanResult") -> None:
        with self._lock:
            self._results[thread_ts] = result

    def get(self, thread_ts: str) -> "ScanResult | None":
        with self._lock:
            return self._results.get(thread_ts)

    def has(self, thread_ts: str) -> bool:
        with self._lock:
            return thread_ts in self._results

    def clear(self) -> None:
        with self._lock:
            self._results.clear()


# Single module-level default. Production swaps via set_store(); tests
# call .clear() between cases.
_default_store = InMemoryThreadStore()


def get_store() -> ThreadContextStore:
    return _default_store


def set_store(store: ThreadContextStore) -> None:
    global _default_store
    _default_store = store  # type: ignore[assignment]
