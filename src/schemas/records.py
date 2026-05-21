"""
Persisted record shapes.

These dataclasses are the on-disk representation. They mirror the
in-memory Finding / RemediationResult types but flatten enums to
strings (so SQLite + JSON both work) and add timestamp / scan_id /
audit fields.

`schema_version` is enforced — every record carries it. Loaders apply
migrators (registered below) if they encounter an older version.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


CURRENT_SCHEMA_VERSION = "1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FindingRecord:
    """A finding as it was observed at scan time."""
    id: str
    pattern_id: str
    resource_id: str
    resource_type: str
    resource_arn: str | None
    account_id: str | None
    region: str
    monthly_impact_usd: float
    risk_tier: str          # "low" | "medium" | "high"
    confidence: float
    summary: str
    explanation: str | None
    fix_command: str | None
    fix_pr: str | None
    safe_to_fix: bool
    evidence: dict[str, Any]
    metadata: dict[str, Any]
    scan_id: str            # groups findings from one scan
    observed_at: str = field(default_factory=_now)
    schema_version: str = CURRENT_SCHEMA_VERSION


@dataclass
class RemediationRecord:
    """An audit-log entry: one row per remediate() call."""
    id: str
    finding_id: str
    pattern_id: str
    mode: str               # RemediationMode value: "dry_run" | … | "api_call"
    success: bool
    message: str
    output: str | None
    actor: str | None       # user_id (Slack), CLI invoker, "lambda", …
    evidence: dict[str, Any] = field(default_factory=dict)
    attempted_at: str = field(default_factory=_now)
    schema_version: str = CURRENT_SCHEMA_VERSION


@dataclass
class PromptRecord:
    """LLM prompt+response (mirrors llm.PromptLogRecord, persisted here for
    long-term audit when the customer wants it in their database rather
    than just the JSONL file)."""
    id: str
    provider: str
    model: str
    boundary_crossed: bool
    prompt_template: str | None
    messages_json: str          # JSON-serialised list[{role, content}]
    response_text: str
    input_tokens: int | None
    output_tokens: int | None
    sent_at: str = field(default_factory=_now)
    schema_version: str = CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------

Migrator = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATORS: dict[tuple[str, str, str], Migrator] = {}


def register_migrator(record_type: str, from_version: str, to_version: str
                      ) -> Callable[[Migrator], Migrator]:
    """Register a migrator from `from_version` to `to_version`."""

    def decorator(fn: Migrator) -> Migrator:
        _MIGRATORS[(record_type, from_version, to_version)] = fn
        return fn
    return decorator


def migrate(record_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Apply any registered migrators until raw[schema_version] == current."""
    version = raw.get("schema_version", "0")
    while version != CURRENT_SCHEMA_VERSION:
        next_version_pairs = [
            (frm, to) for (rt, frm, to) in _MIGRATORS
            if rt == record_type and frm == version
        ]
        if not next_version_pairs:
            raise ValueError(
                f"no migrator from schema {version!r} for record type {record_type!r}"
            )
        frm, to = next_version_pairs[0]
        raw = _MIGRATORS[(record_type, frm, to)](dict(raw))
        raw["schema_version"] = to
        version = to
    return raw


def to_dict(record: Any) -> dict[str, Any]:
    """Coerce a record dataclass to a plain dict. Dicts pass through unchanged."""
    if isinstance(record, dict):
        return dict(record)
    return asdict(record)


def dumps_messages(messages: list[dict[str, Any]]) -> str:
    """Stable JSON for PromptRecord.messages_json."""
    return json.dumps(messages, ensure_ascii=False, sort_keys=False)


def loads_messages(blob: str) -> list[dict[str, Any]]:
    return json.loads(blob)
