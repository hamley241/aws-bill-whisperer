# `_legacy/` — pre-clean-architecture code, retained for reference

Nothing in this directory is imported by the active codebase. It's
kept in-repo so the original direction is auditable and so individual
pieces can be lifted (with rework) if a later PR wants to.

## What's here

- **`strandsagents/`** — the original Strands-Agents-SDK skeleton:
  category-specialist agents (`compute_agent`, `storage_agent`,
  `monitoring_agent`), an orchestrator that fanned out to them, a
  FastAPI + WebSocket web chat, a CLI chat front-end, and a
  keyword-based intent router.

  Retired because:

  1. The multi-agent topology added complexity (inter-agent
     communication, conflicting recommendations, ad-hoc dict result
     formats) without commensurate benefit. One agent loop with a
     sharp tool list is easier to reason about, easier to evaluate,
     and produces more coherent plans.
  2. The agents returned ad-hoc dicts rather than the canonical
     `Finding` objects every other surface speaks (principle 2). The
     gap kept widening.
  3. The new direction (see `CLAUDE.md`: "agentic FinOps, deterministic
     framework underneath") makes a single planner with replayable
     traces a better fit than a multi-agent dance.

## Why keep it

- The chat UX shapes (slash commands, threaded responses, command
  buttons) influenced the Slack handler design.
- The agent prompts and the orchestrator's fanout-and-merge logic are
  useful priors when the new agent loop is built.
- Removing it entirely would erase the "we tried that and learned" trail.

## Do not import from here

If a fresh PR needs something from `_legacy/`, copy it into the active
tree and rework it against the clean modules (`patterns`, `llm`,
`storage`, `presenters`, `audit`). Do not add `_legacy` to any import
path. Do not refactor in place.
