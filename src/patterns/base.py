"""
Base Pattern class — the universal interface every detection pattern
implements (CLAUDE.md principle 1).

Adding a new pattern is one new file in this directory. The file must:

- Subclass BasePattern.
- Set PATTERN_ID, NAME, DESCRIPTION, CATEGORY, COMPLEXITY, SERVICES,
  REQUIRED_IAM, SUPPORTED_REGIONS.
- Implement scan(regions) -> list[Finding].
- Optionally implement remediate(finding, mode) -> RemediationResult
  for whichever modes the pattern supports.

The Finding dataclass below is the universal currency of the system
(principle 2). Remediation goes through remediate(), one entry point
per pattern that dispatches on mode (principle 4).
"""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from schemas.records import ScanError


logger = logging.getLogger(__name__)


SCHEMA_VERSION = "1"


class RiskTier(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Complexity(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


# Pattern categories — used by specialist agents and Slack routing.
class Category(Enum):
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    DATABASE = "database"
    MONITORING = "monitoring"
    ML = "ml"
    SECURITY = "security"
    GENERAL = "general"


class RemediationMode(Enum):
    """Modes are orthogonal to OSS-vs-paid (principle 4).

    The OSS tier exposes all modes for single-account use. The paid
    tier orchestrates the same modes at scale.
    """
    DRY_RUN = "dry_run"      # log what would happen, change nothing
    COMMAND = "command"      # emit a shell command for manual execution
    PR = "pr"                # emit an IaC diff suitable for a PR
    API_CALL = "api_call"    # execute the AWS API call directly (audited)


@dataclass
class Finding:
    """A single waste finding. See CLAUDE.md principle 2 for the schema contract."""

    resource_id: str
    resource_type: str
    region: str
    monthly_impact_usd: float
    summary: str

    pattern_id: str = ""
    resource_arn: str | None = None
    account_id: str | None = None
    risk_tier: RiskTier = RiskTier.MEDIUM
    confidence: float = 0.8
    explanation: str | None = None
    fix_command: str | None = None
    fix_pr: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    safe_to_fix: bool = False

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "pattern_id": self.pattern_id,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "resource_arn": self.resource_arn,
            "account_id": self.account_id,
            "region": self.region,
            "monthly_impact_usd": round(self.monthly_impact_usd, 2),
            "risk_tier": self.risk_tier.value,
            "confidence": round(self.confidence, 3),
            "summary": self.summary,
            "explanation": self.explanation,
            "fix_command": self.fix_command,
            "fix_pr": self.fix_pr,
            "evidence": self.evidence,
            "metadata": self.metadata,
            "safe_to_fix": self.safe_to_fix,
        }


@dataclass
class RemediationResult:
    """The outcome of a remediate(finding, mode) call.

    Every persistence layer (audit log, Slack ack, CLI output) speaks
    this shape. Schema versioned per principle 8.
    """
    finding_id: str
    pattern_id: str
    mode: RemediationMode
    success: bool
    message: str
    output: str | None = None   # command text, PR diff, or API response repr
    evidence: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "finding_id": self.finding_id,
            "pattern_id": self.pattern_id,
            "mode": self.mode.value,
            "success": self.success,
            "message": self.message,
            "output": self.output,
            "evidence": self.evidence,
        }


class BasePattern(ABC):
    """
    Base class for all waste detection patterns.

    Subclass conventions:

      PATTERN_ID            unique three-digit ID (e.g. "001")
      NAME                  human-readable
      DESCRIPTION           one-sentence
      CATEGORY              Category enum value
      COMPLEXITY            Complexity enum value
      SERVICES              AWS service codes queried (e.g. ["ec2"])
      REQUIRED_IAM          list of IAM actions the scan needs
      SUPPORTED_REGIONS     Region scope for the coverage denominator,
                            read by run_across_regions():
                              None -> every requested region applies (the
                                default; no filtering, common case).
                              [..] -> narrows the requested regions to
                                effective_regions = [r for r in requested
                                                     if r in SUPPORTED_REGIONS].
                            Non-regional (global) patterns do not use the
                            region loop at all, so they need no sentinel.

    Subclasses must implement:
      scan(regions) -> list[Finding]

    Subclasses may override:
      remediate(finding, mode) -> RemediationResult
        The base implementation handles DRY_RUN and COMMAND off
        finding.fix_command and returns a "not supported" result for
        API_CALL and PR. Patterns add API_CALL by overriding.
    """

    # Override in subclass
    PATTERN_ID: str = "000"
    NAME: str = "Base Pattern"
    DESCRIPTION: str = "Override this description"
    CATEGORY: Category = Category.GENERAL
    COMPLEXITY: Complexity = Complexity.EASY
    SERVICES: list[str] = []
    REQUIRED_IAM: list[str] = []
    SUPPORTED_REGIONS: list[str] | None = None  # None = any region

    def __init__(self, session=None):
        import boto3
        self.session = session or boto3.Session()
        self._findings: list[Finding] = []
        # Every pattern carries scan_errors whether or not it uses the
        # base-owned region loop, so coverage is uniformly inspectable.
        self.scan_errors: list[ScanError] = []

    @abstractmethod
    def scan(self, regions: list[str] = None) -> list[Finding]:
        """Scan for this pattern. Tag every Finding with pattern_id."""

    # ------------------------------------------------------------------
    # Coverage-aware region loop (CLAUDE.md principle: failures are
    # recorded, not silently swallowed).
    # ------------------------------------------------------------------
    def _scan_region(self, region: str) -> list[Finding]:
        """Scan a single region, returning its findings.

        Extension point for per-region patterns: implement this and let
        run_across_regions() own the loop, the SUPPORTED_REGIONS filter,
        and the record-error-and-continue behaviour. Non-regional
        patterns don't use the loop and needn't implement it.
        """
        raise NotImplementedError(
            f"pattern {self.PATTERN_ID} does not implement _scan_region"
        )

    def run_across_regions(self, regions: list[str] | None) -> list[Finding]:
        """Run _scan_region across the effective regions, recording any
        per-region failure as a ScanError and continuing.

        Effective regions are `regions or self.get_all_regions()`, then
        narrowed by SUPPORTED_REGIONS (None = no narrowing). Control flow
        matches the hand-rolled loops it replaces: log with pattern id +
        region, record the error, continue.
        """
        requested = regions or self.get_all_regions()
        if self.SUPPORTED_REGIONS is None:
            effective_regions = list(requested)
        else:
            effective_regions = [
                r for r in requested if r in self.SUPPORTED_REGIONS
            ]

        # Reset per-scan state so a reused pattern instance reports only
        # the current scan's findings and failures, not stale ones.
        self._findings = []
        self.scan_errors = []
        for region in effective_regions:
            try:
                self._findings.extend(self._scan_region(region))
            except Exception as exc:
                logger.exception(
                    "p%s scan failed for region %s; continuing",
                    self.PATTERN_ID, region,
                )
                self._record_region_error(region, exc)
                continue
        return self._findings

    def _record_region_error(self, region: str | None, exc: Exception) -> None:
        """Append a ScanError for a failed region (None = global failure)."""
        self.scan_errors.append(
            ScanError(
                pattern_id=self.PATTERN_ID,
                region=region,
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        """Apply a fix in the requested mode.

        Default behaviour:
          DRY_RUN / COMMAND — derived from finding.fix_command.
          API_CALL / PR     — "not supported" result (override to support).
        """
        if mode == RemediationMode.COMMAND:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=finding.pattern_id or self.PATTERN_ID,
                mode=mode,
                success=bool(finding.fix_command),
                message=(
                    "emitted fix command"
                    if finding.fix_command
                    else "no fix command available for this finding"
                ),
                output=finding.fix_command,
            )
        if mode == RemediationMode.DRY_RUN:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=finding.pattern_id or self.PATTERN_ID,
                mode=mode,
                success=bool(finding.fix_command),
                message=(
                    f"would execute: {finding.fix_command}"
                    if finding.fix_command else "no fix command available"
                ),
                output=finding.fix_command,
            )
        if mode in (RemediationMode.API_CALL, RemediationMode.PR):
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=finding.pattern_id or self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"{mode.value} not supported by pattern {self.PATTERN_ID}",
            )
        raise ValueError(f"unknown remediation mode: {mode}")

    def get_all_regions(self) -> list[str]:
        ec2 = self.session.client('ec2', region_name='us-east-1')
        regions = ec2.describe_regions()['Regions']
        return [r['RegionName'] for r in regions]

    @property
    def total_monthly_waste(self) -> float:
        return sum(f.monthly_impact_usd for f in self._findings)

    def __repr__(self):
        return f"<Pattern {self.PATTERN_ID}: {self.NAME}>"
