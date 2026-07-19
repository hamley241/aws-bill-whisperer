# p004_idle_ec2

**Pattern ID:** p004
**Name:** Idle EC2 Instances
**Category:** Compute
**Severity:** Medium – High (depends on instance type, ASG membership, prod tag)

The authoritative agent-behavior spec lives at
[agentic/p004_idle_ec2_agentic.md](../../agentic/p004_idle_ec2_agentic.md).

## What it detects

Running EC2 instances ≥14 days old whose CloudWatch metrics over a 14-day
window all show below-threshold activity:

| Signal | Threshold |
|---|---|
| `avg_cpu_14d` (mean of hourly Average) | < 5% |
| `max_cpu_14d` (max of hourly Maximum) | < 20% |
| `network_bytes_per_hour_14d` (sum of `NetworkIn + NetworkOut`) | < 1 MiB/h |
| `disk_bytes_per_hour_14d` (sum of `DiskReadBytes + DiskWriteBytes`) | < 1 MiB/h |
| `cpu_datapoint_coverage` (hourly samples) | ≥ 280 / 336 |

If CloudWatch returns fewer than 280 hourly datapoints (≈83% coverage),
the scanner refuses to emit a finding — the p004 analogue of p006's
`hourly_only` conservatism. The invariant is pinned by
`tests/test_p004_idle_ec2.py::TestNoUnattestedIdle`.

## Safety gates (closed enum)

Every emitted finding carries an `evidence.gates` dict with these keys.
`safe_to_fix` is `True` iff every gate is `True`.

| Gate | Source | What it blocks |
|---|---|---|
| `cpu_data_sufficient` | scanner | findings without enough CW data |
| `age_ok` | scanner | instances <14d old |
| `not_in_asg` | `aws:autoscaling:groupName` tag | ASG members (subsumes warm-pool members) |
| `no_alb_nlb_attachment` | `elbv2:DescribeTargetGroups` + `DescribeTargetHealth` | ALB/NLB targets |
| `not_prod` | `Env` / `Environment` tag in `{prod, production}` | production-tagged instances |
| `ebs_root` | `RootDeviceType == "ebs"` | instance-store-backed instances (stop would lose ephemeral storage) |
| `not_spot` | `InstanceLifecycle != "spot"` | spot instances (different lifecycle, separate spike) |

ASG membership subsumes warm-pool membership: warm pools are an ASG
feature, so any warm-pool instance is by definition in an ASG and is
already blocked by `not_in_asg`. There is no separate `not_in_warm_pool`
gate.

Classic ELB (CLB) attachment is **not** checked. An instance attached
only to a CLB will appear as `no_alb_nlb_attachment=True`. Adding CLB
support requires `elasticloadbalancing:DescribeInstanceHealth` and
per-CLB enumeration.

## Remediation modes

| Mode | OSS today | Notes |
|---|---|---|
| `dry_run` | ✅ always | Renders evidence + every gate's pass/fail |
| `command` | ✅ when `safe_to_fix=True` | Emits `aws ec2 stop-instances --instance-ids …` |
| `pr` | ❌ deferred | Instance run-state isn't cleanly modelled in Terraform |
| `api_call` | ✅ when `safe_to_fix=True` | Calls `ec2.stop_instances()`; refuses unsafe findings without making the AWS call |

The resolver and the remediator both consult `finding.safe_to_fix` —
there is one definition of eligibility. The invariant is pinned by
`tests/test_p004_idle_ec2.py::TestResolverAndRemediatorAgreeOnEligibility`.

Terminate is **out of scope** for v1: it fails the reversibility test
that underwrites `api_call` eligibility. See the agentic doc for the
reversibility-blast-radius principle.

## Cost model

| Field | Value |
|---|---|
| `cost_source` | `static_list_price` |
| `pricing_region` | `us-east-1` |
| `hourly_usd` | hardcoded us-east-1 on-demand list price by `InstanceType` |
| `monthly_cost_usd` | `hourly_usd * 720` |
| `confidence` | `low` |

`cost_source` describes the kind of measurement, never the region. A
future PR can add `cost_source="pricing_api"` or `cost_source="cur_actual"`
without retrofitting the field's semantics. `pricing_region` is separate
metadata.

## CLI usage

```bash
# Scan
python whisper.py scan --pattern 004 --json

# Preview the stop (no AWS calls)
python whisper.py fix 004 i-12345678 --dry-run

# Emit the stop command (only succeeds for safe_to_fix findings)
python whisper.py fix 004 i-12345678 --mode command
```
