"""
audit_remediation — the single call site that bridges remediate() and the
audit log. Every surface (CLI, Slack, scheduled jobs) goes through here so
the audit log is the complete history of remediations attempted.

Per CLAUDE.md principle 4 (one entry point per pattern) + principle 8
(state owned by customer): the pattern owns the remediation; the
repository owns the persistence; this helper wires them together with a
consistent actor/timestamp/error contract.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SRC = Path(__file__).parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if TYPE_CHECKING:
    from patterns.base import BasePattern, Finding, RemediationMode, RemediationResult
    from storage import WhisperRepository


logger = logging.getLogger(__name__)


def audit_remediation(
    pattern: "BasePattern",
    finding: "Finding",
    mode: "RemediationMode",
    *,
    actor: str | None = None,
    repository: "WhisperRepository | None" = None,
) -> "RemediationResult":
    """Run pattern.remediate(finding, mode) and record the result.

    The repository write is best-effort — if it fails we still return
    the RemediationResult so the calling surface can show the user what
    happened. The logger surfaces persistence failures loudly.
    """
    result = pattern.remediate(finding, mode)

    try:
        repo = repository
        if repo is None:
            from storage import default_repository
            repo = default_repository()
        repo.record_remediation(result, actor=actor)
    except Exception as e:
        logger.exception(
            "audit log write failed (actor=%s, pattern=%s, mode=%s): %s",
            actor, pattern.PATTERN_ID, mode.value, e,
        )

    return result
