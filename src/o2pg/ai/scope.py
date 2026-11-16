"""Which review points a model may attempt, and which need a person.

Review points fall into two groups:

* **Code rewrites.** The fix is local to the routine and has a known
  PostgreSQL form: ROWNUM to LIMIT, CONNECT BY to WITH RECURSIVE, ``(+)`` to
  LEFT JOIN, BULK COLLECT/FORALL to set-based SQL, Oracle NULL semantics for
  ``''`` and ``||``, integer division, negative SUBSTR, and so on. A model can
  propose these, and the gates can check the proposal.
* **Design decisions.** The fix changes architecture or the contract with
  other code: autonomous transactions, package state, Oracle-supplied packages
  (UTL_FILE, DBMS_AQ, UTL_MAIL), transaction control inside functions,
  flashback, unconverted object kinds. ``ai-convert`` does not send units with
  any of these to a model, because a plausible-looking answer (a fake
  "autonomous" insert, a session variable pretending to be package state) is
  worse than an honest blocker.

Some code rewrites necessarily change the routine's signature (a pipelined
function becomes ``RETURNS TABLE``; a function with OUT parameters becomes a
procedure). Those rules are listed in ``CONTRACT_RULES``; a signature change is
only accepted when one of them is present, and it is always reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from o2pg.convert import ConvertedUnit
from o2pg.convert.engine import KIND_NOT_CONVERTED, KIND_STATE

DESIGN_RULES: dict[str, str] = {
    "O2PG-RV-001": "object kind is not converted",
    "O2PG-RV-002": "autonomous transaction",
    "O2PG-RV-011": "package state",
    "O2PG-RV-014": "Oracle-supplied package (file, queue, mail, scheduler ...)",
    "O2PG-RV-016": "transaction control inside a function or trigger",
    "O2PG-RV-020": "package initialisation section",
    "O2PG-RV-027": "flashback query",
    "O2PG-RV-029": "unsupported routine or trigger form",
}

CONTRACT_RULES: frozenset[str] = frozenset(
    {
        "O2PG-RV-008",  # PIPELINED -> RETURNS TABLE / SETOF
        "O2PG-RV-009",  # collection or record types
        "O2PG-RV-010",  # a type with no mapping, possibly in the header
        "O2PG-RV-024",  # function with OUT parameters
        "O2PG-RV-025",  # parameter default order
    }
)

SKIP_DESIGN = "needs a design decision"
SKIP_CLEAN = "no review points"
SKIP_KIND = "not a routine"


@dataclass(frozen=True)
class Scope:
    """Whether a unit goes to the model, and why not if it doesn't."""

    eligible: bool
    reason: str = ""
    rules: tuple[str, ...] = ()
    design: tuple[str, ...] = ()

    @property
    def contract_change_allowed(self) -> bool:
        """True when a review point here can legitimately change the signature."""
        return any(rule in CONTRACT_RULES for rule in self.rules)


def scope_for(unit: ConvertedUnit, include_review: bool = True) -> Scope:
    """Decide whether ``unit`` is sent to the model."""
    if unit.kind in {KIND_STATE, KIND_NOT_CONVERTED}:
        return Scope(False, SKIP_KIND)
    rules = tuple(dict.fromkeys(r.rule for r in unit.reviews))
    design = tuple(r for r in rules if r in DESIGN_RULES)
    if design:
        names = ", ".join(f"{r} ({DESIGN_RULES[r]})" for r in design)
        return Scope(False, f"{SKIP_DESIGN}: {names}", rules, design)
    if not rules:
        return Scope(False, SKIP_CLEAN, rules)
    if not include_review and not any(r.blocking for r in unit.reviews):
        return Scope(False, "only non-blocking review points (--blocking-only)", rules)
    return Scope(True, "", rules)
