"""Rule model, registry and shared detection helpers."""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from o2pg.inventory import CodeUnit, Inventory
from o2pg.plsql import line_of


class Severity(str, enum.Enum):
    """How much a finding blocks or slows a migration."""

    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """0 for blocker up to 4 for info; lower is more severe."""
        return list(Severity).index(self)

    @property
    def weight(self) -> int:
        """Weight used by the complexity score."""
        return _SEVERITY_WEIGHTS[self]


_SEVERITY_WEIGHTS = {
    Severity.BLOCKER: 25,
    Severity.HIGH: 8,
    Severity.MEDIUM: 3,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class Category(str, enum.Enum):
    """Rule category. The value is what users see; the prefix appears in rule ids."""

    DATATYPES = "datatypes"
    PLSQL = "plsql"
    SQL_DIALECT = "sql-dialect"
    OBJECTS = "objects"
    PARTITIONING = "partitioning"
    SECURITY = "security"
    PERFORMANCE = "performance"
    AZURE = "azure-specific"

    @property
    def prefix(self) -> str:
        """Two-letter prefix used in rule ids, e.g. ``DT`` for datatypes."""
        return _CATEGORY_PREFIXES[self]


_CATEGORY_PREFIXES = {
    Category.DATATYPES: "DT",
    Category.PLSQL: "PL",
    Category.SQL_DIALECT: "SQ",
    Category.OBJECTS: "OB",
    Category.PARTITIONING: "PT",
    Category.SECURITY: "SE",
    Category.PERFORMANCE: "PF",
    Category.AZURE: "AZ",
}

AZURE_ALLOWED_EXTENSIONS = frozenset(
    {
        "orafce",
        "pg_cron",
        "pg_partman",
        "postgis",
        "vector",
        "postgres_fdw",
        "pg_stat_statements",
        "pgcrypto",
        "uuid-ossp",
        "hstore",
    }
)
"""Extensions o2pg may recommend. All are on the Azure Database for PostgreSQL
Flexible Server allow-list (pgvector is enabled as ``vector``). Enable them with
the ``azure.extensions`` server parameter before ``CREATE EXTENSION``; pg_cron
and pg_stat_statements also need ``shared_preload_libraries``."""

RULE_ID = re.compile(r"^O2PG-(?P<prefix>[A-Z]{2})-\d{3}$")


@dataclass(frozen=True)
class Hit:
    """One affected object.

    ``object_type`` is an Oracle object type such as TABLE, COLUMN, VIEW or
    PACKAGE BODY. ``detail`` is a short human explanation for this object.
    """

    object_name: str
    object_type: str
    detail: str = ""


Detector = Callable[[Inventory], Iterable[Hit]]


@dataclass(frozen=True)
class Rule:
    """A single migration check.

    ``effort`` is in effort points per affected object; a point is roughly a
    focused half day for someone who knows both databases, but treat it as a
    relative unit. ``ai_risk`` is set when the issue is the kind of implicit
    Oracle behaviour an LLM is likely to convert incorrectly; its text says what
    to tell the model.
    """

    id: str
    title: str
    category: Category
    severity: Severity
    effort: float
    description: str
    remediation: str
    detect: Detector = field(repr=False, compare=False)
    ai_risk: str | None = None
    extensions: tuple[str, ...] = ()

    def evaluate(self, inventory: Inventory) -> Finding | None:
        """Run the detector; return a finding when anything matched."""
        hits = tuple(self.detect(inventory))
        return Finding(self, hits) if hits else None


@dataclass(frozen=True)
class Finding:
    """A rule together with the objects it matched."""

    rule: Rule
    hits: tuple[Hit, ...]

    @property
    def effort_points(self) -> float:
        """Effort points for all hits of this finding."""
        return round(self.rule.effort * len(self.hits), 2)


_REGISTRY: dict[str, Rule] = {}


def register(rule: Rule) -> Rule:
    """Add ``rule`` to the registry after validating its metadata."""
    match = RULE_ID.match(rule.id)
    if not match:
        raise ValueError(f"bad rule id {rule.id!r}; expected O2PG-XX-000")
    if match.group("prefix") != rule.category.prefix:
        raise ValueError(f"{rule.id}: prefix does not match category {rule.category.value}")
    if rule.id in _REGISTRY:
        raise ValueError(f"duplicate rule id {rule.id}")
    unknown = set(rule.extensions) - AZURE_ALLOWED_EXTENSIONS
    if unknown:
        raise ValueError(f"{rule.id}: extensions not on the allow-list: {sorted(unknown)}")
    if rule.effort < 0:
        raise ValueError(f"{rule.id}: effort must not be negative")
    _REGISTRY[rule.id] = rule
    return rule


def rule(
    rule_id: str,
    title: str,
    category: Category,
    severity: Severity,
    effort: float,
    *,
    description: str,
    remediation: str,
    ai_risk: str | None = None,
    extensions: tuple[str, ...] = (),
) -> Callable[[Detector], Rule]:
    """Decorator that turns a detector function into a registered :class:`Rule`."""

    def decorate(detect: Detector) -> Rule:
        return register(
            Rule(
                id=rule_id,
                title=title,
                category=category,
                severity=severity,
                effort=effort,
                description=description,
                remediation=remediation,
                detect=detect,
                ai_risk=ai_risk,
                extensions=extensions,
            )
        )

    return decorate


def registered_rules() -> list[Rule]:
    """All registered rules sorted by id."""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def scan_units(
    units: Iterable[CodeUnit], pattern: re.Pattern[str], *, plsql_only: bool = False
) -> Iterator[Hit]:
    """Yield one hit per code unit whose cleaned text matches ``pattern``."""
    for unit in units:
        if plsql_only and not unit.plsql:
            continue
        matches = list(pattern.finditer(unit.cleaned))
        if not matches:
            continue
        count = len(matches)
        first = line_of(unit.cleaned, matches[0].start())
        noun = "match" if count == 1 else "matches"
        yield Hit(unit.qualified, unit.kind, f"{count} {noun}, first at line {first}")


def pattern_rule(
    rule_id: str,
    title: str,
    category: Category,
    severity: Severity,
    effort: float,
    pattern: str,
    *,
    description: str,
    remediation: str,
    plsql_only: bool = False,
    ai_risk: str | None = None,
    extensions: tuple[str, ...] = (),
) -> Rule:
    """Register a rule that scans code units with a case-insensitive regex."""
    regex = re.compile(pattern, re.IGNORECASE)

    def detect(inventory: Inventory) -> Iterator[Hit]:
        return scan_units(inventory.code_units, regex, plsql_only=plsql_only)

    return register(
        Rule(
            id=rule_id,
            title=title,
            category=category,
            severity=severity,
            effort=effort,
            description=description,
            remediation=remediation,
            detect=detect,
            ai_risk=ai_risk,
            extensions=extensions,
        )
    )
