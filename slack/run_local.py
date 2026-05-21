"""
Local-dev entry point for the Whisper Slack app.

Two modes:

  --socket-mode (default if SLACK_APP_TOKEN is set)
      Uses Socket Mode — no public webhook needed. Best for laptop dev.
      Requires SLACK_APP_TOKEN (xapp-...) in addition to bot token.

  --http
      Starts a Flask-style HTTP listener on the given port. Point
      Slack's request URL (e.g. via ngrok) at /slack/events.

Both modes load credentials through WhisperConfig — env vars or
~/.whisper/config.toml per principle 9.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import load_config  # noqa: E402
from slack import make_app  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whisper-slack")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--socket-mode", action="store_true",
                      help="Use Socket Mode (requires SLACK_APP_TOKEN).")
    mode.add_argument("--http", action="store_true",
                      help="Start an HTTP listener for Slack request URLs.")
    parser.add_argument("--port", type=int, default=3000,
                        help="Port for --http mode (default: 3000).")
    parser.add_argument("--log-level", default="INFO",
                        help="Python logging level (default: INFO).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    cfg = load_config()
    app = make_app(cfg)

    socket_token = os.environ.get("SLACK_APP_TOKEN")
    use_socket = args.socket_mode or (not args.http and bool(socket_token))

    if use_socket:
        if not socket_token:
            print(
                "SLACK_APP_TOKEN is required for Socket Mode. "
                "Create one in your Slack app settings (xapp-...) and export it.",
                file=sys.stderr,
            )
            return 1
        from slack_bolt.adapter.socket_mode import SocketModeHandler
        SocketModeHandler(app, socket_token).start()
        return 0

    app.start(port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
