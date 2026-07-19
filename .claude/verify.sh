#!/usr/bin/env bash
# aws-bill-whisperer's verification gate.
#
# This is the deterministic arbiter of correctness for automated change loops
# (e.g. potluck): it must pass before any change is reviewed, and again after
# fixes are applied.
#
# Scope note: this runs the TEST SUITE ONLY, deliberately. `ruff` and `mypy`
# are declared as dev dependencies but do not currently pass on the existing
# codebase (789 and 101 findings respectively as of this commit), so gating on
# them would fail every run before a single line was reviewed. A gate can only
# enforce what the project already holds true; tightening it is its own project.
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer the project venv; fall back to whatever python3 is on PATH.
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

exec "$PY" -m pytest -q
