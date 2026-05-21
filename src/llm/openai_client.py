"""OpenAI implementation of LLMClient — crosses the customer's account boundary."""

from __future__ import annotations

from .base import LLMClient, LLMResponse, Message


DEFAULT_MODEL = "gpt-4o"


class OpenAIClient(LLMClient):
    provider = "openai"
    boundary_crossed = True  # prompt leaves the customer's AWS account

    def __init__(self, *, api_key: str, default_model: str | None = None):
        try:
            import openai
        except ImportError as e:  # pragma: no cover — install-time path
            raise ImportError(
                "openai package required for OpenAI provider. "
                "Install with: pip install 'aws-bill-whisperer[openai]'"
            ) from e
        self._default_model = default_model or DEFAULT_MODEL
        self._client = openai.OpenAI(api_key=api_key)

    @property
    def default_model(self) -> str:
        return self._default_model

    def complete(self, messages: list[Message], *, model: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        model_id = model or self._default_model
        payload = self._client.chat.completions.create(
            model=model_id,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = payload.choices[0]
        usage = getattr(payload, "usage", None)
        return LLMResponse(
            text=choice.message.content or "",
            provider=self.provider,
            model=model_id,
            boundary_crossed=self.boundary_crossed,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            raw=None,  # OpenAI SDK objects aren't JSON; skip raw to keep log clean
        )
