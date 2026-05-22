# cross_pattern_adversarial

The cross-pattern equivalent of the per-pattern adversarial fixtures
(`p001`-no-fixture-needed, `p004_adversarial_*`, `p006_adversarial_*`).

## What it proves

Per-step validator drops fire correctly when a single LLM response mixes
defective emissions across multiple patterns. Three drop reasons fire
in one plan; one valid `p001` step survives so `status=ok`.

| Step | Pattern | Defect | Expected drop |
|------|---------|--------|---------------|
| 1 | `p001` | (valid) | kept |
| 2 | `p004` | `monthly_impact_usd` inflated to $999 | `MONTHLY_IMPACT_MISMATCH` |
| 3 | `p004` | `api_call` against `safe_to_fix=false` (ASG member) | `UNSUPPORTED_MODE` |
| 4 | `p006` | sub-action claims $200 savings on inferred candidate (canonical 0) | `CANDIDATE_SAVINGS_MISMATCH` |

`total_impact_within_input_sum` is the belt-and-braces aggregate-bound
check; per-step canonicalisation is the primary defense.

## Why both p004 emissions can target the same finding without
## triggering DUPLICATE_FINDING_ID

`seen_finding_ids` only grows on accept (see
`validators.validate_steps`). Both p004 emissions are dropped, so the
set stays empty and the second p004 emission reaches its own distinct
validator stage (mode check) rather than failing duplicate.

## Re-recording

Replay is the default. To re-record against a live LLM, set
`WHISPER_ALLOW_REAL_LLM=1` and run
`python -m agent.evals.runner cross_pattern_adversarial --re-record`.
A real LLM is unlikely to emit this exact set of defects on demand;
this fixture is intentionally hand-crafted for the validator surface.
