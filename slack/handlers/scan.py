"""
`/whisper` slash command dispatcher + `scan` subcommand.

The slash-command registration lives here because Bolt allows only one
listener per command. The dispatcher recognises `scan` and `plan`
subcommands and routes accordingly. The `plan` flow lives in
`handlers/plan.py`; this module owns the `scan` flow and the routing.

`scan` flow:
  1. ack() within Slack's 3-second window.
  2. Post "scan started" to the channel via chat.postMessage (captures
     the parent message ts).
  3. Off-thread, run the scan, ask the LLM to explain top findings,
     and post Block Kit findings as a threaded reply under the parent.
  4. Remember the parent ts → ScanResult mapping so subsequent thread
     messages and app mentions can answer questions with context.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

_SRC = Path(__file__).parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from analyzer.explainer import explain_findings  # noqa: E402
from presenters import BlockKitPresenter, ScanResult  # noqa: E402

from ..scanner import run_scan  # noqa: E402
from ..thread_store import get_store  # noqa: E402
from . import plan as plan_handler  # noqa: E402


SCAN_STARTED_TEXT = (
    "🔍 *Scanning your AWS account…*\n"
    "I'll post the findings in this thread when I'm done."
)

UNKNOWN_SUBCOMMAND_TEMPLATE = (
    "Unknown subcommand: `{text}`.\n"
    "Available: `scan`, `plan`. Try `/whisper scan` or `/whisper plan`."
)

USAGE_TEXT = (
    "*Whisper commands*\n"
    "`/whisper scan` — scan your AWS account for cost waste.\n"
    "`/whisper plan` — scan and rank a remediation plan. "
    "Add `goal: <text>` to steer the plan "
    "(e.g. `/whisper plan goal: cut 20% this month`).\n"
)

# Override in tests.
_scan_runner: Callable[..., ScanResult] = run_scan
_explainer: Callable[..., None] = explain_findings
_spawn_background: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(
    target=fn, daemon=True
).start()


def set_scan_runner(runner: Callable[..., ScanResult]) -> None:
    global _scan_runner
    _scan_runner = runner


def set_explainer(explainer: Callable[..., None]) -> None:
    global _explainer
    _explainer = explainer


def set_background_runner(runner: Callable[[Callable[[], None]], None]) -> None:
    global _spawn_background
    _spawn_background = runner


def register(app: Any) -> None:
    """Attach the /whisper command listener to the given Bolt App."""

    config = getattr(app, "_whisper_config", None)

    @app.command("/whisper")
    def handle_whisper(ack, respond, command, client, logger):
        ack()
        raw_text = (command.get("text") or "").strip()
        # Split on first whitespace: subcommand vs. rest (goal text etc.).
        # Subcommand matching is case-insensitive; rest preserves case
        # because goal text is free-form user input.
        subcommand, _, rest = raw_text.partition(" ")
        subcommand_lower = subcommand.lower()

        if subcommand_lower in ("", "help"):
            respond(text=USAGE_TEXT, response_type="ephemeral")
            return

        if subcommand_lower == "scan":
            _handle_scan(respond, command, client, logger, config)
            return

        if subcommand_lower == "plan":
            plan_handler.handle_plan(
                respond=respond,
                command=command,
                client=client,
                logger=logger,
                config=config,
                rest=rest,
            )
            return

        respond(
            text=UNKNOWN_SUBCOMMAND_TEMPLATE.format(text=raw_text),
            response_type="ephemeral",
        )


def _handle_scan(respond, command, client, logger, config) -> None:
    """The scan flow body. Extracted so the dispatcher stays small."""
    channel = command.get("channel_id")
    user = command.get("user_id")
    logger.info("scan requested by user=%s channel=%s", user, channel)

    try:
        parent = client.chat_postMessage(channel=channel, text=SCAN_STARTED_TEXT)
    except Exception as e:
        logger.exception("failed to post scan-started message")
        respond(
            text=f":x: Couldn't post to the channel: `{e}`. "
                 "Make sure the Whisper app has been added to this channel.",
            response_type="ephemeral",
        )
        return

    parent_ts = parent.get("ts") if isinstance(parent, dict) else parent["ts"]

    _spawn_background(
        lambda: _run_and_post(config, client, channel, parent_ts, logger)
    )


def _run_and_post(config, client, channel: str, parent_ts: str, logger) -> None:
    """Run the scan, explain top findings, post threaded findings, remember the thread."""
    try:
        result = _scan_runner(config) if config is not None else _scan_runner()
    except Exception as e:
        logger.exception("scan failed")
        _safe_post(client, channel, parent_ts,
                   text=f":x: Scan failed: `{e}`", logger=logger)
        return

    try:
        _explainer(result.findings, config=config)
    except Exception:
        logger.exception("explanation step failed; continuing without explanations")

    blocks = BlockKitPresenter().blocks_for_scan(result)
    fallback = (
        f"Scan complete: {result.finding_count} findings, "
        f"${result.total_monthly_impact_usd:.2f}/mo waste."
    )
    _safe_post(client, channel, parent_ts, text=fallback, blocks=blocks, logger=logger)

    # Remember the thread so message + app_mention handlers can answer
    # follow-up questions with scan context.
    get_store().set(parent_ts, result)


def _safe_post(client, channel: str, thread_ts: str, *,
               text: str, blocks: list | None = None, logger) -> None:
    try:
        kwargs: dict = {"channel": channel, "thread_ts": thread_ts, "text": text}
        if blocks is not None:
            kwargs["blocks"] = blocks
        client.chat_postMessage(**kwargs)
    except Exception:
        logger.exception("chat.postMessage failed")
