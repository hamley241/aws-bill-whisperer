# cross_pattern_rank_headline

The headline "agent ranks across categories" fixture. Three findings,
one each from `p001` (storage), `p004` (compute), and `p006` (network).
All findings are data-valid and the recorded plan uses modes within
each pattern's resolver-exposed set.

## What it proves

- Planner consumes mixed-category findings without schema breakage or
  validator drops.
- Mode selection respects pattern-specific resolvers in cross-pattern
  context: `p001` → `pr` (terraform-managed), `p004` → `command` (all
  safety gates pass), `p006` → `dry_run` (Flow Logs absent).
- `total_impact_within_input_sum` holds across categories — the
  load-bearing aggregate-bound check.
- The ranked order (`p004` ranked #1, `p001` #2, `p006` #3) reflects
  the dollar-maximization goal.

## Paired with

- `cross_pattern_goal_adaptive_safe_first` uses the same patterns but a
  different goal and a different `p004` safety profile (production-
  tagged) to demonstrate goal-driven selection. Reading both fixtures
  together is the easiest way to see what the goal field is actually
  doing.
- `cross_pattern_adversarial` covers the validator-drop path under the
  same three patterns.

## Re-recording

Replay is the default. To re-record against a live LLM, set
`WHISPER_ALLOW_REAL_LLM=1` and run
`python -m agent.evals.runner cross_pattern_rank_headline --re-record`.
