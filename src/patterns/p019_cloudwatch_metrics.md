# p019_cloudwatch_metrics

**Pattern ID:** p019  
**Name:** High-Cardinality CloudWatch Custom Metrics  
**Severity:** Low-Medium

## What It Detects

Custom CloudWatch metric namespaces with high cardinality:
1. **Dimension explosions** - Each unique dimension combination = new billable metric
2. **High-dimension metrics** - >5 dimensions per metric = cardinality risk
3. **Custom namespace bloat** - >100 unique metric streams in a namespace

| Metric | Value |
|--------|-------|
| Cost per Metric | $0.30/month (first 10K), $0.10 (10K-240K) |
| PutMetricData | $0.01 per 1,000 datapoints |
| Detection | CloudWatch ListMetrics API |

## Why It Matters

- **Silent Cost Explosion:** Adding request_id or user_id as dimensions = massive bills
- **Hard to Spot:** CloudWatch bills don't break down by namespace easily
- **Common Anti-pattern:** Logging unique IDs as metric dimensions
- **Example:** 10,000 unique user IDs × 5 metrics = 50,000 metric streams = $5,000/month

## Cost Estimation

```python
# CloudWatch Custom Metrics Pricing (per month)
TIER_1 = $0.30/metric   # First 10,000
TIER_2 = $0.10/metric   # 10,001 - 240,000
TIER_3 = $0.05/metric   # 240,001+

# PutMetricData API
PUT_COST = $0.01 / 1,000 datapoints

# Example: 50,000 metrics with 1 datapoint/minute
# Metric cost: $10K × $0.30 + $40K × $0.10 = $3,000 + $4,000 = $7,000/month
# Put cost: 50,000 × 43,200 × $0.00001 = $21,600/month
# Total: $28,600/month 😱
```

## Agent Actions

### CLI Usage

```bash
# Scan for high-cardinality metrics
python whisper.py scan --pattern 019 --json

# Show human-readable
python whisper.py scan --pattern 019

# Scan specific region
python whisper.py scan --pattern 019 --regions us-east-1
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("019")
findings = pattern().scan()

for f in findings:
    namespace = f.metadata.get('namespace')
    streams = f.metadata.get('total_metric_streams')
    print(f"{namespace}: {streams} streams, ${f.monthly_cost:.2f}/mo")
```

## Fix Workflow

### Identify the Problematic Dimensions

1. **Check top metrics** → Look at metadata.top_metrics
2. **Find high-cardinality dimensions** → Usually IDs, timestamps, request tokens
3. **Trace to code** → Find where metrics are published

### Fix Strategies

1. **Remove unique ID dimensions**
   ```python
   # BAD: Creates a metric per user
   cloudwatch.put_metric_data(
       MetricName='RequestCount',
       Dimensions=[{'Name': 'UserId', 'Value': user_id}]
   )
   
   # GOOD: Aggregate by user type
   cloudwatch.put_metric_data(
       MetricName='RequestCount',
       Dimensions=[{'Name': 'UserType', 'Value': user_type}]
   )
   ```

2. **Use EMF (Embedded Metric Format)** for high-cardinality data → Goes to logs, cheaper

3. **Reduce dimension combinations**
   ```python
   # BAD: 3 dimensions = many combinations
   Dimensions=[Region, Service, Endpoint]
   
   # BETTER: Combine into one
   Dimensions=[{'Name': 'Route', 'Value': f'{region}/{service}/{endpoint}'}]
   ```

4. **Delete old unused metrics** → Can't delete directly, just stop publishing

### Safety Rules

- ⚠️ **Requires code changes** - No quick fix command
- ✅ **Start with biggest offenders** - Fix top 3 namespaces first
- ❌ **Don't delete blindly** - Some metrics may have dashboards/alarms

## Integration Points

### For Agents (Rusty, etc.)

```python
from whisper import get_pattern_by_id

def check_cloudwatch_metrics():
    pattern = get_pattern_by_id("019")
    findings = pattern().scan()
    
    if findings:
        total_cost = sum(f.monthly_cost for f in findings)
        worst = max(findings, key=lambda f: f.monthly_cost)
        return (f"Found {len(findings)} high-cardinality metric namespaces. "
                f"Total: ${total_cost:.0f}/mo. Worst: {worst.resource_id}")
    return "No high-cardinality custom metrics found"
```

### Weekly Report

```python
# Include in cost report
for f in findings:
    report.add_line(
        f"⚠️ {f.resource_id}: {f.metadata['total_metric_streams']} streams, "
        f"${f.monthly_cost:.0f}/mo"
    )
```

## Output Format

```json
{
  "pattern_id": "019",
  "findings": [
    {
      "resource_id": "MyApp/API",
      "resource_type": "CloudWatch Custom Namespace",
      "region": "us-east-1",
      "monthly_cost": 450.00,
      "severity": "medium",
      "safe_to_fix": false,
      "recommendation": "Namespace 'MyApp/API' has 1500 unique metric streams. Top metrics: RequestCount, Latency, Errors. Consider using metric math or reducing dimension cardinality.",
      "metadata": {
        "namespace": "MyApp/API",
        "total_metric_streams": 1500,
        "unique_metric_names": 8,
        "top_metrics": [
          {
            "name": "RequestCount",
            "stream_count": 800,
            "max_dimensions": 4,
            "dimension_names": ["Environment", "Service", "Endpoint", "StatusCode"]
          },
          {
            "name": "Latency",
            "stream_count": 500,
            "max_dimensions": 3,
            "dimension_names": ["Environment", "Service", "Endpoint"]
          }
        ],
        "high_dimension_metrics": [],
        "estimated_put_cost": 648.00
      }
    }
  ]
}
```

## Related Patterns

- **p008_s3_lifecycle** - Similar "hidden cost" detection
- **p009_cross_az_transfer** - Another sneaky cost source
