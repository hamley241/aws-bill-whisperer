"""
LLMClient — the single abstraction every prompt flows through.

See CLAUDE.md principle 5:
  - All prompts go through this interface.
  - Every call writes the full prompt to a local file (data sovereignty).
  - Every response is tagged with provider + whether the prompt left the
    customer's account boundary.
  - Token counts logged so customers can audit LLM spend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass
class LLMResponse:
    text: str
    provider: str  # "bedrock" | "openai" | "anthropic"
    model: str
    boundary_crossed: bool  # True iff the prompt left the customer's AWS account
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict | None = field(default=None, repr=False)


class LLMClient(ABC):
    """
    Synchronous chat-completion interface.

    Implementations must:
    1. Call their provider's API.
    2. Return an LLMResponse with provider + boundary_crossed populated.
    3. Not log anything themselves — logging is the responsibility of the
       wrapping LoggedLLMClient. This separation means tests can exercise
       provider code without writing files.
    """

    provider: str = ""
    # If True, sending data through this client takes prompts outside the
    # customer's AWS account (i.e. across a network boundary to a third
    # party). Used by the logger so the user can audit boundary crossings.
    boundary_crossed: bool = False

    @abstractmethod
    def complete(self, messages: list[Message], *, model: str | None = None,
                 max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        ...

    @property
    def default_model(self) -> str:
        """Provider's default model when none is specified on the call."""
        return ""
