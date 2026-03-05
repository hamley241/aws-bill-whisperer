"""
Base Pattern class - extend this to add new waste patterns
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Complexity(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class Finding:
    """A single waste finding"""
    resource_id: str
    resource_type: str
    region: str
    monthly_cost: float
    recommendation: str
    severity: Severity = Severity.MEDIUM
    metadata: dict = field(default_factory=dict)
    safe_to_fix: bool = False
    fix_command: str | None = None

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "region": self.region,
            "monthly_cost": round(self.monthly_cost, 2),
            "recommendation": self.recommendation,
            "severity": self.severity.value,
            "safe_to_fix": self.safe_to_fix,
            "fix_command": self.fix_command,
            "metadata": self.metadata,
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
            List of Finding objects
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
        return sum(f.monthly_cost for f in self._findings)

    def __repr__(self):
        return f"<Pattern {self.PATTERN_ID}: {self.NAME}>"
