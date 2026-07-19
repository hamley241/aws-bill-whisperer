"""
Interactive action handlers (buttons, overflow menus).

The Open-PR button looks up the finding in the active thread's
ScanResult, dispatches the pattern's `pr` remediation mode through
audit.audit_remediation, and posts the result back in the thread.

Patterns that don't implement `pr` mode return a failed
RemediationResult; we surface that message to the user verbatim so
they know what's missing (typically: missing Terraform tag).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

_SRC = Path(__file__).parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from audit import audit_remediation  # noqa: E402
from patterns import discover_patterns  # noqa: E402
from patterns.base import Finding, RemediationMode  # noqa: E402

from ..thread_store import get_store  # noqa: E402


PR_NO_THREAD_TEXT = (
    ":grey_question: I can't find the scan this finding came from. "
    "Run `/whisper scan` again and click *Open PR* on a fresh finding."
)
PR_FINDING_MISSING_TEXT = (
    ":grey_question: That finding is no longer in this thread's scan. "
    "Run `/whisper scan` again to refresh."
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


# Tests inject a stub.
_pattern_resolver: Callable[[str], type | None] = None  # set lazily


def _resolve_pattern_class(pattern_id: str) -> type | None:
    """Find the pattern class with a given PATTERN_ID. Cached at module level."""
    global _pattern_resolver
    if _pattern_resolver is None:
        index = {cls.PATTERN_ID: cls for cls in discover_patterns()}
        _pattern_resolver = index.get
    return _pattern_resolver(pattern_id)


def _find_finding_in_threads(finding_id: str) -> tuple[str, Finding] | None:
    """Search every known thread's ScanResult for a finding with this id.

    Returns (thread_ts, finding) on a hit. We search across threads
    rather than keying by thread because the button click only carries
    the finding id.
    """
    store = get_store()
    # Walk InMemoryThreadStore's internal dict (best-effort — the
    # Protocol doesn't define iteration, but the OSS default does).
    # PR #9: the values are ThreadContext wrappers now; the findings
    # live on context.scan_result.findings.
    items = getattr(store, "_contexts", None)
    if items is None:
        return None
    for thread_ts, context in items.items():
        for finding in context.scan_result.findings:
            if finding.id == finding_id:
                return thread_ts, finding
    return None


def register(app: Any) -> None:
    """Attach button + overflow listeners to the given Bolt App."""

    @app.action("open_pr")
    def handle_open_pr(ack, body, client, logger):
        ack()
        finding_id = (body.get("actions") or [{}])[0].get("value", "?")
        channel = body.get("channel", {}).get("id")
        message = body.get("message") or {}
        thread_ts = message.get("thread_ts") or message.get("ts")
        user = body.get("user", {}).get("id", "?")

        logger.info("open_pr clicked by user=%s for finding=%s", user, finding_id)

        hit = _find_finding_in_threads(finding_id)
        if hit is None:
            _post(client, channel, thread_ts, PR_FINDING_MISSING_TEXT, logger)
            return
        _, finding = hit

        pattern_cls = _resolve_pattern_class(finding.pattern_id)
        if pattern_cls is None:
            _post(client, channel, thread_ts,
                  f":grey_question: No pattern registered for "
                  f"`{finding.pattern_id}`.", logger)
            return

        result = audit_remediation(
            pattern_cls(),
            finding,
            RemediationMode.PR,
            actor=user,
        )

        if result.success and result.output:
            _post(client, channel, thread_ts,
                  f":white_check_mark: *PR change for `{finding.resource_id}`*\n"
                  f"```{result.output}```", logger)
        else:
            _post(client, channel, thread_ts,
                  f":no_entry_sign: *Open PR refused for `{finding.resource_id}`*\n"
                  f"{result.message}", logger)

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


def _post(client, channel, thread_ts, text, logger) -> None:
    try:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except Exception:
        logger.exception("chat.postMessage failed")
