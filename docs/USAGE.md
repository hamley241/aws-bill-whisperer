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
