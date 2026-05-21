"""
Planner output schemas — what the LLM is allowed to produce and what
the planner ultimately returns.

The LLM emits a JSON object the parser turns into raw `dict` step
records. The validators promote raw emissions to `PlanStep` (kept) or
`DroppedStep` (rejected, with a reason from `DropReason`). The final
`PlanResult` is what reaches the user.

Schema versioned per principle 8. Bump CURRENT_SCHEMA_VERSION when the
on-disk shape of PlanRecord changes — agent.schemas and
schemas.records move together.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


CURRENT_SCHEMA_VERSION = "1"

PlanStatus = Literal["ok", "validation_failed"]


class DropReason(str, Enum):
    """Why a raw emission failed validation. The set is closed so the
    eval rubric can assert against specific reasons."""
    UNKNOWN_FINDING_ID = "unknown_finding_id"
    UNSUPPORTED_MODE = "unsupported_mode"
    MONTHLY_IMPACT_MISMATCH = "monthly_impact_mismatch"
    MONTHLY_IMPACT_MISSING = "monthly_impact_missing"
    SCHEMA_INVALID = "schema_invalid"
    DUPLICATE_FINDING_ID = "duplicate_finding_id"


@dataclass
class PlanStep:
    """A validated step the planner is willing to surface to the user.

    `monthly_impact_usd` is the *canonical* value from the source Finding,
    not what the LLM emitted. Validators check the emission against the
    canonical value before this dataclass is constructed.
    """
    finding_id: str
    pattern_id: str
    suggested_mode: str       # RemediationMode value
    monthly_impact_usd: float
    rationale: str
    order_rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "pattern_id": self.pattern_id,
            "suggested_mode": self.suggested_mode,
            "monthly_impact_usd": round(self.monthly_impact_usd, 2),
            "rationale": self.rationale,
            "order_rank": self.order_rank,
        }


@dataclass
class DroppedStep:
    """A raw emission that failed validation. Kept in the trace so
    invention attempts are visible to evals."""
    raw_emission: dict[str, Any]
    reason: str               # DropReason value
    validator: str            # which validator function rejected it
    detail: str | None = None  # human-friendly extra context

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_emission": self.raw_emission,
            "reason": self.reason,
            "validator": self.validator,
            "detail": self.detail,
        }


@dataclass
class PlanResult:
    """The structured object the planner returns to the caller."""
    plan_id: str
    goal: str | None
    status: PlanStatus
    steps: list[PlanStep]
    dropped_steps: list[DroppedStep]
    total_monthly_impact_usd: float
    summary: str
    confidence: float
    # Trace fields — populated by the planner, never by the LLM.
    prompt_template: str
    prompt_template_version: str
    model: str
    provider: str
    boundary_crossed: bool
    parse_retry_count: int
    input_finding_ids: list[str]
    # Identity / lineage
    scan_id: str | None = None
    actor: str | None = None
    schema_version: str = CURRENT_SCHEMA_VERSION
    plan_id_factory: Any = field(default=None, repr=False)  # test seam, unused at runtime

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "schema_version": self.schema_version,
            "scan_id": self.scan_id,
            "goal": self.goal,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "dropped_steps": [d.to_dict() for d in self.dropped_steps],
            "total_monthly_impact_usd": round(self.total_monthly_impact_usd, 2),
            "summary": self.summary,
            "confidence": round(self.confidence, 3),
            "prompt_template": self.prompt_template,
            "prompt_template_version": self.prompt_template_version,
            "model": self.model,
            "provider": self.provider,
            "boundary_crossed": self.boundary_crossed,
            "parse_retry_count": self.parse_retry_count,
            "input_finding_ids": list(self.input_finding_ids),
            "actor": self.actor,
        }


def new_plan_id() -> str:
    return str(uuid.uuid4())
