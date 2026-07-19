# AWS Bill Whisperer — Project Context for Claude Code

This file is the durable strategic context for the project. Read it before starting any task. It explains *why* we're building what we're building, so implementation choices align with the product direction.

## Product identity

**AWS Bill Whisperer** is the platform engineer's cost copilot. It explains AWS bills in plain English, detects waste, and (in the paid tier) autonomously opens PRs to fix it.

**Buyer:** mid-market platform/infra teams ($50K–500K/mo AWS spend). One platform engineer wears the cost hat as 20% of their job. They live in Slack and Terraform/IaC.

**Wedge:** in-account AWS bill explanation and safe single-account remediation. **Moat:** agentic FinOps — cross-finding reasoning, account memory, and goal-driven savings workflows grounded in deterministic evidence, audit logs, and safe remediation modes. **Pricing model:** open-core with paid tier.

## Non-negotiable architectural principles

These constraints override convenience. If a design decision conflicts with one of these, the principle wins.

1. **Data sovereignty.** Customer data — bills, resource metadata, utilization metrics, findings, fix history — NEVER leaves the customer's AWS account or their chosen LLM endpoint. The vendor (us) does not host or store customer data anywhere.

2. **Bring your own model.** Bedrock (in-account) is the default and recommended path. OpenAI/Anthropic-direct API is supported but flagged as "your prompts leave your account." Customer chooses with informed consent.

3. **In-account execution.** Both OSS and paid tiers run entirely inside the customer's AWS account. The paid tier is a deployable CloudFormation/Terraform stack, NOT a hosted SaaS. The vendor operates only a control plane (license check, software updates) that never touches customer data.

4. **PR-native remediation.** Fixes land as pull requests to the customer's IaC repo wherever possible. The approval workflow is the PR review — we do not build a separate approval UI.

5. **Generous OSS.** The OSS tier must be the most complete free cost-optimization tool available. Detection, explanation, agent framework, and single-account remediation are ALWAYS OSS. Gating intelligence kills the wedge.

## Design rule: LLM proposes; framework disposes

This is the rule that lets us sell "agentic" without ceding the audit trail. The LLM is a reasoning interface, not a decision authority. Every action the user observes lands through a deterministic path: detection → safety gate → mode dispatch → `audit_remediation` write. The LLM's role is presentation, ranking, and planning *around* that path, never inside it.

**The LLM may:**
- explain findings in plain English
- rank and prioritize across findings using context the user provided
- plan a sequence of fixes toward a stated savings goal
- ask clarifying follow-up questions
- suggest which of a pattern's available modes (`dry_run`, `command`, `pr`, `api_call`) fits the situation; the pattern's eligibility checks decide whether that mode actually runs

**The LLM never:**
- invents finding IDs, resource IDs, ARNs, account IDs, or regions
- reports cost figures absent from `Finding.evidence` or `Finding.monthly_impact_usd`
- claims a safety gate passed — only the deterministic gate code in the pattern can state that
- describes a remediation as completed — only an `audit_remediation` write with `success=True` can
- bypasses, overrides, or argues against a safety gate — gates are not negotiable, including by the user via the LLM
- infers a resource's state from prior context — only a fresh boto3 call through the pattern reflects current state
- recommends an action the pattern hasn't exposed as a remediation mode with passing safety gates

Agent outputs that rank, plan, or recommend action are replayable against recorded scan fixtures. The full trace schema lives in `docs/agent-traceability.md`, produced by the agent-loop spike.

## The OSS/paid seam

**Public contract — write this down, commit to it:**

Always OSS (trust ladder rungs 1–4):
- All detection patterns and the pattern plugin interface
- Local agent loop (single-process) that reasons across findings, ranks, plans
- Plain-English explanation via customer's chosen LLM
- Single-account scanning and remediation
- All four remediation modes per pattern (`dry_run`, `command`, `pr`, `api_call`) for single-account use
- Self-hostable Slack app
- CLI + Slack + multi-turn thread Q&A
- Local audit logs (SQLite at `~/.whisper/whisper.db` + JSONL prompt log)
- Agentic specs for all patterns (markdown + runnable code)
- Single-account PR generation for any pattern that supports `pr` mode

Always paid (trust ladder rungs 5–6):
- Multi-account orchestration (AWS Organizations + cross-account roles)
- Scheduled scans with state tracking across runs
- Account-level memory for the agent (cross-scan context, "we agreed last month not to touch X")
- Cross-account prioritization and savings campaigns
- Policy packs and team-ownership mapping
- Approval workflows and PR autopilot at scale (merge tracking, regression rollback)
- Closed-loop optimization: monitor → revert → re-plan
- SSO/SAML, RBAC, audit retention guarantees
- Enterprise integrations (Datadog, PagerDuty, ServiceNow)
- Upgrade automation, support, SLA

**The seam in one sentence:** *OSS has local reasoning — single account, one engineer, on-demand. Paid has recurring, multi-account, governed autonomy.*

## Current state

- 20 detection patterns (`p001`–`p020`). The pattern interface has been upgraded around Category, REQUIRED_IAM, `RemediationMode`, `RemediationResult`, and a single `remediate(finding, mode)` entry point; p001 is the first fully bulletproof implementation of the new contract.
- **p001 unattached EBS** ships all four remediation modes, safety gates, evidence-rich findings, and a Slack Open-PR button wired through the audit log.
- Single `LLMClient` interface in `src/llm/` (Bedrock default, OpenAI + Anthropic-direct supported). Every prompt logged to `~/.whisper/prompts.log` with provider + boundary-crossed metadata.
- Prompt templates in `src/prompts/` — cost analysis, anomaly, recommendations, finding explanation, thread reply.
- `FindingPresenter` abstraction in `src/presenters/` — text, markdown, JSON, Slack Block Kit.
- `WhisperConfig` + `whisper-config doctor` for validation (`--json`, `--check`, `--no-network`).
- SQLite-backed audit log in `src/storage/` + versioned `src/schemas/` (findings, remediations, prompts).
- Slack app: `/whisper scan`, Block Kit findings, thread Q&A via `LLMClient`, Open-PR button for p001, SAM Lambda adapter + manifest + quickstart docs.
- `_legacy/strandsagents/` contains the retired pre-clean-architecture Strands skeleton; it is kept for reference and not imported.
- ~390 tests passing, no AWS / Slack network access required.

## 90-day plan

**Weeks 1–3 — Slack app, self-hostable.** *Shipped (PRs 1–6 + refactor wave 0a–0d).* Slack app posts threaded findings, explains them, takes follow-up questions, runs on Lambda or Socket Mode.

**Weeks 4–7 — Agent foundations + first bulletproof patterns.** Sequence:

1. *Shipped:* Pattern interface upgrade (Category, REQUIRED_IAM, `RemediationMode`, `RemediationResult`, `remediate(finding, mode)` single entry point — PR 7a).
2. *Shipped:* SQLite audit log + versioned `schemas/` module (PR 7b).
3. *Shipped:* **p001 unattached EBS — first bulletproof pattern**, all four remediation modes, safety gates, Slack Open-PR button wired through the audit log (PR 7c).
4. **Agent-loop spike (1 week).** A real LLM agent loop over the clean modules — not Strands. Goal: prove cross-finding reasoning works on a stub scan; lay down the agent contract before any new pattern is designed against it.
5. **Agent evaluation harness, built in the same spike window.** Recorded scan fixtures + canned questions + expected behaviour. Every agent-loop change runs through it before merge. This is what keeps "agentic" honest.
6. **p006 NAT Gateway as the first agent-native pattern.** Evidence schema designed around what the planner needs (egress destinations, top traffic, VPC endpoint candidates). Detection deterministic; "which endpoint, in what order, with what risk" is LLM-proposed against deterministic evidence.
7. **p004 idle EC2.** Same shape: deterministic evidence, agentic prioritization and recommendation framing.
8. **Cross-pattern savings planner.** Given a scan and a goal ("cut 20%"), the planner walks findings across patterns and proposes an ordered plan with $ impact, risk, and the modes it would use. Output is a plan, not an execution — the user clicks per step.

**Weeks 8–10 — Trust ladder (six rungs, replaces prior two-rung spec).** See "Trust ladder" section below. Rungs 1–4 are OSS. Rungs 5–6 are the paid-tier boundary.

**Weeks 11–13 — Multi-account.** Cross-account role assumption, AWS Organizations, account-level memory in the agent. First exclusively-paid feature.

## Trust ladder (replaces prior Weeks 8–10 two-rung spec)

Six rungs, mapped to the OSS/paid seam. A customer climbs one rung at a time; the agent never reaches above where the customer has explicitly opted in.

1. **Explain.** Scan, render findings, narrate in plain English. *OSS, shipped.*
2. **Prioritize.** Rank findings by $ impact, risk, and customer-stated goal. *OSS, in progress (cross-pattern reasoning).*
3. **Plan.** Given a goal, propose an ordered remediation sequence with per-step $ impact and risk. *OSS, planned (cross-pattern planner).*
4. **Propose.** Generate the exact PR diff or AWS CLI command for each step. *OSS, shipped for p001; expanding pattern-by-pattern.*
5. **Autopilot with approval.** Scheduled scans + queued PRs; a human approves each PR but the agent runs the loop. *Paid.*
6. **Closed-loop optimization.** Agent monitors post-remediation metrics, reverts on regression, runs the next scan, plans, proposes, repeats. *Paid.*

Rungs 5–6 are the paid boundary because they require recurring infrastructure operated in the customer's account (state, schedulers, PR-tracking webhooks) — the kind of thing a single engineer with a laptop doesn't run themselves.

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

Python 3.10+, slack-bolt, boto3, Bedrock/OpenAI/Anthropic SDKs, AWS SAM, SQLite. MIT-licensed.

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

Pattern authors: a pattern never `print()`s — it logs with `logger.exception`/`logger.info` carrying structured `extra={"pattern_id", "region", "outcome", ...}` (see `p004_idle_ec2.py` for the reference shape), because those records are read by machines, not just humans.

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

### 6. One agent, many tools; tools are patterns

There is one agent loop in OSS. It does not have specialist sub-agents. Patterns are *tools* the agent calls — discovery (which findings exist), evidence (data behind a finding), modes (dry_run / command / pr / api_call). Category metadata helps the agent filter and route ("the user asked about storage — restrict to `Category.STORAGE` patterns"), but no `ComputeAgent` class exists.

The Strands-shaped multi-agent layer in the original design has been retired (see `_legacy/strandsagents/`). Specialist agents added complexity (inter-agent communication, conflicting recommendations, ad-hoc dict formats) without commensurate benefit. One agent with a sharp tool list is easier to reason about, easier to evaluate, and produces more coherent plans.

The paid tier may run multiple planner instances across accounts — but each instance is still a single loop calling the same pattern tools. There is no compute-vs-storage split.

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
- First task to touch the agent layer: build it fresh over the clean modules (`patterns`, `llm`, `storage`, `presenters`, `audit`). Do not refactor `_legacy/strandsagents/`.

This is intentional. We pay the refactoring cost once, at the moment it's most clearly justified by the next feature, rather than as a separate "cleanup" project that never gets prioritized. Tasks should budget for this refactoring work explicitly in scope.
