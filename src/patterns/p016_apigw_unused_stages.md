# p016_apigw_unused_stages

**Pattern ID:** p016  
**Name:** Unused API Gateway Stages  
**Severity:** Low-Medium

## What It Detects

API Gateway stages (REST, HTTP, WebSocket) with:
- Zero requests/messages over 30 days
- Optional: Cache clusters enabled (significant cost!)

| Metric | Value |
|--------|-------|
| Detection | CloudWatch Count / MessageCount |
| Lookback | 30 days |
| API Types | REST (v1), HTTP (v2), WebSocket |

## Why It Matters

- **Cache Cost:** REST API stages with cache enabled cost $14-$2,800/month
- **Clutter:** Forgotten dev/test stages accumulate
- **Security:** Old stages may have outdated auth or vulnerabilities
- **Quota:** API Gateway has limits on number of APIs/stages

## Cost Estimation

### REST API Cache Pricing (per hour, us-east-1)

| Cache Size | $/Hour | $/Month |
|------------|--------|---------|
| 0.5 GB | $0.02 | ~$14.60 |
| 1.6 GB | $0.038 | ~$27.74 |
| 6.1 GB | $0.20 | ~$146 |
| 13.5 GB | $0.25 | ~$182.50 |
| 28.4 GB | $0.50 | ~$365 |
| 58.2 GB | $1.00 | ~$730 |
| 118 GB | $1.90 | ~$1,387 |
| 237 GB | $3.80 | ~$2,774 |

### Request Pricing (reference)

| API Type | Price |
|----------|-------|
| REST API | $3.50/million requests |
| HTTP API | $1.00/million requests |
| WebSocket | $1.00/million messages |

*Note: Zero requests = zero request cost, but cache still runs*

## Agent Actions

### CLI Usage

```bash
# Scan for unused API Gateway stages
python whisper.py scan --pattern 016 --json

# Human-readable
python whisper.py scan --pattern 016

# Filter by region
python whisper.py scan --pattern 016 --region us-east-1

# Delete a stage (with confirmation)
python whisper.py fix 016 arn:aws:apigateway:us-east-1::/restapis/abc123/stages/dev --dry-run
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("016")
findings = pattern().scan()

for f in findings:
    meta = f.metadata
    print(f"{meta['api_name']}/{meta['stage_name']}:")
    print(f"  Type: {meta['api_type']}")
    print(f"  Requests (30d): {meta['request_count_30d']}")
    if meta.get('cache_enabled'):
        print(f"  Cache: {meta['cache_size_gb']}GB = ${f.monthly_cost:.2f}/mo")
```

## Fix Workflow

1. **Identify** → Find stages with zero traffic
2. **Check Dependencies** → Lambda functions, integrations, CloudFormation
3. **Verify Purpose** → Is it a warm standby? Scheduled use?
4. **Disable Cache First** → If cache enabled, disable before deletion
5. **Delete Stage** → Remove the unused stage
6. **Update IaC** → Remove from Terraform/CloudFormation if applicable

### Safety Rules

- ✅ **Safe for test/dev stages** → Usually fine to delete
- ⚠️ **Check CloudFormation** → May be managed by IaC
- ⚠️ **Check client integrations** → Some clients may have hardcoded endpoints
- ❌ **Don't delete prod** → Even if unused, may be intentional standby

## Integration Points

### For Agents

```python
from whisper import get_pattern_by_id

def check_unused_apigw_stages():
    pattern = get_pattern_by_id("016")
    findings = pattern().scan(regions=["us-east-1", "us-west-2"])
    
    if not findings:
        return "No unused API Gateway stages found"
    
    # Separate by cost (cache vs no cache)
    with_cost = [f for f in findings if f.monthly_cost > 0]
    no_cost = [f for f in findings if f.monthly_cost == 0]
    
    total_cost = sum(f.monthly_cost for f in with_cost)
    
    msg = f"Found {len(findings)} unused API Gateway stages:\n"
    if with_cost:
        msg += f"- {len(with_cost)} with cache enabled (${total_cost:.2f}/mo)\n"
    if no_cost:
        msg += f"- {len(no_cost)} without cache (cleanup recommended)\n"
    
    return msg
```

### Cron Job

```yaml
# Run monthly - API stages don't change often
schedule: "0 10 1 * *"
command: python whisper.py scan --pattern 016 --json > apigw_unused.json
```

## Output Format

```json
{
  "pattern_id": "016",
  "findings": [
    {
      "resource_id": "arn:aws:apigateway:us-east-1::/restapis/abc123xyz/stages/staging",
      "resource_type": "API Gateway Stage (REST)",
      "region": "us-east-1",
      "monthly_cost": 27.74,
      "severity": "medium",
      "safe_to_fix": true,
      "fix_command": "aws apigateway delete-stage --rest-api-id abc123xyz --stage-name staging --region us-east-1",
      "recommendation": "REST API stage 'my-api/staging' has zero requests in 30 days. Cache enabled (1.6GB) costs $27.74/mo. Appears to be a test/dev stage. Consider deleting if no longer needed.",
      "metadata": {
        "api_id": "abc123xyz",
        "api_name": "my-api",
        "stage_name": "staging",
        "api_type": "REST",
        "cache_enabled": true,
        "cache_size_gb": "1.6",
        "request_count_30d": 0,
        "is_likely_test": true
      }
    },
    {
      "resource_id": "arn:aws:apigateway:us-east-1::/apis/xyz789abc/stages/dev",
      "resource_type": "API Gateway Stage (HTTP)",
      "region": "us-east-1",
      "monthly_cost": 0.0,
      "severity": "low",
      "safe_to_fix": true,
      "fix_command": "aws apigatewayv2 delete-stage --api-id xyz789abc --stage-name dev --region us-east-1",
      "recommendation": "HTTP API stage 'payments-api/dev' has zero requests in 30 days. Consider deleting if no longer needed.",
      "metadata": {
        "api_id": "xyz789abc",
        "api_name": "payments-api",
        "stage_name": "dev",
        "api_type": "HTTP",
        "request_count_30d": 0,
        "is_likely_test": true
      }
    }
  ]
}
```

## Common Scenarios

| Scenario | Cost | Action |
|----------|------|--------|
| Staging stage with cache | $15-150/mo | Disable cache or delete |
| Old dev stage (HTTP API) | $0 | Delete for cleanup |
| Canary stage (prod backup) | Varies | Leave if intentional |
| Test stage from old project | $0-150/mo | Delete |

## Exclusion Tags

API Gateway doesn't support tags on stages directly. Use naming conventions:

```
# Skip stages matching these patterns:
*-canary
*-standby
prod-*
```

Or use API-level tags:
```
whisper:exclude-stages = "canary,standby"
```

## Related Patterns

- **p010_idle_load_balancer** — May have unused ALBs fronting these APIs
- **p014_ecs_fargate_idle** — Backend services may also be idle
- **p015_lambda_memory** — Lambda backends may be over-provisioned
