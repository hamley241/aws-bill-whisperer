"""
WhisperConfig — single source of truth for configuration.

Implements CLAUDE.md principle 9: one module owns the merge and validation.
Precedence: CLI flags > environment variables > config file > defaults.

Capabilities are surfaced via the doctor so each milestone (scan, llm,
slack, ...) can answer "is this install ready for X?" without each
caller re-implementing checks.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]


DEFAULT_CONFIG_PATH = Path("~/.whisper/config.toml").expanduser()
DEFAULT_PROMPT_LOG_PATH = Path("~/.whisper/prompts.log").expanduser()


@dataclass
class WhisperConfig:
    """All config keys live here. Each one corresponds to a ConfigOption below."""

    llm_backend: str = "bedrock"
    llm_model: str | None = None
    aws_profile: str | None = None
    aws_region: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_webhook: str | None = None
    prompt_log_path: str = str(DEFAULT_PROMPT_LOG_PATH)
    analysis_days: int = 30
    # Plan-thread freshness tiers. The conversation layer reads these to
    # decide whether a follow-up question against a cached plan gets a
    # normal answer, an aging footer, a stale-warning prefix, or a hard
    # refusal. The defaults map to the user-visible trust contract
    # documented in agentic/plan_thread_qa_agentic.md.
    plan_thread_freshness_aging_after_min: int = 30
    plan_thread_freshness_stale_after_hours: int = 4
    plan_thread_freshness_expired_after_hours: int = 24

    # Provenance — set by load_config() so the doctor can show where each
    # value came from. Not part of the schema users edit.
    _sources: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ConfigOption:
    name: str
    env_vars: tuple[str, ...]  # primary first, then legacy fallbacks
    default: Any
    description: str
    secret: bool = False  # masked in doctor output
    coerce: Callable[[str], Any] = str
    choices: tuple[str, ...] | None = None


OPTIONS: tuple[ConfigOption, ...] = (
    ConfigOption(
        name="llm_backend",
        env_vars=("WHISPER_LLM_BACKEND", "LLM_PROVIDER"),
        default="bedrock",
        description="Which LLM provider to use for analysis and explanations.",
        choices=("bedrock", "openai", "anthropic"),
    ),
    ConfigOption(
        name="llm_model",
        env_vars=("WHISPER_LLM_MODEL", "LLM_MODEL"),
        default=None,
        description="Model ID override (e.g. anthropic.claude-3-5-sonnet-...).",
    ),
    ConfigOption(
        name="aws_profile",
        env_vars=("WHISPER_AWS_PROFILE", "AWS_PROFILE"),
        default=None,
        description="Named AWS profile to use for SDK calls.",
    ),
    ConfigOption(
        name="aws_region",
        env_vars=("WHISPER_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"),
        default=None,
        description="Default AWS region for scans and Bedrock.",
    ),
    ConfigOption(
        name="openai_api_key",
        env_vars=("OPENAI_API_KEY",),
        default=None,
        description="OpenAI API key (required if llm_backend=openai).",
        secret=True,
    ),
    ConfigOption(
        name="anthropic_api_key",
        env_vars=("ANTHROPIC_API_KEY",),
        default=None,
        description="Anthropic API key (required if llm_backend=anthropic).",
        secret=True,
    ),
    ConfigOption(
        name="slack_bot_token",
        env_vars=("SLACK_BOT_TOKEN",),
        default=None,
        description="Slack bot token (xoxb-...) for the customer's own Slack app.",
        secret=True,
    ),
    ConfigOption(
        name="slack_signing_secret",
        env_vars=("SLACK_SIGNING_SECRET",),
        default=None,
        description="Slack app signing secret for verifying inbound requests.",
        secret=True,
    ),
    ConfigOption(
        name="slack_webhook",
        env_vars=("SLACK_WEBHOOK",),
        default=None,
        description="Legacy incoming-webhook URL (used by Lambda handler).",
        secret=True,
    ),
    ConfigOption(
        name="prompt_log_path",
        env_vars=("WHISPER_PROMPT_LOG_PATH",),
        default=str(DEFAULT_PROMPT_LOG_PATH),
        description="Where every LLM prompt is logged (required by principle 5).",
    ),
    ConfigOption(
        name="analysis_days",
        env_vars=("WHISPER_ANALYSIS_DAYS", "ANALYSIS_DAYS"),
        default=30,
        description="Default look-back window for cost analysis.",
        coerce=int,
    ),
    ConfigOption(
        name="plan_thread_freshness_aging_after_min",
        env_vars=("WHISPER_PLAN_THREAD_AGING_MIN",),
        default=30,
        description=(
            "Minutes after which a plan thread's freshness footer is "
            "appended to conversational answers."
        ),
        coerce=int,
    ),
    ConfigOption(
        name="plan_thread_freshness_stale_after_hours",
        env_vars=("WHISPER_PLAN_THREAD_STALE_HOURS",),
        default=4,
        description=(
            "Hours after which a plan thread's answers gain a stale-warning "
            "prefix advising the user to re-scan."
        ),
        coerce=int,
    ),
    ConfigOption(
        name="plan_thread_freshness_expired_after_hours",
        env_vars=("WHISPER_PLAN_THREAD_EXPIRED_HOURS",),
        default=24,
        description=(
            "Hours after which a plan thread refuses to answer follow-ups "
            "against the cached plan; the framework returns a deterministic "
            "refusal instead of calling the LLM."
        ),
        coerce=int,
    ),
)


def _coerce(option: ConfigOption, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return option.coerce(value)
        except (ValueError, TypeError) as e:
            raise ValueError(f"invalid value for {option.name}: {value!r} ({e})") from e
    return value


def _from_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    # Flatten one level: support both [whisper] table and top-level keys.
    if "whisper" in data and isinstance(data["whisper"], dict):
        return data["whisper"]
    return data


def load_config(
    cli_overrides: dict[str, Any] | None = None,
    *,
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> WhisperConfig:
    """
    Merge config sources with precedence CLI > env > file > default.

    `env` defaults to os.environ; `config_path` defaults to ~/.whisper/config.toml.
    Returns a WhisperConfig with `_sources` annotated so the doctor knows
    where each value originated.
    """
    env = env if env is not None else dict(os.environ)
    cli_overrides = cli_overrides or {}
    path = config_path if config_path is not None else DEFAULT_CONFIG_PATH

    file_values = _from_file(path)

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for opt in OPTIONS:
        chosen: Any = opt.default
        source = "default"

        if opt.name in file_values:
            chosen = file_values[opt.name]
            source = f"file:{path}"
        for env_var in opt.env_vars:
            if env_var in env and env[env_var] != "":
                chosen = env[env_var]
                source = f"env:{env_var}"
                break
        if opt.name in cli_overrides and cli_overrides[opt.name] is not None:
            chosen = cli_overrides[opt.name]
            source = "cli"

        values[opt.name] = _coerce(opt, chosen)
        sources[opt.name] = source

    cfg = WhisperConfig(**values)
    cfg._sources = sources
    return cfg


# ---------------------------------------------------------------------------
# Validation / doctor checks
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    capability: str  # e.g. "scan", "llm:openai"
    ok: bool
    message: str


def _check_choice(cfg: WhisperConfig) -> list[CheckResult]:
    """Settings-level validation (choices, ranges)."""
    results: list[CheckResult] = []
    for opt in OPTIONS:
        if opt.choices:
            value = getattr(cfg, opt.name)
            if value is not None and value not in opt.choices:
                results.append(CheckResult(
                    capability=f"setting:{opt.name}",
                    ok=False,
                    message=f"{value!r} not in {opt.choices}",
                ))
    return results


def _check_plan_thread_freshness(cfg: WhisperConfig) -> CheckResult:
    """Freshness tiers must be monotonically increasing once normalised to
    the same unit. Misordered thresholds would silently swap which tier
    a given age falls into and produce contradictory user-visible
    language; failing loud at doctor time is the cheaper failure mode."""
    aging_min = cfg.plan_thread_freshness_aging_after_min
    stale_min = cfg.plan_thread_freshness_stale_after_hours * 60
    expired_min = cfg.plan_thread_freshness_expired_after_hours * 60
    if not (0 < aging_min < stale_min < expired_min):
        return CheckResult(
            capability="setting:plan_thread_freshness",
            ok=False,
            message=(
                f"freshness thresholds must satisfy "
                f"0 < aging({aging_min}m) < stale({stale_min}m) < "
                f"expired({expired_min}m)"
            ),
        )
    return CheckResult(
        capability="setting:plan_thread_freshness",
        ok=True,
        message=(
            f"aging>{aging_min}m, stale>{cfg.plan_thread_freshness_stale_after_hours}h, "
            f"expired>{cfg.plan_thread_freshness_expired_after_hours}h"
        ),
    )


def _check_scan(cfg: WhisperConfig) -> CheckResult:
    """Can we make AWS API calls? Local-credentials check only — no network."""
    try:
        import boto3
        session_kwargs: dict[str, Any] = {}
        if cfg.aws_profile:
            session_kwargs["profile_name"] = cfg.aws_profile
        if cfg.aws_region:
            session_kwargs["region_name"] = cfg.aws_region
        session = boto3.Session(**session_kwargs)
        creds = session.get_credentials()
        if creds is None:
            return CheckResult(
                capability="scan",
                ok=False,
                message="no AWS credentials found (try `aws configure` or AWS_PROFILE)",
            )
        return CheckResult(
            capability="scan",
            ok=True,
            message=f"credentials available (profile={cfg.aws_profile or '<default>'})",
        )
    except Exception as e:  # pragma: no cover — boto3 import or session failure
        return CheckResult(capability="scan", ok=False, message=str(e))


def _check_llm(cfg: WhisperConfig) -> list[CheckResult]:
    results: list[CheckResult] = []
    backend = cfg.llm_backend

    if backend == "bedrock":
        scan = _check_scan(cfg)
        results.append(CheckResult(
            capability="llm:bedrock",
            ok=scan.ok,
            message="AWS credentials available" if scan.ok
                    else "Bedrock needs AWS credentials (same as scan)",
        ))
    elif backend == "openai":
        ok = bool(cfg.openai_api_key)
        results.append(CheckResult(
            capability="llm:openai",
            ok=ok,
            message="OPENAI_API_KEY set" if ok else "OPENAI_API_KEY not set",
        ))
    elif backend == "anthropic":
        ok = bool(cfg.anthropic_api_key)
        results.append(CheckResult(
            capability="llm:anthropic",
            ok=ok,
            message="ANTHROPIC_API_KEY set" if ok else "ANTHROPIC_API_KEY not set",
        ))
    else:
        results.append(CheckResult(
            capability=f"llm:{backend}",
            ok=False,
            message=f"unknown llm_backend {backend!r}",
        ))
    return results


def _check_slack(cfg: WhisperConfig, *, verify_token: bool = True) -> list[CheckResult]:
    """Verify Slack credentials. When verify_token=True and a bot token is
    present, performs a real auth.test against Slack (one HTTP request)."""
    results: list[CheckResult] = []
    has_app = bool(cfg.slack_bot_token and cfg.slack_signing_secret)
    if not has_app:
        results.append(CheckResult(
            capability="slack",
            ok=False,
            message="SLACK_BOT_TOKEN and/or SLACK_SIGNING_SECRET not set",
        ))
    elif not verify_token:
        results.append(CheckResult(
            capability="slack",
            ok=True,
            message="bot token and signing secret set (auth.test skipped)",
        ))
    else:
        results.append(_slack_auth_test(cfg))

    if cfg.slack_webhook:
        results.append(CheckResult(
            capability="slack-webhook",
            ok=True,
            message="incoming-webhook URL configured (legacy path)",
        ))
    return results


def _slack_auth_test(cfg: WhisperConfig) -> CheckResult:
    """Call Slack's auth.test to confirm the bot token actually works.

    Lazy-imports slack_sdk so the doctor still loads if it isn't installed
    (it always is in practice — slack-bolt depends on it).
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:  # pragma: no cover — slack-bolt always brings slack-sdk
        return CheckResult(
            capability="slack",
            ok=False,
            message="slack_sdk not installed (pip install slack-bolt)",
        )

    try:
        resp = WebClient(token=cfg.slack_bot_token).auth_test()
    except SlackApiError as e:
        return CheckResult(
            capability="slack",
            ok=False,
            message=f"auth.test failed: {e.response.get('error', e)}",
        )
    except Exception as e:  # network errors, etc.
        return CheckResult(
            capability="slack",
            ok=False,
            message=f"auth.test errored: {e}",
        )

    team = resp.get("team", "?")
    user = resp.get("user", "?")
    return CheckResult(
        capability="slack",
        ok=True,
        message=f"authenticated as {user}@{team}",
    )


def _check_prompt_log(cfg: WhisperConfig) -> CheckResult:
    """Confirm the prompt log directory exists or can be created."""
    path = Path(cfg.prompt_log_path).expanduser()
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        # Check writability without actually writing.
        return CheckResult(
            capability="prompt-log",
            ok=os.access(parent, os.W_OK),
            message=f"writable: {parent}" if os.access(parent, os.W_OK) else f"not writable: {parent}",
        )
    except OSError as e:
        return CheckResult(capability="prompt-log", ok=False, message=str(e))


def run_checks(cfg: WhisperConfig, *, verify_slack: bool = True) -> list[CheckResult]:
    """Run every doctor check. Order matters for output readability.

    `verify_slack=False` skips the network call to Slack's auth.test
    (offline mode / fast CI).
    """
    results: list[CheckResult] = []
    results.extend(_check_choice(cfg))
    results.append(_check_plan_thread_freshness(cfg))
    results.append(_check_scan(cfg))
    results.extend(_check_llm(cfg))
    results.extend(_check_slack(cfg, verify_token=verify_slack))
    results.append(_check_prompt_log(cfg))
    return results


def settings_view(cfg: WhisperConfig) -> list[tuple[ConfigOption, Any, str]]:
    """For doctor output — yield (option, value, source) per setting."""
    out: list[tuple[ConfigOption, Any, str]] = []
    for opt in OPTIONS:
        value = getattr(cfg, opt.name)
        source = cfg._sources.get(opt.name, "default")
        out.append((opt, value, source))
    return out


def field_names() -> list[str]:
    """All config field names (used by tests + tooling)."""
    return [f.name for f in fields(WhisperConfig) if not f.name.startswith("_")]
