"""
Interactive action handlers (buttons, overflow menus).

PR 2 ships stubs: the buttons and menu options are wired and logged,
but the actual PR-opening and "show more" flows land in later PRs.
"""

from __future__ import annotations

from typing import Any


PR_COMING_TEXT = (
    ":construction: *Open PR* is on its way.\n"
    "PR-native remediation lands in a future release "
    "(see docs/tasks/TASK-slack-app.md)."
)

OVERFLOW_REPLIES = {
    "show_all": (
        ":construction: *Show all findings* is on its way.\n"
        "For now, run `whisper scan --json` from the CLI for the full list."
    ),
    "download_json": (
        ":construction: *Download JSON* is on its way.\n"
        "For now, run `whisper scan --json` from the CLI."
    ),
}


def register(app: Any) -> None:
    """Attach button + overflow listeners to the given Bolt App."""

    @app.action("open_pr")
    def handle_open_pr(ack, body, respond, logger):
        ack()
        finding_id = (body.get("actions") or [{}])[0].get("value", "?")
        user = body.get("user", {}).get("id", "?")
        logger.info("open_pr clicked by user=%s for finding=%s", user, finding_id)
        respond(text=PR_COMING_TEXT, response_type="ephemeral", replace_original=False)

    @app.action("scan_overflow")
    def handle_scan_overflow(ack, body, respond, logger):
        ack()
        selected = (
            (body.get("actions") or [{}])[0]
            .get("selected_option", {})
            .get("value", "")
        )
        user = body.get("user", {}).get("id", "?")
        logger.info("scan_overflow=%s by user=%s", selected, user)
        respond(
            text=OVERFLOW_REPLIES.get(selected, ":grey_question: Unknown option."),
            response_type="ephemeral",
            replace_original=False,
        )
