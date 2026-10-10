"""Run converted PL/pgSQL through a real PostgreSQL server.

These tests need ``O2PG_TEST_DSN`` (a superuser or a role with CREATEDB) and
psycopg 3. They create a throwaway database, load the tables and sequences that
``o2pg map-types`` suggests for the bundled sample and for
``tests/fixtures/plsql/schema.json``, then:

* create every converted routine and trigger that has no blocking review point,
  with ``check_function_bodies = on``, and require each one to succeed;
* call a set of converted routines and check what they return.

Two relaxations are applied to the sample DDL, both for reasons outside the
converter: ``geometry`` columns become ``text`` (PostGIS is not assumed), and
``PARTITION BY`` clauses are dropped (``map-types`` marks the sample's
partitioned primary keys as TODOs because they lack the partition key).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from o2pg.cli import sample_inventory_text
from o2pg.convert import (
    ConversionResult,
    ConvertedObject,
    ConvertedUnit,
    convert_inventory,
    convert_sql,
)
from o2pg.ddl import suggest_ddl
from o2pg.inventory import Inventory

pytestmark = pytest.mark.integration

DSN = os.environ.get("O2PG_TEST_DSN", "")
FIXTURES = Path(__file__).parent / "fixtures" / "plsql"

try:  # psycopg is only needed when the integration tests actually run
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
except ImportError:  # pragma: no cover - exercised only without the pg extra
    psycopg = None  # type: ignore[assignment]

SAMPLE = convert_inventory(Inventory.from_json(sample_inventory_text()))
FIXTURE_SQL = "\n".join(p.read_text(encoding="utf-8") for p in sorted(FIXTURES.glob("*.sql")))
FIXTURE = convert_sql(FIXTURE_SQL)


def _executable(result: ConversionResult) -> list[tuple[ConvertedObject, ConvertedUnit]]:
    return [(obj, unit) for obj in result.objects for unit in obj.units if unit.executable]


SAMPLE_UNITS = _executable(SAMPLE)
FIXTURE_UNITS = _executable(FIXTURE)

EXPECTED_SAMPLE_UNITS = {
    "hr.update_job_history (trigger on hr.employees)",
    "hr.add_job_history",
    "emp_mgmt.employees_in",
    "order_proc.upsert_customer",
    "order_proc.purge_stage",
    "sales.orders_bi (trigger on sales.orders)",
    "sales.get_order_total",
    "sales.product_snippet",
}


def _relax(ddl: str) -> str:
    ddl = re.sub(r"\bgeometry\b", "text", ddl)
    return re.sub(r"\)\s*PARTITION BY [A-Z]+ \([^)]*\);", ");", ddl)


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    if psycopg is None:
        pytest.skip("psycopg is not installed (pip install 'o2pg[pg]')")
    name = f"o2pg_it_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="module")
def created(database: str) -> dict[str, str | None]:
    """Load tables, then create every executable unit. Maps unit name to error (or None)."""
    outcomes: dict[str, str | None] = {}
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(_relax(suggest_ddl(Inventory.from_json(sample_inventory_text()))))
        conn.execute(suggest_ddl(Inventory.from_path(FIXTURES / "schema.json")))
        conn.execute("SET check_function_bodies = on")
        for obj, unit in SAMPLE_UNITS + FIXTURE_UNITS:
            try:
                with conn.transaction():
                    for statement in obj.preamble:
                        conn.execute(statement)
                    conn.execute(unit.sql)
                outcomes[unit.name] = None
            except psycopg.Error as exc:
                outcomes[unit.name] = str(exc).strip()
    return outcomes


@pytest.fixture
def conn(database: str, created: dict[str, str | None]) -> Iterator[Any]:
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("SET search_path = app, sales, hr, public")
        yield connection


def _one(conn: Any, query: str, *params: Any) -> Any:
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------ CREATE checks


def test_sample_has_the_expected_executable_units() -> None:
    assert {unit.name for _, unit in SAMPLE_UNITS} == EXPECTED_SAMPLE_UNITS


@pytest.mark.parametrize(
    ("obj", "unit"), SAMPLE_UNITS + FIXTURE_UNITS, ids=lambda x: getattr(x, "name", "")
)
def test_unit_creates_in_postgresql(
    created: dict[str, str | None], obj: ConvertedObject, unit: ConvertedUnit
) -> None:
    assert created[unit.name] is None, f"{unit.name}: {created[unit.name]}"


def test_fixture_blocked_units_are_the_ones_expected() -> None:
    blocked = {u.name for u in FIXTURE.units if u.status == "blocked"}
    assert blocked == {"app.archive_orders", "app.top_customer", "app.audit_now", "app.org_chart"}


# ------------------------------------------------------------------ behaviour


def test_nvl_becomes_coalesce(conn: Any) -> None:
    assert _one(conn, "SELECT app.display_name(NULL)") == "anonymous"
    assert _one(conn, "SELECT app.display_name('Ada')") == "Ada"


@pytest.mark.parametrize(
    ("grade", "label"), [("A", "excellent"), ("B", "good"), (None, "missing"), ("Z", "other")]
)
def test_decode_keeps_null_matching(conn: Any, grade: str | None, label: str) -> None:
    assert _one(conn, "SELECT app.grade_label(%s)", grade) == label


def test_nvl2(conn: Any) -> None:
    assert _one(conn, "SELECT app.has_value(NULL)") == "unset"
    assert _one(conn, "SELECT app.has_value(1)") == "set"


def test_sequence_nextval_and_currval(conn: Any) -> None:
    first = _one(conn, "SELECT app.next_customer_id()")
    second = _one(conn, "SELECT app.next_customer_id()")
    assert second == first + 1
    assert _one(conn, "SELECT app.current_customer_id()") == second


def test_select_into_strict_raises_no_data_found(conn: Any) -> None:
    with pytest.raises(psycopg.errors.NoDataFound):
        conn.execute("SELECT app.customer_name(-1)")


def test_select_into_strict_raises_too_many_rows(conn: Any) -> None:
    conn.execute(
        "INSERT INTO app.customers (name, email) VALUES ('Twin one', 'twin@example.com'), "
        "('Twin two', 'twin@example.com')"
    )
    with pytest.raises(psycopg.errors.TooManyRows):
        conn.execute("SELECT app.customer_by_email('twin@example.com')")


def test_no_data_found_handler_still_fires(conn: Any) -> None:
    assert _one(conn, "SELECT app.customer_name_or_default(-1)") == "unknown"


def test_raise_application_error(conn: Any) -> None:
    assert _one(conn, "SELECT app.check_amount(5)") == 5
    with pytest.raises(psycopg.errors.RaiseException) as caught:
        conn.execute("SELECT app.check_amount(-1)")
    assert caught.value.diag.sqlstate == "P0001"
    assert caught.value.diag.message_primary == "Amount must not be negative"
    assert caught.value.diag.message_hint == "Oracle error code -20001"


def test_zero_divide_maps_to_division_by_zero(conn: Any) -> None:
    assert _one(conn, "SELECT app.safe_ratio(1, 0)") is None
    assert _one(conn, "SELECT app.safe_ratio(6, 3)") == 2


def test_trunc_sysdate_minus_days(conn: Any) -> None:
    today = _one(conn, "SELECT date_trunc('day', LOCALTIMESTAMP)")
    assert _one(conn, "SELECT app.days_ago(2)") == today - timedelta(days=2)


def test_trunc_month_add_months_and_to_date(conn: Any) -> None:
    assert _one(conn, "SELECT app.month_start('2026-02-17 10:30:00')") == datetime(2026, 2, 1)
    assert _one(conn, "SELECT app.add_quarter('2026-01-31')") == datetime(2026, 4, 30)
    assert _one(conn, "SELECT app.parse_moment('2026-03-04 05:06:07')") == datetime(
        2026, 3, 4, 5, 6, 7
    )


def test_substr_from_zero_and_instr(conn: Any) -> None:
    assert _one(conn, "SELECT app.first_chars('abcdef', 3)") == "abc"
    assert _one(conn, "SELECT app.position_of('hello', 'l')") == 3


def test_concatenation_review_point_is_real(conn: Any) -> None:
    # Oracle returns '321' because NULL || '3' is '3' there. PostgreSQL returns NULL,
    # which is exactly why the converter leaves an O2PG-RV-035 marker on that line.
    assert _one(conn, "SELECT app.countdown(3)") is None
    unit = next(u for u in FIXTURE.units if u.name == "app.countdown")
    assert [r.rule for r in unit.reviews] == ["O2PG-RV-035"]


def test_out_and_inout_parameters(conn: Any) -> None:
    row = conn.execute("CALL app.split_amount(10, 4, NULL, 5)").fetchone()
    assert row == (Decimal("2.50"), Decimal("6"))


def test_dbms_output_becomes_notice(conn: Any) -> None:
    notices: list[str] = []
    conn.add_notice_handler(lambda diag: notices.append(diag.message_primary))
    conn.execute("CALL app.log_message('hello from o2pg')")
    assert "hello from o2pg" in notices
    assert _one(conn, "SELECT count(*) FROM app.audit_log WHERE msg = 'hello from o2pg'") >= 1


def test_procedure_with_commit_and_rowcount(conn: Any) -> None:
    conn.execute(
        "INSERT INTO app.orders (customer_id, amount, status, created_at) VALUES "
        "(1, 10, 'OPEN', LOCALTIMESTAMP - interval '40 days'), "
        "(1, 20, 'OPEN', LOCALTIMESTAMP)"
    )
    closed = conn.execute("CALL app.close_old_orders(30, NULL)").fetchone()[0]
    assert closed >= 1
    assert _one(conn, "SELECT count(*) FROM app.orders WHERE status = 'OPEN' AND amount = 10") == 0


def test_cursor_loops(conn: Any) -> None:
    conn.execute(
        "INSERT INTO app.orders (customer_id, amount, status) VALUES "
        "(77, 5, 'CLOSED'), (77, 7, 'CLOSED'), (77, 100, 'OPEN'), (78, 1000, 'OPEN')"
    )
    assert _one(conn, "SELECT app.order_total_for(77)") == Decimal("112")


def test_execute_immediate(conn: Any) -> None:
    conn.execute("CALL app.purge_audit()")
    assert _one(conn, "SELECT count(*) FROM app.audit_log") == 0


def test_trigger_sets_new_columns(conn: Any) -> None:
    row = conn.execute(
        "INSERT INTO app.customers (name, code) VALUES ('Grace', 'abc') "
        "RETURNING customer_id, code, created_at, updated_at"
    ).fetchone()
    customer_id, code, created_at, updated_at = row
    assert customer_id >= 1000
    assert code == "ABC"
    assert created_at is not None
    assert updated_at is None
    conn.execute("UPDATE app.customers SET code = 'xyz' WHERE customer_id = %s", (customer_id,))
    code, updated_at = conn.execute(
        "SELECT code, updated_at FROM app.customers WHERE customer_id = %s", (customer_id,)
    ).fetchone()
    assert code == "XYZ"
    assert updated_at is not None


def test_trigger_when_clause_and_referencing_aliases(conn: Any) -> None:
    order_id = _one(
        conn,
        "INSERT INTO app.orders (customer_id, amount, status) VALUES (5, 1, 'NEW') "
        "RETURNING order_id",
    )
    assert order_id is not None
    assert (
        _one(conn, "SELECT msg FROM app.audit_log WHERE order_id = %s", order_id) == "order added"
    )
    conn.execute("DELETE FROM app.orders WHERE order_id = %s", (order_id,))
    messages = {
        r[0] for r in conn.execute("SELECT msg FROM app.audit_log WHERE order_id = %s", (order_id,))
    }
    assert messages == {"order added", "order deleted"}


def test_package_constant_and_sibling_calls(conn: Any) -> None:
    assert _one(conn, "SELECT billing.gross(100)") == Decimal("120.00")
    order_id = conn.execute("CALL billing.add_order(9, 50, NULL)").fetchone()[0]
    assert _one(conn, "SELECT amount FROM app.orders WHERE order_id = %s", order_id) == Decimal(
        "60.00"
    )


def test_sample_get_order_total_and_orders_trigger(conn: Any) -> None:
    conn.execute(
        "INSERT INTO sales.customers (customer_id, cust_first_name, cust_last_name) "
        "VALUES (1, 'Ada', 'Lovelace')"
    )
    conn.execute("INSERT INTO sales.products (product_id, product_name) VALUES (1, 'Widget')")
    order_id = _one(
        conn,
        "INSERT INTO sales.orders (order_date, customer_id) VALUES (LOCALTIMESTAMP, 1) "
        "RETURNING order_id",
    )
    assert order_id == 182401001  # orders_bi filled it from orders_seq
    assert _one(conn, "SELECT sales.get_order_total(%s)", order_id) == 0
    conn.execute(
        "INSERT INTO sales.order_items (order_id, line_item_id, product_id, unit_price, quantity, "
        "discount) VALUES (%s, 1, 1, 10, 3, 0.5), (%s, 2, 1, 4, 1, NULL)",
        (order_id, order_id),
    )
    assert _one(conn, "SELECT sales.get_order_total(%s)", order_id) == 19


def test_sample_merge_upsert(conn: Any) -> None:
    conn.execute("CALL order_proc.upsert_customer(500, 'old@example.com', 'Hopper')")
    conn.execute("CALL order_proc.upsert_customer(500, 'new@example.com', 'Hopper')")
    assert conn.execute(
        "SELECT email, cust_last_name FROM sales.customers WHERE customer_id = 500"
    ).fetchone() == ("new@example.com", "Hopper")


def test_sample_refcursor_function(conn: Any) -> None:
    with conn.transaction():
        cursor_name = _one(conn, "SELECT emp_mgmt.employees_in(10)")
        rows = conn.execute(
            sql.SQL("FETCH ALL FROM {}").format(sql.Identifier(cursor_name))
        ).fetchall()
    assert rows == []


def test_sample_trigger_calls_converted_procedure(conn: Any) -> None:
    conn.execute(
        "INSERT INTO hr.jobs (job_id, job_title) VALUES ('DEV', 'Developer'), ('LEAD', 'Lead')"
    )
    employee_id = _one(
        conn,
        "INSERT INTO hr.employees (last_name, email, hire_date, job_id) "
        "VALUES ('Hamilton', 'mh', '2020-01-01', 'DEV') RETURNING employee_id",
    )
    conn.execute("UPDATE hr.employees SET job_id = 'LEAD' WHERE employee_id = %s", (employee_id,))
    history = conn.execute(
        "SELECT start_date, job_id FROM hr.job_history WHERE employee_id = %s", (employee_id,)
    ).fetchall()
    assert history == [(datetime(2020, 1, 1), "DEV")]
