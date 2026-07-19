# Pattern 004: Idle EC2 Instances — AGENTIC VERSION

## Implementation status (third bulletproof pattern; second planner-aware compute pattern)

| Capability | OSS today | Notes |
|---|---|---|
| Detection (cross-region scan) | ✅ | `src/patterns/p004_idle_ec2.py:scan()` |
| Utilization evidence (CPU avg + max, network/disk bytes per hour, datapoint coverage) | ✅ | `_cpu_stats()`, `_bytes_per_hour()` |
| ELB-attachment index (ALB / NLB target groups) | ✅ | `_build_elb_target_index()` |
| Closed safety-gate set (7 gates → `safe_to_fix`) | ✅ | `GATE_NAMES`, `evidence.gates` |
| Risk tier (prod tag, ASG, ELB, $ impact, family, EIP-less public IP) | ✅ | `_risk_tier()` |
| Static us-east-1 list-price cost (`cost_source="static_list_price"`) | ✅ | `HOURLY_USD_US_EAST_1`, `pricing_region` field |
| Per-instance live pricing via Pricing API or CUR (`cost_source="pricing_api"`) | ❌ later | follow-up PR |
| `dry_run` mode | ✅ | renders evidence + per-gate pass/fail; no AWS calls |
| `command` mode | ✅ (only when `safe_to_fix=True`) | emits `aws ec2 stop-instances …`; resolver hides the mode for unsafe findings |
| `pr` mode | ❌ deferred | instance run-state isn't cleanly modelled in Terraform |
| `api_call` mode (stop) | ✅ (only when `safe_to_fix=True`) | calls `ec2.stop_instances()`; refuses unsafe findings without making the AWS call |
| `api_call` mode (terminate / rightsize) | ❌ out of scope | terminate fails reversibility; rightsize needs its own recommendation engine |
| Classic ELB (CLB) attachment check | ❌ later | requires `elasticloadbalancing:DescribeLoadBalancers` + `DescribeInstanceHealth` via `boto3.client("elb")`; see "Known gaps" below |
| Planner: sub-action `recommended_sequence` | ❌ N/A | p004 is one-finding-one-action; no candidate menu |
| Cross-pattern ranking via planner | ✅ (preview only this PR) | `p001_p004_preview` fixture proves the planner consumes mixed findings cleanly |
| Cross-pattern eval rubric semantics | ❌ next PR | dedicated cross-pattern eval PR |
| Multi-account scanning | ❌ (paid tier) | single-account OSS scope |

## Agent behavior

### Objective

Reduce compute spend on instances the customer is paying for but not
using. The planner ranks idle EC2 candidates against storage and network
findings using deterministic evidence the scanner extracted (CPU, network,
disk, instance role, blast-radius signals). The LLM never invents
instance IDs, CPU figures, ASG membership, cost figures, or safety-gate
outcomes.

### Trigger conditions

- **Scheduled** (paid tier): nightly per-account scan.
- **OSS today**: on-demand via `/whisper scan` in Slack or CLI.
- **Event-driven** (paid tier): EC2 instance state-change events,
  ASG launch/terminate events, deployment completion events.

### Investigation steps (deterministic)

1. List `running` EC2 instances across regions.
2. For each instance ≥14 days old, pull 14-day hourly CloudWatch metrics:
   - `CPUUtilization` with `Statistics=["Average", "Maximum"]`
   - `NetworkIn` + `NetworkOut` with `Statistics=["Sum"]`
   - `DiskReadBytes` + `DiskWriteBytes` with `Statistics=["Sum"]`
3. Refuse to emit a finding when CW returns fewer than 280 hourly
   datapoints — the p004 analogue of p006's `hourly_only` conservatism.
4. Classify as idle iff: `avg_cpu_14d < 5%` AND `max_cpu_14d < 20%`
   AND `network_bytes_per_hour_14d < 1 MiB/h` AND
   `disk_bytes_per_hour_14d < 1 MiB/h`.
5. Build the per-region ELB target-group index via
   `elbv2:DescribeTargetGroups` + `DescribeTargetHealth`.
6. Compute the closed safety-gate set (see below) and derive
   `safe_to_fix = all(gates.values())`.
7. Compute risk tier from prod tag, ASG membership, ALB/NLB attachment,
   monthly impact, instance family, and EIP-less public IP.

### Decision policy (planner)

The LLM proposes **one step per p004 finding**. There is no
`recommended_sequence`; idle EC2 is a one-finding-one-action shape, not
a candidate menu like NAT Gateways. The validator drops any step that
invents a finding ID, mismatches `monthly_impact_usd`, or suggests a
mode that wasn't in the finding's `available_modes`.

For unsafe findings, the resolver offers only `dry_run`. The LLM
emitting `command` or `api_call` against an unsafe finding is dropped
with `UNSUPPORTED_MODE` — no special "refused with header" code path,
no executable text on gate-fail.

### Autonomous actions

The agent may execute **without approval** (single-account OSS):

- Emit a `dry_run` plan.
- Emit a `command` suggestion (text only, no execution) for findings
  where every gate passes.
- Call `ec2.stop_instances()` via `api_call` for findings where every
  gate passes. Stops are fully reversible (`start_instances` restores
  the instance with EBS root preserved); the audit log records every
  attempt and result.

The agent **must require approval** before:

- Any production-tagged instance change — gates already block this in OSS.
- Any ASG-member or ELB-target change — gates already block this.

The agent is **forbidden from**:

- Terminating any EC2 instance.
- Modifying instance attributes (rightsize) without explicit operator
  intent. Rightsizing has its own evidence shape and is a separate
  pattern.

These are not permission flags the customer can toggle in OSS — they
are wired into the mode resolver and the pattern's `remediate()`.

### Reversibility-blast-radius principle

`api_call` is allowed when **all three** hold:

1. **Reversible.** The action can be undone with a small bounded amount
   of work and no data loss. Stop is reversible (`start_instances`
   resumes the instance with EBS root preserved). Terminate is not.
2. **Gates can be expressed deterministically.** The conditions under
   which the action is safe must be a finite set of boolean checks
   computable from `Finding.evidence` — never "the LLM thinks it's
   okay." The closed `GATE_NAMES` set is the p004 expression.
3. **Blast radius is bounded.** A wrong call affects one resource the
   pattern is targeted at, not a cascade. Stopping one EC2 instance
   affects that instance. Removing a NAT Gateway can affect every
   private subnet in a VPC — different blast-radius class.

Future patterns evaluate `api_call` against this principle directly.
A pattern whose remediation fails any of the three should default to
`api_call` deferred or forbidden, the way p006 does for route-table
mutation.

### Safety mechanisms

- **Closed gate set** (`GATE_NAMES`). `safe_to_fix` is the AND of every
  gate. New gates require updates to `GATE_NAMES`, the scanner, the
  agentic doc, and `TestSafeToFixImpliesAllGatesPass` in lockstep.
- **Single eligibility function**. The modes resolver and the
  remediator's `command` / `api_call` branches all consult
  `finding.safe_to_fix`. There is one boolean, not two. The invariant
  is pinned by `TestResolverAndRemediatorAgreeOnEligibility`.
- **No unattested idle**. The scanner refuses to emit a finding without
  ≥280 hourly CPU datapoints. The invariant is pinned by
  `TestNoUnattestedIdle`.
- **Validators**. The planner-level validators (UNKNOWN_FINDING_ID,
  UNSUPPORTED_MODE, MONTHLY_IMPACT_MISMATCH/MISSING) drop any LLM
  emission that bypasses the resolver's gating.
- **Audit log**. Every `remediate()` call goes through
  `audit.audit_remediation` and lands in the SQLite repository,
  including refusals and AWS failures.

### Verification protocol

After a customer applies a `command` suggestion or runs an `api_call`:

1. Re-scan within 24h. The instance should appear in `running` state
   only if the customer or an automation has explicitly started it back
   up. If not, the finding will not be re-emitted (the scanner filters
   on `running`).
2. The audit log row for the original remediation carries the
   `PreviousState` / `CurrentState` transition and the actor — that's
   the customer's record of the change.

### Cost-source enum principle

`cost_source` describes the **kind of measurement**, never the region
or scope. v1 emits `"static_list_price"`. A follow-up PR can add
`"pricing_api"` (live Pricing API lookup) or `"cur_actual"` (Cost &
Usage Report) without retrofitting the field's semantics. Region is
metadata, surfaced via `pricing_region`. This matches p006's enum
philosophy (`"hourly_only"` / `"cloudwatch_derived"`).

### Expected outcomes

v1 ships the deterministic detection + 4-mode bulletproof pattern.
Realized savings happen the moment a customer hits the `command`-mode
output or invokes `api_call` against an idle non-prod instance. The
planner-side cross-pattern ranking ships in this PR as a preview
fixture only; the next PR adds the cross-pattern rubric semantics.

### Known gaps (carry-forward)

- Classic ELB (CLB) attachment is not checked. An instance attached
  only to a CLB will satisfy `no_alb_nlb_attachment=True` — the gate
  name reflects what's actually checked. Closing the gap requires:
  - **IAM**: `elasticloadbalancing:DescribeLoadBalancers` (enumerate
    CLBs per region) and `elasticloadbalancing:DescribeInstanceHealth`
    (per-CLB instance-target health). The IAM service prefix
    `elasticloadbalancing` covers both v1 and v2, but the APIs are
    separate surfaces.
  - **API / boto3 client**: `boto3.client("elb")` for Classic v1.
    Note that ALB/NLB v2 uses `boto3.client("elbv2")`; the
    boto3 service name `"elasticloadbalancing"` is **not valid**
    (raises `UnknownServiceError`). Per region:
    `describe_load_balancers()` to list CLBs, then for each CLB
    `describe_instance_health(LoadBalancerName=<name>)` to collect
    `InstanceStates[].InstanceId`.
  - **Contract**: add a new gate `no_classic_elb_attachment` alongside
    `no_alb_nlb_attachment`, update `GATE_NAMES`, and extend
    `_build_elb_target_index()` (or split it into v1 / v2
    sub-builders). Honest gate names matter — a single gate that
    conflates both surfaces would become a footgun the moment one
    silently breaks.
- Cost figures are static us-east-1 list prices. Regions other than
  `us-east-1` see approximate numbers. Future fix: Pricing API or CUR
  via a new `cost_source` enum value.
- Rightsize is not modelled. A persistently low-CPU `m5.2xlarge` is
  often a candidate for `m5.large` rather than stop. Future pattern.
- Terminate is intentionally out of scope. Adding it requires its own
  safety-gate set (snapshots, AMIs, ASG cleanup) and a separate
  `action_kind`. Future pattern.

---

*p004 is the third bulletproof pattern and the second planner-aware
compute pattern. Together with p001 (storage) and p006 (network), it
lets the planner rank across all three categories — the foundation
the next PR's cross-pattern eval rubric is built on.*
