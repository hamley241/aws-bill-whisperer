"""
Build a `PlanRecord` from a `PlanResult` and write it through the
repository.

Pulled into its own module so the planner can stay focused on
"prompt → emissions → validation → PlanResult" and the persistence
side stays narrow.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from schemas import PlanRecord

if TYPE_CHECKING:
    from storage import WhisperRepository

    from .schemas import PlanResult


logger = logging.getLogger(__name__)


def build_plan_record(result: "PlanResult") -> PlanRecord:
    """Pure conversion. No I/O. Easy to unit-test."""
    return PlanRecord(
        id=result.plan_id,
        scan_id=result.scan_id,
        goal=result.goal,
        status=result.status,
        steps_json=json.dumps([s.to_dict() for s in result.steps],
                              ensure_ascii=False),
        dropped_steps_json=json.dumps([d.to_dict() for d in result.dropped_steps],
                                      ensure_ascii=False),
        total_monthly_impact_usd=result.total_monthly_impact_usd,
        summary=result.summary,
        confidence=result.confidence,
        prompt_template=result.prompt_template,
        prompt_template_version=result.prompt_template_version,
        model=result.model,
        provider=result.provider,
        boundary_crossed=result.boundary_crossed,
        parse_retry_count=result.parse_retry_count,
        input_finding_ids_json=json.dumps(list(result.input_finding_ids)),
        actor=result.actor,
    )


def write_plan(result: "PlanResult", repository: "WhisperRepository | None") -> None:
    """Best-effort persistence — log on failure, never raise into the
    planner. The PlanResult itself is the source of truth returned to
    the caller; the audit log is the durable copy.
    """
    if repository is None:
        return
    try:
        repository.record_plan(build_plan_record(result))
    except Exception:
        logger.exception("failed to persist PlanRecord %s", result.plan_id)
