# p002_unattached_eip

**Pattern ID:** p002  
**Name:** Unattached Elastic IPs  
**Severity:** Medium

## What It Detects

Elastic IPs (EIPs) not attached to any instance or ENI.

| Metric | Value |
|--------|-------|
| Monthly Cost | $0.005/hr = ~$3.60/month per unattached EIP |
| Detection | `describe_addresses` → `Association` is null |

## Why It Matters

- **Waste:** $3.60/month per detached EIP adds up
- **Limit:** AWS limits EIPs per account (5 per region); orphans block new allocations
- **Risk:** EIP doesn't protect instance in use

## Agent Actions

```bash
# Scan
python whisper.py scan --pattern 002 --json

# Fix (release)
python whisper.py fix 002 eip-12345678
python whisper.py fix 002 eip-12345678 --dry-run
```

## Integration

```python
from whisper import get_pattern_by_id
findings = get_pattern_by_id("002")().scan()
```

## Safety

- ✅ **Auto-safe:** Detached → release
- ⚠️ **Manual:** EIP in use but not detected (check Association)