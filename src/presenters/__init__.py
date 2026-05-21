"""Presentation layer — see CLAUDE.md principle 3.

Surfaces render Finding objects via these presenters. New surfaces add
a new presenter; they never reach for detection or remediation code.
"""

from .base import FindingPresenter, ScanResult
from .json_presenter import JSONPresenter
from .markdown import MarkdownPresenter
from .slack_blocks import BlockKitPresenter
from .text import TextPresenter

__all__ = [
    "FindingPresenter",
    "ScanResult",
    "TextPresenter",
    "MarkdownPresenter",
    "JSONPresenter",
    "BlockKitPresenter",
]
