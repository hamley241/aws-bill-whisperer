# conv_happy_dev_only

User asks "can I do this in dev first?" — a refinement that would
naturally invite the assistant to re-rank the plan with a new
constraint. The expected behaviour is the opposite: the conversation
layer redirects the user to `/whisper plan goal: dev only` (the only
sanctioned bridge from conversation to recommendation) and does NOT
re-rank or claim to have re-planned.

## What this proves

- The "conversation layer owns explanation; planner owns
  recommendations" constraint holds against the most tempting
  refinement question.
- The redirect to `/whisper plan` lands in the assistant's prose,
  not as a synthesised in-thread plan.

## Paired with

- `tests/test_plan_conversation.py::TestConversationLayerCannotInvokePlanner`
  — the import-level tripwire that prevents the conversation module
  from ever calling the planner at runtime.

## Re-recording

    python -m agent.evals.runner conv_happy_dev_only --surface conversation --re-record
