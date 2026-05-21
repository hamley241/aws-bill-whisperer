"""Anthropic-direct implementation — crosses the customer's account boundary."""

from __future__ import annotations

from .base import LLMClient, LLMResponse, Message


DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicClient(LLMClient):
    provider = "anthropic"
    boundary_crossed = True

    def __init__(self, *, api_key: str, default_model: str | None = None):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover — install-time path
            raise ImportError(
                "anthropic package required for Anthropic-direct provider. "
                "Install with: pip install anthropic"
            ) from e
        self._default_model = default_model or DEFAULT_MODEL
        self._client = anthropic.Anthropic(api_key=api_key)

    @property
    def default_model(self) -> str:
        return self._default_model

    def complete(self, messages: list[Message], *, model: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        model_id = model or self._default_model
        system_text: str | None = None
        chat: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_text = m.content
            else:
                chat.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat,
        }
        if system_text is not None:
            kwargs["system"] = system_text

        response = self._client.messages.create(**kwargs)
        text = response.content[0].text if response.content else ""
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=model_id,
            boundary_crossed=self.boundary_crossed,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            raw=None,
        )
