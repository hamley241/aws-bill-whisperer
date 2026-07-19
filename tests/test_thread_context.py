"""
Unit tests for ThreadContext and ConversationTurn (PR #9).

These types are the per-thread state wrapper consumed by both the
analyzer layer (plan-thread Q&A) and the slack handler layer (which
holds the in-memory ThreadContextStore). The tests pin:

  - wrapper construction defaults (created_at = now, source_plan_id
    mirrors plan_result.plan_id when present),
  - turn ring is bounded (DEFAULT_TURN_RING_MAXLEN = 6, configurable),
  - age_now() is monotonic and injectable for frozen-time tests,
  - ConversationTurn is immutable (frozen dataclass).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from analyzer.thread_context import (
    DEFAULT_TURN_RING_MAXLEN,
    ConversationTurn,
    ThreadContext,
    new_thread_context,
)
from patterns.base import Finding, RiskTier
from presenters import ScanResult


def _finding(monthly_impact_usd: float = 100.0, fid: str = "f-1") -> Finding:
    return Finding(
        id=fid,
        resource_id="vol-x",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=monthly_impact_usd,
        summary="x",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
    )


def _scan() -> ScanResult:
    return ScanResult.from_findings([_finding()])


class _StubPlan:
    """Minimal duck-typed stand-in for PlanResult — only `plan_id`
    is read by `new_thread_context`."""
    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id


class TestNewThreadContext:
    def test_scan_only_thread_has_plan_result_none(self):
        ctx = new_thread_context(_scan())
        assert ctx.plan_result is None
        assert ctx.source_plan_id is None

    def test_plan_thread_mirrors_plan_id_to_source_plan_id(self):
        plan = _StubPlan("abc-1234")
        ctx = new_thread_context(_scan(), plan_result=plan)
        assert ctx.plan_result is plan
        assert ctx.source_plan_id == "abc-1234"

    def test_created_at_defaults_to_now_utc(self):
        before = datetime.now(timezone.utc)
        ctx = new_thread_context(_scan())
        after = datetime.now(timezone.utc)
        assert before <= ctx.created_at <= after
        assert ctx.created_at.tzinfo is not None

    def test_created_at_injectable_for_frozen_time(self):
        frozen = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ctx = new_thread_context(_scan(), now=frozen)
        assert ctx.created_at == frozen

    def test_turn_ring_defaults_to_maxlen_six(self):
        ctx = new_thread_context(_scan())
        assert ctx.turns.maxlen == DEFAULT_TURN_RING_MAXLEN == 6

    def test_turn_ring_maxlen_configurable(self):
        ctx = new_thread_context(_scan(), turn_ring_maxlen=2)
        assert ctx.turns.maxlen == 2


class TestThreadContextAge:
    def test_age_zero_at_creation(self):
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
        ctx = new_thread_context(_scan(), now=now)
        assert ctx.age_now(now=now) == 0.0

    def test_age_grows_monotonically(self):
        start = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
        ctx = new_thread_context(_scan(), now=start)
        assert ctx.age_now(now=start + timedelta(minutes=15)) == 900.0
        assert ctx.age_now(now=start + timedelta(hours=3)) == 10800.0


class TestTurnRing:
    def _turn(self, q: str, *, ts: datetime) -> ConversationTurn:
        return ConversationTurn(
            user_question=q,
            assistant_answer=f"answer-to-{q}",
            cited_finding_ids=(),
            turn_kind="answered",
            created_at=ts,
        )

    def test_record_turn_appends(self):
        ctx = new_thread_context(_scan())
        ts = datetime.now(timezone.utc)
        ctx.record_turn(self._turn("q1", ts=ts))
        assert len(ctx.turns) == 1
        assert ctx.turns[0].user_question == "q1"

    def test_ring_drops_oldest_when_full(self):
        ctx = new_thread_context(_scan(), turn_ring_maxlen=3)
        ts = datetime.now(timezone.utc)
        for i in range(5):
            ctx.record_turn(self._turn(f"q{i}", ts=ts))
        questions = [t.user_question for t in ctx.turns]
        # Five appended, ring size 3 → newest three kept.
        assert questions == ["q2", "q3", "q4"]


class TestConversationTurnImmutability:
    def test_frozen_dataclass(self):
        ts = datetime.now(timezone.utc)
        turn = ConversationTurn(
            user_question="why?",
            assistant_answer="because",
            cited_finding_ids=("f-1",),
            turn_kind="answered",
            created_at=ts,
        )
        with pytest.raises(Exception):
            turn.user_question = "other"  # type: ignore[misc]

    def test_cited_finding_ids_is_tuple(self):
        """Tuple, not list — frozen dataclass equality / hashability."""
        ts = datetime.now(timezone.utc)
        turn = ConversationTurn(
            user_question="q", assistant_answer="a",
            cited_finding_ids=("f-1", "f-2"),
            turn_kind="answered", created_at=ts,
        )
        assert isinstance(turn.cited_finding_ids, tuple)
