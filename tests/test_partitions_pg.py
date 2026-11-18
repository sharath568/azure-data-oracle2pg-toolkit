"""Generated partitions on a real PostgreSQL server (O2PG_TEST_DSN).

* The fixture DDL from map-types loads with ON_ERROR_STOP semantics.
* Every row in data.sql lands in the PostgreSQL partition that corresponds to
  the Oracle partition Oracle puts it in (expected_counts.json). The oracle CI
  job checks the same thing against real Oracle.
* The interval maintenance procedure creates the partitions Oracle would have
  created on insert.
* The lab's facts hold (timings are not asserted).
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from o2pg.ddl import suggest_ddl
from o2pg.inventory import Inventory
from o2pg.partitions import plan_partitions

pytestmark = pytest.mark.integration

DSN = os.environ.get("O2PG_TEST_DSN", "")
ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "partitions"

psycopg = pytest.importorskip("psycopg") if DSN else None


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    if not DSN:
        pytest.skip("O2PG_TEST_DSN is not set")
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    name = f"o2pg_part_{uuid.uuid4().hex[:8]}"
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
def loaded(database: str) -> Inventory:
    inv = Inventory.from_path(FIXTURES / "inventory.json")
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(suggest_ddl(inv))
        conn.execute("SET search_path = part")
        for line in (FIXTURES / "data.sql").read_text().splitlines():
            if line.strip() and not line.startswith("--"):
                conn.execute(line)
    return inv


def _leaf_counts(conn: Any, table: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT tableoid::regclass::text, count(*) FROM part.{table} GROUP BY 1"
    ).fetchall()
    return {name.split(".")[-1]: count for name, count in rows}


def test_rows_land_where_oracle_puts_them(database: str, loaded: Inventory) -> None:
    expected = json.loads((FIXTURES / "expected_counts.json").read_text())["tables"]
    plans = {p.table.lower(): p for p in plan_partitions(loaded)}
    mismatches = []
    with psycopg.connect(database) as conn:
        for table, want in expected.items():
            got = _leaf_counts(conn, table)
            if "_total" in want:
                if sum(got.values()) != want["_total"]:
                    mismatches.append(
                        f"{table}: {sum(got.values())} rows, expected {want['_total']}"
                    )
                continue
            names = {
                c.oracle_name: c.name for c in plans[table].all_children() if not c.partition_by
            }
            for oracle_name, count in want.items():
                if got.get(names[oracle_name], 0) != count:
                    mismatches.append(
                        f"{table}.{oracle_name} -> {names[oracle_name]}: "
                        f"{got.get(names[oracle_name], 0)}, expected {count}"
                    )
    assert mismatches == []


def test_null_range_key_goes_to_default(database: str, loaded: Inventory) -> None:
    with psycopg.connect(database) as conn:
        where = conn.execute(
            "SELECT tableoid::regclass::text FROM part.readings WHERE reading_date IS NULL"
        ).fetchone()[0]
    assert where == "part.readings_p_max"


def test_interval_procedure_creates_the_missing_months(database: str, loaded: Inventory) -> None:
    with psycopg.connect(database, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO part.events VALUES (100, LOCALTIMESTAMP, 'now')")
        conn.execute("CALL part.events_add_partitions()")
        conn.execute("INSERT INTO part.events VALUES (100, LOCALTIMESTAMP, 'now')")
        before = conn.execute(
            "SELECT count(*) FROM pg_inherits WHERE inhparent = 'part.events'::regclass"
        ).fetchone()[0]
        conn.execute("CALL part.events_add_partitions()")
        after = conn.execute(
            "SELECT count(*) FROM pg_inherits WHERE inhparent = 'part.events'::regclass"
        ).fetchone()[0]
    assert before == after  # running it again creates nothing new


def _lab() -> Any:
    spec = importlib.util.spec_from_file_location(
        "o2pg_lab", ROOT / "labs" / "partitioning" / "lab.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_lab_facts() -> None:
    if not DSN:
        pytest.skip("O2PG_TEST_DSN is not set")
    lab = _lab()
    lab.ROWS_PER_MONTH = 2_000
    results = lab.run_all(DSN)
    assert "no partition" in results["null_keys"]["maxvalue_error"]
    assert results["null_keys"]["default_accepts"][-1] == "nk_def_pmax"
    assert "must include all partitioning columns" in results["global_unique"]["refused"]
    assert results["global_unique"]["same_id_rows"] == 2
    pruning = {k: v for k, v in results["pruning"].items() if not k.startswith("_")}
    assert pruning["created >= '2025-03-01' AND created < '2025-04-01'"] == 1
    assert pruning["date_trunc('month', created) = '2025-03-01'"] == 12
    assert results["pruning"]["_runtime_subplans_removed"] == 11
    assert "would be violated" in results["default_cost"]["blocked_by_default_rows"]
    assert results["archive"]["rows_left_after_detach"] == 11 * 2_000
    assert results["row_movement"] == {"before": "rm_m01", "after": "rm_m02"}
    assert "Partitions scanned" in lab.markdown(results)
