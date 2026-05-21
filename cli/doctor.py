#!/usr/bin/env python3
"""
`whisper-config doctor` — validate the whole config schema.

Per CLAUDE.md principle 9, this command is a universal validator over
every config option, not just the ones the current milestone needs. New
milestones extend the option/capability registry in src/config.py; this
command's surface stays stable.

Exit code 0 if all capabilities the user appears to want are ready.
Exit code 1 if anything required for the configured llm_backend or for
scanning is broken.

Flags:
  --json              Machine-readable output for CI / scripting.
  --check NAME[,…]    Run only checks whose capability matches one of
                      the comma-separated prefixes (e.g. "scan",
                      "slack", "llm").
  --no-network        Skip checks that hit the network (Slack auth.test).
"""

from __future__ import annotations

import argparse
import json
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


def _filter_checks(checks: list[CheckResult],
                   selectors: list[str] | None) -> list[CheckResult]:
    """Keep only checks whose capability starts with one of the selectors.

    `selectors=["slack"]` keeps "slack" and "slack-webhook".
    `selectors=["llm"]` keeps "llm:bedrock", "llm:openai", "llm:anthropic".
    Empty/None means keep everything.
    """
    if not selectors:
        return checks
    out: list[CheckResult] = []
    for c in checks:
        if any(c.capability == s or c.capability.startswith(s + ":")
               or c.capability.startswith(s + "-")
               or c.capability.startswith(s)
               for s in selectors):
            out.append(c)
    return out


def _exit_code(checks: list[CheckResult], cfg, selectors: list[str] | None) -> int:
    """0 iff every required capability is OK.

    Without --check, requirements are {scan, llm:<backend>}. With --check,
    requirements are exactly the checks the user asked about — any one
    failing is exit 1.
    """
    if selectors:
        return 0 if all(c.ok for c in checks) else 1
    ready, _ = summarize(checks)
    required = {"scan", f"llm:{cfg.llm_backend}"}
    return 0 if required.issubset(set(ready)) else 1


def doctor(
    config_path: Path | None = None,
    *,
    selectors: list[str] | None = None,
    as_json: bool = False,
    no_network: bool = False,
) -> int:
    path = config_path or DEFAULT_CONFIG_PATH
    cfg = load_config(config_path=path)
    all_checks = run_checks(cfg, verify_slack=not no_network)
    checks = _filter_checks(all_checks, selectors)

    if as_json:
        sys.stdout.write(json.dumps(_json_payload(path, cfg, checks, selectors),
                                    indent=2, default=str))
        sys.stdout.write("\n")
        return _exit_code(checks, cfg, selectors)

    style = _Style(_supports_color(sys.stdout))
    print(style.bold("WHISPER CONFIG DOCTOR"))
    file_note = (
        f"using {path}" if path.exists() else f"{path} not found — using env + defaults"
    )
    print(f"  {style.dim(file_note)}")
    print()

    if not selectors:
        print(render_settings(settings_view(cfg), style))
        print()

    print(render_checks(checks, style))
    print()

    ready, blocked = summarize(checks)
    if ready:
        print(style.bold("Ready for: ") + ", ".join(ready))
    if blocked:
        print(style.yellow("Not ready: ") + ", ".join(blocked))

    return _exit_code(checks, cfg, selectors)


def _json_payload(
    path: Path,
    cfg,
    checks: list[CheckResult],
    selectors: list[str] | None,
) -> dict:
    return {
        "config_path": str(path),
        "config_path_exists": path.exists(),
        "selectors": selectors or [],
        "settings": [
            {
                "name": opt.name,
                "value": _mask(value, opt.secret),
                "source": source,
                "secret": opt.secret,
            }
            for opt, value, source in settings_view(cfg)
        ],
        "checks": [
            {"capability": c.capability, "ok": c.ok, "message": c.message}
            for c in checks
        ],
        "ready": [c.capability for c in checks
                  if c.ok and not c.capability.startswith("setting:")],
        "blocked": [c.capability for c in checks if not c.ok],
    }


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
    doctor_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output machine-readable JSON instead of the formatted report.",
    )
    doctor_p.add_argument(
        "--check",
        action="append",
        metavar="CAPABILITY",
        help="Run only checks whose capability matches the given prefix. "
             "Repeatable. E.g. --check slack --check scan.",
    )
    doctor_p.add_argument(
        "--no-network",
        action="store_true",
        help="Skip checks that hit the network (Slack auth.test).",
    )

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor(
            args.config,
            selectors=args.check,
            as_json=args.as_json,
            no_network=args.no_network,
        )
    return 1  # pragma: no cover — argparse rejects unknown subcommands


if __name__ == "__main__":
    sys.exit(main())
