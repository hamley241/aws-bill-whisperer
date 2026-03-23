# AWS Bill Whisperer

AI-powered AWS cost analysis that explains your bill in plain English.

## What It Does

- Analyzes AWS costs using Bedrock (Claude) or OpenAI
- Scans for waste: idle instances, unattached volumes, old snapshots
- Connects billing trends to specific resource findings
- Provides actionable fix commands

## Features

- **AI Analysis**: Natural language explanations of your bill
- **Pattern Scanning**: Auto-detects cost optimization opportunities  
- **Slack Integration**: Get daily cost summaries in Slack
- **Self-Hosted**: Your data never leaves your AWS account

## Quick Start

```bash
# Install
pip install aws-bill-whisperer

# Configure AWS
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# Run analysis
whisper analyze --days 30

# Or scan for waste
whisper scan --patterns all
```

## Installation


<!-- Included from: docs/INSTALL.md -->
# Installation

## Requirements

- Python 3.10+
- AWS credentials with Cost Explorer access

## pip install

```bash
pip install aws-bill-whisperer
```

## From Source

```bash
git clone https://github.com/gpclaws/aws-bill-whisperer
cd aws-bill-whisperer
pip install -e .
```

<!-- End include: docs/INSTALL.md -->


## Usage


<!-- Included from: docs/USAGE.md -->
# Usage

## CLI

### Analyze costs

```bash
whisper analyze --days 30
```

### Scan for waste

```bash
whisper scan --patterns all --regions us-east-1
```

### Combined analysis

```bash
whisper full --output markdown
```

## Programmatic

```python
from whisperer import CostAnalyzer

analyzer = CostAnalyzer()
results = analyzer.analyze(days=30)
print(results.summary)
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_REGION` | Default region |
| `LLM_PROVIDER` | bedrock or openai |

<!-- End include: docs/USAGE.md -->


## License

MIT License - see LICENSE file.
