"""Bedrock implementation of LLMClient — does not cross the account boundary."""

from __future__ import annotations

import json

from .base import LLMClient, LLMResponse, Message


DEFAULT_MODEL = "anthropic.claude-sonnet-4-6:0"
ANTHROPIC_VERSION = "bedrock-2023-05-31"


class BedrockClient(LLMClient):
    provider = "bedrock"
    boundary_crossed = False  # in-account inference

    def __init__(self, *, default_model: str | None = None, region: str | None = None,
                 session=None):
        import boto3  # local import keeps tests cheap
        self._default_model = default_model or DEFAULT_MODEL
        session = session or boto3.Session()
        self._client = session.client("bedrock-runtime", region_name=region)

    @property
    def default_model(self) -> str:
        return self._default_model

    def complete(self, messages: list[Message], *, model: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        model_id = model or self._default_model
        system_msg, chat = _split_system(messages)

        body: dict = {
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in chat],
        }
        if system_msg is not None:
            body["system"] = system_msg.content

        response = self._client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read())

        text = payload["content"][0]["text"]
        usage = payload.get("usage", {})
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=model_id,
            boundary_crossed=self.boundary_crossed,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw=payload,
        )


def _split_system(messages: list[Message]) -> tuple[Message | None, list[Message]]:
    """Bedrock Claude wants system in a top-level field, not in messages."""
    system: Message | None = None
    chat: list[Message] = []
    for m in messages:
        if m.role == "system":
            system = m  # last one wins
        else:
            chat.append(m)
    return system, chat
