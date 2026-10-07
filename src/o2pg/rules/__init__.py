"""Rule registry. Importing this package registers every built-in rule."""

from __future__ import annotations

from o2pg.rules import (  # noqa: F401  (imported for their registration side effect)
    azure,
    datatypes,
    objects,
    partitioning,
    performance,
    plsql,
    security,
    sql_dialect,
)
from o2pg.rules.base import (
    AZURE_ALLOWED_EXTENSIONS,
    Category,
    Finding,
    Hit,
    Rule,
    Severity,
    registered_rules,
)


def all_rules() -> list[Rule]:
    """Every built-in rule, sorted by id."""
    return registered_rules()


def get_rule(rule_id: str) -> Rule:
    """Look up a rule by id (case-insensitive). Raises KeyError if unknown."""
    wanted = rule_id.upper()
    for candidate in registered_rules():
        if candidate.id == wanted:
            return candidate
    raise KeyError(rule_id)


__all__ = [
    "AZURE_ALLOWED_EXTENSIONS",
    "Category",
    "Finding",
    "Hit",
    "Rule",
    "Severity",
    "all_rules",
    "get_rule",
]
