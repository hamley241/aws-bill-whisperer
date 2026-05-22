# p001_p004_preview

This is a **preview** fixture for cross-pattern planning. It proves the
planner can consume a mixed set of findings (one p001 storage, one p004
compute) without schema or ranking breakage. The recorded response
ranks both findings with mode choices that respect each pattern's
resolver gating: `pr` for the terraform-managed p001 volume, `dry_run`
for the prod-tagged p004 instance whose `not_prod` gate fails.

## What this fixture is NOT

It is **not** a cross-pattern eval rubric. The assertions in
`assertions.yaml` are deliberately minimal:

- structural validity
- both findings ranked (`steps_count == 2`)
- no drops
- `order_rank_unique`
- per-pattern mode constraints (`pr` never used for p004; invasive
  modes never used for unsafe p004 findings)

The dedicated **cross-pattern eval PR** that follows p004 will add the
real cross-pattern semantics: ranking by risk-adjusted impact, savings
campaigns, prioritization across pattern categories. Those assertions
need their own design pass and don't belong here.

## Why it exists in this PR

Without this fixture, the cross-pattern eval PR would have nothing to
build on. Shipping the preview now means:

1. The planner is exercised on a heterogeneous finding set in this
   PR's eval run — any regression in mode resolution or validator
   wiring across patterns shows up immediately, not in the next PR.
2. The cross-pattern eval PR can extend `assertions.yaml` here rather
   than re-inventing the fixture from scratch.

## Carry-forward note

When the cross-pattern eval rubric lands, the assertions in this file
will grow. The recorded response may need to be re-recorded against
the live LLM (with `WHISPER_ALLOW_REAL_LLM=1` and `--re-record`) to
include richer rationales that the new rubric can audit.
