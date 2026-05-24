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
