"""Tests for analyzer.explainer — per-finding LLM explanation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from analyzer.explainer import DEFAULT_TOP_N, explain_findings
from llm import LLMClient
from llm.base import LLMResponse, Message
from patterns.base import Finding, RiskTier


class _StubClient(LLMClient):
    provider = "stub"
    boundary_crossed = False

    def __init__(self, answer: str = "Because the volume is unattached."):
        self.calls: list[list[Message]] = []
        self._answer = answer

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        self.calls.append(messages)
        return LLMResponse(
            text=self._answer,
            provider=self.provider,
            model="stub-model",
            boundary_crossed=False,
        )

    @property
    def default_model(self):
        return "stub-model"


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
        evidence={"size_gb": 100},
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestExplainFindings:
    def test_populates_explanation_on_top_findings(self):
        client = _StubClient("Plain-English explanation.")
        findings = [_finding(resource_id=f"vol-{i}", monthly_impact_usd=float(i + 1))
                    for i in range(3)]
        explain_findings(findings, client=client)
        for f in findings:
            assert f.explanation == "Plain-English explanation."

    def test_top_n_caps_llm_calls(self):
        client = _StubClient()
        findings = [_finding(resource_id=f"vol-{i}", monthly_impact_usd=float(i + 1))
                    for i in range(10)]
        explain_findings(findings, client=client, top_n=3)
        assert len(client.calls) == 3

    def test_top_n_picks_highest_impact(self):
        client = _StubClient()
        findings = [
            _finding(resource_id="cheap", monthly_impact_usd=1.0),
            _finding(resource_id="big", monthly_impact_usd=500.0),
            _finding(resource_id="mid", monthly_impact_usd=50.0),
            _finding(resource_id="tiny", monthly_impact_usd=0.5),
        ]
        explain_findings(findings, client=client, top_n=2)
        explained_ids = {f.resource_id for f in findings if f.explanation}
        assert explained_ids == {"big", "mid"}

    def test_skips_findings_with_existing_explanation(self):
        client = _StubClient("new explanation")
        findings = [
            _finding(resource_id="a", explanation="already done", monthly_impact_usd=10.0),
            _finding(resource_id="b", monthly_impact_usd=20.0),
        ]
        explain_findings(findings, client=client)
        assert len(client.calls) == 1
        assert next(f for f in findings if f.resource_id == "a").explanation == "already done"

    def test_prompt_includes_finding_details(self):
        client = _StubClient()
        explain_findings([_finding()], client=client)
        prompt_text = client.calls[0][0].content
        assert "EBS Volume" in prompt_text
        assert "us-east-1" in prompt_text
        assert "high" in prompt_text
        assert "Delete unattached volume" in prompt_text
        assert "size_gb" in prompt_text  # evidence rendered

    def test_llm_failure_does_not_break_chain(self):
        class _ExplodingClient(LLMClient):
            provider = "boom"
            boundary_crossed = False
            def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
                raise RuntimeError("LLM exploded")
            @property
            def default_model(self):
                return "boom"

        findings = [
            _finding(resource_id="a", monthly_impact_usd=10.0),
            _finding(resource_id="b", monthly_impact_usd=20.0),
        ]
        # Should not raise.
        explain_findings(findings, client=_ExplodingClient())
        # No explanations got set, but the call returned normally.
        assert all(f.explanation is None for f in findings)

    def test_no_client_no_config_is_no_op(self):
        findings = [_finding()]
        explain_findings(findings)  # no client, no config
        assert findings[0].explanation is None

    def test_empty_findings_returns_early(self):
        client = _StubClient()
        explain_findings([], client=client)
        assert client.calls == []

    def test_default_top_n_is_five(self):
        assert DEFAULT_TOP_N == 5
