# Task: Self-hostable Slack app for AWS Bill Whisperer

**Read `CLAUDE.md` first.** It contains the strategic context, the OSS/paid seam, and the architectural principles that constrain this work. This task is Weeks 1–3 of the 90-day plan: shipping the Slack app that makes the killer demo real.

## Goal

A self-hostable Slack app that a platform engineer can install in under 10 minutes and use to scan their AWS account, ask cost questions in natural language, and receive plain-English findings with copy-paste fix commands — entirely from Slack.

This is the OSS surface. It must be free, complete, and self-hosted. No vendor infrastructure in the data path.

## Success criteria

A new user with an AWS account and a Slack workspace can:

1. Clone the repo and run a single command to start the Slack app webhook locally (or via a one-click deploy to AWS Lambda).
2. Follow a documented flow to create their own Slack app from a provided manifest and install it to their workspace.
3. Configure AWS credentials and LLM backend (Bedrock by default) via environment variables or a config file.
4. Type `/whisper scan` in any Slack channel and within 90 seconds receive a thread with the top 3–5 cost findings, each with $ impact and a suggested fix command.
5. Reply in-thread with follow-up questions ("why is NAT Gateway so expensive?", "show me idle EC2") and get specialist-agent responses routed via the existing intent router.

End-to-end time from `git clone` to first finding in Slack: **under 10 minutes** for someone who already has AWS creds and Slack admin rights.

## What to build

### 1. Slack integration layer (`slack/` module)

- Slack Bolt for Python framework (`slack-bolt`) for handling slash commands, events, and interactive components.
- Slash command handler for `/whisper scan` that triggers the existing orchestrator and posts findings as a threaded message.
- `app_mention` event handler so users can `@whisper` in a channel for ad-hoc questions, routed through the existing intent router.
- Message event handler for in-thread follow-ups (only when the thread was started by the bot).
- Interactive button handler for "Open PR" actions on findings (button is present but stubbed for now — full PR flow lands in Weeks 4–7).

### 2. Slack app manifest

- A `slack/manifest.yaml` the user pastes into Slack's app config to create their own app.
- Documented OAuth scopes — minimum needed: `chat:write`, `commands`, `app_mentions:read`, `channels:history` (only for bot's own threads).
- Clear separation: the manifest is for the *customer's* Slack app. We do not ship a vendor-hosted Slack app in OSS.

### 3. Deployment options

Provide two ways to run the webhook handler, both self-hosted:

- **Local / Docker:** `docker-compose up` runs the webhook handler on a port, with `ngrok` instructions in the docs for local dev / first-time demo.
- **AWS Lambda via SAM:** extend the existing SAM template with a Lambda function for the Slack webhook. Customer deploys to their own AWS account. This is the recommended production path for OSS users.

Do NOT add a vendor-hosted deployment option. That belongs to the paid tier (later).

### 4. Finding presentation

Findings posted to Slack must:

- Lead with total $ waste detected and the count of findings.
- Show the top 3–5 findings as separate Block Kit sections with: pattern name, affected resource(s), $/mo impact, plain-English explanation, fix command.
- Use the LLM (via the customer's configured backend) to generate the plain-English explanation, with the prompt logged locally per the principle in `CLAUDE.md`.
- Include an "Open PR" button on findings that have a PR-capable remediation (stub the action for now — log to console and reply "PR support coming in a future release").
- Include a "Show me more" overflow menu for the long tail of findings.

### 5. Intent routing in threads

- When a user replies in a thread started by the bot, route the message through the existing intent router (`intent_router` module) to the appropriate specialist agent.
- The specialist's response is posted back in the same thread.
- Maintain thread-level context: subsequent messages in the same thread should be aware of the original scan findings (pass them as context to the agent).

### 6. Configuration

- `WHISPER_LLM_BACKEND` env var: `bedrock` (default) or `openai` or `anthropic`.
- `WHISPER_AWS_PROFILE` / standard AWS env vars for the account to scan.
- `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` for the customer's own Slack app.
- A `whisper config doctor` CLI command that validates all of the above and prints a clear "ready / not ready" status.

### 7. Documentation

A new `docs/slack-quickstart.md` that walks through the entire setup in under 10 minutes, with screenshots of the Slack app config flow. Test this with someone who hasn't seen the repo before — the time-to-first-finding is the success metric.

## Constraints (from `CLAUDE.md`, restated)

- **Data sovereignty:** the customer's Slack token, AWS creds, and all scan data stay on their infrastructure. No telemetry to us by default. If we add opt-in telemetry later, it's a separate explicit feature.
- **Prompt logging:** every LLM call must log the full prompt to a local file (configurable path, default `~/.whisper/prompts.log`) so the customer can audit what data was sent to the model.
- **Dry-run by default:** the "Open PR" button is stubbed in this milestone. Any AWS API call that mutates state must support `--dry-run` and log the intended action.
- **OSS only:** everything in this task is in the OSS repo. Don't add license-check hooks, feature flags for paid tier, or anything that gates functionality.

## Architectural work this task inherits

Per the refactoring expectations in `CLAUDE.md`, this is the first task to touch several layers. Bring them into conformance with the principles as part of this work:

- **LLM layer (principle 5):** This task introduces LLM-generated explanations for findings. Before writing those, introduce the `LLMClient` interface, migrate existing Bedrock/OpenAI call sites to it, and move all prompts into a `prompts/` directory as templates. Mark each prompt template with provider metadata so the boundary-crossing check is enforced at the interface level.
- **Output surface abstraction (principle 3):** The Slack handler is a new presentation surface. Before writing it, factor the existing CLI and web UI into a common presenter pattern that consumes `Finding` objects. The Slack handler is then a third implementation of the same pattern, not a parallel codebase. If this reveals that findings aren't structured well enough to support multiple presenters cleanly, fix the `Finding` schema as part of this task (principle 2).
- **Configuration (principle 9):** `whisper config doctor` is introduced here. Build it as a universal validator over the whole config schema, not just the Slack-relevant fields. This is the foundation every later milestone will extend.
- **Tests (principle 10):** Every new module needs unit tests. The Slack handler needs a test that asserts a mock `Finding` renders to the expected Block Kit payload. The presenter abstraction needs a test that asserts all surfaces (CLI, web, Slack) render the same finding consistently.

Scope this refactoring honestly. If it's larger than expected, surface that early — it's better to land the refactor as PR 0 and the Slack feature in subsequent PRs than to entangle them.

## Architectural work this task does NOT inherit

To keep scope bounded, the following principles can wait for the task that first needs them:

- Pattern plugin interface (principle 1) — wait for the three-patterns task in Weeks 4–7, which will refactor the existing 20 patterns.
- Storage / schema module (principle 8) — wait for the trust-ladder task in Weeks 8–10, which introduces persistent state for scan history.
- Control/data plane split (principle 7) — wait for the multi-account task in Weeks 11–13, which is the first to need a control plane.
- Agent composition by category (principle 6) — defer; current hand-coded agents are acceptable until we add a new category.

If this task ends up needing one of those abstractions sooner than expected, stop and ask before improvising a version that the later task will have to redo.

## What NOT to build in this task

- Vendor-hosted Slack app with OAuth install flow (paid tier, later).
- Actual PR-opening logic (Weeks 4–7).
- Multi-account support (Weeks 11–13).
- Scheduled / continuous scanning (paid tier, later).
- A web dashboard for findings (the existing FastAPI UI stays as-is; Slack is the new primary surface for this milestone).
- New detection patterns (use the existing 20).

## Suggested implementation order

1. Read `CLAUDE.md` and the existing repo structure. Confirm the orchestrator, intent router, and specialist agents work as documented.
2. Add `slack-bolt` dependency and create the `slack/` module with a minimal `/whisper scan` handler that calls the orchestrator and posts a "scan started" message.
3. Wire up the orchestrator's output to a Block Kit findings message.
4. Add LLM-based plain-English explanation for each finding (use the existing LLM backend abstraction).
5. Add thread reply handling routed through the intent router.
6. Add the SAM Lambda function for the webhook.
7. Write the manifest, the quickstart doc, and the `whisper config doctor` command.
8. End-to-end test: fresh AWS account, fresh Slack workspace, time the setup. Iterate until it's under 10 minutes.

## How to ask questions

If the existing repo's behavior contradicts something in this prompt, surface the contradiction and ask before working around it. If a design choice has OSS/paid implications not covered by `CLAUDE.md`'s seam contract, ask before deciding. Otherwise, proceed and ship.

Land this as a series of small PRs, not one monolith. Suggested splits:
- PR 1: `slack/` module skeleton, slash command, "scan started" reply.
- PR 2: Block Kit findings rendering.
- PR 3: LLM-based explanations + prompt logging.
- PR 4: Thread reply routing.
- PR 5: SAM Lambda deployment + manifest + docs.
- PR 6: `whisper config doctor` + final docs polish.
