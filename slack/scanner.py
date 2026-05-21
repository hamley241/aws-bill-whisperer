"""
Run pattern scans on behalf of the Slack handler.

Wraps the pattern-discovery machinery from src/patterns/ into a single
function that returns a ScanResult. Keeping this separate from the
handler lets tests inject a stub and verify the Slack flow without
hitting boto3.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from patterns import discover_patterns  # noqa: E402
from presenters import ScanResult  # noqa: E402

if TYPE_CHECKING:
    from config import WhisperConfig
    from patterns.base import Finding


DEFAULT_REGIONS = ("us-east-1", "us-west-2")

logger = logging.getLogger(__name__)


def run_scan(config: "WhisperConfig", *,
             regions: list[str] | None = None) -> ScanResult:
    """Discover every pattern, run it, and aggregate findings into a ScanResult.

    `regions` falls back to DEFAULT_REGIONS for the common US-only case.
    Pattern errors are logged and recorded in metadata; one broken pattern
    does not abort the whole scan.
    """
    import boto3

    session_kwargs = {}
    if config.aws_profile:
        session_kwargs["profile_name"] = config.aws_profile
    if config.aws_region:
        session_kwargs["region_name"] = config.aws_region
    session = boto3.Session(**session_kwargs)

    scan_regions = list(regions) if regions else list(DEFAULT_REGIONS)
    all_findings: list[Finding] = []
    errors: dict[str, str] = {}

    for PatternClass in discover_patterns():
        try:
            pattern = PatternClass(session=session)
            findings = pattern.scan(regions=scan_regions)
            all_findings.extend(findings)
        except Exception as e:
            logger.warning("pattern %s failed: %s", PatternClass.PATTERN_ID, e)
            errors[PatternClass.PATTERN_ID] = str(e)

    metadata: dict = {"regions": scan_regions}
    if errors:
        metadata["pattern_errors"] = errors
    return ScanResult.from_findings(all_findings, metadata=metadata)
