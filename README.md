# AWS Bill Whisperer


<!-- Included from: docs/ARCHITECTURE-ANALYSIS.md -->
# AWS Bill Whisperer - Architecture Analysis

**Date:** March 23, 2026  
**Author:** Rusty (AI Assistant)

---

## Executive Summary

AWS Bill Whisperer has two well-built systems that aren't connected:
1. **Pattern Scanner** — Detects waste (EBS, EC2, NAT, RDS)
2. **Cost Analyzer** — Fetches Cost Explorer + Bedrock analysis

The AI layer exists but only sees aggregate data, not the pattern findings.

---

## System Architecture

### 1. Pattern System (`src/patterns/`)

**7 patterns implemented:**

| ID | Pattern | Complexity | Safe Fix? |
|----|---------|------------|-----------|
| 001 | Unattached EBS | Easy | ✅ (if snapshot exists) |
| 002 | Unattached EIP | Easy | ✅ |
| 003 | GP2 → GP3 | Easy | ✅ |
| 004 | Idle EC2 (<5% CPU) | Medium | ❌ (manual) |
| 005 | Old Snapshots | Easy | ✅ |
| 006 | NAT Gateway | Medium | ❌ (manual) |
| 007 | Idle RDS | Medium | ❌ (manual) |

**Strengths:**
- Clean `BasePattern` ABC with auto-discovery
- Adding new pattern = drop a file in `patterns/`
- Safety-first: `safe_to_fix` flag prevents accidents
- Rich metadata: age, tags, costs, fix commands
- Real AWS pricing (not placeholders)
- Multi-region scanning

**Gaps:**
- No CUR integration (queries live APIs)
- Limited coverage (~30% of common waste)
- Missing: S3 lifecycle, Lambda sizing, RI/SP, cross-AZ traffic

### 2. Analyzer System (`src/analyzer/`)

| Component | Purpose |
|-----------|---------|
| `cost_explorer.py` | Fetches Cost Explorer API: usage, daily, regional, comparison |
| `llm.py` | Calls Bedrock (Claude) or OpenAI |
| `prompts.py` | Analysis, anomaly, recommendation prompts |
| `handler.py` | Lambda entry point |
| `formatter.py` | Output: markdown, JSON, Slack |

**Flow:**
```
Lambda → Cost Explorer API → Format → Bedrock/Claude → Markdown → Slack/return
```

**Prompts ask for:**
1. Executive summary
2. Top cost drivers
3. What changed
4. Actionable recommendations
5. Potential savings

**Strengths:**
- Comprehensive Cost Explorer data (usage, daily, regional, comparison)
- Good prompt engineering
- Slack integration
- OpenAI fallback

**Gaps:**
- One-shot analysis (not conversational)
- No natural language queries
- No CUR integration (limited granularity)

---

## The Missing Bridge

**Pattern findings don't reach the AI.**

Current:
```
Patterns → Finding objects → (nowhere)
Analyzer → Cost Explorer → Bedrock → Report
```

Should be:
```
Patterns → Finding objects ──┐
                             ├──→ Bedrock → Enriched Report
Analyzer → Cost Explorer ────┘
```

---

## Recommendations

### Priority 1: Connect Patterns to AI

Modify `handler.py`:
```python
from patterns import discover_patterns

def lambda_handler(event, context):
    # Existing cost data
    cost_data = get_full_analysis(days)
    
    # NEW: Run patterns
    pattern_findings = []
    for PatternClass in discover_patterns():
        pattern = PatternClass()
        findings = pattern.scan(regions=['us-east-1', 'us-west-2'])
        pattern_findings.extend([f.to_dict() for f in findings])
    
    # Add to LLM context
    cost_data['waste_findings'] = pattern_findings
    
    # Analyze with enriched data
    analysis = analyze_costs(cost_data, provider='bedrock')
```

Update prompt to include:
```
### Detected Waste (from automated scans):
{waste_findings}
```

### Priority 2: Add More Patterns

| Pattern | Monthly Waste Potential |
|---------|------------------------|
| S3 incomplete multipart uploads | $$ |
| S3 intelligent tiering candidates | $$ |
| Unused Elastic Load Balancers | $$$ |
| Cross-AZ data transfer | $$$$ |
| Reserved Instance utilization | $$$$ |
| Savings Plan coverage gaps | $$$$ |
| Lambda over-provisioned memory | $$ |
| CloudWatch log retention | $ |

### Priority 3: Add CUR Integration

Cost Explorer API has limitations:
- 24-hour data lag
- Limited granularity
- No line-item detail

CUR provides:
- Hourly data
- Line-item detail
- Resource-level costs

### Priority 4: Conversational Interface

Add API Gateway endpoint:
```
POST /ask
{ "question": "Why did my EC2 cost spike last week?" }
```

Use Bedrock with conversation history for follow-ups.

---

## Assessment

| Aspect | Grade | Notes |
|--------|-------|-------|
| Pattern system | **B+** | Clean, extensible, 7 patterns |
| Cost Explorer fetch | **A-** | Comprehensive data |
| Bedrock integration | **B** | Works, but one-shot |
| Prompt engineering | **B+** | Good structure |
| End-to-end value | **C+** | Two good systems that don't talk |

**Current state:** 60% of a good product. Pieces exist, need integration.

---

## Competitor Positioning

**Our moat:** Privacy + AI + Self-hosted

No competitor offers all three:
- AWS native tools: No AI
- Vantage/CloudHealth: Data leaves account
- OpenCost: K8s-only, no AI

**Market:** $5-13B → $23-38B by 2029-2034

**Verdict:** Worth pursuing with clear milestones.

---

## Known Limitations

### Hardcoded Pricing Tables

The following patterns contain hardcoded AWS pricing estimates:

| Pattern | File | Pricing Type |
|---------|------|--------------|
| P004 Idle EC2 | `p004_idle_ec2.py` | EC2 hourly costs by instance type |
| P007 Idle RDS | `p007_idle_rds.py` | RDS hourly costs by instance class |

**Impact:**
- Prices are based on us-east-1 on-demand rates as of March 2026
- Actual prices vary by region, OS (Windows/Linux), and change over time
- Cost estimates may drift from actual costs

**Mitigation options:**
1. Integrate with [AWS Price List API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-list-api.html)
2. Maintain a pricing database updated via Lambda scheduled job
3. Use Cost Explorer API for actual billed amounts (more accurate but slower)
4. Accept estimates as "directionally correct" for prioritization purposes

For now, estimates are sufficient for identifying idle resources, even if the exact
dollar amounts drift. The goal is to find waste, not calculate precise costs.

---

## Next Steps

1. [x] Connect patterns → AI (Priority 1) ✅
2. [ ] Add 3 more high-value patterns
3. [ ] Test with real AWS account
4. [ ] Add natural language query endpoint
5. [ ] AWS Marketplace listing

---

*Analysis by Rusty, March 23, 2026*

<!-- End include: docs/ARCHITECTURE-ANALYSIS.md -->


## Installation

```bash
pip install aws-bill-whisperer
```


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


## Quick Start

```bash
# Configure AWS credentials
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# Run analysis
whisper analyze --days 30
```


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
