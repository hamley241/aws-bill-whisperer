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

# THE RULE: this gate answers from the PROJECT environment, or it does not
# answer at all. A wrong answer is worse than no answer, because an automated
# loop treats green as permission to proceed.
#
# There is deliberately NO implicit fallback to whatever `python3` is on PATH.
# That fallback already diverges on this machine (.venv has pytest; the system
# python3 does not) and elsewhere could resolve to a different pytest, plugin
# set, or dependency versions — returning a pass or fail that says nothing
# about this project.
#
# WHISPER_GATE_PY is the explicit opt-out for an operator who has chosen an
# interpreter deliberately. It is never inferred, and when set the interpreter
# is echoed so the transcript records which environment answered.
if [ -n "${WHISPER_GATE_PY:-}" ]; then
    PY="$WHISPER_GATE_PY"
    echo "verify.sh: using WHISPER_GATE_PY=$PY" >&2
else
    PY=".venv/bin/python"
    if [ ! -x "$PY" ]; then
        echo "verify.sh: $PY not found or not executable." >&2
        echo "  Refusing to fall back to a system python: a result from an" >&2
        echo "  environment nobody chose is not a result." >&2
        echo "  Create the project environment:" >&2
        echo "    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
        echo "  Or set WHISPER_GATE_PY to an interpreter you have chosen." >&2
        exit 1
    fi
fi

# -q keeps a passing run terse (580+ dots, not 580+ lines); -ra adds a short
# summary naming each failure, which is what the loop surfaces on escalation.
exec "$PY" -m pytest -q -ra
