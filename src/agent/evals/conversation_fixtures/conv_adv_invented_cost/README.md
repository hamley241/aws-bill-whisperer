# conv_adv_invented_cost

User asks for an annual projection. The LLM writes `$1658.88/yr`
inline (a derived figure based on the canonical monthly value times
twelve).

## What this proves

- The arithmetic-phrasing heuristic ("/yr", "per year") routes the
  drop to `FallbackReason.SYNTHESIZED_COST` even though the LLM
  performed real arithmetic on a real canonical value. Under
  regex-strict-rules, ANY arithmetic phrasing alongside an inline
  `$` is a hard drop — the planner is the only source of derived
  totals.
- The eval rubric can audit raw invention vs derivation separately
  via `INVENTED_COST` and `SYNTHESIZED_COST`.
- The user sees the deterministic synthesized-cost fallback that
  steers them back to `/whisper plan` for the totals they want, not
  the LLM's annualised number.

## Why this is a separate fixture from `conv_adv_synthesized_cost`

Sign-off requested the SYNTHESIZED_COST drop reason alongside
INVENTED_COST so a future analysis can quantify how often the LLM
does arithmetic vs how often it just hallucinates a figure. This
fixture exercises the annualisation path (one canonical value × 12);
`conv_adv_synthesized_cost` exercises the sum path (two canonical
values added together).

## Re-recording

    python -m agent.evals.runner conv_adv_invented_cost --surface conversation --re-record
