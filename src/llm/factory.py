"""
Construct an LLMClient from WhisperConfig.

Every entry point in the app should go through make_llm_client(config)
rather than reaching for boto3/openai/anthropic directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import LLMClient
from .logger import LoggedLLMClient, PromptLogger

if TYPE_CHECKING:
    from config import WhisperConfig  # noqa: F401  (avoids runtime import cycle)


def make_llm_client(config, *, prompt_template: str | None = None) -> LLMClient:
    """
    Return a LoggedLLMClient wrapping the provider that the config selects.

    `prompt_template` is the optional template name to tag log records with;
    callers that load a known template should pass it for traceability.
    """
    backend = config.llm_backend
    inner: LLMClient

    if backend == "bedrock":
        from .bedrock import BedrockClient
        inner = BedrockClient(
            default_model=config.llm_model,
            region=config.aws_region,
        )
    elif backend == "openai":
        if not config.openai_api_key:
            raise ValueError(
                "llm_backend=openai but openai_api_key is not set. "
                "Set OPENAI_API_KEY or run `whisper-config doctor` for help."
            )
        from .openai_client import OpenAIClient
        inner = OpenAIClient(
            api_key=config.openai_api_key,
            default_model=config.llm_model,
        )
    elif backend == "anthropic":
        if not config.anthropic_api_key:
            raise ValueError(
                "llm_backend=anthropic but anthropic_api_key is not set. "
                "Set ANTHROPIC_API_KEY or run `whisper-config doctor` for help."
            )
        from .anthropic_client import AnthropicClient
        inner = AnthropicClient(
            api_key=config.anthropic_api_key,
            default_model=config.llm_model,
        )
    else:
        raise ValueError(f"unknown llm_backend: {backend!r}")

    logger = PromptLogger(config.prompt_log_path)
    return LoggedLLMClient(inner, logger, prompt_template=prompt_template)
