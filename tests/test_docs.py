"""Generated docs and the collector script stay in sync with the code."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from o2pg.catalog import rules_markdown
from o2pg.convert import conversion_rules, conversion_rules_markdown
from o2pg.inventory import SECTION_TYPES, Metadata
from o2pg.rules import Category, all_rules

ROOT = Path(__file__).resolve().parents[1]
RULES_DOC = ROOT / "docs" / "rules.md"
CONVERSION_DOC = ROOT / "docs" / "conversion-rules.md"
COLLECTOR = ROOT / "collector" / "collect.sql"


def test_rules_doc_is_up_to_date() -> None:
    assert RULES_DOC.read_text(encoding="utf-8") == rules_markdown(), (
        "docs/rules.md is stale; run: python scripts/gen_rules_doc.py"
    )


def test_conversion_rules_doc_is_up_to_date() -> None:
    assert CONVERSION_DOC.read_text(encoding="utf-8") == conversion_rules_markdown(), (
        "docs/conversion-rules.md is stale; run: python scripts/gen_rules_doc.py"
    )


def test_conversion_rules_doc_mentions_every_rule() -> None:
    text = conversion_rules_markdown()
    for rule in conversion_rules():
        assert f"### {rule.id}: {rule.title}" in text


def test_gen_rules_doc_check_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_rules_doc.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_rules_doc_mentions_every_rule_and_category() -> None:
    text = rules_markdown()
    for rule in all_rules():
        assert f"### {rule.id}: {rule.title}" in text
    for category in Category:
        assert f"## {category.value}" in text


@pytest.fixture(scope="module")
def collector_sql() -> str:
    return COLLECTOR.read_text(encoding="utf-8")


@pytest.mark.parametrize("section", [*sorted(SECTION_TYPES), "source", "metadata"])
def test_collector_emits_every_section(collector_sql: str, section: str) -> None:
    assert f'"{section}":' in collector_sql


@pytest.mark.parametrize(
    ("section", "record_type"), sorted(SECTION_TYPES.items()), ids=sorted(SECTION_TYPES)
)
def test_collector_emits_every_field(collector_sql: str, section: str, record_type: type) -> None:
    for field in fields(record_type):
        assert re.search(rf"'{field.name}'\s+VALUE", collector_sql), f"{section}.{field.name}"


def test_collector_emits_metadata_fields(collector_sql: str) -> None:
    for field in fields(Metadata):
        assert re.search(rf"'{field.name}'\s+VALUE", collector_sql), field.name


def test_collector_is_read_only(collector_sql: str) -> None:
    code = re.sub(r"--[^\n]*|^REM[^\n]*", "", collector_sql, flags=re.MULTILINE).upper()
    for verb in (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "MERGE ",
        "CREATE ",
        "ALTER ",
        "DROP ",
        "GRANT ",
        "TRUNCATE ",
    ):
        assert verb not in code, verb
