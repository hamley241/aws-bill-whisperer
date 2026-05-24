"""
Tests for the `whisper-plan` CLI bad-input exit-code contract.

The docstring promises:
    0   plan status is "ok"
    1   plan status is "validation_failed" / LLM not configured
    2   bad input (file missing, malformed JSON, malformed finding dict)

Previously only the "JSON is not a list" case returned 2; other bad-input
paths (missing file, decode error, hydrate failure) crashed with a
traceback. These tests pin the contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cli.plan import _load_findings, main  # noqa: E402
from cli.plan import _BadInput  # noqa: E402


def _write_findings(tmp_path: Path, contents: str) -> Path:
    p = tmp_path / "findings.json"
    p.write_text(contents, encoding="utf-8")
    return p


class TestLoadFindingsBadInput:
    """Unit-test the loader path. Faster + cleaner than driving main()
    through the LLM machinery."""

    def test_missing_file_raises_bad_input(self, tmp_path):
        with pytest.raises(_BadInput, match="file not found"):
            _load_findings(tmp_path / "does-not-exist.json")

    def test_malformed_json_raises_bad_input(self, tmp_path):
        p = _write_findings(tmp_path, "{this is not json")
        with pytest.raises(_BadInput, match="malformed JSON"):
            _load_findings(p)

    def test_non_list_top_level_raises_bad_input(self, tmp_path):
        p = _write_findings(tmp_path, json.dumps({"oops": "object"}))
        with pytest.raises(_BadInput, match="expected a JSON list"):
            _load_findings(p)

    def test_non_dict_item_raises_bad_input(self, tmp_path):
        p = _write_findings(tmp_path, json.dumps(["not-a-dict"]))
        with pytest.raises(_BadInput, match="item 0 is a str"):
            _load_findings(p)

    def test_missing_required_field_raises_bad_input(self, tmp_path):
        # Finding requires resource_id, resource_type, region,
        # monthly_impact_usd, summary. Drop one.
        bad = [{
            "resource_id": "vol-1",
            "resource_type": "EBS Volume",
            "region": "us-east-1",
            # monthly_impact_usd omitted
            "summary": "x",
        }]
        p = _write_findings(tmp_path, json.dumps(bad))
        with pytest.raises(_BadInput, match="failed to hydrate"):
            _load_findings(p)

    def test_invalid_risk_tier_raises_bad_input(self, tmp_path):
        bad = [{
            "resource_id": "vol-1",
            "resource_type": "EBS Volume",
            "region": "us-east-1",
            "monthly_impact_usd": 10.0,
            "summary": "x",
            "risk_tier": "definitely-not-a-tier",
        }]
        p = _write_findings(tmp_path, json.dumps(bad))
        with pytest.raises(_BadInput, match="failed to hydrate"):
            _load_findings(p)

    def test_valid_findings_round_trip(self, tmp_path):
        good = [{
            "id": "00000000-0001-4000-8000-000000000001",
            "resource_id": "vol-1",
            "resource_type": "EBS Volume",
            "region": "us-east-1",
            "monthly_impact_usd": 10.0,
            "summary": "x",
            "pattern_id": "001",
            "risk_tier": "medium",
            "confidence": 0.9,
            "safe_to_fix": True,
            "evidence": {},
        }]
        p = _write_findings(tmp_path, json.dumps(good))
        findings = _load_findings(p)
        assert len(findings) == 1
        assert findings[0].resource_id == "vol-1"


class TestMainExitCodes:
    """End-to-end exit codes via main(argv). LLM config is irrelevant
    on the bad-input path because the CLI returns 2 BEFORE configuring
    the LLM — the relevant assertion."""

    def test_missing_file_exits_2(self, tmp_path, capsys):
        rc = main([str(tmp_path / "nope.json")])
        assert rc == 2
        err = capsys.readouterr().err
        assert "file not found" in err

    def test_malformed_json_exits_2(self, tmp_path, capsys):
        p = _write_findings(tmp_path, "[not, valid")
        rc = main([str(p)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "malformed JSON" in err

    def test_non_list_exits_2(self, tmp_path, capsys):
        p = _write_findings(tmp_path, json.dumps({"x": 1}))
        rc = main([str(p)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "expected a JSON list" in err

    def test_malformed_finding_exits_2(self, tmp_path, capsys):
        p = _write_findings(tmp_path, json.dumps([{"only": "junk"}]))
        rc = main([str(p)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "failed to hydrate" in err
