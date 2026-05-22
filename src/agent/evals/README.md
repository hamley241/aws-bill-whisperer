# SavingsPlanner eval harness

This directory holds the record/replay eval harness that exercises
`SavingsPlanner` against fixed LLM responses. Each fixture is a single
end-to-end scenario: `findings.json` (planner input) plus a recorded
LLM response plus an `assertions.yaml` rubric the runner applies to the
resulting `PlanResult`.

The harness is the safety net for everything CLAUDE.md calls "LLM
proposes; framework disposes." When the validator drops a bad emission
or the parser refuses a malformed response, an adversarial fixture is
how we prove it.

## Running

```bash
# Replay everything (default; no network).
python -m agent.evals.runner

# Replay one fixture.
python -m agent.evals.runner p001_only

# Re-record a fixture against the real LLM. Double-locked: needs both
# the flag AND the env var, so CI cannot accidentally call the live API.
WHISPER_ALLOW_REAL_LLM=1 python -m agent.evals.runner p001_only --re-record
```

A multi-fixture run prints a `=== suite summary ===` block at the end
with pass/fail counts and the `parse_retry_average` metric.

## Fixtures

| scenario | kind | what it proves |
| --- | --- | --- |
| `p001_only` | happy path | the planner promotes a clean response into a `status="ok"` plan with zero drops. |
| `adversarial_unknown_finding_id` | adversarial | validator drops emissions whose `finding_id` is not in the input set. |
| `adversarial_unsupported_mode` | adversarial | validator drops modes the resolver does not expose for the finding. |
| `adversarial_monthly_impact_missing` | adversarial | validator drops `monthly_impact_usd: null` or non-numeric emissions. |
| `adversarial_monthly_impact_mismatch` | adversarial | validator drops dollar values that disagree with the canonical Finding beyond `$0.01`. |
| `adversarial_schema_invalid` | adversarial | validator drops emissions missing required keys (here, `rationale`). |
| `all_steps_dropped` | adversarial end-to-end | three broken emissions in one response, none survive; `status="validation_failed"`. |
| `mixed_valid_and_adversarial` | adversarial mixed | one valid + two broken in one response; the valid step survives, both drops are recorded. |

Each adversarial fixture's `recorded_response.json` metadata block
includes `kind: adversarial` and `drop_reason_under_test` so the intent
is discoverable from the fixture alone.

### Known taxonomy quirk — `monthly_impact_missing` vs `schema_invalid`

The validator's shape check fires before the explicit
`monthly_impact_usd` check, so:

- LLM emits `"monthly_impact_usd": null` → `MONTHLY_IMPACT_MISSING` ✓
- LLM emits `"monthly_impact_usd": "eighty"` → `MONTHLY_IMPACT_MISSING` ✓
- LLM **omits the key entirely** → `SCHEMA_INVALID` ✗

In production, LLMs are more likely to omit the key than emit explicit
`null`, so real "missing dollar" events get bucketed as
`SCHEMA_INVALID`. The `adversarial_monthly_impact_missing` fixture
emits `null` to hit the intended reason; this should be migrated to
omitted-key once the validator is fixed. Tracked in
[issue #3](https://github.com/hamley241/aws-bill-whisperer/issues/3).

## Rubric vocabulary

See `rubric.py` for the closed set of assertion types. Most relevant for
adversarial fixtures:

- `dropped_reason_present: <reason>` — assert one specific drop reason
  fired (singular).
- `dropped_step_reasons: [<reason>, ...]` — assert the *set* of drop
  reasons (set equality) matches an expected list. Use this when more
  than one emission was dropped and you want to pin down every reason.

## `parse_retry_count` — answered for PR 3

The suite summary reports `parse_retry_average` (the mean
`PlanResult.parse_retry_count` across non-errored fixtures). This is
the planner's signal for whether to invest in
`LLMClient.response_format="json"` work — if the LLM frequently emits
unparseable text, the repair retry is masking a structural problem.

**Decision rule** (the threshold itself lives in
[`PARSE_RETRY_THRESHOLD`](runner.py) — this README never hardcodes it):

- If `parse_retry_average > PARSE_RETRY_THRESHOLD`, open a follow-up
  issue documenting the rate and recommending `LLMClient`
  `response_format="json"` work.
- If `parse_retry_average ≤ PARSE_RETRY_THRESHOLD`, document the rate
  here and close the question.

**Current suite (PR 3, 8 fixtures):**
- `parse_retry_total`: 0
- `parse_retry_average`: 0.0000
- **Resolution:** the measured rate is at or below
  `PARSE_RETRY_THRESHOLD` — no follow-up issue today. The planner's
  repair retry is fully unit-tested in
  `tests/test_planner.py::TestParseRetry` (covers first-try success,
  retry success, and both-attempts-failed paths); we do not need a
  fixture-level stress test for the retry machinery itself.

**When this answer should be revisited:**
- Real (live-LLM) recordings replace the hand-crafted ones for any
  fixture. The current `parse_retry_count` reflects fixture *design*,
  not LLM *behavior*. As recorded responses become live samples, the
  metric becomes meaningful.
- A new fixture is added with deliberately-unparseable first turn.
  These are useful as machinery tests but should be excluded from the
  steady-state rate (annotate the recording's `metadata.kind`
  accordingly when this comes up).
- The suite summary surfaces a `! parse_retry rate exceeds threshold`
  warning. The threshold is `PARSE_RETRY_THRESHOLD` in
  [`runner.py`](runner.py) — change it there, nowhere else.

## Adding a new fixture

1. Create `src/agent/evals/fixtures/<scenario>/`.
2. Add `findings.json` (a list of Finding dicts in the canonical
   schema, same shape as `Finding.to_dict()` with `risk_tier` as a
   string).
3. Add a `goal` text file (one line, plain English).
4. Add a `recorded_response.json` envelope:
   ```json
   {
     "responses": ["<the LLM response text>"],
     "metadata": {
       "model": "...",
       "provider": "...",
       "kind": "happy" | "adversarial",
       "drop_reason_under_test": "<DropReason value, if adversarial>",
       "note": "..."
     }
   }
   ```
   For multi-turn responses (parse retry), `responses` has one entry per
   turn.
5. Add `assertions.yaml` with the rubric.
6. Run the runner — `python -m agent.evals.runner <scenario>`.

For adversarial fixtures, prefer the helper at
`scripts/build_adversarial_fixtures.py` — JSON-inside-JSON escaping is
mechanical and the script makes the regeneration deterministic.
