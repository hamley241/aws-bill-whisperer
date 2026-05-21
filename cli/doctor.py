#!/usr/bin/env python3
"""
`whisper config doctor` — validate the whole config schema.

Per CLAUDE.md principle 9, this command is a universal validator over
every config option, not just the ones the current milestone needs. New
milestones extend the option/capability registry in src/config.py; this
command's surface stays stable.

Exit code 0 if all capabilities the user appears to want are ready.
Exit code 1 if anything required for the configured llm_backend or for
scanning is broken.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add src to path so `python -m cli.doctor` works without install.
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import (
    DEFAULT_CONFIG_PATH,
    CheckResult,
    ConfigOption,
    load_config,
    run_checks,
    settings_view,
)


def _supports_color(stream) -> bool:
    return getattr(stream, "isatty", lambda: False)() and os.environ.get("NO_COLOR") is None


class _Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _w(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text

    def green(self, text: str) -> str: return self._w("32", text)
    def red(self, text: str) -> str: return self._w("31", text)
    def yellow(self, text: str) -> str: return self._w("33", text)
    def dim(self, text: str) -> str: return self._w("2", text)
    def bold(self, text: str) -> str: return self._w("1", text)


def _mask(value: object, secret: bool) -> str:
    if value is None or value == "":
        return "(unset)"
    if secret:
        s = str(value)
        return ("•" * 4) + s[-4:] if len(s) > 4 else "••••"
    return str(value)


def render_settings(view: list[tuple[ConfigOption, object, str]], style: _Style) -> str:
    lines = [style.bold("Settings")]
    name_w = max(len(opt.name) for opt, _, _ in view)
    val_w = max(len(_mask(v, opt.secret)) for opt, v, _ in view)
    for opt, value, source in view:
        rendered_value = _mask(value, opt.secret)
        line = (
            f"  {opt.name:<{name_w}}  "
            f"{rendered_value:<{val_w}}  "
            f"{style.dim('[' + source + ']')}"
        )
        lines.append(line)
    return "\n".join(lines)


def render_checks(checks: list[CheckResult], style: _Style) -> str:
    lines = [style.bold("Capabilities")]
    cap_w = max((len(c.capability) for c in checks), default=10)
    for c in checks:
        mark = style.green("✓") if c.ok else style.red("✗")
        lines.append(f"  {mark} {c.capability:<{cap_w}}  {c.message}")
    return "\n".join(lines)


def summarize(checks: list[CheckResult]) -> tuple[list[str], list[str]]:
    ready = [c.capability for c in checks if c.ok and not c.capability.startswith("setting:")]
    blocked = [c.capability for c in checks if not c.ok]
    return ready, blocked


def doctor(config_path: Path | None = None) -> int:
    style = _Style(_supports_color(sys.stdout))
    path = config_path or DEFAULT_CONFIG_PATH

    print(style.bold("WHISPER CONFIG DOCTOR"))
    file_note = (
        f"using {path}" if path.exists() else f"{path} not found — using env + defaults"
    )
    print(f"  {style.dim(file_note)}")
    print()

    cfg = load_config(config_path=path)
    print(render_settings(settings_view(cfg), style))
    print()

    checks = run_checks(cfg)
    print(render_checks(checks, style))
    print()

    ready, blocked = summarize(checks)
    if ready:
        print(style.bold("Ready for: ") + ", ".join(ready))
    if blocked:
        print(style.yellow("Not ready: ") + ", ".join(blocked))

    # Exit code: 0 only if scan and the configured llm backend are both OK.
    required = {"scan", f"llm:{cfg.llm_backend}"}
    return 0 if required.issubset(set(ready)) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whisper-config",
        description="Configuration tools for AWS Bill Whisperer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_p = sub.add_parser("doctor", help="Validate the current configuration.")
    doctor_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to TOML config (default: {DEFAULT_CONFIG_PATH}).",
    )

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor(args.config)
    return 1  # pragma: no cover — argparse rejects unknown subcommands


if __name__ == "__main__":
    sys.exit(main())
