# Usage

## CLI (After Install)

```bash
# Install
pip install aws-bill-whisperer

# Analyze costs
whisper analyze --days 30

# Scan for waste
whisper scan --patterns all --regions us-east-1

# Combined analysis
whisper full --output markdown
```

## CLI (Run Without Installing)

```bash
git clone https://github.com/hamley241/aws-bill-whisperer.git
cd aws-bill-whisperer

# Scan for waste (no AWS credentials needed for pattern scan)
python3 src/whisper.py scan

# Analyze costs (requires AWS credentials)
python3 cli/analyze.py --days 30

# Test without AWS
python3 cli/analyze.py --mock
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
