"""
End-to-end test for the Slack Open-PR button — finding lookup →
audit_remediation → in-thread reply.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from patterns.base import Finding, RiskTier
from presenters import ScanResult
from slack.handlers import actions as action_handlers
from slack.thread_store import get_store


class _StubApp:
    def __init__(self):
        self.actions: dict = {}

    def action(self, name):
        def decorator(fn):
            self.actions[name] = fn
            return fn
        return decorator


def _finding_in_thread(**overrides) -> tuple[str, Finding]:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.9,
        safe_to_fix=True,
        evidence={"size_gb": 100, "age_days": 30,
                  "has_snapshot": True, "latest_snapshot_age_days": 5,
                  "terraform_managed": True},
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
    )
    defaults.update(overrides)
    f = Finding(**defaults)
    thread_ts = "1700000000.001"
    get_store().set(thread_ts, ScanResult.from_findings([f]))
    return thread_ts, f


@pytest.fixture(autouse=True)
def _reset_store():
    get_store().clear()
    yield
    get_store().clear()


def _invoke_open_pr(client, finding_id: str, *, thread_ts: str = "1700000000.001"):
    ack = MagicMock()
    logger = MagicMock()
    stub = _StubApp()
    action_handlers.register(stub)
    stub.actions["open_pr"](
        ack=ack,
        body={
            "user": {"id": "U-test"},
            "actions": [{"value": finding_id}],
            "channel": {"id": "C-test"},
            "message": {"ts": thread_ts, "thread_ts": thread_ts},
        },
        client=client,
        logger=logger,
    )
    return ack, logger


class TestOpenPRButton:
    def test_terraform_managed_volume_emits_diff(self, monkeypatch, tmp_path):
        # Isolate the audit log so we don't write to ~/.whisper.
        from storage import SqliteBackend, WhisperRepository, set_default_repository
        repo = WhisperRepository(backend=SqliteBackend(db_path=tmp_path / "a.db"))
        set_default_repository(repo)

        client = MagicMock()
        thread_ts, finding = _finding_in_thread()

        _invoke_open_pr(client, finding.id)

        client.chat_postMessage.assert_called_once()
        kwargs = client.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == "C-test"
        assert kwargs["thread_ts"] == thread_ts
        assert "PR change" in kwargs["text"]
        assert "aws_ebs_volume" in kwargs["text"]

        # Audit log captured the PR attempt.
        rows = repo.list_remediations()
        assert len(rows) == 1
        assert rows[0].mode == "pr"
        assert rows[0].success is True
        assert rows[0].actor == "U-test"

        set_default_repository(None)

    def test_untagged_volume_posts_refusal(self, monkeypatch, tmp_path):
        from storage import SqliteBackend, WhisperRepository, set_default_repository
        repo = WhisperRepository(backend=SqliteBackend(db_path=tmp_path / "b.db"))
        set_default_repository(repo)

        client = MagicMock()
        thread_ts, finding = _finding_in_thread(
            evidence={"size_gb": 100, "age_days": 30,
                      "has_snapshot": True, "latest_snapshot_age_days": 5,
                      "terraform_managed": False},
        )

        _invoke_open_pr(client, finding.id)

        kwargs = client.chat_postMessage.call_args.kwargs
        assert "refused" in kwargs["text"].lower() or "managed-by-terraform" in kwargs["text"]

        rows = repo.list_remediations()
        assert len(rows) == 1
        assert rows[0].mode == "pr"
        assert rows[0].success is False

        set_default_repository(None)

    def test_finding_not_in_any_thread(self):
        client = MagicMock()
        _invoke_open_pr(client, "no-such-finding-id")
        kwargs = client.chat_postMessage.call_args.kwargs
        assert "no longer" in kwargs["text"] or "find the scan" in kwargs["text"]
