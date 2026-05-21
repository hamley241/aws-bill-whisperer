"""
Versioned record schemas — CLAUDE.md principle 8.

Every persisted record carries a schema_version. New versions migrate
forward via the migrators registered here. There is one place in the
codebase that owns the schema (this module), and every storage backend
reads/writes through it.

The OSS tier persists to SQLite at ~/.whisper/whisper.db. The paid
tier swaps in DynamoDB / Postgres behind the same shape.
"""

from .records import (
    CURRENT_SCHEMA_VERSION,
    FindingRecord,
    PlanRecord,
    PromptRecord,
    RemediationRecord,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "FindingRecord",
    "PlanRecord",
    "PromptRecord",
    "RemediationRecord",
]
