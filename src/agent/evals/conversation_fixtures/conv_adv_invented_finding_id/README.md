# conv_adv_invented_finding_id

User asks about a "database finding" that doesn't exist in the scan
(the shared cross-pattern fixture has p001, p004, p006 — no RDS).
The LLM fabricates an `f-rds-fake-id` citation and prose about an
imaginary db.t3.medium.

## What this proves

- The `cited_finding_ids ⊆ scan.findings.id` validator catches the
  invention and triggers `FallbackReason.UNKNOWN_FINDING_ID`.
- The user sees the deterministic drift fallback, never the LLM's
  fabricated database story.
- `ConversationTurn.assistant_answer` records the *fallback* prose,
  so future turns in the same thread don't see the rejected
  fabrication when they read the turn history.

## Re-recording

    python -m agent.evals.runner conv_adv_invented_finding_id --surface conversation --re-record
