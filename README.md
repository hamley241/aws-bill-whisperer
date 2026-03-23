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

See [docs/INSTALL.md](docs/INSTALL.md) for detailed setup.

## Usage

See [docs/USAGE.md](docs/USAGE.md) for full CLI reference.

## Architecture

See [docs/ARCHITECTURE-ANALYSIS.md](docs/ARCHITECTURE-ANALYSIS.md) for technical details.

## License

MIT License - see LICENSE file.
