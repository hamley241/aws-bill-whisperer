"""
Guard tests for Pattern.SERVICES metadata.

SERVICES is documented in patterns/base.py as "AWS service codes queried"
and is surfaced verbatim by the `list-patterns` command (src/whisper.py),
so it is user-facing documentation of what a pattern touches. It is NOT the
permission list — REQUIRED_IAM is a separate field and owns the actual IAM
actions. These tests keep the documentation honest so it cannot drift.

Two invariants over every registered pattern, discovered through the same
registry the rest of the suite uses (never a hand-maintained list — that is
the same drift problem one level up):

1. Every SERVICES entry is a real boto3 client name.
2. SERVICES equals the set of boto3 clients the pattern's module actually
   constructs, derived by parsing the module source with `ast` (not regex).

Both run offline: get_available_services() reads bundled botocore data and
`ast` reads local source. Nothing here constructs a client or hits the network.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns import discover_patterns


PATTERNS = discover_patterns()

# Bundled botocore service catalog — no credentials, no network.
VALID_SERVICES = frozenset(boto3.Session().get_available_services())


def _constructed_clients(pattern_cls) -> tuple[list[str], list[str]]:
    """Parse a pattern's module source and return the boto3 client names it
    constructs.

    Returns (client_names, unresolved). A ``.client(<literal>)`` call yields a
    client name; a ``.client(<non-literal>)`` call — a name built from a
    variable, say — is unresolved and surfaced so a human decides, rather than
    being silently dropped.
    """
    source = Path(inspect.getsourcefile(pattern_cls)).read_text()
    tree = ast.parse(source)

    clients: list[str] = []
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "client"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            clients.append(node.args[0].value)
        else:
            unresolved.append(f"line {node.lineno}: {ast.unparse(node)}")

    return clients, unresolved


@pytest.mark.parametrize("pattern_cls", PATTERNS, ids=lambda c: c.PATTERN_ID)
def test_services_are_real_boto3_clients(pattern_cls):
    """Every declared SERVICES code is a real boto3 client name.

    Trivially true today; it guards the future — a typo'd or renamed service
    code would otherwise only surface when a user reads list-patterns output.
    """
    invalid = [s for s in pattern_cls.SERVICES if s not in VALID_SERVICES]
    assert not invalid, (
        f"Pattern {pattern_cls.PATTERN_ID} declares SERVICES that are not "
        f"boto3 client names: {invalid}"
    )


@pytest.mark.parametrize("pattern_cls", PATTERNS, ids=lambda c: c.PATTERN_ID)
def test_services_match_constructed_clients(pattern_cls):
    """SERVICES equals the set of clients the pattern's module constructs.

    Client names are derived with `ast`, not regex — approximate evidence is
    the kind of thing this project avoids. A client the parser cannot resolve
    statically fails loudly, naming the pattern and the call, rather than
    silently skipping.
    """
    constructed, unresolved = _constructed_clients(pattern_cls)

    assert not unresolved, (
        f"Pattern {pattern_cls.PATTERN_ID} constructs a boto3 client whose "
        f"name cannot be determined statically, so SERVICES cannot be "
        f"verified against it: {unresolved}. Resolve by using a string "
        f"literal, or document the exemption here with the reason."
    )

    declared = set(pattern_cls.SERVICES)
    used = set(constructed)
    assert declared == used, (
        f"Pattern {pattern_cls.PATTERN_ID} SERVICES {sorted(declared)} does "
        f"not match the clients its module constructs {sorted(used)}. "
        f"Missing from SERVICES: {sorted(used - declared)}; "
        f"declared but never constructed: {sorted(declared - used)}."
    )
