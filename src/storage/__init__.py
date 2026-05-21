"""
Customer-owned persistence — CLAUDE.md principle 8.

We define the schema (in src/schemas/), customers hold the bytes. The
OSS default is SQLite at ~/.whisper/whisper.db. Repositories speak the
versioned record types from src/schemas; the underlying backend can
swap to DynamoDB / Postgres without any caller changes.
"""

from .repository import (
    WhisperRepository,
    default_repository,
    set_default_repository,
)
from .sqlite_backend import SqliteBackend, default_db_path

__all__ = [
    "WhisperRepository",
    "SqliteBackend",
    "default_db_path",
    "default_repository",
    "set_default_repository",
]
