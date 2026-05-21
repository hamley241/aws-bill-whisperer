# Installation

## Requirements

- Python 3.10+
- AWS credentials with at least `ce:GetCostAndUsage`,
  `cloudwatch:GetMetricStatistics`, and the `ec2:Describe*` /
  `rds:DescribeDBInstances` family used by the 20 waste patterns.
  (Full IAM policy in [`template.yaml`](../template.yaml) under
  `WhisperSlackFunction`.)
- For Slack: a workspace you can create custom apps in.

## From source (recommended)

```bash
git clone https://github.com/hamley241/aws-bill-whisperer.git
cd aws-bill-whisperer
python3 -m venv .venv && source .venv/bin/activate

# Core install + dev tooling
pip install -e '.[dev]'

# Optional LLM providers
pip install -e '.[openai]'           # OpenAI API
pip install anthropic                # Anthropic-direct (no extra yet)
```

This gives you three console scripts:

- `aws-bill-whisperer` — the original cost-narrative CLI
- `whisper-config` — `whisper-config doctor` (validate config)

…plus runnable Python entry points:

- `python -m slack.run_local` — local Slack webhook (Socket Mode or HTTP)
- `python -m cli.doctor doctor` — the doctor command via module path
- `python src/whisper.py scan` — scan-only CLI

## Verify

```bash
whisper-config doctor
```

If you see green ticks for `scan` and your configured LLM backend,
you're ready to run scans.

## Next: choose a path

- **Slack app** (recommended) — see
  [`slack-quickstart.md`](slack-quickstart.md).
- **CLI scans** — `python3 src/whisper.py scan` (see [`USAGE.md`](USAGE.md)).
- **AWS Lambda deployment** — `sam deploy` against
  [`template.yaml`](../template.yaml); covered in the Slack quickstart.

## Data sovereignty

The OSS install runs entirely in your environment. No telemetry, no
phone-home. Bedrock keeps prompts inside your AWS account; OpenAI /
Anthropic-direct send prompts across the boundary — the prompt log at
`~/.whisper/prompts.log` records which calls crossed.
