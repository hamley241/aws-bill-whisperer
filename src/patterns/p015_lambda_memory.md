# p015_lambda_memory

**Pattern ID:** p015  
**Name:** Over-Provisioned Lambda Memory  
**Severity:** Medium

## What It Detects

Lambda functions with memory configured significantly higher than actual usage:
- Configured memory > 256MB
- Max memory used < 70% of configured (after headroom)
- At least 100 invocations in lookback period

| Metric | Value |
|--------|-------|
| Detection | CloudWatch MaxMemoryUsed / Invocations |
| Lookback | 14 days |
| Headroom | 30% above max used |

## Why It Matters

- **Linear Scaling:** Lambda costs scale linearly with memory
  - 512MB costs **2x** what 256MB costs
  - 1024MB costs **4x** what 256MB costs
- **Default Trap:** Many devs leave functions at default 1024MB
- **Hidden Waste:** High-volume functions amplify the waste

## Cost Estimation

```python
# Lambda pricing (us-east-1)
PRICE_PER_GB_SECOND = 0.0000166667

# Example: 1000 invocations/day, 500ms avg duration
# At 1024MB: 1000 * 30 * 0.5 * 1.0 * $0.0000166667 = $0.25/month
# At 256MB:  1000 * 30 * 0.5 * 0.25 * $0.0000166667 = $0.0625/month
# Savings: ~$0.19/month per function

# Scale to 100 functions = $19/month
# Scale to 1M invocations/day = $188/month savings
```

## Agent Actions

### CLI Usage

```bash
# Scan for over-provisioned Lambda functions
python whisper.py scan --pattern 015 --json

# Human-readable
python whisper.py scan --pattern 015

# Fix a specific function (reversible)
python whisper.py fix 015 arn:aws:lambda:us-east-1:123456789:function:my-func --dry-run
python whisper.py fix 015 arn:aws:lambda:us-east-1:123456789:function:my-func
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("015")
findings = pattern().scan()

for f in findings:
    meta = f.metadata
    print(f"{meta['function_name']}:")
    print(f"  Configured: {meta['configured_memory_mb']}MB")
    print(f"  Max Used: {meta['max_memory_used_mb']:.0f}MB")
    print(f"  Recommended: {meta['recommended_memory_mb']}MB")
    print(f"  Savings: ${f.monthly_cost:.2f}/mo")
```

## Fix Workflow

1. **Scan** → Identify over-provisioned functions
2. **Review** → Check function purpose and traffic patterns
3. **Test** → Apply in non-prod first
4. **Apply** → Update memory configuration (reversible!)
5. **Monitor** → Watch for throttling or errors

### Safety Rules

- ✅ **Safe to automate** → Memory changes are instantly reversible
- ⚠️ **Test first** → Some functions may be memory-bound
- ⚠️ **Watch cold starts** → Lower memory = slower cold starts
- 💡 **Staged rollout** → Apply to 10% of functions, monitor, then expand

## Integration Points

### For Agents

```python
from whisper import get_pattern_by_id

def optimize_lambda_memory():
    pattern = get_pattern_by_id("015")
    findings = pattern().scan(regions=["us-east-1"])
    
    if not findings:
        return "All Lambda functions are right-sized"
    
    # Sort by savings
    findings.sort(key=lambda f: f.monthly_cost, reverse=True)
    
    top_5 = findings[:5]
    total_savings = sum(f.monthly_cost for f in findings)
    
    msg = f"Found {len(findings)} over-provisioned Lambdas. Potential savings: ${total_savings:.2f}/mo\n\n"
    msg += "Top 5:\n"
    for f in top_5:
        msg += f"- {f.metadata['function_name']}: {f.metadata['configured_memory_mb']}MB → {f.metadata['recommended_memory_mb']}MB (${f.monthly_cost:.2f}/mo)\n"
    
    return msg
```

### Automated Fix (with approval)

```python
def apply_lambda_memory_fixes(findings, max_functions=5):
    """Apply fixes to top N functions by savings."""
    pattern = get_pattern_by_id("015")
    
    # Sort by savings, take top N
    to_fix = sorted(findings, key=lambda f: f.monthly_cost, reverse=True)[:max_functions]
    
    results = []
    for f in to_fix:
        success = pattern.fix(f, dry_run=False)
        results.append({
            "function": f.metadata["function_name"],
            "success": success,
            "old_memory": f.metadata["configured_memory_mb"],
            "new_memory": f.metadata["recommended_memory_mb"],
        })
    
    return results
```

## Output Format

```json
{
  "pattern_id": "015",
  "findings": [
    {
      "resource_id": "arn:aws:lambda:us-east-1:123456789:function:api-handler",
      "resource_type": "Lambda Function",
      "region": "us-east-1",
      "monthly_cost": 12.50,
      "severity": "medium",
      "safe_to_fix": true,
      "fix_command": "aws lambda update-function-configuration --function-name api-handler --memory-size 256 --region us-east-1",
      "recommendation": "Lambda 'api-handler' has 1024MB configured but only uses ~180MB max. Recommend 256MB (saves $12.50/mo, 75% reduction).",
      "metadata": {
        "function_name": "api-handler",
        "runtime": "python3.11",
        "configured_memory_mb": 1024,
        "max_memory_used_mb": 180,
        "recommended_memory_mb": 256,
        "memory_savings_mb": 768,
        "savings_percent": 75,
        "invocations_14d": 50000,
        "avg_duration_ms": 120
      }
    }
  ]
}
```

## Considerations

### When NOT to reduce memory

1. **CPU-bound functions** → Lambda allocates CPU proportional to memory
2. **Bursty workloads** → May need headroom for spikes
3. **Cold start sensitive** → Lower memory = slower cold starts
4. **ML/Data processing** → Often legitimately memory-intensive

### Memory Tiers

Lambda supports 128MB to 10240MB in 1MB increments. Common tiers:

| Tier | Use Case |
|------|----------|
| 128MB | Simple handlers, API proxies |
| 256MB | Light processing, small payloads |
| 512MB | Medium workloads, JSON processing |
| 1024MB | Data transformation, moderate ML |
| 2048MB+ | Heavy processing, large datasets |

## Exclusion Tags

Add these tags to exclude functions:

```
whisper:exclude = true
whisper:reason = "CPU-bound, needs memory for CPU allocation"
```
