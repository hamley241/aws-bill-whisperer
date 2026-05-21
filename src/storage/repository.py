"""
WhisperRepository — the only persistence surface app code touches.

Speaks `FindingRecord` / `RemediationRecord` / `PromptRecord` from
schemas/. Routes reads through schemas.migrate() so older rows
upgrade transparently.

Module-level default repository for "fire and forget" audit calls
from the Slack handler etc.; tests override via set_default_repository().
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from schemas import (
    FindingRecord,
    PromptRecord,
    RemediationRecord,
)
from schemas.records import migrate

from .sqlite_backend import SqliteBackend, default_db_path

if TYPE_CHECKING:
    from patterns.base import Finding, RemediationResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WhisperRepository:
    """One per process. Thread-safe (the SQLite backend serializes writes)."""

    def __init__(self, backend: SqliteBackend | None = None):
        self.backend = backend or SqliteBackend()

    # ----- findings -----
    def record_finding(self, finding: "Finding", *, scan_id: str) -> FindingRecord:
        rec = FindingRecord(
            id=finding.id,
            pattern_id=finding.pattern_id,
            resource_id=finding.resource_id,
            resource_type=finding.resource_type,
            resource_arn=finding.resource_arn,
            account_id=finding.account_id,
            region=finding.region,
            monthly_impact_usd=finding.monthly_impact_usd,
            risk_tier=finding.risk_tier.value,
            confidence=finding.confidence,
            summary=finding.summary,
            explanation=finding.explanation,
            fix_command=finding.fix_command,
            fix_pr=finding.fix_pr,
            safe_to_fix=finding.safe_to_fix,
            evidence=finding.evidence,
            metadata=finding.metadata,
            scan_id=scan_id,
        )
        self.backend.insert_finding(asdict(rec))
        return rec

    def list_findings(self, *, scan_id: str | None = None) -> list[FindingRecord]:
        rows = self.backend.list_findings(scan_id=scan_id)
        return [FindingRecord(**migrate("finding", row)) for row in rows]

    # ----- remediations / audit log -----
    def record_remediation(
        self,
        result: "RemediationResult",
        *,
        actor: str | None = None,
    ) -> RemediationRecord:
        rec = RemediationRecord(
            id=result.id,
            finding_id=result.finding_id,
            pattern_id=result.pattern_id,
            mode=result.mode.value,
            success=result.success,
            message=result.message,
            output=result.output,
            actor=actor,
            evidence=result.evidence,
        )
        self.backend.insert_remediation(asdict(rec))
        return rec

    def list_remediations(self, *, finding_id: str | None = None
                          ) -> list[RemediationRecord]:
        rows = self.backend.list_remediations(finding_id=finding_id)
        return [RemediationRecord(**migrate("remediation", row)) for row in rows]

    # ----- prompts (optional mirror of the JSONL log) -----
    def record_prompt(
        self,
        *,
        provider: str,
        model: str,
        boundary_crossed: bool,
        prompt_template: str | None,
        messages: list[dict],
        response_text: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> PromptRecord:
        import json
        rec = PromptRecord(
            id=str(uuid.uuid4()),
            provider=provider,
            model=model,
            boundary_crossed=boundary_crossed,
            prompt_template=prompt_template,
            messages_json=json.dumps(messages, ensure_ascii=False),
            response_text=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self.backend.insert_prompt(asdict(rec))
        return rec

    def list_prompts(self) -> list[PromptRecord]:
        rows = self.backend.list_prompts()
        return [PromptRecord(**migrate("prompt", row)) for row in rows]

    # ----- bulk import (used by the scanner to atomically save a scan) -----
    def record_scan(self, findings: Iterable["Finding"], *, scan_id: str
                    ) -> list[FindingRecord]:
        return [self.record_finding(f, scan_id=scan_id) for f in findings]


# ---------------------------------------------------------------------------
# Module-level default — handler code does
#   `from storage import default_repository`
# and tests override with set_default_repository(MagicMock()).
# ---------------------------------------------------------------------------
_default: WhisperRepository | None = None


def default_repository() -> WhisperRepository:
    global _default
    if _default is None:
        _default = WhisperRepository()
    return _default


def set_default_repository(repo: WhisperRepository | None) -> None:
    """Inject a test repo; pass None to reset."""
    global _default
    _default = repo
