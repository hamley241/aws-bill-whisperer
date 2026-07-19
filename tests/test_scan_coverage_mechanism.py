"""
Guard tests for the scan-coverage mechanism (stage 1).

These prove the base-owned region loop: failures are recorded as
ScanError and the scan continues; SUPPORTED_REGIONS narrows the region
scope; and the migration debt (patterns not yet on run_across_regions)
is explicit and can only shrink.
"""
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns import discover_patterns
from patterns.base import BasePattern, Category, Finding
from schemas.records import ScanError, SCAN_ERROR_MESSAGE_CAP


def _finding(region: str) -> Finding:
    return Finding(
        resource_id=f"r-{region}",
        resource_type="Test",
        region=region,
        monthly_impact_usd=1.0,
        summary=f"finding in {region}",
    )


class _StubPattern(BasePattern):
    """Minimal pattern that delegates scan() to the base loop and lets a
    test control _scan_region behaviour."""

    PATTERN_ID = "999"
    NAME = "Stub"
    CATEGORY = Category.GENERAL

    def __init__(self, scan_region_fn):
        super().__init__(session=MagicMock())
        self._scan_region_fn = scan_region_fn
        self.calls: list[str] = []

    def scan(self, regions=None):
        return self.run_across_regions(regions)

    def _scan_region(self, region):
        self.calls.append(region)
        return self._scan_region_fn(region)


# ---------------------------------------------------------------------------
# 1. Errors are recorded; other regions still scan.
# ---------------------------------------------------------------------------
def test_region_error_recorded_and_scan_continues():
    def scan_region(region):
        if region == "eu-west-1":
            raise ValueError("boom in eu-west-1")
        return [_finding(region)]

    pattern = _StubPattern(scan_region)

    findings = pattern.scan(regions=["us-east-1", "eu-west-1", "us-west-2"])

    # The two healthy regions still return their findings.
    assert {f.region for f in findings} == {"us-east-1", "us-west-2"}

    # Exactly one error recorded, with the right shape.
    assert len(pattern.scan_errors) == 1
    err = pattern.scan_errors[0]
    assert isinstance(err, ScanError)
    assert err.pattern_id == "999"
    assert err.region == "eu-west-1"
    assert err.error_type == "ValueError"
    assert "boom" in err.message


def test_scan_error_message_is_capped():
    long = "x" * (SCAN_ERROR_MESSAGE_CAP + 100)
    err = ScanError(pattern_id="999", region="us-east-1",
                    error_type="ValueError", message=long)
    assert len(err.message) == SCAN_ERROR_MESSAGE_CAP


def test_global_failure_recordable_with_region_none():
    # region=None is the documented shape for a non-regional failure.
    err = ScanError(pattern_id="008", region=None,
                    error_type="ClientError", message="list_buckets failed")
    assert err.region is None


# ---------------------------------------------------------------------------
# 2. SUPPORTED_REGIONS filters the region scope both ways.
# ---------------------------------------------------------------------------
def test_supported_regions_narrows_scope():
    pattern = _StubPattern(lambda r: [_finding(r)])
    pattern.SUPPORTED_REGIONS = ["us-east-1"]

    pattern.scan(regions=["us-east-1", "eu-west-1", "us-west-2"])

    assert pattern.calls == ["us-east-1"]


def test_supported_regions_none_scans_all():
    pattern = _StubPattern(lambda r: [_finding(r)])
    assert pattern.SUPPORTED_REGIONS is None

    pattern.scan(regions=["us-east-1", "eu-west-1", "us-west-2"])

    assert pattern.calls == ["us-east-1", "eu-west-1", "us-west-2"]


# ---------------------------------------------------------------------------
# 3. Migration debt is explicit and can only shrink.
# ---------------------------------------------------------------------------
#
# TEMPORARY migration debt: patterns whose scan() still holds the region
# loop INLINE rather than delegating to BasePattern.run_across_regions.
# This list MUST reach empty as the remaining patterns migrate in later
# batches. It is NOT a permanent exemption list — a pattern that migrates
# must be removed from here, or the shrink assertion below fails.
NOT_YET_MIGRATED = {
    "002", "003", "004", "005", "007", "008", "009", "010", "011",
    "012", "013", "014", "015", "016", "017", "018", "019", "020",
}


def _is_migrated(pattern_cls) -> bool:
    """A pattern is migrated when its scan() delegates to the base loop."""
    try:
        source = inspect.getsource(pattern_cls.scan)
    except (OSError, TypeError):
        return False
    return "run_across_regions" in source


def test_migration_debt_is_explicit_and_shrinking():
    patterns = {p.PATTERN_ID: p for p in discover_patterns()}

    migrated = {pid for pid, cls in patterns.items() if _is_migrated(cls)}
    not_migrated = {pid for pid in patterns if pid not in migrated}

    # Every discovered pattern is accounted for: either migrated, or on
    # the explicit debt list — never silently unclassified.
    assert not_migrated == NOT_YET_MIGRATED, (
        "A pattern's migration status changed. If you migrated a pattern to "
        "run_across_regions, remove its ID from NOT_YET_MIGRATED. If you "
        "added a new pattern, migrate it or add it to the list.\n"
        f"  still inline (detected): {sorted(not_migrated)}\n"
        f"  NOT_YET_MIGRATED (list): {sorted(NOT_YET_MIGRATED)}"
    )

    # A migrated pattern must not linger on the debt list.
    assert migrated.isdisjoint(NOT_YET_MIGRATED)

    # The two patterns this stage migrates are proven migrated.
    assert {"001", "006"} <= migrated
