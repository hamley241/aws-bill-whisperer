"""
AWS Lambda entry point for the Whisper Slack app.

Slack delivers slash commands, interactive components, and events to
the API Gateway URL the customer registers. Bolt's
SlackRequestHandler translates Lambda's event/context shape into the
Bolt App's handlers — same code path as the local Socket/HTTP runners.

The handler module construction (load_config, make_app) runs once per
cold start; warm invocations reuse the App. The Lambda environment
needs all the same env vars the local runner needs:
  SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
  WHISPER_LLM_BACKEND (default bedrock) + any matching API key
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import load_config  # noqa: E402

from . import make_app  # noqa: E402


_config = load_config()
_app = make_app(_config)


def handler(event, context):
    """API Gateway → Bolt entry point."""
    # Lazy import keeps `import slack.lambda_handler` cheap for tests
    # that don't actually invoke Lambda.
    from slack_bolt.adapter.aws_lambda import SlackRequestHandler
    return SlackRequestHandler(_app).handle(event, context)
