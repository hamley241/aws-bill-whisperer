# conv_happy_what_about_nat

User asks "what about the NAT gateway in us-east-1?" — a narrow
clarifying question about a specific finding.

## What this proves

- A single-finding question produces a single cited id, not a
  blanket reference list.
- A single inline canonical dollar figure ($32.40/mo) passes the
  regex validator without triggering INVENTED_COST or
  SYNTHESIZED_COST.
- Sub-action context (the `observe_and_reassess` recommendation, the
  hourly_only / inferred evidence framing) flows from the plan into
  the answer without re-derivation.

## Re-recording

    python -m agent.evals.runner conv_happy_what_about_nat --surface conversation --re-record
