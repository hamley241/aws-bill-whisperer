# Pattern 006: NAT Gateway Optimization — AGENTIC VERSION

## Implementation Status (as of p006 PR — first agent-native pattern)

| Capability | OSS today | Notes |
|---|---|---|
| Detection (cross-region scan) | ✅ | `src/patterns/p006_nat_gateway.py:scan()` |
| Topology evidence (route tables, subnets, existing VPCEs) | ✅ | `_topology()` |
| Endpoint candidate enumeration (S3 + DynamoDB Gateway) | ✅ | `_candidates()` |
| Observed-vs-inferred schema separation | ✅ | `evidence.observed` / `evidence.inferred` top-level keys |
| Risk tier (prod tag, route count, subnet count, traffic) | ✅ | `_risk_tier()` |
| Hourly cost ($/mo) | ✅ | `cost_source="hourly_only"` |
| Processed-byte cost ($/mo) | ❌ deferred | Awaits AWS-cited CloudWatch metric semantics; v1 emits 0 |
| `dry_run` mode | ✅ | presents candidates, no AWS calls |
| `command` mode | ✅ (observed candidates only) | emits `create-vpc-endpoint`; inferred-only returns `insufficient_evidence_for_command` |
| `pr` mode | ❌ deferred | Terraform diff for endpoint + rtb assoc is materially more complex than p001 |
| `api_call` mode | ❌ forbidden in OSS | route-table mutation is high-blast-radius; returns `not_supported_in_oss_milestone` |
| Planner: `recommended_sequence` sub-actions | ✅ | `PlanStep.recommended_sequence: list[SubAction]` |
| Planner validators (UNKNOWN_CANDIDATE_ID, savings/tier/kind) | ✅ | `src/agent/validators.py:_validate_sub_actions` |
| Rationale hedging audit (warning) | ✅ | `rationale_hedges_inferred` rubric check, warning level |
| VPC Flow Logs ingestion | ❌ next PR | enables `observed`-tier candidates |
| CUR ingestion | ❌ next PR | enables `cost_source="cur_actual"` |
| Interface endpoint candidates (ECR, STS, …) | ❌ later | requires inference + per-service blast model |
| Multi-account scanning | ❌ (paid tier) | single-account OSS scope |

## Agent behavior

### Objective

Reduce NAT Gateway spend with zero blast-radius surprises. The planner reasons about which VPC endpoint candidates to add, in what order, and when to pause and observe — using deterministic evidence the scanner extracted. The LLM never invents resources, candidates, traffic destinations, cost figures, route tables, subnets, or safety-gate outcomes.

### Trigger conditions

- **Scheduled** (paid tier): nightly per-account scan.
- **OSS today**: on-demand via `/whisper scan` in Slack or CLI.
- **Event-driven** (paid tier): VPC config change events.

### Investigation steps (deterministic)

1. List `available` NAT Gateways across regions.
2. For each NAT: capture identity, tags, age.
3. Compute baseline hourly cost (`NAT_HOURLY_USD * 24 * 30`).
4. Derive topology: route tables targeting the NAT, the subnets associated with those route tables, existing VPC endpoints in the VPC.
5. Enumerate the two Gateway endpoint candidates (S3, DynamoDB) — skipping any already present in the VPC. Mark `evidence_tier="inferred"` in v1 (Flow Logs absent ⇒ no observed share).
6. Compute risk tier from prod tag, affected route count, private subnet count, processed-byte volume.

### Decision policy (planner)

The LLM proposes one step per NAT Gateway, with an optional `recommended_sequence` of sub-actions. The validator drops any step whose sub-actions are inconsistent with the candidate evidence. The closed `action_kind` enum for v1:

- `add_vpc_endpoint`
- `observe_and_reassess`

`remove_nat` and `downsize_nat` are intentionally absent. The planner cannot recommend deleting a NAT because the corresponding sub-action verb does not exist.

### Autonomous actions

The agent may execute **without approval** (single-account OSS):

- Emit a `dry_run` plan.
- Emit a `command` suggestion (text only, no execution) **only** when the top candidate is observed-tier.

The agent **must require a PR review** before:

- Adding any VPC endpoint (deferred to a follow-up PR that ships the Terraform diff generator).

The agent is **forbidden from**:

- Direct API mutation of route tables.
- Deleting any NAT Gateway.

These are not permission flags the user can toggle in OSS — they are wired into the mode resolver and the pattern's `remediate()`. `pr` is deferred; `api_call` returns `not_supported_in_oss_milestone`.

### Verification protocol

After a customer manually applies a `command`-mode suggestion:

1. Re-scan within 24h. The new VPC endpoint should appear in `topology.existing_vpc_endpoints`; the candidate should drop out of `inferred.endpoint_candidates`.
2. With Flow Logs (next PR), processed-byte cost on the NAT should fall.
3. With CUR (next PR), the savings should appear in the next month's bill.

### Safety mechanisms

- **Schema separation** (`observed` vs `inferred` keys) prevents the LLM from blurring evidence tiers.
- **Validators** drop any sub-action that invents a candidate, mismatches savings, mismatches tier, or uses an unknown action_kind.
- **Whole-step drop** on sub-action failure — no partial salvage of a corrupt sequence.
- **Rubric warning** `rationale_hedges_inferred` flags confident verbs (`shows`, `confirmed`, `measured`) in inferred sub-action rationales. Warning only; promoted to a gate once empirically reliable.
- **Mode resolver** never exposes `pr` or `api_call` to the LLM for p006 findings; the validator drops any emission that tries.

### Expected outcomes

v1 ships the agent-native machinery. The visible customer outcome from v1 alone is a clearer plan ("here's the NAT, here are the candidates, here's the recommended sequence"). Realized savings require the follow-up PRs that add Flow Logs ingestion (to produce observed-tier candidates with non-zero savings) and Terraform PR generation (to actually open the PR).

---

*This pattern is the seam between deterministic detection and LLM-mediated planning. Future patterns adopt the same shape: evidence the planner can reason about, validators that constrain what the planner can say, and a closed action vocabulary the planner cannot escape.*
