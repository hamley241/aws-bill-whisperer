# conv_happy_why_step

The headline conversational happy path. User asks "why did you pick
step 1 over step 2?" against the shared 3-finding cross-pattern scan
+ plan. The LLM emits a JSON envelope that:

- cites both step findings by id (no invention),
- writes inline canonical dollar figures ($138.24, $80.00) drawn
  directly from the plan steps — no arithmetic, no derived totals,
- marks the question as in-scope with `implies_action_taken=false`.

## What this proves

- The regex-strict-rules protocol passes a clean answer through
  unchanged when every inline `$N` matches a canonical scan/plan
  value and no arithmetic phrasing is present.
- `cited_finding_ids` are canonicalised into scan-impact order before
  being recorded on the conversation turn.
- No validator path fires; `turn_kind == "answered"`.

## Paired with

- `conv_happy_what_about_nat` — same plan, narrower question scope.
- `conv_happy_dev_only` — same plan, user asks about a refinement that
  the conversation layer redirects to `/whisper plan`.
- All five `conv_adv_*` fixtures share these findings and this plan,
  exercising one drop path each.

## Re-recording

Replay is the default. To re-record against a live LLM, set
`WHISPER_ALLOW_REAL_LLM=1` and run:

    python -m agent.evals.runner conv_happy_why_step --surface conversation --re-record
