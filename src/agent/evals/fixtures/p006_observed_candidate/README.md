# p006_observed_candidate

This fixture exercises the observed-candidate-with-savings code path:
the NAT finding carries an `observed` evidence block (Flow Logs
present) and a candidate with `evidence_tier="observed"` and a non-zero
`est_monthly_savings_usd`. The recorded LLM response cites that
candidate, and the planner validators canonicalise the savings against
the candidate.

**Production p006 under `hourly_only` does not emit non-zero candidate
savings.** This fixture represents *future state* — what the planner
should do once Flow Logs ingestion and NAT processed-byte cost math
land in a follow-up PR. The invariant is enforced by
`tests/test_p006_nat_gateway.py::TestNoFabricatedSavings`.

The fixture exists in this PR so that the validators, prompt, and
rubric for observed-tier sub-actions ship together with the inferred
path. Removing this fixture would leave the observed-tier validator
branch untested until the follow-up.
