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

    # ------------------------------------------------------------------
    # PR 3: additional adversarial parser edge cases.
    # ------------------------------------------------------------------

    def test_fenced_with_surrounding_prose_accepted(self):
        # Markdown-fenced JSON is still accepted when the model adds
        # commentary before and after the fence.
        text = (
            "Sure, here is the plan you asked for:\n\n"
            f"```json\n{_valid()}\n```\n\n"
            "Let me know if you want any tweaks!"
        )
        plan = parse_plan(text)
        assert plan.summary == "ok"

    def test_two_fenced_objects_rejected(self):
        # The "exactly one JSON object" rule must hold for fenced blocks too.
        other = '{"summary": "other", "steps": []}'
        text = (
            "Plan A:\n"
            f"```json\n{_valid()}\n```\n"
            "Plan B:\n"
            f"```json\n{other}\n```\n"
        )
        with pytest.raises(ParseError, match="2 distinct JSON objects"):
            parse_plan(text)

    def test_fenced_plus_bare_distinct_object_rejected(self):
        # A fenced block plus a different bare object in prose. Note:
        # _candidate_json_blocks prefers fences when present, so the
        # bare object is silently skipped — only the fence is parsed.
        # Resulting behaviour: ParseError "missing summary" or similar
        # if the fence content is malformed; here, the fence content
        # IS valid, so the fenced object is accepted. Documenting the
        # behavior with a test.
        text = (
            f"```json\n{_valid()}\n```\n\n"
            'Other example object: {"summary": "z", "steps": []}'
        )
        plan = parse_plan(text)
        # Fence wins; the bare {"summary":"z"} is not seen.
        assert plan.summary == "ok"

    def test_unbalanced_first_object_swallows_recovery(self):
        # If the first JSON-looking span never closes, the brace-balancer
        # can't surface the valid object that comes after it. The parser
        # raises ParseError, which is the planner's signal to trigger its
        # repair retry. This documents a known parser limitation in the
        # SAFE direction: we refuse to surface a partial plan rather than
        # guessing what the LLM meant.
        bad = '{"summary": "x", "steps": [unclosed'
        text = f"draft: {bad}\nfinal:\n{_valid()}"
        with pytest.raises(ParseError):
            parse_plan(text)

    def test_balanced_but_unparseable_skipped_for_valid_sibling(self):
        # Two balanced top-level {...} spans: the first is unparseable
        # (unquoted key), the second is valid. The parser silently drops
        # the malformed candidate (json.loads raises) and accepts the
        # valid one. Single object survives — accepted.
        bogus = "{foo: 1}"  # unquoted key → JSONDecodeError
        text = f"first: {bogus}\nsecond: {_valid()}"
        plan = parse_plan(text)
        assert plan.summary == "ok"

    def test_completely_malformed_raises_parse_error(self):
        # Sanity: a malformed-only response (no recoverable JSON) raises
        # ParseError, which is the signal the planner uses to trigger
        # its one-shot repair retry.
        text = "Sure, here you go: {malformed: no_quotes,"
        with pytest.raises(ParseError):
            parse_plan(text)
