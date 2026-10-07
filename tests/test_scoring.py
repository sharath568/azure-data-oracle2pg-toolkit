"""Complexity, effort and AI readiness scoring."""

from __future__ import annotations

import pytest

from helpers import code, column, inventory, table
from o2pg.assessment import assess
from o2pg.inventory import Inventory
from o2pg.rules import Hit, Severity, get_rule
from o2pg.rules.base import Finding
from o2pg.scoring import ai_readiness, complexity, object_scores


def _finding(rule_id: str, hits: int = 1) -> Finding:
    rule = get_rule(rule_id)
    return Finding(rule, tuple(Hit(f"APP.T{i}", "TABLE") for i in range(hits)))


def test_empty_inventory_scores_zero() -> None:
    score = complexity([], Inventory())
    assert score.score == 0
    assert score.level == "Low"
    assert score.effort_band == "S"
    assert score.blockers == 0


def test_blockers_raise_level_to_at_least_high() -> None:
    score = complexity([_finding("O2PG-DT-010")], Inventory())
    assert score.blockers == 1
    assert score.level == "High"


def test_more_findings_never_lower_the_score() -> None:
    one = complexity([_finding("O2PG-SQ-001")], Inventory()).score
    two = complexity([_finding("O2PG-SQ-001"), _finding("O2PG-OB-005")], Inventory()).score
    assert two > one


def test_hits_count_sub_linearly() -> None:
    few = complexity([_finding("O2PG-DT-003", 1)], Inventory()).score
    many = complexity([_finding("O2PG-DT-003", 64)], Inventory()).score
    assert few < many < few * 10


def test_score_is_capped_at_100() -> None:
    findings = [_finding("O2PG-DT-010", 1000) for _ in range(50)]
    assert complexity(findings, Inventory()).score <= 100


def test_effort_includes_table_and_plsql_baseline() -> None:
    inv = inventory(table(), code("x\n" * 150, kind="PROCEDURE"))
    score = complexity([], inv)
    assert score.effort_points == pytest.approx(1.1)


def test_findings_by_severity_counts() -> None:
    score = complexity([_finding("O2PG-DT-010"), _finding("O2PG-DT-003")], Inventory())
    assert score.findings_by_severity["blocker"] == 1
    assert score.findings_by_severity["high"] == 1
    assert score.findings_by_severity["info"] == 0


@pytest.mark.parametrize(
    ("points_rule", "hits", "band"),
    [
        ("O2PG-PL-015", 1, "S"),
        ("O2PG-PL-015", 10, "M"),
        ("O2PG-PL-015", 40, "L"),
        ("O2PG-PL-015", 60, "XL"),
    ],
)
def test_effort_bands(points_rule: str, hits: int, band: str) -> None:
    assert complexity([_finding(points_rule, hits)], Inventory()).effort_band == band


def test_object_scores_roll_columns_up_to_tables() -> None:
    finding = Finding(
        get_rule("O2PG-DT-003"),
        (Hit("APP.T1.A", "COLUMN"), Hit("APP.T1.B", "COLUMN"), Hit("APP.T2.C", "COLUMN")),
    )
    scores = object_scores([finding], Inventory())
    assert scores[0].name == "APP.T1"
    assert scores[0].object_type == "TABLE"
    assert scores[0].score > scores[1].score


def test_object_scores_add_code_size() -> None:
    inv = inventory(code("x\n" * 500, kind="PROCEDURE", name="BIG"), code("x\n", kind="PROCEDURE"))
    finding = Finding(
        get_rule("O2PG-SQ-004"),
        (Hit("APP.P1", "PROCEDURE"), Hit("APP.BIG", "PROCEDURE")),
    )
    assert object_scores([finding], inv)[0].name == "APP.BIG"


def test_object_scores_limit() -> None:
    finding = _finding("O2PG-OB-014", 40)
    assert len(object_scores([finding], Inventory(), limit=5)) == 5


def test_ai_readiness_without_risks() -> None:
    result = ai_readiness([_finding("O2PG-DT-010")])
    assert result.score == 100
    assert result.risks == ()
    assert "No implicit-semantics risks" in result.verdict


def test_ai_readiness_orders_by_severity() -> None:
    result = ai_readiness([_finding("O2PG-DT-005"), _finding("O2PG-DT-003")])
    assert [r.rule_id for r in result.risks] == ["O2PG-DT-003", "O2PG-DT-005"]
    assert 0 < result.score < 100


@pytest.mark.parametrize(
    ("rule_ids", "verdict_start"),
    [
        (["O2PG-DT-005"], "Usable with care"),
        (["O2PG-DT-003", "O2PG-PL-002", "O2PG-SQ-008"], "Risky"),
        (
            [
                "O2PG-DT-003",
                "O2PG-PL-002",
                "O2PG-SQ-008",
                "O2PG-PL-003",
                "O2PG-OB-004",
                "O2PG-DT-001",
                "O2PG-SQ-007",
                "O2PG-SQ-018",
                "O2PG-SQ-002",
                "O2PG-SE-004",
                "O2PG-PL-007",
                "O2PG-OB-016",
            ],
            "Do not paste",
        ),
    ],
)
def test_ai_verdicts(rule_ids: list[str], verdict_start: str) -> None:
    assert ai_readiness([_finding(r) for r in rule_ids]).verdict.startswith(verdict_start)


def test_assess_min_severity_filters_rules() -> None:
    inv = inventory(column("DATE"), column("BINARY_DOUBLE", name="C2"))
    ids = {f.rule.id for f in assess(inv, min_severity=Severity.HIGH).findings}
    assert "O2PG-DT-003" in ids
    assert "O2PG-DT-002" not in ids


def test_assess_sorts_by_severity_then_hits() -> None:
    inv = inventory(column("DATE"), column("BFILE", name="C2"), column("DATE", name="C3"))
    severities = [f.rule.severity.rank for f in assess(inv).findings]
    assert severities == sorted(severities)


def test_assess_collects_extensions() -> None:
    inv = inventory(code("SELECT NVL(a, 0) FROM t", "VIEW"))
    extensions = assess(inv).extensions
    assert extensions["orafce"] == ["O2PG-AZ-005", "O2PG-SQ-004"]


def test_assess_with_explicit_rule_subset() -> None:
    result = assess(inventory(column("DATE")), rules=[get_rule("O2PG-DT-003")])
    assert result.rules_evaluated == 1
    assert [f.rule.id for f in result.findings] == ["O2PG-DT-003"]
