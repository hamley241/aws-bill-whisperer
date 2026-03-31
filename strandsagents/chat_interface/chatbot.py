"""Conversational interface for AWS Bill Whisperer agents."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..orchestrator import AWSCostOrchestrator
from ..agents.storage_agent import StorageOptimizationAgent
from ..agents.compute_agent import ComputeOptimizationAgent


@dataclass
class ChatResponse:
    """Structured response returned by the chat interface."""

    text: str
    commands: List[Dict[str, str]] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None


class BillWhispererChat:
    """High-level conversational wrapper around the cost optimization agents."""

    def __init__(self, aws_session=None):
        self.orchestrator = AWSCostOrchestrator(aws_session=aws_session)
        session = self.orchestrator.aws_session
        self.storage_agent = StorageOptimizationAgent(aws_session=session)
        self.compute_agent = ComputeOptimizationAgent(aws_session=session)
        self.conversation: List[Dict[str, str]] = []

    async def ask(self, message: str) -> ChatResponse:
        """Route a natural-language question to the right agents and format the answer."""

        self.conversation.append({"role": "user", "content": message})
        intent = self._detect_intent(message)

        if intent == "storage":
            payload = await asyncio.to_thread(self.storage_agent.analyze)
            response = self._format_storage_response(payload)
        elif intent == "compute":
            payload = await asyncio.to_thread(self.compute_agent.analyze)
            response = self._format_compute_response(payload)
        elif intent == "quick":
            payload = await self.orchestrator.quick_wins_analysis()
            response = self._format_orchestrator_response(payload)
        elif intent == "report":
            payload = await self.orchestrator.run_full_analysis()
            response = self._format_orchestrator_response(payload)
        else:
            # Default: run storage + compute quick summary
            storage_raw = await asyncio.to_thread(self.storage_agent.quick_wins)
            compute_raw = await asyncio.to_thread(self.compute_agent.quick_wins)
            response = self._format_combined_summary(storage_raw, compute_raw)

        self.conversation.append({"role": "assistant", "content": response.text})
        return response

    def _detect_intent(self, message: str) -> str:
        lowered = message.lower()
        if any(kw in lowered for kw in ["s3", "storage", "ebs", "bucket"]):
            return "storage"
        if any(kw in lowered for kw in ["ec2", "compute", "lambda", "rightsizing", "reserved"]):
            return "compute"
        if "quick" in lowered or "top" in lowered:
            return "quick"
        if any(kw in lowered for kw in ["full", "report", "comprehensive", "summary"]):
            return "report"
        return "general"

    def _safe_json(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"raw": payload}
        return {"raw": str(payload)}

    def _format_storage_response(self, payload: Any) -> ChatResponse:
        data = self._safe_json(payload)
        volumes = data.get("volumes", [])
        findings = []
        commands: List[Dict[str, str]] = []

        for vol in volumes[:5]:
            findings.append(
                f"• {vol.get('VolumeId')} — {vol.get('SizeGB')} GB {vol.get('VolumeType')} costing ${vol.get('MonthlyCost')} /mo"
            )
            if vol.get("FixCommand"):
                commands.append({
                    "description": f"Cleanup {vol.get('VolumeId')}",
                    "command": vol["FixCommand"],
                })
            if vol.get("SafetyCommand"):
                commands.append({
                    "description": f"Snapshot before deleting {vol.get('VolumeId')}",
                    "command": vol["SafetyCommand"],
                })

        text = (
            f"Storage scan found {data.get('total_unattached_volumes', 0)} unattached EBS volumes "
            f"wasting ${data.get('total_monthly_waste', 0)} per month.\n" + "\n".join(findings)
        )

        return ChatResponse(text=text, commands=commands, raw=data)

    def _format_compute_response(self, payload: Any) -> ChatResponse:
        data = self._safe_json(payload)
        idle = data.get('idle_instances', {}).get('instances', [])
        rightsizing = data.get('underutilized_instances', {}).get('instances', [])
        lines = []
        commands: List[Dict[str, str]] = []

        if idle:
            inst = idle[0]
            lines.append(
                f"Idle: {inst['InstanceId']} ({inst['InstanceType']}) running {inst['RunningDays']} days at ${inst['MonthlyCost']}/mo"
            )
            if inst.get('StopCommand'):
                commands.append({
                    "description": f"Stop {inst['InstanceId']}",
                    "command": inst['StopCommand'],
                })
        if rightsizing:
            inst = rightsizing[0]
            lines.append(
                f"Rightsize: {inst['InstanceId']} {inst['CurrentType']} → {inst['RecommendedType']} saves ${inst['MonthlySavings']}/mo"
            )
            if inst.get('RightsizeCommand'):
                commands.append({
                    "description": f"Resize {inst['InstanceId']}",
                    "command": inst['RightsizeCommand'],
                })

        text = (
            f"Compute scan: {data.get('idle_instances', {}).get('count', 0)} idle instances and "
            f"{data.get('underutilized_instances', {}).get('count', 0)} rightsize targets.\n" + "\n".join(lines)
        )

        return ChatResponse(text=text, commands=commands, raw=data)

    def _format_orchestrator_response(self, payload: Any) -> ChatResponse:
        data = self._safe_json(payload)
        summary = data.get('total_optimization_potential') or data.get('total_monthly_savings_potential')
        text = ""
        if isinstance(summary, dict):
            text = (
                f"Total savings: ${summary.get('monthly_savings', 0)} /mo "
                f"(~${summary.get('annual_savings', 0)} annually)."
            )
        elif isinstance(summary, (int, float)):
            text = f"Total savings: ${summary:.2f} per month."
        else:
            text = "Generated comprehensive optimization report."

        return ChatResponse(text=text, commands=[], raw=data)

    def _format_combined_summary(self, storage_raw: Any, compute_raw: Any) -> ChatResponse:
        storage = self._safe_json(storage_raw)
        compute = self._safe_json(compute_raw)
        text = (
            f"Quick wins ready. Storage savings ≈ ${storage.get('total_monthly_savings_potential', storage.get('total_monthly_waste', 0))}/mo. "
            f"Compute savings ≈ ${compute.get('total_monthly_savings_potential', 0)}/mo."
        )
        commands: List[Dict[str, str]] = []

        first_storage = storage.get('volumes', [{}])[:1]
        for vol in first_storage:
            if vol.get('FixCommand'):
                commands.append({
                    "description": f"Delete {vol.get('VolumeId')}",
                    "command": vol['FixCommand'],
                })

        first_compute = compute.get('functions', [{}])[:1]
        for func in first_compute:
            if func.get('FixCommand'):
                commands.append({
                    "description": f"Tune Lambda {func.get('FunctionName')}",
                    "command": func['FixCommand'],
                })

        return ChatResponse(text=text, commands=commands, raw={"storage": storage, "compute": compute})
