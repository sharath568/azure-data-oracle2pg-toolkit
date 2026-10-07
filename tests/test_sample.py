"""End-to-end checks on the bundled sample inventory and the shipped example files."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from o2pg.assessment import Assessment, assess
from o2pg.cli import sample_inventory_text
from o2pg.inventory import Inventory
from o2pg.render import render_markdown

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_INVENTORY = ROOT / "examples" / "sample_inventory.json"
EXAMPLE_REPORT = ROOT / "examples" / "sample_report.md"


@pytest.fixture(scope="module")
def sample() -> Inventory:
    return Inventory.from_json(sample_inventory_text())


@pytest.fixture(scope="module")
def result(sample: Inventory) -> Assessment:
    return assess(sample)


def test_bundled_sample_matches_examples_copy() -> None:
    assert json.loads(sample_inventory_text()) == json.loads(
        EXAMPLE_INVENTORY.read_text(encoding="utf-8")
    )


def test_sample_shape(sample: Inventory) -> None:
    counts = sample.object_counts()
    assert counts["tables"] >= 10
    assert counts["triggers"] >= 3
    assert counts["part_tables"] >= 2
    assert {s.type for s in sample.source} >= {"PACKAGE", "PACKAGE BODY", "TRIGGER", "PROCEDURE"}


def test_sample_hits_most_rules(result: Assessment) -> None:
    assert len(result.findings) >= 0.8 * result.rules_evaluated


@pytest.mark.parametrize(
    "rule_id",
    [
        "O2PG-DT-003",  # DATE
        "O2PG-DT-006",  # NOT NULL + ''
        "O2PG-PL-002",  # package state
        "O2PG-PL-003",  # autonomous transaction
        "O2PG-PL-015",  # wrapped body
        "O2PG-SQ-001",  # CONNECT BY
        "O2PG-SQ-008",  # '' literal
        "O2PG-PT-005",  # PK without partition key
        "O2PG-OB-005",  # compound trigger
        "O2PG-AZ-002",  # executable job
    ],
)
def test_sample_triggers_key_rules(result: Assessment, rule_id: str) -> None:
    assert rule_id in {f.rule.id for f in result.findings}


def test_sample_scores(result: Assessment) -> None:
    assert result.complexity.level == "Very high"
    assert result.complexity.blockers >= 3
    assert result.ai.score < 40
    assert result.ai.verdict.startswith("Do not paste")


def test_sample_extensions_are_allow_listed(result: Assessment) -> None:
    assert {"orafce", "pg_cron", "pg_partman", "postgis"} <= set(result.extensions)


def _strip_timestamp(text: str) -> str:
    return re.sub(r"Generated \S+ by", "Generated <ts> by", text)


def test_example_report_is_current(result: Assessment) -> None:
    """examples/sample_report.md must be regenerated when rules or scoring change."""
    expected = _strip_timestamp(render_markdown(result))
    actual = _strip_timestamp(EXAMPLE_REPORT.read_text(encoding="utf-8"))
    assert actual == expected, (
        "run: o2pg assess examples/sample_inventory.json -o examples/sample_report.md"
    )
