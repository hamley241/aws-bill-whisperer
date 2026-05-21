"""
Prompt logger + LoggedLLMClient wrapper.

Principle 5 contract: every LLM call writes a JSONL record with the full
prompt, provider, model, boundary-crossed flag, and token counts. The
log lives on the customer's machine (default ~/.whisper/prompts.log)
and never leaves. Customers can grep it to audit exactly what data was
sent where.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .base import LLMClient, LLMResponse, Message


@dataclass
class PromptLogRecord:
    timestamp: str  # ISO-8601 UTC
    provider: str
    model: str
    boundary_crossed: bool
    messages: list[dict]  # [{role, content}]
    response_text: str
    input_tokens: int | None
    output_tokens: int | None
    prompt_template: str | None = None  # if known, the name of the template used


class PromptLogger:
    """Append JSONL records to a local file. Thread-unsafe by design;
    callers should serialize their LLM calls or one writer per process."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: PromptLogRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def iter_records(self) -> Iterator[PromptLogRecord]:
        if not self.path.exists():
            return iter([])
        def _gen() -> Iterator[PromptLogRecord]:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    yield PromptLogRecord(**json.loads(line))
        return _gen()


class LoggedLLMClient(LLMClient):
    """
    Decorator over any LLMClient that records every call to a PromptLogger.

    Use this — not raw provider clients — everywhere in app code. Tests
    can still construct provider clients directly when log writes would
    be noise.
    """

    def __init__(self, inner: LLMClient, logger: PromptLogger,
                 *, prompt_template: str | None = None):
        self._inner = inner
        self._logger = logger
        self.provider = inner.provider
        self.boundary_crossed = inner.boundary_crossed
        self._prompt_template = prompt_template

    @property
    def default_model(self) -> str:
        return self._inner.default_model

    def complete(self, messages: list[Message], *, model: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        response = self._inner.complete(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        record = PromptLogRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider=response.provider,
            model=response.model,
            boundary_crossed=response.boundary_crossed,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            response_text=response.text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            prompt_template=self._prompt_template,
        )
        try:
            self._logger.write(record)
        except OSError as e:  # pragma: no cover — disk-full or similar
            # Failing to log is a violation of principle 5; surface loudly.
            raise RuntimeError(
                f"prompt logging failed (path={self._logger.path}): {e}. "
                "Refusing to silently continue — see CLAUDE.md principle 5."
            ) from e
        return response
