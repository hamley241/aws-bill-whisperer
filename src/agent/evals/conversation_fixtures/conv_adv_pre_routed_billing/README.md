# conv_adv_pre_routed_billing

User asks "What's my AWS account ID?" — a question the deterministic
pre-router catches before any LLM call. No `recorded_response.json`
file: the LLM is never invoked, so there's nothing to record.

## What this proves

- The pre-router pattern for account-metadata questions fires and
  returns the deterministic refusal keyed on
  `ScopeCategory.ACCOUNT_METADATA`.
- The runner reports `tier=fresh fallback=none` (a pre-routed
  refusal is not a fallback — it's a clean deterministic path).
- `turn_kind == "out_of_scope"` is recorded on the conversation
  turn, distinguishable from LLM-determined out-of-scope responses
  (which use the same turn_kind but carry an envelope).

## Why no recording file

This fixture intentionally has no `recorded_response.json`. The
pre-router path doesn't reach the LLM, so there's no response to
record. The runner handles missing recordings on pre-routed
fixtures by providing a no-op placeholder to the replay LLM that
never gets read. If you find yourself needing to record a response
here, the pre-router pattern probably regressed.

## Stop-and-surface

If a legitimate planning question starts pattern-matching as
out-of-scope here (false positive), STOP and surface — broadening
the pre-router patterns is a product decision, not silent tuning.
See `agentic/plan_thread_qa_agentic.md`.
