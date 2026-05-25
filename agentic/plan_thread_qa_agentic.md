# Plan-Thread Q&A — Agentic Design Notes (PR #9)

The plan-thread Q&A path answers follow-up questions about an existing
plan inside the same Slack thread that posted it. This doc explains
the architectural choices and the load-bearing constraints so a
future contributor doesn't read them as oversights.

It sits one rung above `plan_surface_agentic.md` in the renderer
guarantee hierarchy (see below). When the two docs conflict, the
conversation safety boundary wins.

## What this surface MAY do

- Explain, filter, compare, contextualise existing findings and plan
  steps the planner has already produced.
- Render placeholder-substituted canonical dollar figures from the
  scan and plan.
- Surface stale / aging / expired tier signals to the user as
  warnings or refusals.
- Record a bounded ring of `ConversationTurn`s on the
  `ThreadContext`, where the answer text stored is the
  post-validation surfaced prose — never the raw LLM emission.

## What this surface MUST NOT do

- Re-rank, re-prioritise, or re-plan in-thread. The planner is the
  only place recommendations come from. The conversation layer never
  imports or invokes `SavingsPlanner`. The
  `TestConversationLayerCannotInvokePlanner` tripwire enforces this
  at the module level.
- Invent finding IDs, costs, modes, sub-actions, or candidates.
- Imply an action was taken ("I stopped", "I deleted", "I opened a
  PR"). Recommendation language ("you could stop", "the plan
  recommends") is fine; past-tense execution language is a hard drop.
- Compute derived dollar figures (sums, percentages, annual
  projections). Arithmetic over canonical figures is a hard drop
  (`SYNTHESIZED_COST`) so the planner stays the single source of
  totals.
- Silently expand scope beyond the current cached scan + plan.
- Imply a re-scan or re-plan occurred unless one actually did. The
  only bridge from conversation to recommendation is the explicit
  `/whisper plan goal: <text>` slash command in the channel.

## Renderer guarantee hierarchy (extended)

The hierarchy from `plan_surface_agentic.md` gets a new top rung in
PR #9:

0. **Conversation safety boundary** (NEW — short-circuits everything
   else)
   - May explain / filter / compare / contextualise
   - May NOT silently expand scope, invent IDs / costs / modes, imply
     action taken, imply a re-plan happened
   - Freshness gating runs here BEFORE any LLM call
1. **Modes contract** — what the planner offers; resolved by
   `AvailableModesResolver`. Consulted by the planner (gating LLM
   emissions) and by the renderer (computing `is_safe_executable`).
2. **Planner / rubric guarantees** — canonical totals, dropped-step
   bookkeeping, ranking metadata. Pinned by the planner rubric.
3. **Renderer guarantees** — mode badges, hedge wording, button
   affordances, footer format. Operates on `RenderablePlan`.

The conversation layer reads the planner's `PlanResult` as canonical
(rung 2) and the scan's `ScanResult` as canonical. It never re-ranks,
never re-computes totals, never re-checks safety. Its validators are
about what the LLM's response is allowed to say, not about
re-deriving planner truth.

## Architectural placement

```
Slack /whisper plan
       │
       ▼
   handlers/plan.py  ──── scan + planner.plan()  ────── PlanResult
       │                                                  │
       ▼                                                  │
   ThreadContext(scan, plan, created_at, turns)  ◀───────┘
       │
       ▼
   InMemoryThreadStore (keyed by parent_ts)
       │
       ▼  later — user replies in thread
       │
   handlers/threads.py  ─── routes on plan_result presence
       │
       ▼
   analyzer/plan_conversation.answer_plan_thread_question(...)
       │
       ▼
   freshness gate  →  pre-router  →  LLM (envelope JSON)
                                      │
                                      ▼
                            validators chain
                                      │
                                      ▼
                       surfaced prose + ConversationTurn
                                      │
                                      ▼
                          context.record_turn(turn)
```

`ThreadContext` is the value object both the analyzer and the slack
layer share. It lives in `src/analyzer/thread_context.py` to keep the
import direction one-way: slack depends on src, never the other way
round. The `InMemoryThreadStore` stays in `slack/thread_store.py` —
it's the slack-side state holder keyed by Slack thread ts strings.

## The dollar regex-strict-rules protocol

The LLM may write `$N(.NN)?` figures inline in its `answer`, but
every such figure MUST equal a canonical scan/plan value within
`$0.01`. The canonical universe is:

- every finding's `monthly_impact_usd`,
- every plan step's `monthly_impact_usd`,
- the plan's `total_monthly_impact_usd`,
- every sub-action's `est_monthly_savings_usd`.

Inline `$N` literals that don't match any canonical value drop the
envelope as `INVENTED_COST`.

Arithmetic phrasing alongside any inline `$` is a hard drop as
`SYNTHESIZED_COST`, EVEN WHEN the figure coincidentally matches a
canonical value. The arithmetic-marker list is in
`plan_conversation._looks_synthesized` and currently catches:

- `together`, `combined`, `total of`, `totals`, `in total`
- `summed`, `adds up`, `roughly`, `approximately`, `%`, `percent`
- `average`, `per year`, `/year`, `/yr`

The planner is the only place derived totals come from. The
conversation layer must not compute them, even when arithmetic is
trivial — once "$138.24 + $80.00 = $218.24" is acceptable, "$1,658.88
per year" becomes acceptable, and a future LLM hallucinated derivation
becomes harder to distinguish from a legitimate one.

### Decision record — regex-strict-rules over placeholders

The PR #9 contract sign-off offered two protocols:

1. **Placeholders** — LLM emits `{{0}}`, `{{1}}` slots in `answer` +
   parallel `cited_dollar_amounts` array. Framework validates each
   amount against canonical values, then substitutes.
2. **Regex-strict-rules** — what shipped.

Sign-off rule: try placeholders first; fall back to regex if the LLM
ignores the template instructions across recordings; STOP and surface
which path was taken. Falling back without surfacing is silent
retreat.

What actually happened: the PR #9 author had no usable LLM
credentials in the build environment to run a live trial against the
placeholder protocol. Every recorded fixture response was hand-
crafted by the author. The user authorised the regex-strict-rules
path (sign-off option c) rather than push the placeholder protocol
on hand-crafted recordings alone. Regex-strict-rules ships.

The `INVENTED_COST` vs `SYNTHESIZED_COST` distinction stays
semantically the same under both protocols: invented = `$N` not in
canonical set, synthesised = arithmetic phrasing present. Adversarial
fixtures don't change shape on a protocol switch.

If a future change re-introduces placeholders, document the switch
here, re-record fixtures, and remove the regex inline-$ canonical-
match step from `validate_envelope`. Two parallel protocols are not
acceptable.

### Follow-up: validate the placeholder protocol against real LLM recordings

**TODO (deferred from PR #9):** Validate the dollar-placeholder
protocol against real LLM recordings before replacing
regex-strict-rules.

Concrete steps:

1. Construct an alternative `plan_thread_reply` prompt that demands
   `{{N}}` slots in `answer` and a parallel `cited_dollar_amounts`
   array (the prompt text from this file's git history at the
   PR #9 commit boundary is a starting point — pre-regex-switch).
2. Run `WHISPER_ALLOW_REAL_LLM=1 python -m agent.evals.runner
   --surface conversation --re-record` against all 8 conversation
   fixtures with the placeholder prompt in place.
3. Measure: how many recordings contain at least one `{{N}}` slot?
   How many contain inline `$N` literals despite the prompt?
   How many emit a syntactically correct `cited_dollar_amounts`
   array? Real-LLM compliance below ~95% per-fixture is fragile;
   keep regex-strict-rules and document the rate observed.
4. If compliance is high: swap the prompt, swap `validate_envelope`
   to use placeholder integrity instead of inline-$ canonical match,
   re-record fixtures with the substituted output as the surfaced
   text, update this decision record with the empirical evidence.

The placeholder protocol has a higher theoretical safety ceiling
(every `$` figure carries an explicit canonical-citation index; no
prose-level arithmetic detection is needed). Regex-strict-rules
covers the same failure modes via prose validation, but the
arithmetic-heuristic is necessarily heuristic. The follow-up exists
because the better contract may yet be reachable; it's deferred,
not abandoned.

## Out-of-scope handling — two layers, both required

| Layer | Trigger | LLM call? |
|---|---|---|
| **A: deterministic pre-router** | Account ID, billing portal, "stop X for me" / "go ahead and apply it" matches | No — no tokens spent |
| **B: LLM envelope `is_in_scope=false`** | Anything Layer A missed that the LLM judges out of scope | Yes — but the LLM does not write the user-visible refusal |

Layer A patterns are intentionally narrow (high precision over
recall). When in doubt, the question falls through to Layer B, which
the LLM resolves by setting `is_in_scope=false` and a
`scope_category` from a closed set: `account_metadata`,
`billing_portal`, `iam_policy`, `other`.

Layer B requires the envelope to have an empty `answer` and empty
citation arrays when `is_in_scope=false`. The framework renders the
deterministic refusal keyed on `scope_category`. The LLM never
writes the prose surfaced to the user for out-of-scope questions.

**Stop-and-surface:** if the closed `scope_category` enum needs more
than the four values to cover real failure modes, that's a product
decision. Don't silently add a category. Same for false positives in
the pre-router: a legitimate planning question that matches as
out-of-scope is a tuning question that needs review, not a silent
regex tweak.

## Freshness contract

The trust contract on plan age. First user-visible time-based
boundary in the product.

| Tier | Age | Behaviour | User-visible language |
|---|---|---|---|
| **fresh** | 0–30 min | normal answer | (no extra footer) |
| **aging** | 30 min – 4 h | normal answer + age footer | `_(plan is 1h 14m old)_` |
| **stale** | 4–24 h | warning prefix + softened-language LLM answer | `:warning: Plan is 6h old; resource state may have changed — run /whisper scan for fresh data.` |
| **expired** | > 24 h | no LLM call; deterministic refusal | `:hourglass: This plan was generated 36h ago. AWS state and costs may have changed materially. Run /whisper scan then /whisper plan for an up-to-date answer.` |

Why these thresholds (defaults; configurable via
`WhisperConfig.plan_thread_freshness_*`):

- **30 min** — typical "I posted this and someone in the channel is
  replying now" window. Pestering with footers here is noise.
- **4 hours** — workday session boundary. A user returning after
  lunch should see they're looking at older data.
- **24 hours** — meaningful business-day boundary. EBS volumes get
  attached/detached, EC2 instances get retired, IaC PRs merge. Past
  this point, confident answers are bad-faith.

Three monotonic threshold fields on `WhisperConfig`; doctor
validates the ordering. The expired check runs BEFORE the LLM call;
no tokens spent on day-old plans. Stale-tier behaviour also includes
a prompt instruction to soften recommendation language —
"contextualizing" verbs rather than "you should definitely". A
warning-level rubric check audits adherence; not a hard drop.

**Stop-and-surface:** freshness gate edge cases (DST transitions,
clock skew between scan and reply, cross-process timestamp drift)
are not currently exercised. If one of these surfaces in real
deployment, surface it explicitly rather than silently widening the
thresholds.

## Conversation safety boundary — the four rules

Reproduced here from the constraint sign-off for in-place reference:

1. **May explain / filter / compare / contextualise** existing
   findings and plan steps.
2. **MUST NOT silently expand scope** beyond the current scan + plan.
3. **MUST NOT invent** finding IDs, resource IDs, costs, modes,
   actions, sub-actions, candidates.
4. **MUST NOT imply** a re-scan or re-plan occurred unless one
   actually did — and one never does from the conversation layer.

## What's deferred to later PRs

- **In-thread re-planning** — the conversation layer is forbidden
  from invoking the planner. Re-plan is a real escalation with its
  own UX (replace vs append the plan, audit-log shape, re-rendering
  the Slack message) that deserves its own contract review.
- **Multi-turn evaluation with recorded conversation chains.** PR #9
  evaluates one turn at a time. Multi-turn record-replay is its own
  design.
- **Persistent thread storage across process restarts.** Paid-tier
  concern. The in-memory `InMemoryThreadStore` is OSS-tier-correct.
- **Action affordances in conversational responses.** No new
  buttons; remediation flows through the per-finding Block Kit
  surface from `/whisper scan`.
- **Cross-thread memory.** Each thread is independent.
- **Scheduled-scan-triggered threads.** Paid tier.
- **Multi-account.** Paid tier.
- **Markdown presenter for conversational responses.** No consumer.
- **Persisting `ConversationTurn`s to the audit log.** PII /
  retention / prompt-log overlap is its own design (paid tier
  governance touches this).

## Eval discipline

Conversation fixtures inherit the same replay/rerecord discipline as
planner fixtures. Live LLM calls require both `--re-record` and
`WHISPER_ALLOW_REAL_LLM=1`. The runner uses a `--surface
conversation` flag to switch fixture roots; both surfaces share one
runner, one fixture philosophy, and one assertion-vocabulary
registry. Conversation evals are NOT soft integration tests — they
gate CI the same way planner evals do, with the same warning-vs-gate
discipline for assertions that aren't yet reliable enough to fail
loud on.
