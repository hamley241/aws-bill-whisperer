"""
SQLite backend for the WhisperRepository.

Tables track findings (one row per finding per scan), remediations
(one row per remediate() call), and prompts (optional mirror of the
JSONL log when the customer wants queryable history). Every row keeps
its schema_version; reads route through schemas.migrate().
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator


DEFAULT_DB_PATH = Path("~/.whisper/whisper.db").expanduser()


def default_db_path() -> Path:
    return DEFAULT_DB_PATH


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS findings (
    id              TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL,
    scan_id         TEXT NOT NULL,
    pattern_id      TEXT NOT NULL,
    resource_id     TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    resource_arn    TEXT,
    account_id      TEXT,
    region          TEXT NOT NULL,
    monthly_impact_usd REAL NOT NULL,
    risk_tier       TEXT NOT NULL,
    confidence      REAL NOT NULL,
    summary         TEXT NOT NULL,
    explanation     TEXT,
    fix_command     TEXT,
    fix_pr          TEXT,
    safe_to_fix     INTEGER NOT NULL,
    evidence_json   TEXT NOT NULL,
    metadata_json   TEXT NOT NULL,
    observed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_scan_id_idx ON findings(scan_id);
CREATE INDEX IF NOT EXISTS findings_pattern_id_idx ON findings(pattern_id);

CREATE TABLE IF NOT EXISTS remediations (
    id              TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL,
    finding_id      TEXT NOT NULL,
    pattern_id      TEXT NOT NULL,
    mode            TEXT NOT NULL,
    success         INTEGER NOT NULL,
    message         TEXT NOT NULL,
    output          TEXT,
    actor           TEXT,
    evidence_json   TEXT NOT NULL,
    attempted_at    TEXT NOT NULL
    -- intentionally no FK on finding_id: the audit log is append-only and
    -- must survive even if a finding row is later purged.
);
CREATE INDEX IF NOT EXISTS remediations_finding_id_idx ON remediations(finding_id);
CREATE INDEX IF NOT EXISTS remediations_attempted_at_idx ON remediations(attempted_at);

CREATE TABLE IF NOT EXISTS prompts (
    id              TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    boundary_crossed INTEGER NOT NULL,
    prompt_template TEXT,
    messages_json   TEXT NOT NULL,
    response_text   TEXT NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    sent_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prompts_sent_at_idx ON prompts(sent_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SqliteBackend:
    """Thin SQLite wrapper. One file, one process; we serialize writes."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
            finally:
                conn.close()

    # ----- findings -----
    def insert_finding(self, row: dict) -> None:
        with self._conn() as conn:
            conn.execute(_FINDING_INSERT_SQL, _finding_params(row))

    def get_finding(self, finding_id: str) -> dict | None:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM findings WHERE id = ?", (finding_id,)
            )
            row = cur.fetchone()
            return _finding_row_to_dict(row) if row else None

    def list_findings(self, *, scan_id: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if scan_id is None:
                cur = conn.execute("SELECT * FROM findings ORDER BY observed_at DESC")
            else:
                cur = conn.execute(
                    "SELECT * FROM findings WHERE scan_id = ? ORDER BY observed_at",
                    (scan_id,),
                )
            return [_finding_row_to_dict(r) for r in cur.fetchall()]

    # ----- remediations -----
    def insert_remediation(self, row: dict) -> None:
        with self._conn() as conn:
            conn.execute(_REMEDIATION_INSERT_SQL, _remediation_params(row))

    def list_remediations(self, *, finding_id: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if finding_id is None:
                cur = conn.execute(
                    "SELECT * FROM remediations ORDER BY attempted_at DESC"
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM remediations WHERE finding_id = ? ORDER BY attempted_at",
                    (finding_id,),
                )
            return [_remediation_row_to_dict(r) for r in cur.fetchall()]

    # ----- prompts -----
    def insert_prompt(self, row: dict) -> None:
        with self._conn() as conn:
            conn.execute(_PROMPT_INSERT_SQL, _prompt_params(row))

    def list_prompts(self) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM prompts ORDER BY sent_at DESC")
            return [_prompt_row_to_dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Row <-> dict translators (kept here so the schema SQL is the only place
# that knows column names)
# ---------------------------------------------------------------------------

_FINDING_INSERT_SQL = """
INSERT INTO findings (
    id, schema_version, scan_id, pattern_id, resource_id, resource_type,
    resource_arn, account_id, region, monthly_impact_usd, risk_tier,
    confidence, summary, explanation, fix_command, fix_pr, safe_to_fix,
    evidence_json, metadata_json, observed_at
) VALUES (
    :id, :schema_version, :scan_id, :pattern_id, :resource_id, :resource_type,
    :resource_arn, :account_id, :region, :monthly_impact_usd, :risk_tier,
    :confidence, :summary, :explanation, :fix_command, :fix_pr, :safe_to_fix,
    :evidence_json, :metadata_json, :observed_at
)
"""


def _finding_params(row: dict) -> dict:
    return {
        **row,
        "safe_to_fix": int(bool(row["safe_to_fix"])),
        "evidence_json": json.dumps(row.get("evidence") or {}, default=str),
        "metadata_json": json.dumps(row.get("metadata") or {}, default=str),
    }


def _finding_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["safe_to_fix"] = bool(d["safe_to_fix"])
    d["evidence"] = json.loads(d.pop("evidence_json"))
    d["metadata"] = json.loads(d.pop("metadata_json"))
    return d


_REMEDIATION_INSERT_SQL = """
INSERT INTO remediations (
    id, schema_version, finding_id, pattern_id, mode, success, message,
    output, actor, evidence_json, attempted_at
) VALUES (
    :id, :schema_version, :finding_id, :pattern_id, :mode, :success, :message,
    :output, :actor, :evidence_json, :attempted_at
)
"""


def _remediation_params(row: dict) -> dict:
    return {
        **row,
        "success": int(bool(row["success"])),
        "evidence_json": json.dumps(row.get("evidence") or {}, default=str),
    }


def _remediation_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["success"] = bool(d["success"])
    d["evidence"] = json.loads(d.pop("evidence_json"))
    return d


_PROMPT_INSERT_SQL = """
INSERT INTO prompts (
    id, schema_version, provider, model, boundary_crossed, prompt_template,
    messages_json, response_text, input_tokens, output_tokens, sent_at
) VALUES (
    :id, :schema_version, :provider, :model, :boundary_crossed, :prompt_template,
    :messages_json, :response_text, :input_tokens, :output_tokens, :sent_at
)
"""


def _prompt_params(row: dict) -> dict:
    return {
        **row,
        "boundary_crossed": int(bool(row["boundary_crossed"])),
    }


def _prompt_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["boundary_crossed"] = bool(d["boundary_crossed"])
    return d
