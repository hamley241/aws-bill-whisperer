"""
Slack mrkdwn safety helpers.

Slack's mrkdwn format treats certain characters as control characters
for mentions (`<@USER>`), broadcasts (`<!channel>`, `<!here>`), and
links (`<URL>`, `<URL|label>`). When the renderer interpolates
untrusted text — LLM output (rationales, summaries, explanations),
user slash-command input (the `goal: ...` text), or scanner-derived
content that could contain user-controlled resource names — into an
mrkdwn field, those control characters become an injection surface:

  - A prompt-injected rationale could emit `<!channel>` and ping the
    entire shared channel from a chat-driven analysis.
  - A malicious resource name could include `<@U_admin>` and impersonate
    a mention.
  - A poisoned summary could embed `<https://evil.example|click here>`
    and render a deceptive clickable link.

`escape_mrkdwn` applies Slack's documented escape rules:
https://api.slack.com/reference/surfaces/formatting#escaping

    &  →  &amp;
    <  →  &lt;
    >  →  &gt;

This breaks all three angle-bracket-based injection vectors without
disturbing legitimate mrkdwn formatting (`*bold*`, `_italic_`,
`` `code` ``, etc.) — which we deliberately let through so
human-readable rationales render the way the planner intended.

Apply to every untrusted field interpolated into mrkdwn. Skip
renderer-internal text (mode badges, headers, fixed strings, numeric
formats) — those are deterministic and safe by construction.
"""

from __future__ import annotations


def escape_mrkdwn(text: str | None) -> str:
    """Apply Slack's documented mrkdwn escape rules.

    Pass-through for non-string inputs (None renders as empty string —
    defensive against missing fields). Order matters: `&` is escaped
    first so subsequent replacements don't get re-escaped.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Slack's per-text-element ceiling for `mrkdwn` (section / context).
# Source: https://api.slack.com/reference/block-kit/composition-objects#text
# Exceeding this returns `invalid_blocks` from chat.postMessage and the
# message is rejected — the same failure mode as the 50-block limit.
SLACK_MAX_MRKDWN_CHARS = 3000

_ELLIPSIS = "… (clipped)"


def clip_for_mrkdwn(text: str, max_chars: int = SLACK_MAX_MRKDWN_CHARS) -> str:
    """Clip an already-escaped string to fit a Slack text-element budget.

    Appends "… (clipped)" so truncation is visible to the user (silent
    truncation would be worse — they'd think they're seeing the whole
    rationale). Walks back from the cut point if it lands inside an
    HTML entity (e.g. `&am`) so we don't leave a malformed entity prefix.

    Apply AFTER `escape_mrkdwn` so the budget reflects what Slack receives
    (escape can expand `<` to `&lt;`, 4x worst case). Callers should
    pass a per-field budget that leaves headroom for the surrounding
    template (titles, prefixes, decorators) summing to under the hard
    Slack limit per composed block.
    """
    if len(text) <= max_chars:
        return text
    budget = max(0, max_chars - len(_ELLIPSIS))
    out = text[:budget]
    # If we cut mid-entity, walk back to the `&` so we don't render
    # `&am…` as literal characters.
    amp = out.rfind("&")
    if amp >= 0 and ";" not in out[amp:]:
        out = out[:amp]
    return out + _ELLIPSIS


def safe_mrkdwn(text: str | None, max_chars: int = SLACK_MAX_MRKDWN_CHARS) -> str:
    """Escape + clip in one call. The canonical way to interpolate an
    untrusted field into a Slack mrkdwn text element."""
    return clip_for_mrkdwn(escape_mrkdwn(text), max_chars)


def safe_mrkdwn_code(text: str | None,
                     max_chars: int = SLACK_MAX_MRKDWN_CHARS) -> str:
    """Like `safe_mrkdwn` but additionally strips backticks.

    Use when the result will be placed inside a Slack inline code span
    or triple-backtick code fence. A backtick inside the content would
    otherwise close the surrounding code span prematurely and let any
    following content render as raw mrkdwn — re-introducing the injection
    vector even when angle-bracket escaping is in place. Slack mrkdwn
    has no documented backtick escape, so we substitute single-quote.
    """
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    s = s.replace("`", "'")
    return safe_mrkdwn(s, max_chars)
