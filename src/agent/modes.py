"""
AvailableModesResolver — what remediation modes can the planner actually
suggest for a given Finding?

The planner asks the resolver, renders the set into the prompt, and
validates LLM emissions against it. This is the "modes are not
negotiable" enforcement point — the LLM never invents an unsupported
mode, and even if it tries, the validator drops it.

The spike scopes to p001 because that's the only fully bulletproof
pattern. p006 and p004 plug their resolver logic in here later; the
resolver itself stays a thin dispatch on `Finding.pattern_id`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from patterns.base import RemediationMode

if TYPE_CHECKING:
    from patterns.base import Finding


# Every Finding gets DRY_RUN and COMMAND from the BasePattern default —
# they're always safe (no side effect, no AWS call beyond what the scan
# already did).
_UNIVERSAL_MODES = frozenset({RemediationMode.DRY_RUN, RemediationMode.COMMAND})


def _p001_modes(finding: "Finding") -> set[RemediationMode]:
    """p001 unattached EBS:

      - PR mode only when the volume is terraform-managed (matches
        UnattachedEBSPattern._remediate_pr).
      - API_CALL only when safe_to_fix=True (snapshot exists + age gates).
    """
    modes = set(_UNIVERSAL_MODES)
    if finding.evidence.get("terraform_managed"):
        modes.add(RemediationMode.PR)
    if finding.safe_to_fix:
        modes.add(RemediationMode.API_CALL)
    return modes


def _p006_modes(_finding: "Finding") -> set[RemediationMode]:
    """p006 NAT Gateway optimization:

      - DRY_RUN and COMMAND are always available (universal).
      - PR is deferred (Terraform diff for VPC endpoints + route-table
        associations is materially more complex than p001's).
      - API_CALL is forbidden in this milestone — route-table mutation
        or NAT removal is high-blast-radius and never auto-executable.

    No per-finding variation: candidate-tier gating happens inside the
    pattern's COMMAND handler, not in the resolver.

    TODO (post-Flow-Logs): once observed-tier candidates exist, consider
    moving the inferred-only "insufficient_evidence_for_command" check
    out of pattern.remediate() and into the resolver — i.e., drop
    COMMAND from the resolver's output when no observed candidate is
    present. That tightens the "validator drops emissions the LLM was
    never offered" guarantee.
    """
    return set(_UNIVERSAL_MODES)


# Pattern-specific resolvers. Patterns not in this map fall back to the
# universal-only set — they expose dry_run/command but nothing else,
# which matches the BasePattern default behaviour.
_RESOLVERS: dict[str, Callable[["Finding"], set[RemediationMode]]] = {
    "001": _p001_modes,
    "006": _p006_modes,
}


class AvailableModesResolver:
    """Maps Finding → available remediation modes.

    Inject a custom resolver dict in tests; the production singleton is
    the module-level `_RESOLVERS` map.
    """

    def __init__(self,
                 resolvers: dict[str, Callable[["Finding"], set[RemediationMode]]] | None = None):
        self._resolvers = resolvers if resolvers is not None else _RESOLVERS

    def resolve(self, finding: "Finding") -> set[RemediationMode]:
        impl = self._resolvers.get(finding.pattern_id)
        if impl is None:
            return set(_UNIVERSAL_MODES)
        return impl(finding)

    def resolve_values(self, finding: "Finding") -> set[str]:
        """String values (the form the LLM emits and the validators check)."""
        return {m.value for m in self.resolve(finding)}
