"""
AWS Bill Whisperer — Slack integration (self-hosted).

This module is the customer's own Slack app. There is no vendor-hosted
Slack endpoint; everything runs inside the customer's infrastructure
(per CLAUDE.md data sovereignty principle).

Public surface:
    from slack import make_app
    app = make_app(load_config())
"""

from .app import make_app

__all__ = ["make_app"]
