# AWS Bill Whisperer

> **The self-hosted, privacy-first AWS cost analyzer.**
> Your billing data never leaves your account.

Most AWS cost tools are SaaS platforms that require access to your billing data. For security-conscious teams, regulated industries, or anyone with strict data governance — **that's a non-starter.**

AWS Bill Whisperer is different: it runs **100% inside your AWS account**. Deploy via CloudFormation, get AI-powered cost explanations via Bedrock. Your data stays yours.

---

## 🔐 Why Bill Whisperer?

| | Bill Whisperer | SaaS Tools (Vantage, etc.) |
|--|----------------|----------------------------|
| **Where data lives** | Your AWS account | Third-party infrastructure |
| **Who sees your costs** | Only you | Vendor + their infra |
| **Monthly cost** | ~$1-5 (AWS charges) | $30-200+ subscription |
| **Customization** | Fork it (MIT license) | Limited |
| **Vendor lock-in** | None | Yes |

---

## 🏥 Regulated Industries (Healthcare, Finance, Government)

For organizations operating under **HIPAA**, **SOC 2**, **FedRAMP**, or **data residency requirements**, sending billing data to third-party SaaS platforms often requires legal review, BAAs, or is simply prohibited by policy. Bill Whisperer eliminates this friction entirely — your cost data never leaves your AWS account, Bedrock runs in your region, and you maintain full audit control. This makes it ideal for health systems, financial institutions, and government contractors who need cost visibility without compliance headaches.

---

## 🎯 What It Does

1. Reads your AWS Cost & Usage data
2. Analyzes with AI (Claude via Bedrock)
3. Explains in plain English:
   - Why your bill changed
   - Top cost drivers
   - Actionable recommendations

## 🏗️ Architecture

```
Your AWS Account
├── Lambda Function (analyzer)
├── Cost Explorer API access
├── Bedrock Claude (or external LLM)
├── S3 bucket (optional: CUR data)
└── EventBridge (scheduled runs)

Output options:
├── Slack message
├── Email (SES)
├── S3 report
└── CLI stdout
```

## 💰 Your Cost

Running in your account: **~$1-5/month**
- Lambda: ~$0.50 (few invocations)
- Bedrock Claude: ~$1-3 (depending on bill size)
- S3: negligible

## 🚀 Quick Start

### Option 1: SAM Deploy (Recommended)
```bash
# Clone
git clone https://github.com/hamley241/aws-bill-whisperer
cd aws-bill-whisperer

# Deploy
sam build
sam deploy --guided
```

### Option 2: CLI
```bash
# Clone
git clone https://github.com/hamley241/aws-bill-whisperer
cd aws-bill-whisperer

# Deploy
sam build
sam deploy --guided
```

### Option 3: CLI (API)
```bash
# Run locally against your AWS credentials
pip install -r requirements.txt
python cli/analyze.py --days 30
```

### Option 4: CLI (CSV)
```bash
# Analyze from a Cost & Usage Report CSV (no API access needed)
python cli/analyze.py --csv your-cur-export.csv
```

This is useful for:
- Testing without Cost Explorer access
- Air-gapped environments
- Historical analysis from exported data

## 📊 Sample Output

```
📊 AWS Bill Summary: January 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 Total: $1,247.32 (+18% from December)

🔥 Why the increase?
1. EC2 in us-east-1: +$156 (3 new instances, forgot to terminate dev)
2. S3 Transfer: +$89 (CloudFront cache miss spike on Jan 15)
3. RDS: +$42 (storage auto-scaled from 100GB → 150GB)

📈 Top 5 Services:
1. EC2        $523.41  (42%)
2. RDS        $312.18  (25%)
3. S3         $198.44  (16%)
4. Lambda     $87.22   (7%)
5. CloudFront $52.11   (4%)

💡 Recommendations:
• Terminate 3 stopped EC2 instances → Save $89/mo
• Enable S3 Intelligent Tiering → Save ~$40/mo
• Consider Reserved Instances for prod EC2 → Save ~$150/mo

Potential monthly savings: $279
```

## 📖 Understanding the Output

Here's what each section of the report means:

### Header Summary
```
💰 Total: $1,247.32 (+18% from December)
```
- **Total**: Your AWS spend for the analysis period (default: 30 days)
- **Change %**: Comparison to previous period (same length, immediately before)
- 📈 = increase, 📉 = decrease, ➡️ = stable (<5% change)

### Why the Increase/Decrease
```
🔥 Why the increase?
1. EC2 in us-east-1: +$156 (3 new instances, forgot to terminate dev)
```
The AI analyzes your cost changes and explains the **likely reasons**:
- Which service changed the most
- Which region it occurred in
- What probably caused it (based on common patterns)

> ⚠️ **Note**: The AI makes educated guesses based on patterns. Always verify before taking action.

### Top Services
```
📈 Top 5 Services:
1. EC2        $523.41  (42%)
```
Your highest-cost AWS services, sorted by spend:
- **Service name**: AWS service (EC2, RDS, S3, etc.)
- **Cost**: Dollar amount for the period
- **Percentage**: Share of your total bill

### Recommendations
```
💡 Recommendations:
• Terminate 3 stopped EC2 instances → Save $89/mo
```
Actionable suggestions to reduce costs:
- **Specific**: Names exact resources when possible
- **Estimated savings**: Approximate monthly savings
- **Prioritized**: Ordered by potential impact

Common recommendation types:
| Type | What It Means |
|------|---------------|
| Terminate instances | EC2 instances that are stopped but still incurring EBS costs |
| Right-size | Instances using <20% CPU could be smaller |
| Reserved/Savings Plans | Stable workloads that could use commitments |
| Storage tiering | S3 data that could move to cheaper storage classes |
| Cleanup | Unattached EBS volumes, old snapshots, unused IPs |

### Potential Savings
```
Potential monthly savings: $279
```
Sum of all recommendation savings. This is an **estimate** — actual savings depend on implementation.

### Output Formats

Bill Whisperer supports multiple output formats:

| Format | Command | Best For |
|--------|---------|----------|
| **Markdown** | `--output markdown` | Terminal, docs, README |
| **JSON** | `--output json` | Programmatic parsing, APIs |
| **Slack** | `--output slack` | Slack webhooks (Block Kit) |
| **Raw** | `--output raw` | Debugging, raw cost data |

**Examples:**
```bash
# Pretty markdown (default)
python cli/analyze.py --days 30

# JSON for scripting
python cli/analyze.py --days 7 --output json | jq '.analysis'

# Send to Slack
python cli/analyze.py --output slack | curl -X POST -H 'Content-type: application/json' \
  -d @- https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

---

## 🛠️ Configuration

```yaml
# config.yaml
analysis:
  days: 30                    # Look back period
  comparison: previous_month  # Or: previous_period, year_over_year
  
llm:
  provider: bedrock           # Or: openai, anthropic
  model: claude-3-sonnet      # Or: claude-3-haiku (cheaper)
  
output:
  format: markdown            # Or: json, slack, html
  slack_webhook: ""           # Optional
  email: ""                   # Optional (requires SES)
  
thresholds:
  alert_increase_percent: 20  # Alert if bill increases >20%
  minimum_item_dollars: 10    # Ignore items < $10
```

## 📁 Project Structure

```
aws-bill-whisperer/
├── README.md
├── template.yaml              # SAM/CloudFormation
├── samconfig.toml
├── src/
│   ├── analyzer/
│   │   ├── __init__.py
│   │   ├── handler.py         # Lambda entry point
│   │   ├── cost_explorer.py   # AWS Cost Explorer client
│   │   ├── llm.py             # LLM abstraction (Bedrock/OpenAI)
│   │   ├── prompts.py         # System prompts
│   │   ├── formatter.py       # Output formatting
│   │   └── recommendations.py # Cost optimization rules
│   └── requirements.txt
├── tests/
│   ├── test_analyzer.py
│   └── fixtures/
│       └── sample_cost_data.json
├── cli/
│   └── analyze.py             # Local CLI tool
└── examples/
    ├── sample_output.md
    └── slack_integration.md
```

## 🔍 Waste Pattern Scanner

The Pattern Scanner is an automated tool for detecting waste in your AWS resources, saving costs by identifying inefficiencies. It includes 7 distinct waste detection patterns, some of which can be automatically fixed.

| Pattern ID | Description | Auto-Fix Support |
|------------|-------------|------------------|
| 001        | Unattached EBS Volumes | Yes |
| 002        | Idle Elastic IPs | Yes |
| 003        | Underutilized EC2 Instances | No |
| 004        | Old Snapshots | Yes |
| 005        | Duplicate RDS Instances | No |
| 006        | NAT Gateway Waste | No |
| 007        | Unused Load Balancers | Yes |

## 🛠️ Waste Pattern Scanner CLI Usage

Here are some usage examples for the Pattern Scanner:

```bash
python src/whisper.py scan              # Scan all
python src/whisper.py scan --pattern 001
python src/whisper.py scan --json
python src/whisper.py fix 001 vol-xxx --dry-run
python src/whisper.py patterns          # List patterns
```

## 📊 Sample Pattern Scan Output

Here is a sample of what the pattern scan output looks like:

```
🔍 AWS Bill Whisperer - Waste Scan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pattern 001: Unattached EBS Volumes
  📍 EBS Volume: vol-0abc123
     Region: us-east-1
     Size: 100 GB
     Monthly Cost: $8.00
     ✅ Safe to auto-fix

[etc.]
```

## 🗂️ Project Structure

```
aws-bill-whisperer/
├── README.md
├── template.yaml              # SAM/CloudFormation
├── samconfig.toml
├── src/
│   ├── analyzer/
│   │   ├── __init__.py
│   │   ├── handler.py         # Lambda entry point
│   │   ├── cost_explorer.py   # AWS Cost Explorer client
│   │   ├── llm.py             # LLM abstraction (Bedrock/OpenAI)
│   │   ├── prompts.py         # System prompts
│   │   ├── formatter.py       # Output formatting
│   │   └── recommendations.py # Cost optimization rules
│   ├── whisper.py             # Pattern Scanner
│   ├── patterns/              # Waste detection patterns
│   └── requirements.txt
├── tests/
│   ├── test_analyzer.py
│   └── fixtures/
│       └── sample_cost_data.json
├── cli/
│   └── analyze.py             # Local CLI tool
└── examples/
    ├── sample_output.md
    └── slack_integration.md
```

## 📘 Two Tools in One

AWS Bill Whisperer includes two primary tools:
1. **AI Bill Analyzer (cli/analyze.py)** - Provides LLM-powered explanations for your AWS bills.
2. **Pattern Scanner (src/whisper.py)** - Performs automated waste detection in your AWS infrastructure.

## 💡 Unique Feature

> 💡 **Unique Feature**: Pattern 006 analyzes VPC flow logs to find NAT Gateway waste — something most cost tools miss entirely.

## 🗺️ Roadmap

### v1.0 - Free (Open Source) ✅
- [x] Basic cost analysis
- [x] Plain English explanations
- [x] Top cost drivers
- [x] Simple recommendations
- [x] CloudFormation deploy
- [x] CLI tool
- [x] 7 waste detection patterns
- [x] Auto-fix for safe patterns
- [x] JSON output support

### v2.0 - Pro ($29/mo)
- [ ] Multi-account (Organizations)
- [ ] Historical trends (12 months)
- [ ] Slack/Teams integration
- [ ] Scheduled weekly digests
- [ ] Custom alert thresholds

### v3.0 - Enterprise ($199/mo)
- [ ] Fine-tuned LLM on your data
- [ ] Automated remediation
- [ ] Team roles & RBAC
- [ ] Compliance reports
- [ ] White-label

## 🤝 Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md)

## 📄 License

MIT - Use it, fork it, sell it, whatever.

---

Built by [Goutham Patley](https://linkedin.com/in/goutham-patley-b1391b41) 
• 10 years distributed systems
