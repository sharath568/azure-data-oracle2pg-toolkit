"""Rule metadata quality and registry validation."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from o2pg.inventory import Inventory
from o2pg.rules import AZURE_ALLOWED_EXTENSIONS, Category, Hit, Rule, Severity, all_rules, get_rule
from o2pg.rules.base import RULE_ID, register

RULES = all_rules()


def _noop(_: Inventory) -> Iterator[Hit]:
    return iter(())


def _rule(**overrides: object) -> Rule:
    fields: dict[str, object] = {
        "id": "O2PG-DT-999",
        "title": "t",
        "category": Category.DATATYPES,
        "severity": Severity.LOW,
        "effort": 1.0,
        "description": "d",
        "remediation": "r",
        "detect": _noop,
    }
    fields.update(overrides)
    return Rule(**fields)  # type: ignore[arg-type]


def test_rule_count_is_in_expected_range() -> None:
    assert 60 <= len(RULES) <= 100


def test_rule_ids_are_unique_and_sorted() -> None:
    ids = [r.id for r in RULES]
    assert ids == sorted(set(ids))


def test_every_category_has_rules() -> None:
    assert {r.category for r in RULES} == set(Category)


def test_every_severity_is_used() -> None:
    assert {r.severity for r in RULES} == set(Severity)


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.id)
def test_rule_metadata(rule: Rule) -> None:
    match = RULE_ID.match(rule.id)
    assert match is not None
    assert match.group("prefix") == rule.category.prefix
    assert rule.title
    assert not rule.title.endswith(".")
    assert len(rule.description) > 30
    assert len(rule.remediation) > 30
    assert rule.effort >= 0
    assert set(rule.extensions) <= AZURE_ALLOWED_EXTENSIONS
    if rule.ai_risk is not None:
        assert "model" in rule.ai_risk.lower()


def test_ai_risk_rules_cover_the_article_thesis() -> None:
    flagged = {r.id for r in RULES if r.ai_risk}
    # '' = NULL, DATE time portion, NUMBER precision, trigger side effects, package state
    assert {"O2PG-SQ-008", "O2PG-DT-003", "O2PG-DT-001", "O2PG-OB-004", "O2PG-PL-002"} <= flagged


def test_get_rule_is_case_insensitive() -> None:
    assert get_rule("o2pg-sq-001").id == "O2PG-SQ-001"


def test_get_rule_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_rule("O2PG-XX-000")


def test_severity_rank_and_weight_order() -> None:
    ranks = [s.rank for s in Severity]
    weights = [s.weight for s in Severity]
    assert ranks == sorted(ranks)
    assert weights == sorted(weights, reverse=True)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "DT-001"}, "bad rule id"),
        ({"id": "O2PG-PL-999"}, "prefix does not match"),
        ({"id": "O2PG-DT-001"}, "duplicate rule id"),
        ({"extensions": ("oracle_fdw",)}, "allow-list"),
        ({"effort": -1.0}, "negative"),
    ],
)
def test_register_rejects_bad_rules(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        register(_rule(**overrides))


def test_rule_evaluate_returns_none_without_hits() -> None:
    assert _rule().evaluate(Inventory()) is None


def test_finding_effort_points() -> None:
    rule = _rule(effort=0.5, detect=lambda _: [Hit("A", "TABLE"), Hit("B", "TABLE")])
    finding = rule.evaluate(Inventory())
    assert finding is not None
    assert finding.effort_points == 1.0
