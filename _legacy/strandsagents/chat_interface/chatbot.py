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

    INTENT_RULES = [
        {"name": "storage_quick", "keywords": ["storage quick", "storage win"], "handler": "_handle_storage_quick"},
        {"name": "storage_full", "keywords": ["ebs", "s3", "bucket", "storage", "volume"], "handler": "_handle_storage_full"},
        {"name": "compute_quick", "keywords": ["compute quick", "ec2 quick"], "handler": "_handle_compute_quick"},
        {"name": "compute_full", "keywords": ["ec2", "compute", "lambda", "rightsizing", "reserved", "ri"], "handler": "_handle_compute_full"},
        {"name": "quick", "keywords": ["quick", "top", "fast"], "handler": "_handle_quick"},
        {"name": "report", "keywords": ["full", "report", "comprehensive", "summary", "all"], "handler": "_handle_report"},
    ]

    def __init__(self, aws_session=None):
        self.orchestrator = AWSCostOrchestrator(aws_session=aws_session)
        session = self.orchestrator.aws_session
        self.storage_agent = StorageOptimizationAgent(aws_session=session)
        self.compute_agent = ComputeOptimizationAgent(aws_session=session)
        self.conversation: List[Dict[str, str]] = []

    async def ask(self, message: str) -> ChatResponse:
        """Route a natural-language question to the right agents and format the answer."""

        self.conversation.append({"role": "user", "content": message})
        route = self._detect_intent(message)
        handler_name = route.get("handler", "_handle_general")
        handler = getattr(self, handler_name, self._handle_general)
        response = await handler(message, route)
        self.conversation.append({"role": "assistant", "content": response.text})
        return response

    def _detect_intent(self, message: str) -> Dict[str, Any]:
        lowered = message.lower()
        for rule in self.INTENT_RULES:
            if any(keyword in lowered for keyword in rule["keywords"]):
                return rule
        return {"name": "general", "handler": "_handle_general"}

    async def _handle_storage_full(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await asyncio.to_thread(self.storage_agent.analyze, message)
        return self._format_storage_response(payload, context=message)

    async def _handle_storage_quick(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await asyncio.to_thread(self.storage_agent.quick_wins)
        return self._format_storage_response(payload, context="storage quick wins")

    async def _handle_compute_full(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await asyncio.to_thread(self.compute_agent.analyze, message)
        return self._format_compute_response(payload, context=message)

    async def _handle_compute_quick(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await asyncio.to_thread(self.compute_agent.quick_wins)
        return self._format_compute_response(payload, context="compute quick wins")

    async def _handle_quick(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await self.orchestrator.quick_wins_analysis()
        return self._format_orchestrator_response(payload, context="quick wins")

    async def _handle_report(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await self.orchestrator.run_full_analysis()
        return self._format_orchestrator_response(payload, context="full report")

    async def _handle_general(self, message: str, route: Dict[str, Any]) -> ChatResponse:
        payload = await asyncio.to_thread(self.orchestrator.orchestrator, message)
        try:
            data = self._safe_json(str(payload))
        except Exception:
            data = {"raw": str(payload)}
        return self._format_orchestrator_response(data, context=message)

    def _safe_json(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"raw": payload}
        return {"raw": str(payload)}

    def _format_storage_response(self, payload: Any, context: str | None = None) -> ChatResponse:
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

        header = "Storage scan"
        if context:
            header += f" for '{context}'"
        text = (
            f"{header} found {data.get('total_unattached_volumes', 0)} unattached EBS volumes "
            f"wasting ${data.get('total_monthly_waste', 0)} per month.\n" + "\n".join(findings)
        )

        return ChatResponse(text=text, commands=commands, raw=data)

    def _format_compute_response(self, payload: Any, context: str | None = None) -> ChatResponse:
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

        header = "Compute scan"
        if context:
            header += f" for '{context}'"
        text = (
            f"{header}: {data.get('idle_instances', {}).get('count', 0)} idle instances and "
            f"{data.get('underutilized_instances', {}).get('count', 0)} rightsize targets.\n" + "\n".join(lines)
        )

        return ChatResponse(text=text, commands=commands, raw=data)

    def _format_orchestrator_response(self, payload: Any, context: str | None = None) -> ChatResponse:
        data = self._safe_json(payload)
        summary = data.get('total_optimization_potential') or data.get('total_monthly_savings_potential')
        prefix = f"Result for '{context}': " if context else ""
        if isinstance(summary, dict):
            text = (
                f"{prefix}Total savings: ${summary.get('monthly_savings', 0)} /mo "
                f"(~${summary.get('annual_savings', 0)} annually)."
            )
        elif isinstance(summary, (int, float)):
            text = f"{prefix}Total savings: ${summary:.2f} per month."
        else:
            text = f"{prefix}Generated comprehensive optimization report."

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
