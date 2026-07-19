"""
Guard tests for the scan-coverage mechanism (stage 1).

These prove the base-owned region loop: failures are recorded as
ScanError and the scan continues; SUPPORTED_REGIONS narrows the region
scope; and the migration debt (patterns not yet on run_across_regions)
is explicit and can only shrink.
"""
import ast
import inspect
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
# 3. Migration debt is explicit — and it is TWO categories, different in kind.
# ---------------------------------------------------------------------------
#
# TEMPORARY debt: REGIONAL patterns whose scan() still holds the region loop
# INLINE rather than delegating to BasePattern.run_across_regions. These can
# and MUST migrate; this set must reach empty as later batches land. It is
# NOT a permanent exemption list — a migrated pattern removed from here, or a
# new inline pattern not added, fails the classification assertion below.
NOT_YET_MIGRATED = {
    "002", "003", "004", "005", "007", "009", "010", "011",
    "012", "013", "014", "015", "016", "017", "018", "019", "020",
}

# PERMANENT and principled — NOT debt: patterns that legitimately do not scan
# per-region and never will, so the _scan_region template does not apply.
# p008 makes ONE global list_buckets() call and derives each bucket's region
# from the response; forcing it through the per-region loop would duplicate
# buckets or invent fake region labels. These report coverage failures as a
# global ScanError(region=None), not through run_across_regions.
#
# Membership here is a DESIGN DECISION that needs review — adding a pattern is
# a deliberate act, not a place to park a regional pattern to dodge migration.
# The structural guardrail below (test_non_regional_must_not_define_scan_region)
# is a guardrail against that misuse, not a proof of non-regionality: a
# genuinely non-regional pattern has no per-region entry point (p008 has none),
# while a regional one parked here almost certainly defines _scan_region, or
# will the moment someone tries to make it work.
NON_REGIONAL = {
    "008",
}


def _is_migrated(pattern_cls) -> bool:
    """A pattern is migrated when its scan() DELEGATES to
    run_across_regions(...) as a real, top-level statement of its body —
    not merely mentions the name and not in an unreachable branch.

    The delegation must be one of scan()'s own top-level body statements:
      - a `return run_across_regions(...)`, or
      - the call as a bare expression statement.
    The receiver may be `self` or `super()` — a subclass that overrides
    scan() only to add setup and then does `super().run_across_regions(...)`
    is just as migrated as one calling `self.run_across_regions(...)`.

    Two failure directions are guarded deliberately, and both were real
    findings:

      FALSE POSITIVE — ast.walk() finds a Call node regardless of
      reachability, so `if False: self.run_across_regions(regions)` looked
      migrated. Requiring a TOP-LEVEL statement (not any node anywhere in
      the tree) closes the dead-branch half.

      FALSE NEGATIVE — matching only `self.run_across_regions` rejected a
      legitimate `super().run_across_regions(...)`. Accepting the super()
      receiver closes it.

    Approximate (substring / walk-anywhere) evidence is exactly what lets
    drift persist — the same lesson the SERVICES and logging guards encode.
    """
    try:
        source = textwrap.dedent(inspect.getsource(pattern_cls.scan))
    except (OSError, TypeError):
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    func = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "scan"),
        None,
    )
    if func is None:
        return False

    def _is_delegation_call(call: ast.expr) -> bool:
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            return False
        if call.func.attr != "run_across_regions":
            return False
        recv = call.func.value
        # receiver is `self`
        if isinstance(recv, ast.Name) and recv.id == "self":
            return True
        # receiver is `super()`
        if (
            isinstance(recv, ast.Call)
            and isinstance(recv.func, ast.Name)
            and recv.func.id == "super"
        ):
            return True
        return False

    for stmt in func.body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            if _is_delegation_call(stmt.value):
                return True
        elif isinstance(stmt, ast.Expr):
            if _is_delegation_call(stmt.value):
                return True
    return False


def test_every_pattern_in_exactly_one_category():
    patterns = {p.PATTERN_ID: p for p in discover_patterns()}

    migrated = {pid for pid, cls in patterns.items() if _is_migrated(cls)}

    # Every discovered pattern lands in exactly one of the three categories:
    # migrated, NOT_YET_MIGRATED (temporary), or NON_REGIONAL (permanent).
    for pid in patterns:
        memberships = [
            name for name, group in (
                ("migrated", migrated),
                ("NOT_YET_MIGRATED", NOT_YET_MIGRATED),
                ("NON_REGIONAL", NON_REGIONAL),
            ) if pid in group
        ]
        assert len(memberships) == 1, (
            f"pattern {pid} must be in exactly one category, is in: "
            f"{memberships or ['none']}. If you migrated it to "
            "run_across_regions, remove it from NOT_YET_MIGRATED. If it is a "
            "new pattern, migrate it or classify it."
        )

    # The debt list can only shrink: a migrated pattern must not linger on it.
    assert migrated.isdisjoint(NOT_YET_MIGRATED)

    # p008 is permanent-non-regional, not temporary debt.
    assert "008" in NON_REGIONAL
    assert "008" not in NOT_YET_MIGRATED

    # The two patterns this stage migrates are proven migrated.
    assert {"001", "006"} <= migrated


def test_is_migrated_not_fooled_by_a_mention():
    # scan() only MENTIONS run_across_regions in a comment and a string
    # literal; it never calls it. Must be reported NOT migrated.
    class _MentionOnly(BasePattern):
        PATTERN_ID = "998"
        NAME = "MentionOnly"
        CATEGORY = Category.GENERAL

        def scan(self, regions=None):
            # run_across_regions would go here one day
            note = "run_across_regions"
            return [note] and []

    assert _is_migrated(_MentionOnly) is False


def test_is_migrated_rejects_dead_branch_call():
    # The call is real syntactically but unreachable. A plain ast.walk would
    # find the Call node and wrongly report migrated; requiring a top-level
    # body statement closes this false positive.
    class _DeadBranch(BasePattern):
        PATTERN_ID = "997"
        NAME = "DeadBranch"
        CATEGORY = Category.GENERAL

        def scan(self, regions=None):
            if False:
                self.run_across_regions(regions)
            return []

    assert _is_migrated(_DeadBranch) is False


def test_is_migrated_accepts_super_delegation():
    # A subclass that overrides scan() only to add setup and then delegates
    # via super().run_across_regions(...) is just as migrated as one calling
    # self.run_across_regions(...). Closes the false negative.
    class _SuperDelegate(BasePattern):
        PATTERN_ID = "996"
        NAME = "SuperDelegate"
        CATEGORY = Category.GENERAL

        def scan(self, regions=None):
            return super().run_across_regions(regions)

    assert _is_migrated(_SuperDelegate) is True


# ---------------------------------------------------------------------------
# NON_REGIONAL is bookkeeping, not proof: a pattern parked there must not
# define a per-region entry point. This is a guardrail against misuse (a
# regional pattern dodging migration), not a proof of non-regionality.
# ---------------------------------------------------------------------------
def _non_regional_violations(patterns: dict, non_regional: set) -> list[str]:
    """The guardrail's one and only check, shared by both tests below.

    Returns the ids of patterns listed non-regional that nonetheless define
    their OWN `_scan_region` (an own attribute, not the inherited
    BasePattern._scan_region stub) — i.e. the regional patterns parked in
    NON_REGIONAL to dodge migration, which is exactly what this rule catches.
    An empty list means the rule sees no violations.
    """
    violations = []
    for pid in non_regional:
        cls = patterns.get(pid)
        assert cls is not None, f"NON_REGIONAL lists unknown pattern {pid}"
        if "_scan_region" in cls.__dict__:
            violations.append(pid)
    return violations


def test_non_regional_patterns_do_not_define_scan_region():
    # The REAL registry and the real NON_REGIONAL set: no violations.
    patterns = {p.PATTERN_ID: p for p in discover_patterns()}
    violations = _non_regional_violations(patterns, NON_REGIONAL)
    assert not violations, (
        f"patterns {violations} are listed NON_REGIONAL but define _scan_region "
        "— a genuinely non-regional pattern has no per-region entry point. "
        "This looks like a regional pattern parked here to dodge migration."
    )


def test_non_regional_guardrail_catches_a_regional_pattern():
    # Feed the helper a STUB registry containing a pattern that DOES define
    # _scan_region while listed as non-regional. The helper must REPORT it —
    # this proves the check actually fires, which an assertion about the
    # fixture's own __dict__ never would.
    class _FakeNonRegional(BasePattern):
        PATTERN_ID = "995"
        NAME = "FakeNonRegional"
        CATEGORY = Category.GENERAL

        def scan(self, regions=None):
            return self.run_across_regions(regions)

        def _scan_region(self, region):
            return []

    patterns = {"995": _FakeNonRegional}
    violations = _non_regional_violations(patterns, {"995"})
    assert violations == ["995"]


# ---------------------------------------------------------------------------
# 4. Per-scan state resets before anything that can raise.
# ---------------------------------------------------------------------------
def test_stale_state_cleared_when_region_resolution_fails():
    # First scan succeeds and populates state.
    pattern = _StubPattern(lambda r: [_finding(r)])
    pattern.scan(regions=["us-east-1"])
    assert pattern._findings
    # Seed a stale error too, to prove both lists are cleared.
    pattern.scan_errors.append(
        ScanError(pattern_id="999", region="us-east-1",
                  error_type="X", message="stale")
    )

    # Second scan resolves regions via get_all_regions(), which raises.
    pattern.get_all_regions = MagicMock(side_effect=RuntimeError("no creds"))

    with pytest.raises(RuntimeError):
        pattern.scan(regions=None)

    # State was reset as the first act, before the raise propagated.
    assert pattern._findings == []
    assert pattern.scan_errors == []


# ---------------------------------------------------------------------------
# 5. An empty SUPPORTED_REGIONS intersection is recorded, not silent.
# ---------------------------------------------------------------------------
def test_empty_region_intersection_records_scan_error():
    pattern = _StubPattern(lambda r: [_finding(r)])
    pattern.SUPPORTED_REGIONS = ["ap-south-1"]  # matches nothing requested

    findings = pattern.scan(regions=["us-east-1", "eu-west-1"])

    # Returns [] without raising, and never scanned a region.
    assert findings == []
    assert pattern.calls == []

    # But the coverage fact is recorded, not swallowed.
    assert len(pattern.scan_errors) == 1
    err = pattern.scan_errors[0]
    assert isinstance(err, ScanError)
    assert err.region is None
    assert err.error_type == "NoApplicableRegions"
    assert "SUPPORTED_REGIONS" in err.message


# ---------------------------------------------------------------------------
# 6. p008 records a global failure (region=None) and does not propagate.
# ---------------------------------------------------------------------------
def test_p008_records_global_scan_error_on_top_level_failure():
    from patterns.p008_s3_lifecycle import S3LifecyclePattern

    session = MagicMock()
    # list_buckets() (reached via s3 client) blows up at the top level.
    session.client.return_value.list_buckets.side_effect = RuntimeError(
        "list_buckets exploded"
    )
    pattern = S3LifecyclePattern(session=session)

    # Returns rather than propagating.
    findings = pattern.scan()
    assert findings == []

    # And records the failure as a global (region=None) coverage failure.
    assert len(pattern.scan_errors) == 1
    err = pattern.scan_errors[0]
    assert err.pattern_id == "008"
    assert err.region is None
    assert err.error_type == "RuntimeError"
    assert "exploded" in err.message


def test_p008_failed_then_clean_rescan_clears_scan_errors():
    # The REPRODUCED regression: p008 gained a scan_errors-writing path
    # (_record_region_error(None, exc)) but its bare `self._findings = []`
    # never reset scan_errors. A failing scan followed by a clean one left a
    # stale error — WRONG coverage data that looks authoritative. Assert on
    # p008 specifically, since that is the instance that regressed.
    from patterns.p008_s3_lifecycle import S3LifecyclePattern

    session = MagicMock()
    client = session.client.return_value
    # First scan: list_buckets() explodes -> one global ScanError.
    client.list_buckets.side_effect = RuntimeError("list_buckets exploded")
    pattern = S3LifecyclePattern(session=session)
    pattern.scan()
    assert len(pattern.scan_errors) == 1

    # Second scan on the SAME instance succeeds (no buckets, no failure).
    client.list_buckets.side_effect = None
    client.list_buckets.return_value = {"Buckets": []}
    findings = pattern.scan()

    assert findings == []
    assert pattern.scan_errors == []
