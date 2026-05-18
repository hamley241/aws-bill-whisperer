"""
Base Pattern class - extend this to add new waste patterns.

The Finding dataclass below is the universal currency of the system
(see CLAUDE.md principle 2). Every component that produces or consumes
detection output speaks Finding objects.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


SCHEMA_VERSION = "1"


class RiskTier(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Complexity(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class Finding:
    """A single waste finding. See CLAUDE.md principle 2 for the schema contract."""

    # Required: caller must provide
    resource_id: str
    resource_type: str
    region: str
    monthly_impact_usd: float
    summary: str

    # Optional, populated lazily or by the producing pattern
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

    # Auto-populated
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


class BasePattern(ABC):
    """
    Base class for all waste detection patterns.

    To add a new pattern:
    1. Create a new file in src/patterns/ (e.g., my_pattern.py)
    2. Extend BasePattern
    3. Define PATTERN_ID, NAME, DESCRIPTION, COMPLEXITY
    4. Implement scan() method
    5. Optionally implement fix() method

    The pattern will be auto-discovered and included in scans.
    """

    # Override these in subclass
    PATTERN_ID: str = "000"
    NAME: str = "Base Pattern"
    DESCRIPTION: str = "Override this description"
    COMPLEXITY: Complexity = Complexity.EASY
    SERVICES: list[str] = []  # AWS services this pattern checks

    def __init__(self, session=None):
        """
        Initialize pattern with optional boto3 session.
        If no session provided, uses default credentials.
        """
        import boto3
        self.session = session or boto3.Session()
        self._findings: list[Finding] = []

    @abstractmethod
    def scan(self, regions: list[str] = None) -> list[Finding]:
        """
        Scan for this waste pattern.

        Args:
            regions: List of AWS regions to scan. None = all regions.

        Returns:
            List of Finding objects (each tagged with pattern_id).
        """
        pass

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        """
        Apply fix for a finding.

        Args:
            finding: The finding to fix
            dry_run: If True, only simulate the fix

        Returns:
            True if fix was applied/would be applied successfully
        """
        if not finding.safe_to_fix:
            raise ValueError(f"Finding {finding.resource_id} is not marked safe to fix")

        if dry_run:
            print(f"[DRY RUN] Would execute: {finding.fix_command}")
            return True

        # Override in subclass for actual fix implementation
        raise NotImplementedError("Fix not implemented for this pattern")

    def get_all_regions(self) -> list[str]:
        """Get all available AWS regions"""
        ec2 = self.session.client('ec2', region_name='us-east-1')
        regions = ec2.describe_regions()['Regions']
        return [r['RegionName'] for r in regions]

    @property
    def total_monthly_waste(self) -> float:
        """Sum of monthly costs from all findings"""
        return sum(f.monthly_impact_usd for f in self._findings)

    def __repr__(self):
        return f"<Pattern {self.PATTERN_ID}: {self.NAME}>"
