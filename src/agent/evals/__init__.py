"""
Eval harness for the SavingsPlanner.

Two pieces:

  rubric.py — assertion vocabulary. Each assertion type is a small
              class that takes a parsed dict + the PlanResult and
              returns ok/fail.

  runner.py — loads a fixture (findings.json + assertions.yaml +
              recorded_response.json), drives the planner over a
              replay LLM, applies the rubric.

Record/replay rules:
  - Replay is the default. Fixtures ship a recorded_response.json;
    the runner reads it and feeds it to a replay LLM. No network.
  - Re-recording requires --re-record (CLI) or the equivalent
    Python kwarg. Re-recording calls the real LLM, writes the
    response to disk, and runs the rubric.
  - Tests must never call the live LLM. Tests stub the replay LLM
    with an inline string when they want to vary the response.
"""

from .rubric import CheckResult, load_rubric, run_rubric
from .runner import EvalResult, SuiteSummary, run_fixture, summarize

__all__ = [
    "CheckResult",
    "EvalResult",
    "SuiteSummary",
    "load_rubric",
    "run_fixture",
    "run_rubric",
    "summarize",
]
