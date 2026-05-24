"""
`/whisper plan` subcommand handler.

Flow:
  1. ack() within Slack's 3-second window (done by the dispatcher in scan.py).
  2. Post "🧭 Planning…" parent message.
  3. Background: run scan → run planner → render Block Kit → post threaded
     reply.
  4. Remember (parent_ts → ScanResult) in the thread store so the existing
     Open-PR button handler (slack/handlers/actions.py) keeps working — it
     looks up findings via ScanResult and is shared between scan and plan
     surfaces.

This handler is PRESENTATION-ONLY. It never invents fix actions of its
own. Clicking the Open-PR button (rendered only for safe pr-mode steps)
routes through the existing audit_remediation path.

PR #8 scope decision: only PR-mode steps get a button. command and
api_call modes render as text-only mode badges. See
`agentic/plan_surface_agentic.md` for the rationale.

Goal parsing — `goal:` prefix only, no mini-parser:
    /whisper plan
    /whisper plan goal: cut 20% this month
    /whisper plan goal:              (empty → default goal)
    /whisper plan unused goal: cut X (everything after first `goal:` wins)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

_SRC = Path(__file__).parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.planner import SavingsPlanner  # noqa: E402
from llm import make_llm_client  # noqa: E402
from presenters import ScanResult  # noqa: E402
from presenters.plan import BlockKitPlanPresenter, to_renderable  # noqa: E402

from ..scanner import run_scan  # noqa: E402
from ..thread_store import get_store  # noqa: E402


GOAL_PREFIX = "goal:"

PLAN_STARTED_TEXT = (
    "🧭 *Planning your remediation…*\n"
    "I'll scan, rank, and post the plan in this thread."
)

PLAN_FAILURE_FALLBACK = (
    "The planner did not produce a usable plan (validation failed after retries)."
)


# Override in tests.
_scan_runner: Callable[..., ScanResult] = run_scan
_planner_factory: Callable[[Any], SavingsPlanner] | None = None
_spawn_background: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(
    target=fn, daemon=True
).start()


def set_scan_runner(runner: Callable[..., ScanResult]) -> None:
    global _scan_runner
    _scan_runner = runner


def set_planner_factory(factory: Callable[[Any], SavingsPlanner] | None) -> None:
    global _planner_factory
    _planner_factory = factory


def set_background_runner(runner: Callable[[Callable[[], None]], None]) -> None:
    global _spawn_background
    _spawn_background = runner


def _default_planner_factory(config) -> SavingsPlanner:
    llm = make_llm_client(config, prompt_template="savings_plan")
    return SavingsPlanner(llm=llm)


def parse_goal(rest: str) -> str | None:
    """Extract the goal from text after the `plan` subcommand.

    The `goal:` prefix is case-insensitive. Everything after the first
    occurrence (whitespace-stripped) becomes the goal. An empty / whitespace-
    only goal after the prefix is treated as if the prefix was omitted —
    the planner default goal applies.

    Examples:
        ""                                  → None
        "goal:"                             → None
        "goal:   "                          → None
        "goal: cut 20% this month"          → "cut 20% this month"
        "preamble goal: trim NAT cost"      → "trim NAT cost"
        "GOAL: be careful"                  → "be careful"
    """
    if not rest:
        return None
    lower = rest.lower()
    idx = lower.find(GOAL_PREFIX)
    if idx == -1:
        return None
    after = rest[idx + len(GOAL_PREFIX):].strip()
    return after or None


def handle_plan(*, respond, command, client, logger, config, rest: str) -> None:
    """Entry point called by the /whisper dispatcher in handlers/scan.py.

    `rest` is the raw text after the `plan` subcommand token (case-
    preserved). `respond` is the Bolt ephemeral responder used for
    early errors that never reach the channel.
    """
    channel = command.get("channel_id")
    user = command.get("user_id")
    goal = parse_goal(rest)
    logger.info("plan requested by user=%s channel=%s goal=%r", user, channel, goal)

    try:
        parent = client.chat_postMessage(channel=channel, text=PLAN_STARTED_TEXT)
    except Exception as e:
        logger.exception("failed to post plan-started message")
        respond(
            text=f":x: Couldn't post to the channel: `{e}`. "
                 "Make sure the Whisper app has been added to this channel.",
            response_type="ephemeral",
        )
        return

    parent_ts = parent.get("ts") if isinstance(parent, dict) else parent["ts"]

    _spawn_background(
        lambda: _run_and_post(
            config, client, channel, parent_ts, logger,
            goal=goal, actor=user,
        )
    )


def _run_and_post(config, client, channel: str, parent_ts: str, logger,
                  *, goal: str | None, actor: str | None) -> None:
    """Scan → plan → render → post threaded. Failures land in-thread, not ephemeral."""
    try:
        scan_result = _scan_runner(config) if config is not None else _scan_runner()
    except Exception as e:
        logger.exception("scan failed during plan")
        _safe_post(client, channel, parent_ts,
                   text=f":x: Scan failed: `{e}`", logger=logger)
        return

    factory = _planner_factory or _default_planner_factory
    try:
        planner = factory(config)
        plan = planner.plan(scan_result.findings, goal=goal, actor=actor)
    except Exception as e:
        logger.exception("planner failed")
        _safe_post(client, channel, parent_ts,
                   text=f":x: Planner failed: `{e}`", logger=logger)
        return

    renderable = to_renderable(plan, scan_result.findings)
    blocks = BlockKitPlanPresenter().render(renderable)

    if renderable.status == "ok":
        fallback = (
            f"Plan: {len(renderable.steps)} step(s), "
            f"${renderable.total_monthly_impact_usd:.2f}/mo waste targeted."
        )
    else:
        fallback = PLAN_FAILURE_FALLBACK

    _safe_post(client, channel, parent_ts, text=fallback, blocks=blocks, logger=logger)

    # Store the underlying ScanResult so the Open-PR button handler in
    # actions.py can find the source finding by id — same behaviour as
    # /whisper scan. Future PRs will extend the store to also carry the
    # PlanResult for re-planning / threaded Q&A.
    get_store().set(parent_ts, scan_result)


def _safe_post(client, channel: str, thread_ts: str, *,
               text: str, blocks: list | None = None, logger) -> None:
    try:
        kwargs: dict = {"channel": channel, "thread_ts": thread_ts, "text": text}
        if blocks is not None:
            kwargs["blocks"] = blocks
        client.chat_postMessage(**kwargs)
    except Exception:
        logger.exception("chat.postMessage failed")
