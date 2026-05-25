"""
ThreadContext + ConversationTurn — value types for per-thread state.

These types are consumed by both the analyzer layer (conversational
Q&A on plans, scans) and the slack handler layer (which holds the
in-memory ThreadContextStore). They live under `src/analyzer/` rather
than under `slack/` to keep the import direction one-way: slack/
depends on src/, never the other way round.

The `InMemoryThreadStore` and `get_store()` API remain in
`slack/thread_store.py` because they're the slack-side state holder
keyed by Slack thread ts strings. The values they hold are these
types.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.schemas import PlanResult
    from presenters import ScanResult


# Bounded ring of conversation turns retained per thread. Six is two
# back-and-forth pairs of context with one pair of headroom — fits the
# typical follow-up cadence ("why no p004?" → answer → "what about
# NAT?" → answer → "can I do dev first?" → answer) without unbounded
# growth in prompt size or memory.
DEFAULT_TURN_RING_MAXLEN = 6


@dataclass(frozen=True)
class ConversationTurn:
    """One question-and-answer pair retained in the per-thread ring.

    `assistant_answer` holds only the post-validation prose that was
    surfaced to the user, never the raw LLM emission. This prevents a
    poisoned response from contaminating future turns: even if a turn
    ended in a fallback path, the *fallback* text is what future turns
    see in their conversation context, not the rejected LLM JSON.

    `cited_finding_ids` is canonicalised into the scan's finding order
    by the answerer before construction (same principle as
    RenderablePlan.steps' planner-order sort) — LLM emission order is
    not authoritative.
    """
    user_question: str
    assistant_answer: str
    cited_finding_ids: tuple[str, ...]
    turn_kind: str
    created_at: datetime


@dataclass
class ThreadContext:
    """Per-thread state the conversational layer reads on every reply.

    Mutable because `turns` grows over the thread's lifetime; the deque
    is bounded and clipped from the left when full so memory stays
    flat. The other fields are set once at construction.

    `created_at` lives on the wrapper, not on `ScanResult` /
    `PlanResult`, because both inner records flow through non-thread
    contexts (CLI, audit log) where a thread-creation timestamp would
    be meaningless. The wrapper is the only thread-bound concept.

    `source_plan_id` mirrors `plan_result.plan_id` when present and is
    the provenance anchor for future in-thread re-plan, audit
    continuity, and Slack-message references. Carried explicitly so a
    future PR can stage state where `plan_result` has been swapped out
    but the original plan's identity is still recoverable.
    """
    scan_result: "ScanResult"
    plan_result: "PlanResult | None"
    created_at: datetime
    source_plan_id: str | None = None
    turns: deque = field(default_factory=lambda: deque(maxlen=DEFAULT_TURN_RING_MAXLEN))

    def age_now(self, *, now: datetime | None = None) -> float:
        """Seconds since `created_at`. `now` is an injectable test seam;
        defaults to UTC wall clock."""
        ref = now if now is not None else datetime.now(timezone.utc)
        delta = ref - self.created_at
        return delta.total_seconds()

    def record_turn(self, turn: ConversationTurn) -> None:
        """Append a turn. Deque maxlen drops the oldest on overflow."""
        self.turns.append(turn)


def new_thread_context(
    scan_result: "ScanResult",
    *,
    plan_result: "PlanResult | None" = None,
    now: datetime | None = None,
    turn_ring_maxlen: int = DEFAULT_TURN_RING_MAXLEN,
) -> ThreadContext:
    """Construct a ThreadContext at the moment of thread creation.

    Centralises the `created_at = now()` decision and the
    `source_plan_id = plan.plan_id` derivation so handlers don't
    reimplement either.
    """
    return ThreadContext(
        scan_result=scan_result,
        plan_result=plan_result,
        created_at=now if now is not None else datetime.now(timezone.utc),
        source_plan_id=plan_result.plan_id if plan_result is not None else None,
        turns=deque(maxlen=turn_ring_maxlen),
    )
