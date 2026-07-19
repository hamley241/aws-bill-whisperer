"""
Guard test for the pattern-logging convention.

Detection patterns are *library* code. Per CLAUDE.md architectural
principle 3 ("Output surfaces are presenters, not logic owners"), a
pattern must not own an output surface: rendering belongs to presenters,
not to detection logic. A pattern that calls ``print()`` has quietly taken
over a presenter's job — its output is hard-wired to stdout, so it cannot
be routed, filtered, captured by a log aggregator, or attached to a stack
trace. In a Slack app or a Lambda, that output goes nowhere anyone reads,
and a permissions denial becomes indistinguishable from a transient
throttle.

This test enforces the migration to structured ``logging`` (the shape in
``p006_nat_gateway.py``) as an invariant over *every* module in
``src/patterns/``, discovered from the directory rather than a
hand-maintained list, so the class of bug is closed at its source and a
future pattern cannot silently reintroduce it. Approximate evidence (a
regex or text grep) is what lets drift persist, so both invariants are
derived from the parsed AST, not from string matching.
"""

import ast
from pathlib import Path

import pytest


PATTERNS_DIR = Path(__file__).resolve().parent.parent / "src" / "patterns"


def _pattern_module_paths() -> list[Path]:
    """Every ``*.py`` module under ``src/patterns/``, discovered from disk.

    Deliberately not a hand-maintained list: base.py, _template.py, and
    every pNNN module are included automatically so a newly-dropped
    pattern is governed the moment it lands.
    """
    return sorted(PATTERNS_DIR.glob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _print_call_lines(tree: ast.Module) -> list[int]:
    """Line numbers of every ``print(...)`` call in the module.

    Walks ``Call`` nodes whose function is a bare ``Name`` of ``print`` —
    AST, not a text search, so a ``print`` inside a comment or string
    literal is correctly ignored and one inside code is correctly caught.
    """
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            lines.append(node.lineno)
    return sorted(lines)


def _has_except_handler(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.ExceptHandler) for node in ast.walk(tree))


def _class_method_names(tree: ast.Module) -> set[str]:
    """Names of every method defined on a class in the module.

    Walks ``ClassDef`` bodies for ``FunctionDef`` / ``AsyncFunctionDef``
    nodes — AST, not a text search, so a ``def fix`` inside a comment or
    docstring is correctly ignored and a real method is correctly caught.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(item.name)
    return names


def _has_module_level_logger(tree: ast.Module) -> bool:
    """True iff the module assigns ``logger = logging.getLogger(__name__)``
    at module scope (top level of the module body).

    The shape is checked exactly, not approximately: the value must be a
    call to ``logging.getLogger`` (``logging`` as a bare ``Name``, attribute
    ``getLogger``) whose sole argument is ``__name__``. A looser check
    (any ``.getLogger(...)`` call, any argument) would let a module pass
    this guard while violating the convention — e.g. a hard-coded logger
    name or one derived from something other than ``__name__``, which
    can't be routed or filtered by module the way the convention intends.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets_logger = any(
            isinstance(t, ast.Name) and t.id == "logger" for t in node.targets
        )
        if not targets_logger:
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "logging"
            and value.func.attr == "getLogger"
            and len(value.args) == 1
            and not value.keywords
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "__name__"
        ):
            return True
    return False


def test_no_pattern_module_calls_print():
    """No pattern module may call ``print()``.

    Patterns are library code; per CLAUDE.md principle 3 output belongs to
    presenters, not to detection logic. A pattern that prints has taken
    over a presenter's job and its output cannot be routed, filtered, or
    captured. Failures use structured ``logging`` instead (see p006).
    """
    offenders = {}
    for path in _pattern_module_paths():
        lines = _print_call_lines(_parse(path))
        if lines:
            offenders[path.name] = lines

    assert not offenders, (
        "print() found in pattern module(s) — patterns are library code and, "
        "per CLAUDE.md principle 3, output belongs to presenters, not detection "
        "logic. A pattern that prints has hard-wired its output to stdout, so it "
        "cannot be routed, filtered, or captured. Use logging (logger.exception "
        "for failures, logger.info for status) as p006_nat_gateway.py does. "
        "Offenders (module -> line numbers): "
        + ", ".join(f"{name}:{lns}" for name, lns in sorted(offenders.items()))
    )


def test_modules_handling_exceptions_define_a_module_logger():
    """Any module with an ``except`` handler must define a module-level
    ``logger = logging.getLogger(__name__)``.

    This keeps the migration from half-happening: an exception handler with
    no logger to call would either fall back to print() or swallow the
    failure silently.
    """
    offenders = []
    for path in _pattern_module_paths():
        tree = _parse(path)
        if _has_except_handler(tree) and not _has_module_level_logger(tree):
            offenders.append(path.name)

    assert not offenders, (
        "pattern module(s) handle exceptions but define no module-level "
        "`logger = logging.getLogger(__name__)`, so a failure path has no "
        "structured logger to call (see p006_nat_gateway.py). Offenders: "
        + ", ".join(sorted(offenders))
    )


TEMPLATE_PATH = PATTERNS_DIR / "_template.py"

# The template is the file new patterns are COPIED FROM, so it must teach the
# live remediation contract — remediate(finding, mode) -> RemediationResult —
# and never a hook the framework doesn't call. It was twice left behind by a
# migration (print() after 19 patterns moved to logging; fix() after the
# interface moved to remediate()); a new pattern copied from a stale template
# implements a dead hook whose remediation silently never runs. This guard is
# narrow and mechanical on purpose: it is "the copy source must teach the live
# contract", not a general dead-method detector.
REMOVED_REMEDIATION_HOOK = "fix"
LIVE_REMEDIATION_HOOK = "remediate"


def test_template_does_not_define_removed_fix_hook():
    """``_template.py`` must not define ``fix``.

    ``fix(finding, dry_run)`` was the pre-migration remediation hook; the
    live contract on ``BasePattern`` is ``remediate(finding, mode)``. No
    pattern in ``src/patterns/`` defines ``fix``, so a template that teaches
    it would seed a dead method whose remediation never runs.
    """
    methods = _class_method_names(_parse(TEMPLATE_PATH))
    assert REMOVED_REMEDIATION_HOOK not in methods, (
        "_template.py defines fix() — the removed pre-migration remediation "
        "hook. The template is copied to create new patterns, so it must "
        "teach the live contract remediate(finding, mode) -> RemediationResult "
        "(see p001_unattached_ebs.py), never a hook the framework doesn't call."
    )


def test_template_remediation_hook_is_remediate():
    """If ``_template.py`` defines a remediation hook at all, it must be
    ``remediate`` — the live entry point on ``BasePattern``.

    Guards the same class of miss from the other direction: the copy source
    can't teach some other remediation-shaped method name either.
    """
    methods = _class_method_names(_parse(TEMPLATE_PATH))
    assert LIVE_REMEDIATION_HOOK in methods, (
        "_template.py defines no remediate() method. The template is the "
        "copy source for new patterns and must teach the live remediation "
        "contract remediate(finding, mode) -> RemediationResult (see "
        "p001_unattached_ebs.py)."
    )


# ---------------------------------------------------------------------------
# Meta-tests: prove the guard has teeth — it must actually fire when a
# print() is reintroduced or a logger is missing. These parse synthetic
# source strings; they touch no real module.
# ---------------------------------------------------------------------------
def test_print_detection_fires_on_reintroduced_print():
    src = "def scan():\n    print('regressed')\n"
    assert _print_call_lines(ast.parse(src)) == [2]


def test_print_detection_ignores_print_in_strings_and_comments():
    src = "x = 'print(1)'  # print(2)\n"
    assert _print_call_lines(ast.parse(src)) == []


def test_logger_check_fires_when_except_handler_has_no_logger():
    src = "try:\n    pass\nexcept Exception:\n    pass\n"
    tree = ast.parse(src)
    assert _has_except_handler(tree)
    assert not _has_module_level_logger(tree)


def test_logger_check_passes_with_module_logger():
    src = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n    pass\nexcept Exception:\n    logger.exception('x')\n"
    )
    tree = ast.parse(src)
    assert _has_module_level_logger(tree)


@pytest.mark.parametrize(
    "assignment",
    [
        "logger = logging.getLogger('hard-coded-name')",   # not __name__
        "logger = logging.getLogger()",                     # no argument
        "logger = logging.getLogger(__name__, extra=1)",    # extra kwarg
        "logger = other.getLogger(__name__)",               # not logging.*
        "logger = get_logger(__name__)",                    # bare call, no attribute
        "logger = structlog.get_logger(__name__)",          # wrong attr name
    ],
)
def test_logger_check_rejects_non_conforming_shapes(assignment):
    """The guard must require the exact ``logging.getLogger(__name__)``
    shape — a looser assignment to ``logger`` must not satisfy it, or a
    module could violate the convention while passing the test."""
    tree = ast.parse("import logging\n" + assignment + "\n")
    assert not _has_module_level_logger(tree)


def test_method_name_detection_catches_fix_and_remediate():
    src = (
        "class P:\n"
        "    def scan(self):\n        pass\n"
        "    def remediate(self, finding, mode):\n        pass\n"
    )
    methods = _class_method_names(ast.parse(src))
    assert "remediate" in methods
    assert "fix" not in methods


def test_method_name_detection_fires_on_reintroduced_fix():
    src = "class P:\n    def fix(self, finding, dry_run=True):\n        return True\n"
    assert "fix" in _class_method_names(ast.parse(src))


def test_method_name_detection_ignores_fix_in_strings_and_comments():
    src = "class P:\n    x = 'def fix(self): ...'  # def fix\n    y = 1\n"
    assert "fix" not in _class_method_names(ast.parse(src))


# ---------------------------------------------------------------------------
# Structured-logging convention.
#
# One convention beats two: every ``logger.exception(...)`` / ``logger.info(...)``
# call in ``src/patterns/`` must pass ``extra={...}`` — a dict literal carrying
# at least ``pattern_id`` (see p004_idle_ec2.py for the reference shape, incl.
# ``region``/``outcome``/``exception_type``/``exception_message``). The system
# runs on records that MACHINES read: structured fields are greppable,
# parseable, and correlatable across a scan; positional %-args force re-parsing
# prose to recover the same facts and never fully do.
#
# Derived from the parsed AST, not string matching, so a ``logger.info`` inside
# a comment or string literal is correctly ignored and one in code is caught.
# ---------------------------------------------------------------------------

STRUCTURED_LOG_METHODS = ("exception", "info")
REQUIRED_EXTRA_KEY = "pattern_id"


def _logger_method_calls(tree: ast.Module) -> list[ast.Call]:
    """Every ``logger.<m>(...)`` call whose ``<m>`` is in
    ``STRUCTURED_LOG_METHODS`` — an attribute call on a bare ``logger`` Name."""
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in STRUCTURED_LOG_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            calls.append(node)
    return calls


def _structured_logging_offenders(tree: ast.Module) -> list[tuple[int, str]]:
    """``(lineno, reason)`` for each logger.exception/info call that does not
    pass ``extra={...}`` (a dict literal) containing a literal ``pattern_id``
    key. Empty list means the module conforms.

    The check is exact, not approximate: ``extra`` must be a ``Dict`` literal
    (so the keys are inspectable) and must contain the constant string key
    ``pattern_id``. A non-literal ``extra`` (e.g. ``extra=some_var``) can't be
    verified to carry ``pattern_id`` and so does not satisfy the convention.
    """
    offenders = []
    for call in _logger_method_calls(tree):
        extra_kw = next((k for k in call.keywords if k.arg == "extra"), None)
        if extra_kw is None or not isinstance(extra_kw.value, ast.Dict):
            offenders.append((call.lineno, "no extra={...} dict literal"))
            continue
        literal_keys = {
            k.value
            for k in extra_kw.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if REQUIRED_EXTRA_KEY not in literal_keys:
            offenders.append(
                (call.lineno, f"extra={{...}} missing {REQUIRED_EXTRA_KEY!r} key")
            )
    return sorted(offenders)


def test_pattern_logger_calls_carry_structured_extra_with_pattern_id():
    """Every ``logger.exception``/``logger.info`` in ``src/patterns/`` must
    pass ``extra={...}`` with at least a literal ``pattern_id`` key.

    Fails LOUDLY with file + line for any offender — including a call site that
    legitimately cannot supply ``pattern_id`` (e.g. module-level code outside a
    class). That is deliberately not skipped and not exempted: a human rules on
    it by fixing the call site or the guard, never by adding it to an
    allow-list.
    """
    offenders = {}
    for path in _pattern_module_paths():
        found = _structured_logging_offenders(_parse(path))
        if found:
            offenders[path.name] = found

    assert not offenders, (
        "logger.exception/info call(s) in src/patterns/ do not carry "
        "extra={...} with a literal `pattern_id` key — the structured logging "
        "convention (see p004_idle_ec2.py). Records here are read by machines: "
        'extra={"pattern_id", "region", "outcome", ...} supplements the human '
        "message, it does not replace it. If a call site genuinely cannot "
        "supply pattern_id, that is for a human to rule on — fix the call or "
        "the guard, never add an exemption. "
        "Offenders (module -> [(line, reason), ...]): "
        + "; ".join(f"{name} -> {lst}" for name, lst in sorted(offenders.items()))
    )


# ---------------------------------------------------------------------------
# Meta-tests: prove the structured-logging guard has teeth — it must fire when
# extra= is dropped, when pattern_id is missing, and when extra is not a dict
# literal, and must pass a conforming call. Synthetic source only.
# ---------------------------------------------------------------------------
def test_structured_guard_fires_when_extra_missing():
    src = "logger.exception('p0NN error scanning region %s', region)\n"
    assert _structured_logging_offenders(ast.parse(src)) == [
        (1, "no extra={...} dict literal")
    ]


def test_structured_guard_fires_when_pattern_id_missing():
    src = "logger.info('ok', extra={'region': region, 'outcome': 'ok'})\n"
    assert _structured_logging_offenders(ast.parse(src)) == [
        (1, "extra={...} missing 'pattern_id' key")
    ]


def test_structured_guard_fires_when_extra_is_not_a_dict_literal():
    src = "logger.info('ok', extra=some_precomputed_dict)\n"
    assert _structured_logging_offenders(ast.parse(src)) == [
        (1, "no extra={...} dict literal")
    ]


def test_structured_guard_passes_conforming_call():
    src = (
        "logger.exception('boom %s', region, extra={'pattern_id': "
        "self.PATTERN_ID, 'region': region, 'outcome': 'failed'})\n"
    )
    assert _structured_logging_offenders(ast.parse(src)) == []


def test_structured_guard_ignores_non_logger_and_other_methods():
    # Only logger.exception/info are governed; a debug call or a non-logger
    # object is out of scope and must not be flagged.
    src = (
        "logger.debug('noise')\n"
        "other.info('not the module logger')\n"
    )
    assert _structured_logging_offenders(ast.parse(src)) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
