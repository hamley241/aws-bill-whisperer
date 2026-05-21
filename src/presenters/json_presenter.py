"""JSON presenter — machine-readable output for piping and tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .base import FindingPresenter, ScanResult

if TYPE_CHECKING:
    from patterns.base import Finding


class JSONPresenter(FindingPresenter):
    """Serializes to canonical-name JSON. Stable across surfaces."""

    def __init__(self, *, indent: int | None = 2):
        self._indent = indent

    def to_dict(self, result: ScanResult) -> dict[str, Any]:
        return {
            "finding_count": result.finding_count,
            "total_monthly_impact_usd": round(result.total_monthly_impact_usd, 2),
            "annual_impact_usd": round(result.total_monthly_impact_usd * 12, 2),
            "analysis": result.analysis,
            "metadata": result.metadata,
            "findings": [f.to_dict() for f in result.sorted_by_impact()],
        }

    def render_finding(self, finding: "Finding", *, verbose: bool = False) -> str:
        # verbose is meaningless for JSON: Finding.to_dict() is already complete.
        del verbose
        return json.dumps(finding.to_dict(), indent=self._indent, default=str)

    def render_scan(self, result: ScanResult, *, verbose: bool = False) -> str:
        del verbose
        return json.dumps(self.to_dict(result), indent=self._indent, default=str)
