"""
`/whisper scan` slash command.

Flow:
  1. ack() the slash command within Slack's 3-second window.
  2. Reply "scan started" so the user sees activity.
  3. Off-thread, run the scan and post Block Kit findings as a
     follow-up via response_url (valid for 30 minutes).
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


SCAN_STARTED_TEXT = (
    "🔍 *Scanning your AWS account…*\n"
    "I'll post the findings here in a moment."
)

UNKNOWN_SUBCOMMAND_TEMPLATE = (
    "Unknown subcommand: `{text}`.\n"
    "Available: `scan`. Try `/whisper scan`."
)

USAGE_TEXT = (
    "*Whisper commands*\n"
    "`/whisper scan` — scan your AWS account for cost waste.\n"
)

# Override in tests to run scans synchronously / inject a stub scanner.
_scan_runner: Callable[..., ScanResult] = run_scan
_explainer: Callable[..., None] = explain_findings
_spawn_background: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(
    target=fn, daemon=True
).start()


def set_scan_runner(runner: Callable[..., ScanResult]) -> None:
    """Tests use this to substitute a stub scanner."""
    global _scan_runner
    _scan_runner = runner


def set_explainer(explainer: Callable[..., None]) -> None:
    """Tests use this to substitute a stub explainer (or no-op)."""
    global _explainer
    _explainer = explainer


def set_background_runner(runner: Callable[[Callable[[], None]], None]) -> None:
    """Tests use this to run background work inline (no threads)."""
    global _spawn_background
    _spawn_background = runner


def register(app: Any) -> None:
    """Attach the /whisper command listener to the given Bolt App."""

    config = getattr(app, "_whisper_config", None)

    @app.command("/whisper")
    def handle_whisper(ack, respond, command, logger):
        ack()
        text = (command.get("text") or "").strip().lower()
        if text == "" or text == "help":
            respond(text=USAGE_TEXT, response_type="ephemeral")
            return
        if text == "scan":
            logger.info(
                "scan requested by user=%s channel=%s",
                command.get("user_id"),
                command.get("channel_id"),
            )
            respond(text=SCAN_STARTED_TEXT, response_type="in_channel")
            _spawn_background(lambda: _run_and_post(config, respond, logger))
            return
        respond(
            text=UNKNOWN_SUBCOMMAND_TEMPLATE.format(text=text),
            response_type="ephemeral",
        )


def _run_and_post(config, respond, logger) -> None:
    """Run the scan, ask the LLM to explain top findings, then post."""
    try:
        result = _scan_runner(config) if config is not None else _scan_runner()
    except Exception as e:  # surface to channel; never silently swallow
        logger.exception("scan failed")
        respond(
            text=f":x: Scan failed: `{e}`",
            response_type="in_channel",
            replace_original=False,
        )
        return

    # Best-effort: explanations enrich the UI but the scan still
    # ships if the LLM is misconfigured or unavailable.
    try:
        _explainer(result.findings, config=config)
    except Exception:
        logger.exception("explanation step failed; continuing without explanations")

    presenter = BlockKitPresenter()
    blocks = presenter.blocks_for_scan(result)
    fallback = (
        f"Scan complete: {result.finding_count} findings, "
        f"${result.total_monthly_impact_usd:.2f}/mo waste."
    )
    respond(
        text=fallback,  # accessibility / notification preview
        blocks=blocks,
        response_type="in_channel",
        replace_original=False,
    )
