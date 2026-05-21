"""
`/whisper scan` slash command.

PR 1 scope: acknowledge the command and post a "scan started" reply.
The reply becomes the parent message that subsequent PRs (Block Kit
findings rendering, LLM explanations, etc.) thread under.
"""

from __future__ import annotations

from typing import Any


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


def register(app: Any) -> None:
    """Attach the /whisper command listener to the given Bolt App."""

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
            return
        respond(
            text=UNKNOWN_SUBCOMMAND_TEMPLATE.format(text=text),
            response_type="ephemeral",
        )
