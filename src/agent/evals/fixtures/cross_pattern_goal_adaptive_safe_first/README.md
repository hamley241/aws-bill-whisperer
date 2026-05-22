# cross_pattern_goal_adaptive_safe_first

The goal-adaptive companion to `cross_pattern_rank_headline`. Same
three patterns (`p001` / `p004` / `p006`), same dollar values, but a
different goal — and a different `p004` safety profile to make the
goal substantively engage.

## Pairing with the headline fixture

| | `cross_pattern_rank_headline` | this fixture |
|---|---|---|
| Goal | Maximize savings; rank by dollar impact | Avoid Env=prod this week; prefer reversible |
| `p004` safety | All gates pass (`safe_to_fix=true`) | `Env=prod` tag → `not_prod` fails (`safe_to_fix=false`) |
| Plan size | 3 steps | 2 steps |
| `p004` outcome | Ranked #1, `command` mode | Omitted entirely |

Read both fixtures' `recorded_response.json` side-by-side to see what
goal interpretation looks like in practice. The structural difference
between the two recorded plans is the proof — under replay, the
"adaptation" claim reduces to "the hand-crafted recording for this
goal encodes goal-driven behaviour, and the rubric pins that
behaviour."

## Why p004's safety profile changed too

We could have re-used the headline's `p004` (all-gates-pass,
`safe_to_fix=true`) and asked the LLM to skip it purely on the
production-tag goal. But the project's `_p004_modes` resolver gates on
`safe_to_fix`, not on tags — so an all-gates-pass `p004` finding here
would still expose `command` and `api_call` to the LLM, weakening the
"skipped because production" claim. Making `p004` `Env=prod` here is
the realistic shape that the operator would actually see, and it
exercises both axes of adaptation (evidence + goal) the way production
data would.

## How "p004 was actually skipped" is asserted

Without an `excludes_finding` vocabulary, omission is proved by:

1. `steps_count: equals: 2` plus `dropped_steps_count: equals: 0`
   bounds the surfaced plan to two steps.
2. `includes_finding: terraform_managed: true` pins one of those two
   steps to `p001`.
3. Four `never_recommends_mode` + `for_pattern_id: "004"` assertions
   (one per mode, including `dry_run`) ensure no `p004` step in any
   mode survives. Banning `dry_run` here is a per-fixture, goal-driven
   constraint, not a general rule — `dry_run` for unsafe `p004` is
   the *normal* surface in other fixtures.

The only legal kept set under these rules is `{p001, p006}`.

## Re-recording

Replay is the default. To re-record against a live LLM, set
`WHISPER_ALLOW_REAL_LLM=1` and run
`python -m agent.evals.runner cross_pattern_goal_adaptive_safe_first --re-record`.

If re-recording produces a plan that surfaces `p004` (in any mode),
that is a candidate signal that the live LLM is not interpreting the
goal — surface as a v3 prompt candidate rather than relaxing this
fixture's rubric.
