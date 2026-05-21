"""Tests for src/agent/parser.py — JSON extraction with fenced/bare/multiple cases."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.parser import ParseError, parse_plan


def _valid() -> str:
    return '{"summary": "ok", "steps": [{"finding_id": "x"}]}'


class TestParsePlanSuccess:
    def test_bare_json_object(self):
        plan = parse_plan(_valid())
        assert plan.summary == "ok"
        assert plan.steps == [{"finding_id": "x"}]

    def test_json_fenced_with_label(self):
        text = f"```json\n{_valid()}\n```"
        plan = parse_plan(text)
        assert plan.summary == "ok"

    def test_json_fenced_without_label(self):
        text = f"```\n{_valid()}\n```"
        plan = parse_plan(text)
        assert plan.summary == "ok"

    def test_json_with_surrounding_prose(self):
        # Bare object inside prose — the balanced-object extractor handles it.
        text = "Here is my plan:\n" + _valid() + "\nThanks!"
        plan = parse_plan(text)
        assert plan.summary == "ok"

    def test_nested_json_in_steps(self):
        text = '{"summary": "s", "steps": [{"x": {"y": 1}}, {"a": [1,2]}]}'
        plan = parse_plan(text)
        assert len(plan.steps) == 2

    def test_string_with_curly_brace_in_value(self):
        # A JSON value containing a literal brace inside a string mustn't
        # confuse the balanced-object extractor.
        text = '{"summary": "look at {this}", "steps": []}'
        plan = parse_plan(text)
        assert plan.summary == "look at {this}"


class TestParsePlanFailure:
    def test_no_json(self):
        with pytest.raises(ParseError, match="no JSON object"):
            parse_plan("just some prose")

    def test_malformed_json(self):
        with pytest.raises(ParseError):
            parse_plan('{"summary": "x", "steps": [unclosed')

    def test_top_level_array(self):
        with pytest.raises(ParseError, match="no JSON object"):
            parse_plan("[1, 2, 3]")

    def test_missing_summary(self):
        with pytest.raises(ParseError, match="summary"):
            parse_plan('{"steps": []}')

    def test_missing_steps(self):
        with pytest.raises(ParseError, match="steps"):
            parse_plan('{"summary": "x"}')

    def test_steps_not_a_list(self):
        with pytest.raises(ParseError, match="must be a list"):
            parse_plan('{"summary": "x", "steps": "not a list"}')

    def test_two_distinct_objects_rejected(self):
        text = f"first object:\n{_valid()}\n\nsecond object:\n" + \
               '{"summary": "other", "steps": []}'
        with pytest.raises(ParseError, match="2 distinct JSON objects"):
            parse_plan(text)

    def test_fenced_plus_bare_same_content_is_ok(self):
        # If the same object appears both inside a fence and as the
        # bare text, that's still one distinct object — accepted.
        bare = _valid()
        text = f"```json\n{bare}\n```\n"  # only the fence is non-empty after strip
        plan = parse_plan(text)
        assert plan.summary == "ok"
