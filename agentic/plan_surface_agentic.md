# Plan Surface — Agentic Design Notes (PR #8)

The `/whisper plan` Slack command and `whisper-plan` CLI surface the
cross-pattern planner's output for humans. Both are PRESENTATION-ONLY.
This doc explains the intentional scope boundaries so a future
contributor doesn't read them as oversights.

## Architectural placement

```
Finding → SavingsPlanner.plan() → PlanResult
                                      │
                                      ▼
                       presenters.plan.to_renderable()
                                      │
                                      ▼
                              RenderablePlan  ─── shared intermediate
                              /     │     \
                             ▼      ▼      ▼
                   TextPlan  JSONPlan  BlockKitPlan
                    │           │           │
                    ▼           ▼           ▼
                  CLI       CLI/API    Slack
```

`RenderablePlan` is the contract. Both CLI and Slack derive their
output from the same intermediate. Drift between "Slack fixed a
rendering bug X" and "CLI still has rendering bug X" is the failure
mode this guards against.

## What this surface MAY do

- Render the plan's summary, per-step rationale, mode badges, sub-actions
- Echo the user's goal (or the default-goal placeholder)
- Show a single Open-PR button for PR-mode steps whose
  `is_safe_executable` is True (see "Executable affordances" below)
- Display a dropped-step COUNT
- (CLI only) Surface debug breadcrumbs in the failure path
  (`parse_retry_count`, `model`, `provider`)

## What this surface MUST NOT do

- Re-rank, re-sort, or re-order steps — the planner's `order_rank` is
  canonical. `to_renderable` sorts once and freezes the sequence.
- Re-compute `total_monthly_impact_usd` — the planner's value is the
  one humans see. Re-summing in the renderer is a future-divergence trap.
- Show ANY content from dropped steps — finding ids, rationales, raw
  emissions all stay out of the rendered surface. The count is the
  only signal a user sees.
- Show unhedged action wording on a step whose source finding has
  `safe_to_fix=False`. See the verb-list invariant
  (`src/presenters/_verb_lists.py` +
  `tests/test_plan_rendering.py::TestRenderingPreservesGuarantees`).
- Surface implementation details (model name, provider, retry counts) in
  Slack. Shared-channel messages don't need to leak internals; the CLI
  failure path is where debugging breadcrumbs belong.
- Invent buttons that bypass the existing remediation paths. The
  Open-PR button reuses the existing `open_pr` action_id and the
  existing handler in `slack/handlers/actions.py`. No new action
  handlers were added in PR #8.

## Executable affordances — PR-mode only (PR #8)

In PR #8, the Slack surface attaches an executable button ONLY when:

    step.mode == "pr"
    AND step.is_safe_executable is True
        i.e. step.suggested_mode in finding.available_modes
              AND step.suggested_mode != "dry_run"

`command` and `api_call` modes render their mode badge as text only.
This is intentional, NOT an oversight. Reasons:

1. **The existing button vocabulary is PR-shaped.** The `open_pr`
   action handler resolves to `audit_remediation(..., mode=PR)`. There
   is no `run_command` or `apply_api_call` button in the Slack action
   handler today. Adding those is its own scoped change, with its own
   safety review (especially `api_call`, which mutates AWS state from
   a chat message).

2. **Slack is presentation-only in PR #8.** Plans are a planning surface,
   not an execution surface. Per-finding execution still lives on the
   `/whisper scan` Block Kit findings, which already has the Open-PR
   button for p001. The plan view points to those findings; it doesn't
   own the execution path.

3. **One mode in, one mode out keeps the audit clean.** PR is the only
   mode where the executed artefact (a git diff) gives the customer a
   second review window before anything changes. `command` requires the
   operator to copy-paste-run; `api_call` mutates immediately. Both
   require Slack-specific UX work we deferred.

If you find yourself thinking "this should also have a Run-Command
button" — that's a separate PR with its own action handler, its own
tests, and its own update to this doc. Don't add it inline to a
rendering change.

## Renderer guarantee hierarchy

When invariants conflict, the higher rung wins.

1. **Modes contract** — what the planner offers the user. Computed by
   `AvailableModesResolver`; consulted by both the planner (gating
   what the LLM is allowed to emit) and the renderer (computing
   `is_safe_executable`). This is the highest authority the rendering
   layer ever consults.
2. **Planner / rubric guarantees** — what the planner asserts about a
   plan after the validator promotes raw LLM emissions to `PlanStep`s.
   Canonical totals, dropped-step bookkeeping, ranking metadata. The
   rubric checks (`src/agent/evals/rubric.py`) pin these guarantees.
3. **Renderer guarantees** — how the surface presents what the planner
   produced. Mode badges, hedge wording, button affordances, footer
   format. Operates on the `RenderablePlan` intermediate.

The renderer must not reinterpret planner semantics to satisfy a
wording heuristic. When a wording rule conflicts with a contract
guarantee, the contract wins and the wording rule's scope narrows.

Concrete example: the user's PR #8 sign-off paired two rules — "the
modes contract determines `is_safe_executable`" (rung 1) and
"`safe_to_fix=False` step rationales must hedge" (rung 3). The p006
universal-COMMAND case made them conflict for one fixture
(`p006_observed_candidate`). Resolution: keep rung 1 strict, narrow
rung 3 to `step.mode == "dry_run"` only, and add a tripwire test
(`TestStrictSafeToFixRulingPendingP006ResolverTightening`) that flips
green when the rung-1 fix lands (p006 resolver-tightening — see
`project_p006_followups.md`).

dry_run wording guarantees apply only to observe-only flows.
Actionable modes (`command` / `pr` / `api_call`) are governed by the
modes contract, not by renderer hedging heuristics. A floor invariant
(`UNHEDGED_VERBS` never appear in any rendered rationale regardless of
mode) catches the actual unsafe-language case across all modes.

## `is_safe_executable` — what it asserts, what it doesn't

It asserts: *the modes contract permits this step to be executed via
the UI right now*. Specifically:

    is_safe_executable = (
        step.suggested_mode in finding.available_modes
        AND step.suggested_mode != "dry_run"
    )

It does NOT short-circuit to `finding.safe_to_fix`. `safe_to_fix` is a
pattern-level concept describing whether a remediation's safety gates
all passed. Some patterns (p001 with terraform_managed=True) expose
`pr` mode even when `safe_to_fix` is False — because the gate the user
cares about ("does Terraform own this resource") is upstream of the
gates `safe_to_fix` summarises ("is there a recent snapshot").

The rendering layer must stay out of that policy. It consumes
`available_modes` (computed by `AvailableModesResolver`) and trusts
it. Future patterns can decouple `safe_to_fix` from UI executability
without touching the renderer.

## Stop-and-surface rules carried into this surface

- If a presenter needs a field that's not on `RenderablePlan`, STOP
  and surface. Adding fields opportunistically inside a presenter
  undermines the shared-intermediate contract. The right answer is to
  extend `RenderablePlan` deliberately, with the new field documented
  here.
- If a Block Kit rendering hits a Slack platform limit (50 blocks per
  message, ~3000 chars per text, button-count limits), STOP and
  surface. Working around platform limits in the presenter is scope
  creep. Documenting the limit and shipping a degraded-but-correct
  render (e.g. "showing 5 of 12 steps; see CLI for the rest") is the
  right answer.

## Defensive contracts

### Slack block budget (50-block cap)

`chat.postMessage` rejects messages above 50 blocks with
`invalid_blocks`. The renderer enforces the cap by pre-computing total
block cost and, when over budget, truncating tail steps to fit. A
trailing "X more step(s) totaling $Y/mo not shown — run `whisper-plan`
for the full plan" footer points users at the CLI for the unbounded
view. Order-rank ordering is preserved (shown steps are always the
lowest order_ranks); canonical totals are NOT re-summed from shown
steps (the rendered total stays the planner's value).

`step_block_cost(step)` is the public budget primitive. A unit test
pins `step_block_cost(s) == len(_step_section_blocks(s))` so future
changes to step layout can't silently break the truncation math.

### Slack mrkdwn escaping

Every untrusted text field interpolated into a Slack `mrkdwn` element
passes through one of three helpers in `src/presenters/_slack_text.py`:

| Helper | When to use |
|---|---|
| `escape_mrkdwn(text)` | Raw entity escape only. Internal building block; callers should prefer the composed helpers below. |
| `safe_mrkdwn(text, max_chars)` | Escape + clip. The default for any untrusted field destined for a regular mrkdwn block. |
| `safe_mrkdwn_code(text, max_chars)` | Escape + strip backticks + clip. Required when the field will sit inside an inline code span (`` ` ``) or triple-backtick fence — a stray backtick would otherwise close the surrounding span and re-introduce the injection vector. |

The escape applies Slack's documented entity rules
(https://api.slack.com/reference/surfaces/formatting#escaping):
`&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`. This breaks the three
angle-bracket-based injection vectors — mentions (`<@USER>`),
broadcasts (`<!channel>`), links (`<URL|label>`) — without
disturbing legitimate mrkdwn formatting (`*bold*`, `_italic_`, etc).

Untrusted fields in the plan surface: `goal` (user slash-command
input), `summary` / `rationale` / sub-action `rationale` (LLM output),
`resource_id` / sub-action `candidate_id` (scanner output, may carry
user-controlled tag content; code-span variant). The same helpers
apply on the scan surface (`slack_blocks.py`) for `finding.summary`,
`finding.explanation`, `finding.fix_command`, `finding.resource_*`,
`finding.region`, `finding.evidence` (verbose mode), and
`result.analysis` — same threat model.

CLI text rendering does NOT escape (angle brackets are not control
characters in plain text), so the CLI keeps the original LLM/user
text verbatim. Drift is acceptable here because the escape is
invisible to humans on Slack and absent only where it would be
useless.

### Slack text-length budget (3000-char-per-text-element cap)

The block-count cap (50) is necessary but not sufficient.
`chat.postMessage` also rejects messages whose individual `mrkdwn`
text elements exceed ~3000 characters — same `invalid_blocks` failure
mode, same silent disappearance if the renderer doesn't enforce it.

Each untrusted field has a per-field budget defined as a constant
near the top of the presenter module. Budgets sum to under
`SLACK_MAX_MRKDWN_CHARS` per composed block once decorators (titles,
prefixes, separators) are counted. `safe_mrkdwn` and `safe_mrkdwn_code`
do the clipping; the test class `TestSlackTextLengthBudget` pins the
invariant that every shipped fixture's rendered blocks stay under the
hard limit, plus a parameterized check over all eval fixtures so a
future verbose fixture surfaces a regression in CI rather than in
production.

Clipping appends `… (clipped)` so truncation is visible — silent
truncation would mislead users into thinking they're reading the whole
rationale. The clipper walks back from the cut point if it lands
inside an HTML entity, so post-escape content like `&am…` never
reaches Slack as a malformed entity prefix.

The CLI text path is unbounded — operators running `whisper-plan`
locally see the full rationale verbatim. Only the Slack surface
clips, because only Slack rejects oversize messages.

## What's deferred to later PRs

- Threaded follow-up Q&A on plans (the thread store will carry the
  plan alongside the scan for this; the handler already calls
  `get_store().set(parent_ts, scan_result)` for parity with `/whisper
  scan`)
- Re-planning when the user refines the goal mid-thread
- Per-step `command` / `api_call` execution affordances in Slack
- Multi-account plan rendering (paid-tier concern)
- Persisting plans across CLI invocations
- Markdown plan presenter (no current consumer)
