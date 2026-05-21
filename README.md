# AWS Bill Whisperer

The platform engineer's cost copilot. Explains AWS bills in plain
English, detects waste, and (in the paid tier) opens PRs to fix it —
all from Slack, all in your own AWS account.

> **Status**: open-source. The wedge is Slack-based scanning and
> conversational follow-ups; the moat is the safe-autonomy agent
> framework. See [CLAUDE.md](CLAUDE.md) for the architecture
> principles and the OSS / paid seam.

## What you get (open source)

- **20 waste patterns** — unattached EBS, idle EC2/RDS, expensive NAT
  Gateways, old snapshots, oversized Lambdas, CloudWatch metric
  cardinality, and more. New patterns are plug-in files.
- **Plain-English explanations** via Bedrock (in-account, default),
  OpenAI, or Anthropic-direct. You choose; we tell you when prompts
  leave your AWS account.
- **`/whisper scan` Slack app** that posts findings as Block Kit
  messages, with `@whisper` follow-ups in the thread.
- **CLI + JSON output** for piping into other tools or CI.
- **Local audit log** of every LLM prompt at `~/.whisper/prompts.log`.

## Quick start

Pick the path that fits how you want to use the Whisper.

### Slack app (the killer demo)

[`docs/slack-quickstart.md`](docs/slack-quickstart.md) walks through
`git clone` → first `/whisper scan` finding in under 10 minutes. Two
deployment options: local Socket Mode for laptop demos, AWS Lambda via
SAM for production.

### CLI scan

```bash
git clone https://github.com/hamley241/aws-bill-whisperer.git
cd aws-bill-whisperer
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Scan your AWS account for the 20 waste patterns.
python3 src/whisper.py scan

# Same scan, machine-readable.
python3 src/whisper.py scan --json
```

### Cost-narrative analysis

```bash
# Plain-English bill summary using your configured LLM.
python3 cli/analyze.py --days 30

# Without AWS credentials, on canned data:
python3 cli/analyze.py --mock
```

## Configuration

All knobs live in one place. Precedence: CLI flags > env vars >
`~/.whisper/config.toml` > defaults. Run the doctor to validate:

```bash
whisper-config doctor                # full report
whisper-config doctor --json         # CI-friendly
whisper-config doctor --check slack  # one capability
whisper-config doctor --no-network   # skip Slack auth.test
```

Exit code 0 means every capability you've configured is ready;
non-zero tells you exactly which check failed.

Common env vars:

| Variable | Purpose |
|----------|---------|
| `WHISPER_LLM_BACKEND` | `bedrock` (default), `openai`, or `anthropic` |
| `WHISPER_LLM_MODEL` | Model ID override |
| `AWS_PROFILE` / `AWS_REGION` | Standard AWS env vars |
| `OPENAI_API_KEY` | Required iff `WHISPER_LLM_BACKEND=openai` |
| `ANTHROPIC_API_KEY` | Required iff `WHISPER_LLM_BACKEND=anthropic` |
| `SLACK_BOT_TOKEN` | Your Slack app's bot token (xoxb-…) |
| `SLACK_SIGNING_SECRET` | Your Slack app's signing secret |
| `SLACK_APP_TOKEN` | Optional, for local Socket Mode (xapp-…) |
| `WHISPER_PROMPT_LOG_PATH` | Override the prompt-log location |

## What about the paid tier?

The paid tier is a deployable stack you run in your own AWS account —
not a hosted SaaS. It layers on top of the open-source code:

- AWS Organizations / multi-account scanning
- Continuous scheduled scans with state tracking
- PR-native autopilot (opens IaC PRs and tracks merges)
- SSO/SAML on the local dashboard, RBAC
- Enterprise connectors (Datadog, PagerDuty, ServiceNow)
- Support and SLA

If a single engineer with a laptop and one AWS account can do it,
it's in the open-source repo. If it requires production infrastructure
we maintain, it's paid. See [CLAUDE.md](CLAUDE.md) for the full seam.

## Architecture

- [`src/patterns/`](src/patterns/) — detection patterns (drop-in
  plugins, one file per pattern)
- [`src/llm/`](src/llm/) — single `LLMClient` abstraction with logged
  prompts, provider tagging, and token accounting
- [`src/prompts/`](src/prompts/) — every prompt the system sends, as
  a versioned template
- [`src/presenters/`](src/presenters/) — surface-agnostic `Finding`
  renderers (text, markdown, JSON, Block Kit)
- [`src/config.py`](src/config.py) — the one config schema
- [`slack/`](slack/) — Slack app (slash command, Block Kit, threads,
  Lambda adapter, manifest)
- [`cli/`](cli/) — CLI entry points
- [`tests/`](tests/) — 240+ tests, no AWS credentials required

Read [`CLAUDE.md`](CLAUDE.md) before contributing.

## License

MIT. See [LICENSE](LICENSE).
