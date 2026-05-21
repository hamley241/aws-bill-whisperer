"""
Parse the LLM's response into a `{summary, steps}` dict.

Strategy: extract exactly one JSON object from the response text. The
LLM is asked to emit raw JSON; we tolerate one ```json ... ``` fence
because Claude/GPT both like to wrap. If two distinct JSON objects are
present, or none, or the JSON is malformed, the parser fails — and the
planner is responsible for the one-shot retry through the LLM with a
repair prompt.

We deliberately don't try heroic recovery (regex-fixing trailing commas,
inferring missing quotes, etc.). A response we can't parse cleanly is a
signal the prompt is wrong; tolerating that masks the signal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


class ParseError(Exception):
    """Raised when the LLM response can't be parsed cleanly."""


@dataclass
class ParsedPlan:
    summary: str
    steps: list[dict[str, Any]]


def parse_plan(text: str) -> ParsedPlan:
    """Extract `{summary, steps}` from an LLM response.

    Raises ParseError on:
      - no JSON object found
      - more than one distinct JSON object found
      - top-level value is not an object
      - missing required keys (`summary`, `steps`)
      - `steps` is not a list
    """
    candidates = _candidate_json_blocks(text)
    if not candidates:
        raise ParseError("no JSON object found in response")

    parsed_objects: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed_objects.append(value)

    if not parsed_objects:
        raise ParseError(
            "no valid JSON object parsed — candidates were either malformed "
            "or not top-level objects"
        )
    if len(parsed_objects) > 1:
        raise ParseError(
            f"response contains {len(parsed_objects)} distinct JSON objects; "
            "expected exactly one"
        )

    obj = parsed_objects[0]
    if "summary" not in obj:
        raise ParseError("JSON missing required key 'summary'")
    if "steps" not in obj:
        raise ParseError("JSON missing required key 'steps'")
    if not isinstance(obj["steps"], list):
        raise ParseError(f"'steps' must be a list, got {type(obj['steps']).__name__}")

    summary = str(obj["summary"])
    steps = obj["steps"]
    return ParsedPlan(summary=summary, steps=steps)


def _candidate_json_blocks(text: str) -> list[str]:
    """Pull plausible JSON-object strings out of the response.

    Order of preference:
      1. Contents of any ```json fenced blocks.
      2. The first balanced { ... } span in the raw text.
      3. The raw text itself.

    Returns a deduplicated list of candidate strings. Returns [] if no
    plausible JSON-shaped span is present.
    """
    candidates: list[str] = []

    for match in _FENCE_RE.findall(text):
        stripped = match.strip()
        if stripped:
            candidates.append(stripped)

    if not candidates:
        candidates.extend(_all_balanced_objects(text))

    raw_stripped = text.strip()
    if raw_stripped and raw_stripped not in candidates:
        # Only worth trying the raw text if it starts with '{' —
        # otherwise we waste a json.loads call on prose.
        if raw_stripped[0] == "{":
            candidates.append(raw_stripped)

    return candidates


def _all_balanced_objects(text: str) -> list[str]:
    """Return every balanced top-level {...} span (string-literal-aware).

    Top-level here means depth-0 in the brace nesting, not depth-0 in
    document structure — so two distinct sibling objects in prose both
    show up; a nested object inside one of them does not.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(text[start:i + 1])
                start = -1
    return spans
