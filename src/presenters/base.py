"""
FindingPresenter — the interface every output surface implements.

Principle 3: Slack, CLI, web UI, and the local dashboard are
presentation layers over the same Finding stream. A new surface
(Teams, VSCode extension, …) must be implementable by writing only a
presenter — never by forking detection or remediation logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patterns.base import Finding


@dataclass
class ScanResult:
    """The aggregate a presenter renders. Built once, fed to many surfaces."""

    findings: list["Finding"]
    total_monthly_impact_usd: float
    finding_count: int
    # Optional narrative (LLM-generated summary). Surfaces decide whether
    # to show it; it never replaces the per-finding rendering.
    analysis: str | None = None
    # Optional metadata (account_id, scan duration, region list, etc).
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_findings(
        cls,
        findings: list["Finding"],
        *,
        analysis: str | None = None,
        metadata: dict | None = None,
    ) -> "ScanResult":
        return cls(
            findings=list(findings),
            total_monthly_impact_usd=sum(f.monthly_impact_usd for f in findings),
            finding_count=len(findings),
            analysis=analysis,
            metadata=metadata or {},
        )

    def sorted_by_impact(self) -> list["Finding"]:
        return sorted(self.findings, key=lambda f: f.monthly_impact_usd, reverse=True)

    def by_pattern(self) -> dict[str, list["Finding"]]:
        grouped: dict[str, list["Finding"]] = {}
        for f in self.findings:
            grouped.setdefault(f.pattern_id or "unknown", []).append(f)
        return grouped


class FindingPresenter(ABC):
    """One presenter per output surface. Stateless by design."""

    @abstractmethod
    def render_finding(self, finding: "Finding", *, verbose: bool = False) -> str:
        """Render a single finding (one line / one card / one block)."""

    @abstractmethod
    def render_scan(self, result: ScanResult, *, verbose: bool = False) -> str:
        """Render a complete scan: header + each finding + footer."""
