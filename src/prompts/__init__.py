"""
Prompt template registry.

Principle 5: prompts live in templates, not inline strings scattered
through agent code. New agents compose prompts from this registry.

Each template is a Python module that exports a `TEMPLATE: PromptTemplate`.
Adding a new prompt = drop a file in this directory; it's picked up
automatically by load_template(name).
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    text: str
    description: str
    # If True, the template doesn't bake in any provider-specific
    # convention (system-vs-user, special tokens, etc.) and is safe to
    # send to any LLMClient. Most templates should aim for this.
    provider_neutral: bool = True


def list_templates() -> list[str]:
    """All registered template names (sorted)."""
    package_dir = Path(__file__).parent
    names: list[str] = []
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith("_"):
            continue
        names.append(module_info.name)
    return sorted(names)


def load_template(name: str) -> PromptTemplate:
    """Import the named module and return its TEMPLATE attribute."""
    try:
        module = importlib.import_module(f".{name}", __package__)
    except ImportError as e:
        raise KeyError(f"no prompt template named {name!r}") from e
    template = getattr(module, "TEMPLATE", None)
    if not isinstance(template, PromptTemplate):
        raise KeyError(
            f"{name!r} is registered but does not expose a PromptTemplate"
        )
    return template
