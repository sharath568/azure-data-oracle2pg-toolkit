"""Turn findings into scores: overall complexity, per-object complexity, AI readiness.

All numbers here are heuristics meant for comparing schemas and ranking work,
not for quoting a project plan. The README documents the formulas.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from o2pg.inventory import PLSQL_SOURCE_TYPES, Inventory
from o2pg.rules.base import Finding, Severity

SCORE_SCALE = 300.0
"""Raw weighted points at which the complexity score reaches ~63/100."""

EFFORT_BANDS: tuple[tuple[float, str, str], ...] = (
    (20, "S", "up to ~2 person-weeks"),
    (80, "M", "~2 to 8 person-weeks"),
    (240, "L", "~2 to 6 person-months"),
    (math.inf, "XL", "6+ person-months"),
)
"""Upper bound in effort points, band label and an indicative range."""

LEVELS: tuple[tuple[int, str], ...] = (
    (25, "Low"),
    (50, "Moderate"),
    (75, "High"),
    (101, "Very high"),
)

AI_PENALTIES = {
    Severity.BLOCKER: 30,
    Severity.HIGH: 20,
    Severity.MEDIUM: 10,
    Severity.LOW: 5,
    Severity.INFO: 0,
}

AI_SCALE = 150.0
"""Penalty points at which AI readiness drops to ~37/100."""

PLSQL_LINES_PER_POINT = 150
"""Baseline conversion effort: one point per this many lines of PL/SQL."""

TABLE_BASELINE_POINTS = 0.1
"""Baseline effort per table (DDL, load, validation) before any findings."""


@dataclass(frozen=True)
class ComplexityScore:
    """Overall migration complexity."""

    score: int
    level: str
    effort_points: float
    effort_band: str
    effort_range: str
    blockers: int
    findings_by_severity: dict[str, int]


@dataclass(frozen=True)
class ObjectScore:
    """Complexity of one object, summed from the findings that hit it."""

    name: str
    object_type: str
    score: float
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class AiRisk:
    """One rule that makes blind LLM conversion risky, with what to tell the model."""

    rule_id: str
    title: str
    severity: str
    occurrences: int
    guidance: str


@dataclass(frozen=True)
class AiReadiness:
    """How safe it is to hand this schema to an LLM without extra instructions."""

    score: int
    verdict: str
    risks: tuple[AiRisk, ...] = field(default_factory=tuple)


def _raw_points(findings: list[Finding]) -> float:
    """Severity-weighted points; hits count sub-linearly so one noisy rule cannot dominate."""
    return sum(f.rule.severity.weight * (1 + math.log2(len(f.hits))) for f in findings)


def complexity(findings: list[Finding], inventory: Inventory) -> ComplexityScore:
    """Compute the overall complexity score and effort band."""
    raw = _raw_points(findings)
    score = round(100 * (1 - math.exp(-raw / SCORE_SCALE)))
    level = next(name for bound, name in LEVELS if score < bound)
    blockers = sum(1 for f in findings if f.rule.severity is Severity.BLOCKER)
    if blockers and level in {"Low", "Moderate"}:
        level = "High"
    plsql_lines = sum(s.text.count("\n") for s in inventory.source if s.type in PLSQL_SOURCE_TYPES)
    effort = sum(f.effort_points for f in findings)
    effort += len(inventory.tables) * TABLE_BASELINE_POINTS + plsql_lines / PLSQL_LINES_PER_POINT
    effort = round(effort, 1)
    band, label = next((name, text) for bound, name, text in EFFORT_BANDS if effort <= bound)
    by_severity = {s.value: 0 for s in Severity}
    for finding in findings:
        by_severity[finding.rule.severity.value] += 1
    return ComplexityScore(score, level, effort, band, label, blockers, by_severity)


def _owning_object(name: str, object_type: str) -> tuple[str, str]:
    """Attribute column and constraint hits to their table."""
    if object_type in {"COLUMN", "CONSTRAINT"} and name.count(".") >= 2:
        return name.rsplit(".", 1)[0], "TABLE"
    return name, object_type


def object_scores(
    findings: list[Finding], inventory: Inventory, limit: int = 15
) -> list[ObjectScore]:
    """Rank objects by summed severity weight plus a size factor for code."""
    scores: dict[tuple[str, str], float] = defaultdict(float)
    rules: dict[tuple[str, str], set[str]] = defaultdict(set)
    for finding in findings:
        for hit in finding.hits:
            key = _owning_object(hit.object_name, hit.object_type)
            scores[key] += finding.rule.severity.weight + finding.rule.effort
            rules[key].add(finding.rule.id)
    for unit in inventory.source:
        key = (f"{unit.owner}.{unit.name}", unit.type)
        if key in scores:
            scores[key] += unit.text.count("\n") / 100
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        ObjectScore(name, kind, round(score, 1), tuple(sorted(rules[(name, kind)])))
        for (name, kind), score in ranked[:limit]
    ]


def ai_readiness(findings: list[Finding]) -> AiReadiness:
    """Score how much implicit Oracle behaviour a naive LLM conversion would miss."""
    risks = tuple(
        AiRisk(
            f.rule.id,
            f.rule.title,
            f.rule.severity.value,
            len(f.hits),
            f.rule.ai_risk or "",
        )
        for f in sorted(findings, key=lambda f: (f.rule.severity.rank, f.rule.id))
        if f.rule.ai_risk
    )
    penalty = sum(AI_PENALTIES[Severity(r.severity)] for r in risks)
    score = round(100 * math.exp(-penalty / AI_SCALE))
    if not risks:
        verdict = "No implicit-semantics risks detected. Still review everything the model writes."
    elif score >= 70:
        verdict = "Usable with care: give the model the guidance below with every prompt."
    elif score >= 40:
        verdict = "Risky: write a conversion spec from the guidance below before prompting."
    else:
        verdict = (
            "Do not paste this schema into an LLM as-is. Too much behaviour is implicit; "
            "convert with a written spec and review every object."
        )
    return AiReadiness(score, verdict, risks)
