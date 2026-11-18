"""Partition bounds, plans, DDL and the report (no database needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from helpers import column, constraint, inventory, partitioned, section, table
from o2pg.cli import main, sample_inventory_text
from o2pg.ddl import suggest_ddl
from o2pg.inventory import Inventory
from o2pg.partitions import (
    CHANGED,
    DEFAULT,
    LITERAL,
    MAXVALUE,
    NULL,
    TODO,
    UNKNOWN,
    child_name,
    interval_to_pg,
    maintenance_sql,
    parse_list_values,
    parse_range_bound,
    parse_value,
    plan_partitions,
    report_markdown,
    split_top_level,
)

FIXTURE = Path(__file__).parent / "fixtures" / "partitions" / "inventory.json"


@pytest.fixture(scope="module")
def inv() -> Inventory:
    return Inventory.from_path(FIXTURE)


@pytest.fixture(scope="module")
def plans(inv: Inventory) -> dict:
    return {p.table: p for p in plan_partitions(inv)}


@pytest.fixture(scope="module")
def ddl(inv: Inventory) -> str:
    return suggest_ddl(inv)


# ---------------------------------------------------------------- HIGH_VALUE


@pytest.mark.parametrize(
    ("text", "kind", "sql"),
    [
        (
            "TO_DATE(' 2024-02-01 00:00:00', 'SYYYY-MM-DD HH24:MI:SS', 'NLS_CALENDAR=GREGORIAN')",
            LITERAL,
            "'2024-02-01 00:00:00'",
        ),
        ("TIMESTAMP' 2024-02-01 13:30:00'", LITERAL, "'2024-02-01 13:30:00'"),
        ("TIMESTAMP '2024-02-01 00:00:00.000000000'", LITERAL, "'2024-02-01 00:00:00'"),
        ("100", LITERAL, "100"),
        ("-2.5", LITERAL, "-2.5"),
        ("'O''Brien'", LITERAL, "'O''Brien'"),
        ("N'EU'", LITERAL, "'EU'"),
        ("MAXVALUE", MAXVALUE, ""),
        ("DEFAULT", DEFAULT, ""),
        ("NULL", NULL, "NULL"),
        (
            "TO_DATE('-4712-01-01 00:00:00', 'SYYYY-MM-DD HH24:MI:SS', 'NLS_CALENDAR=GREGORIAN')",
            UNKNOWN,
            "",
        ),
        ("SYSDATE", UNKNOWN, ""),
    ],
)
def test_parse_value(text: str, kind: str, sql: str) -> None:
    value = parse_value(text)
    assert value.kind == kind
    assert value.sql == sql


def test_split_top_level_respects_quotes_and_parentheses() -> None:
    text = "TO_DATE(' 2024-01-01 00:00:00', 'SYYYY-MM-DD', 'X'), 'a,b', 3"
    assert split_top_level(text) == [
        "TO_DATE(' 2024-01-01 00:00:00', 'SYYYY-MM-DD', 'X')",
        "'a,b'",
        "3",
    ]


def test_range_and_list_parsing() -> None:
    assert [v.sql for v in parse_range_bound("2025, 7")] == ["2025", "7"]
    assert [v.kind for v in parse_range_bound("2025, MAXVALUE")] == [LITERAL, MAXVALUE]
    items = parse_list_values("('EU', 1), ('UK', 2)")
    assert [[v.sql for v in item] for item in items] == [["'EU'", "1"], ["'UK'", "2"]]
    assert [item[0].sql for item in parse_list_values("'US', 'CA', NULL")] == [
        "'US'",
        "'CA'",
        "NULL",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NUMTOYMINTERVAL(1, 'MONTH')", ("interval", "1 month")),
        ("NUMTOYMINTERVAL(1,'YEAR')", ("interval", "1 year")),
        ("NUMTODSINTERVAL(7, 'DAY')", ("interval", "7 day")),
        ("INTERVAL'1' MONTH", ("interval", "1 month")),
        ("1000", ("number", "1000")),
        ("NUMTOYMINTERVAL(1,'FORTNIGHT')", None),
        ("SOMETHING_ELSE", None),
    ],
)
def test_interval_to_pg(text: str, expected: tuple[str, str] | None) -> None:
    assert interval_to_pg(text) == expected


def test_child_names_fit_postgres() -> None:
    assert child_name("ORDERS", "SYS_P101") == "orders_sys_p101"
    long = child_name("A" * 40, "B" * 40)
    assert len(long) == 63
    assert long != child_name("A" * 40, "B" * 39 + "C")


# ---------------------------------------------------------------- plans


def test_every_pattern_is_planned(plans: dict) -> None:
    assert {t: p.pg_kind for t, p in plans.items()} == {
        "EVENTS": "RANGE",
        "EVENT_DETAILS": "",
        "READINGS": "RANGE",
        "CUSTOMERS_BY_REGION": "LIST",
        "SESSIONS": "HASH",
        "SALES": "RANGE",
        "LEDGER": "RANGE",
    }


def test_nullable_maxvalue_becomes_default(plans: dict) -> None:
    readings = plans["READINGS"]
    assert [c.bound for c in readings.children][-1] == "DEFAULT"
    assert any(f.level == CHANGED and "NULL" in f.text for f in readings.findings)
    # NOT NULL keys keep a real MAXVALUE bound.
    ledger = plans["LEDGER"]
    assert ledger.children[-1].bound == "FOR VALUES FROM (2026, 1) TO (MAXVALUE, MAXVALUE)"
    assert ledger.children[0].bound == "FOR VALUES FROM (MINVALUE, MINVALUE) TO (2025, 7)"


def test_interval_partitions_get_pg_partman_style_names(plans: dict) -> None:
    events = plans["EVENTS"]
    assert [c.name for c in events.children] == [
        "events_p_before_2026",
        "events_p20260101",
        "events_p20260201",
    ]
    assert events.interval == ("interval", "1 month")


def test_composite_partitions_have_subpartitions(plans: dict) -> None:
    sales = plans["SALES"]
    assert all(c.partition_by == "LIST (channel)" for c in sales.children)
    assert [g.bound for g in sales.children[0].children] == [
        "FOR VALUES IN ('WEB')",
        "FOR VALUES IN ('STORE')",
        "DEFAULT",
    ]
    assert any("SALE_DATE, CHANNEL" in f.text for f in sales.findings if f.level == TODO)


def test_list_with_null_and_default(plans: dict) -> None:
    bounds = [c.bound for c in plans["CUSTOMERS_BY_REGION"].children]
    assert bounds == [
        "FOR VALUES IN ('US', 'CA')",
        "FOR VALUES IN ('UK', 'DE')",
        "FOR VALUES IN (NULL)",
        "DEFAULT",
    ]


def test_hash_uses_modulus_and_remainder(plans: dict) -> None:
    assert [c.bound for c in plans["SESSIONS"].children][-1] == (
        "FOR VALUES WITH (MODULUS 4, REMAINDER 3)"
    )


def test_global_unique_and_reference_are_decisions(plans: dict) -> None:
    assert any("EVENTS_PK" in f.text for f in plans["EVENTS"].findings if f.level == TODO)
    assert any("Reference partitioning" in f.text for f in plans["EVENT_DETAILS"].findings)


def test_unknown_high_value_is_skipped_not_guessed() -> None:
    inv = inventory(
        table("T"),
        column("DATE", name="D", table="T", nullable="N"),
        partitioned("T", keys="D"),
        section(
            "partitions",
            owner="APP",
            table_name="T",
            partition_name="P1",
            partition_position=1,
            high_value="SYSDATE",
        ),
    )
    (plan,) = plan_partitions(inv)
    assert plan.children[0].skipped
    assert any("SYSDATE" in f.text for f in plan.findings)
    assert "-- TODO P1: HIGH_VALUE not understood: SYSDATE" in suggest_ddl(inv)


def test_old_inventory_without_partitions_asks_for_a_new_collection() -> None:
    inv = inventory(table("T"), column("DATE", name="D", table="T"), partitioned("T", keys="D"))
    (plan,) = plan_partitions(inv)
    assert plan.children == []
    assert any("Re-run" in f.text for f in plan.findings)


def test_multi_column_list_is_not_partitioned() -> None:
    inv = inventory(
        table("T"),
        column("VARCHAR2", name="A", table="T"),
        column("NUMBER", name="B", table="T"),
        partitioned("T", ptype="LIST", keys="A,B"),
    )
    (plan,) = plan_partitions(inv)
    assert not plan.converted
    ddl = suggest_ddl(inv)
    assert "PARTITION BY LIST" not in ddl
    assert "multi-column LIST partitioning is not supported" in ddl


# ---------------------------------------------------------------- DDL


def test_ddl_creates_children_and_leaves_out_unenforceable_keys(ddl: str) -> None:
    assert (
        "CREATE TABLE part.readings_p_max PARTITION OF part.readings DEFAULT;  -- Oracle P_MAX"
        in ddl
    )
    assert (
        "CREATE TABLE part.sales_p_2025q1_web PARTITION OF part.sales_p_2025q1 "
        "FOR VALUES IN ('WEB');  -- Oracle P_2025Q1_WEB"
    ) in ddl
    assert "-- CONSTRAINT events_pk PRIMARY KEY (event_id)" in ddl
    assert "-- TODO EVENT_DETAILS_FK: references EVENTS_PK" in ddl
    assert "CREATE OR REPLACE PROCEDURE part.events_add_partitions" in ddl


def test_foreign_keys_to_ordinary_keys_are_kept() -> None:
    inv = inventory(
        table("P"),
        column("NUMBER", name="ID", table="P", nullable="N"),
        constraint("P_PK", "P", "ID", table="P"),
        table("C"),
        column("NUMBER", name="P_ID", table="C"),
        constraint("C_FK", "R", "P_ID", table="C", r_constraint_name="P_PK"),
    )
    assert "ADD CONSTRAINT c_fk FOREIGN KEY (p_id) REFERENCES app.p (id);" in suggest_ddl(inv)


def test_numeric_interval_procedure() -> None:
    inv = inventory(
        table("T"),
        column("NUMBER", name="N", table="T", nullable="N"),
        partitioned("T", keys="N", interval="1000"),
        section(
            "partitions",
            owner="APP",
            table_name="T",
            partition_name="P1",
            partition_position=1,
            high_value="1000",
        ),
    )
    (plan,) = plan_partitions(inv)
    sql = "\n".join(maintenance_sql(plan))
    assert "v_upper + 1000" in sql


# ---------------------------------------------------------------- report and CLI


def test_report(inv: Inventory) -> None:
    text = report_markdown(inv)
    assert "| Partitioned tables | 7 |" in text
    assert "| `part.event_details` | REFERENCE | not partitioned | 0 | 1 |" in text
    assert "becomes the DEFAULT partition, because READING_DATE can be NULL" in text
    assert "part.events_add_partitions" in text


def test_sample_report_is_current() -> None:
    """examples/partitions-report.md must be regenerated when the planner changes."""
    expected = report_markdown(Inventory.from_json(sample_inventory_text()))
    actual = (Path(__file__).parent.parent / "examples" / "partitions-report.md").read_text()
    assert actual == expected, (
        "run: o2pg partitions examples/sample_inventory.json -o examples/partitions-report.md"
    )


def test_cli_partitions(tmp_path: Path) -> None:
    out = tmp_path / "report.md"
    result = CliRunner().invoke(main, ["partitions", str(FIXTURE), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "7 partitioned tables, 3 decisions needed" in result.output
    assert out.read_text().startswith("# o2pg partitioning report")
