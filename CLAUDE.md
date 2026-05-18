# AWS Bill Whisperer — Project Context for Claude Code

This file is the durable strategic context for the project. Read it before starting any task. It explains *why* we're building what we're building, so implementation choices align with the product direction.

## Product identity

**AWS Bill Whisperer** is the platform engineer's cost copilot. It explains AWS bills in plain English, detects waste, and (in the paid tier) autonomously opens PRs to fix it.

**Buyer:** mid-market platform/infra teams ($50K–500K/mo AWS spend). One platform engineer wears the cost hat as 20% of their job. They live in Slack and Terraform/IaC.

**Wedge:** conversational explanation quality. **Moat:** agent framework for safe autonomy. **Pricing model:** open-core with paid tier.

## Non-negotiable architectural principles

These constraints override convenience. If a design decision conflicts with one of these, the principle wins.

1. **Data sovereignty.** Customer data — bills, resource metadata, utilization metrics, findings, fix history — NEVER leaves the customer's AWS account or their chosen LLM endpoint. The vendor (us) does not host or store customer data anywhere.

2. **Bring your own model.** Bedrock (in-account) is the default and recommended path. OpenAI/Anthropic-direct API is supported but flagged as "your prompts leave your account." Customer chooses with informed consent.

3. **In-account execution.** Both OSS and paid tiers run entirely inside the customer's AWS account. The paid tier is a deployable CloudFormation/Terraform stack, NOT a hosted SaaS. The vendor operates only a control plane (license check, software updates) that never touches customer data.

4. **PR-native remediation.** Fixes land as pull requests to the customer's IaC repo wherever possible. The approval workflow is the PR review — we do not build a separate approval UI.

5. **Generous OSS.** The OSS tier must be the most complete free cost-optimization tool available. Detection, explanation, agent framework, and single-account remediation are ALWAYS OSS. Gating intelligence kills the wedge.

## The OSS/paid seam

**Public contract — write this down, commit to it:**

Always OSS:
- All detection patterns (currently 20, will grow)
- All agent logic and the agent framework itself
- Plain-English explanation via customer's chosen LLM
- Single-account scanning and remediation
- Self-hostable Slack app (customer creates their own Slack app)
- CLI and local web UI
- Local audit logs to files or customer's S3
- Agentic specs for all patterns (markdown + runnable code)

Always paid:
- Production-grade deployable stack (CloudFormation/Terraform) for in-account install
- AWS Organizations / multi-account scanning with cross-account role management
- Continuous scheduled scanning with state tracking
- PR-native autopilot operated across many repos with merge tracking
- SSO/SAML on the local dashboard, RBAC
- Audit log retention guarantees, queryable history
- Enterprise integrations (Datadog, PagerDuty, ServiceNow connectors)
- Upgrade automation, support, SLA

**The seam in one sentence:** *If a single engineer with a laptop and one AWS account can do it, it's OSS. If it requires production infrastructure deployed into the customer's account that we maintain, it's paid.*

## Current state

- 20 detection patterns (`p001`–`p020`) — static, hand-coded
- Strands Agents SDK wrapping detection into compute/storage/monitoring specialist agents
- Orchestrator runs specialists concurrently
- FastAPI + WebSocket web UI on `localhost:8000`
- CLI chat alternative
- Intent router dispatches questions to specialists
- LLM backend: Bedrock Claude or OpenAI (configurable)
- Cost Explorer + CloudWatch (real, not mocked) + optional CUR CSV
- Lambda deployment via SAM template
- 3 of 20 patterns have full agentic specs; 17 are static-only

## 90-day plan

**Weeks 1–3 — Slack app, self-hostable.** Make the Whisperer demo-able in 5 minutes via Slack. Slack manifest + one-command installer + clear docs. The OSS surface for the wedge.

**Weeks 4–7 — Three bulletproof patterns.** Pick three patterns, build full agentic specs (detection, confidence scoring, dry-run, PR-based remediation, rollback, audit log). Nominees: unattached EBS volumes, NAT Gateway / missing VPC endpoints, idle EC2/RDS. The other 17 stay detection-only.

**Weeks 8–10 — Trust ladder, two rungs.** Rung 1 (OSS): "show me + tell me how." Rung 2 (paid): "open a PR and wait for me." PR-native autopilot in-account. GitHub App is customer-installed, not vendor-hosted.

**Weeks 11–13 — Multi-account.** The deployable stack supports AWS Organizations and cross-account role assumption. First exclusively-paid feature.

## Explicit non-goals (next 90 days)

- GCP/Azure support
- Migrating the remaining 17 patterns to agentic specs (lazy migration only)
- Custom approval/governance UI (GitHub PRs are the UI)
- Cost anomaly detection / forecasting (AWS does this natively)
- Web dashboard with charts as primary surface (don't become a worse Vantage)
- Hosted SaaS anything (violates data sovereignty principle)

## Killer demo (the target)

> Platform engineer installs the Slack app on Friday afternoon. Types `/whisper scan` in #infra. Within 90 seconds, a thread appears: *"Scanned your account. Found $11,400/mo in likely waste. Top finding: a NAT Gateway in us-east-1 costing $4,200/mo, 78% of which is one EKS workload pulling images from ECR — a VPC endpoint would save ~$3,800/mo. Want me to open the Terraform PR?"* Engineer hits a button. PR appears in their IaC repo with the change, a cost-impact comment, and a rollback plan. They merge it Monday morning.

Every implementation decision should ladder back to making this demo real, repeatable, and trustworthy.

## Stack

Python, Strands Agents SDK, boto3, FastAPI, Bedrock/OpenAI, AWS SAM. MIT-licensed.

## Architectural principles — read before writing any code

These principles exist because we are building in phases, but the phases must compose. The Slack app (Weeks 1–3), the three bulletproof patterns (Weeks 4–7), the trust ladder (Weeks 8–10), and multi-account (Weeks 11–13) are not independent projects — they are progressive layers on the same architecture. Code written in Week 2 must not need to be rewritten in Week 8 because we picked the wrong abstraction.

If a task seems to require violating one of these principles, **stop and surface the conflict** before writing code. The principle wins by default; only the human can authorize an exception.

### 1. Detection patterns are plugins, not hand-coded modules

Adding a new detection pattern must be a matter of dropping a new file in a `patterns/` directory and registering it via a decorator or entry point. It must not require touching the orchestrator, the agents, the Slack handlers, the CLI, or the LLM layer.

Every pattern implements a common interface (something like a `Pattern` protocol or abstract base class) with at minimum:

- A unique ID (`p001`, etc.) and human-readable name
- A category (compute / storage / monitoring / network / database / ...) used for specialist-agent routing
- A `scan(context) -> Iterable[Finding]` method that takes a scan context (AWS session, region, account ID, dry-run flag) and yields findings
- Metadata: risk tier, confidence model, average $ impact range, required IAM permissions, supported regions
- An optional `remediate(finding, mode) -> RemediationResult` method where mode is one of `dry_run | command | pr | api_call`
- Optional agentic spec: triggers, rollback procedure, safety gates

The existing 20 patterns must be refactored to this interface as part of the first task that touches the pattern layer. Don't bolt new patterns onto a different structure than the old ones.

### 2. Findings are the universal currency

Every component in the system that produces or consumes detection output speaks `Finding` objects. A `Finding` is a typed, serializable dataclass with a stable schema:

- `id`, `pattern_id`, `resource_arn`, `account_id`, `region`
- `monthly_impact_usd`, `confidence` (0.0–1.0 with documented meaning)
- `risk_tier` (low / medium / high)
- `summary` (one-line, machine-generated)
- `explanation` (LLM-generated, plain English — optional, populated lazily)
- `fix_command` (CLI/AWS API call), `fix_pr` (IaC diff, optional)
- `evidence` (the data that triggered detection — utilization numbers, dates, etc.)
- `metadata` (free-form pattern-specific dict)

The Slack handler, the CLI, the web UI, the agent layer, the autopilot, the audit log — all consume `Finding` objects. Never let a component invent its own representation. Schema changes are versioned (`schema_version` field) and backward-compatible.

### 3. Output surfaces are presenters, not logic owners

Slack, CLI, web UI, and (later) the local dashboard are **presentation layers** over the same orchestrator output. Each surface renders `Finding` objects and routes user intent — none of them contains detection logic, remediation logic, or LLM prompts.

A new surface (e.g., a Teams integration, a VSCode extension) must be implementable by writing only a presentation module. If a new surface needs to fork detection or remediation logic to work, the abstraction is wrong and must be fixed first.

### 4. Remediation modes are composable, not branched

Remediation is not "OSS does commands, paid does PRs." Every remediation supports a `mode` parameter:

- `dry_run`: log what would happen, change nothing
- `command`: emit a shell command for the user to run manually
- `pr`: emit an IaC diff suitable for a PR
- `api_call`: execute the AWS API call directly (gated, audited)

Modes are orthogonal to OSS-vs-paid. The OSS tier exposes all modes for single-account use. The paid tier exposes the same modes but orchestrates them at scale (across accounts, scheduled, with state tracking). Same code path, different operator.

This means: do not write a "PR remediation function" in Weeks 4–7 that is structurally separate from the "command remediation function." Write one remediation entry point per pattern that dispatches on mode.

### 5. LLM access is a single abstraction with a logged contract

There is one `LLMClient` interface. All prompts go through it. Bedrock, OpenAI, Anthropic-direct are implementations behind the same interface. Adding a new model provider is a new implementation, not changes scattered through the codebase.

The interface enforces:

- **Prompt logging:** every call writes the full prompt to a local file (path configurable, default `~/.whisper/prompts.log`). Non-negotiable, see data sovereignty.
- **Provider tagging:** every call records which provider was used and whether the prompt left the customer's account boundary (Bedrock = no, OpenAI/Anthropic-direct = yes). This metadata travels with the response.
- **Cost accounting:** token counts logged so customers can audit LLM spend caused by the tool.

Prompts themselves live in a `prompts/` directory as templates, not as inline strings scattered through agent code. New agents compose prompts from templates; they don't write them inline.

### 6. Agents compose patterns; orchestrators compose agents

Specialist agents (compute, storage, monitoring, ...) are not hand-coded over specific patterns. An agent is constructed by category — `ComputeAgent` is "the agent that runs all patterns where `category == "compute"`." Adding a pattern to the compute category automatically makes it available to the compute agent. No agent code changes.

The orchestrator composes agents the same way — by querying which agents are registered, not by hard-coding a list. Adding a `NetworkAgent` is a registration, not a refactor.

This is what makes the question "do we need network/database/ML-GPU specialist agents?" cheap to answer later: spinning one up is mostly category metadata, not new orchestration code.

### 7. The control plane / data plane split is real from day one

Even in OSS, write code as if there will be a control plane (license check, software update, opt-in telemetry) and a data plane (everything that touches customer resources). The two must be separable modules with a narrow, documented interface — even if the control plane is a no-op in OSS today.

In practice this means: no customer data ever appears in a function that could one day phone home. If you're tempted to add a "send telemetry" call near a `Finding`, the architecture has drifted. The control plane talks to the vendor; the data plane talks to the customer's AWS account. They share types only via explicit, audited boundary functions.

### 8. State is owned by the customer, schema is owned by us

Every piece of state the tool produces (findings history, audit log, remediation status, scan schedules, configuration) lives in customer-owned storage: a local SQLite file in OSS, a DynamoDB table in their account in paid. We define the schema, they hold the bytes.

Schema is versioned and migration-aware from v1. Don't ship a v1 that can't be migrated to v2. Every persisted record has a `schema_version` field. There is one place in the codebase that owns the schema (a `schemas/` module), and every storage backend (SQLite, DynamoDB, S3) reads/writes through it.

### 9. Configuration is layered, discoverable, and validated

Configuration sources, in precedence order: command-line flags → environment variables → config file (`~/.whisper/config.toml`) → defaults. One module owns the merge and validation. Adding a new config option is one edit, not five.

`whisper config doctor` (introduced in the Slack task) must validate every config option, not just the ones the Slack app needs. It is the universal "is this install healthy?" command across all milestones.

### 10. Tests are part of the contract, not a hygiene chore

Every pattern has a unit test that mocks the AWS API and asserts the finding it produces. Every agent has a test that asserts it routes findings correctly. Every remediation has a dry-run test that asserts the planned action without executing it. The orchestrator has an integration test that runs against `moto` (mocked AWS) end-to-end.

A PR that adds a feature without the corresponding test layer should be rejected by review. This is what lets us migrate patterns to agentic specs later without fear — the tests prove the behavior didn't change.

## How to work in this repo

- Prefer small, reviewable PRs. Each task in this plan should land as one or a few PRs, not a monolith.
- Tests required for new agent logic and any remediation code path (see principle 10).
- Any code that touches customer AWS APIs must support a `--dry-run` flag and log what it *would* do.
- Any code that produces a prompt to an LLM must go through the `LLMClient` interface and log the prompt locally (principle 5).
- Never add a dependency on a vendor-hosted service for customer data flow (principle 7).
- When in doubt about OSS-vs-paid placement, default to OSS and ask.
- When a task seems to require violating an architectural principle, stop and ask. The principle wins by default.

## Refactoring expectations

The existing codebase predates these principles. Some of it conforms; some doesn't. **The first task that touches a given layer is also responsible for bringing that layer into conformance.**

- First task to touch the pattern layer: refactor all 20 patterns to the `Pattern` interface (principle 1).
- First task to touch the LLM layer: introduce the `LLMClient` interface and migrate existing call sites (principle 5).
- First task to touch storage: introduce the schema module and the storage backend abstraction (principle 8).

This is intentional. We pay the refactoring cost once, at the moment it's most clearly justified by the next feature, rather than as a separate "cleanup" project that never gets prioritized. Tasks should budget for this refactoring work explicitly in scope.
