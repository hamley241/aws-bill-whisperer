"""
Bolt App factory.

WhisperConfig is the single source of credentials (principle 9). The
factory wires the configured bot token + signing secret into a
slack_bolt.App and registers every handler.

Only construction logic lives here. Each handler module owns its own
slash-command / event registration via a register() function.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from slack_bolt import App

from .handlers import actions as action_handlers
from .handlers import scan as scan_handler

if TYPE_CHECKING:
    from config import WhisperConfig


def make_app(config: "WhisperConfig") -> App:
    """Build a Bolt App from the customer's config.

    Raises ValueError if Slack credentials are missing — Slack functionality
    is opt-in and never silently disabled.
    """
    if not config.slack_bot_token:
        raise ValueError(
            "slack_bot_token is not set. Configure SLACK_BOT_TOKEN or run "
            "`whisper-config doctor` for help."
        )
    if not config.slack_signing_secret:
        raise ValueError(
            "slack_signing_secret is not set. Configure SLACK_SIGNING_SECRET "
            "or run `whisper-config doctor` for help."
        )

    app = App(
        token=config.slack_bot_token,
        signing_secret=config.slack_signing_secret,
        # Don't ping Slack at construction time — keeps the factory
        # offline-safe and tests fast. `whisper-config doctor` can run
        # an explicit auth.test when the user asks.
        token_verification_enabled=False,
    )

    # Stash the config on the app so handlers can access it. Bolt has no
    # built-in DI; this is the conventional pattern.
    app._whisper_config = config  # type: ignore[attr-defined]

    scan_handler.register(app)
    action_handlers.register(app)

    return app
