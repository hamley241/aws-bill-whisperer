"""
Agent package — the LLM "proposes" layer of CLAUDE.md's
"LLM proposes; framework disposes" design rule.

PR 2 ships:
  SavingsPlanner   — one-shot planner over a fixed set of Findings.
  validators       — finding-ID, mode, and $ checks. Load-bearing safety
                     boundary. Every emission the LLM produces is gated
                     by these before it reaches the user.
  AvailableModesResolver — maps a Finding to the remediation modes its
                           pattern will actually accept. Today scoped to
                           p001; p006/p004 plug in later.
  rubric/runner    — evaluation harness with record/replay.

Explicitly not here yet: multi-turn loops, tool use, specialist
sub-agents, remediation execution. See CLAUDE.md.
"""

from .planner import SavingsPlanner
from .schemas import (
    CURRENT_SCHEMA_VERSION,
    DropReason,
    DroppedStep,
    PlanResult,
    PlanStep,
    PlanStatus,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DropReason",
    "DroppedStep",
    "PlanResult",
    "PlanStatus",
    "PlanStep",
    "SavingsPlanner",
]
