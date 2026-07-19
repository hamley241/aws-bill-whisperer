# conv_adv_synthesized_cost

User asks for a combined impact figure across two steps. The LLM
helpfully adds the two canonical step amounts (138.24 + 80.0 =
218.24) and synthesises a percentage (87%). Both numbers are
derivable but neither is canonical on its own — sign-off rules
treat arithmetic over canonical figures as a hard drop.

## What this proves

- The arithmetic heuristic in `_looks_synthesized` catches "together"
  + "%" + inline dollar patterns and routes to
  `FallbackReason.SYNTHESIZED_COST`.
- The user sees the deterministic synthesized-cost fallback rather
  than the LLM's calculation — the planner is the only place
  derived totals come from.

## Re-recording

    python -m agent.evals.runner conv_adv_synthesized_cost --surface conversation --re-record
