"""Measure the Oracle-to-PostgreSQL partitioning traps on a real PostgreSQL server.

    python labs/partitioning/lab.py postgresql://postgres@localhost:5432/postgres

Creates a throwaway database, runs each experiment, prints the results as
Markdown, and drops the database. Every experiment returns a dict, so the test
suite (tests/test_partitions_pg.py) can assert the facts without the timings.

Experiments:
  null_keys        NULL range key: TO (MAXVALUE) rejects it, DEFAULT accepts it
  global_unique    a primary key without the partition key is refused; with it,
                   the same id can exist twice
  pruning          partitions scanned for four ways of filtering one month
  default_cost     a DEFAULT partition holding rows for a new range blocks it
  archive          DELETE one month vs DETACH PARTITION CONCURRENTLY + DROP
  row_movement     UPDATE of the key moves the row (Oracle needs ROW MOVEMENT)
"""

from __future__ import annotations

import re
import sys
import time
import uuid
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

MONTHS = 12
ROWS_PER_MONTH = 50_000


def _monthly(
    conn: psycopg.Connection, table: str, months: int = MONTHS, default: bool = False
) -> None:
    conn.execute(
        f"CREATE TABLE {table} (id bigint NOT NULL, created timestamp(0) NOT NULL, "
        f"amount numeric(10,2)) PARTITION BY RANGE (created)"
    )
    for m in range(months):
        conn.execute(
            f"CREATE TABLE {table}_m{m + 1:02d} PARTITION OF {table} FOR VALUES "
            f"FROM ('2025-01-01'::timestamp + interval '{m} month') "
            f"TO ('2025-01-01'::timestamp + interval '{m + 1} month')"
        )
    if default:
        conn.execute(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT")


def _load(
    conn: psycopg.Connection, table: str, months: int = MONTHS, per_month: int | None = None
) -> None:
    per_month = per_month or ROWS_PER_MONTH
    conn.execute(
        f"INSERT INTO {table} SELECT g, '2025-01-01'::timestamp + (g % {months}) * interval '1 month' "
        f"+ (g % 28) * interval '1 day', g % 1000 FROM generate_series(1, {months * per_month}) g"
    )
    conn.execute(f"ANALYZE {table}")


def null_keys(conn: psycopg.Connection) -> dict[str, Any]:
    """Oracle puts a NULL range key in the MAXVALUE partition. What does PostgreSQL do?"""
    conn.execute("CREATE TABLE nk_max (k timestamp(0)) PARTITION BY RANGE (k)")
    conn.execute(
        "CREATE TABLE nk_max_p1 PARTITION OF nk_max FOR VALUES FROM (MINVALUE) TO ('2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE nk_max_pmax PARTITION OF nk_max FOR VALUES FROM ('2026-01-01') TO (MAXVALUE)"
    )
    conn.execute("CREATE TABLE nk_def (k timestamp(0)) PARTITION BY RANGE (k)")
    conn.execute(
        "CREATE TABLE nk_def_p1 PARTITION OF nk_def FOR VALUES FROM (MINVALUE) TO ('2026-01-01')"
    )
    conn.execute("CREATE TABLE nk_def_pmax PARTITION OF nk_def DEFAULT")
    try:
        with conn.transaction():
            conn.execute("INSERT INTO nk_max VALUES (NULL)")
        maxvalue_error = None
    except psycopg.Error as exc:
        maxvalue_error = exc.diag.message_primary
    conn.execute("INSERT INTO nk_def VALUES (NULL), ('2030-01-01')")
    landed = [
        r[0]
        for r in conn.execute("SELECT tableoid::regclass::text FROM nk_def ORDER BY k NULLS LAST")
    ]
    return {"maxvalue_error": maxvalue_error, "default_accepts": landed}


def global_unique(conn: psycopg.Connection) -> dict[str, Any]:
    """Oracle's global unique index on order_id. PostgreSQL?"""
    try:
        with conn.transaction():
            conn.execute(
                "CREATE TABLE gu_bad (order_id bigint PRIMARY KEY, order_date timestamp(0) NOT NULL) "
                "PARTITION BY RANGE (order_date)"
            )
        refused = None
    except psycopg.Error as exc:
        refused = exc.diag.message_primary
    conn.execute(
        "CREATE TABLE gu (order_id bigint, order_date timestamp(0) NOT NULL, "
        "PRIMARY KEY (order_id, order_date)) PARTITION BY RANGE (order_date)"
    )
    conn.execute(
        "CREATE TABLE gu_2025 PARTITION OF gu FOR VALUES FROM ('2025-01-01') TO ('2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE gu_2026 PARTITION OF gu FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')"
    )
    conn.execute("INSERT INTO gu VALUES (42, '2025-06-01'), (42, '2026-06-01')")
    duplicates = conn.execute("SELECT count(*) FROM gu WHERE order_id = 42").fetchone()[0]
    return {"refused": refused, "same_id_rows": duplicates}


def _scanned(conn: psycopg.Connection, query: str, params: tuple = ()) -> tuple[int, int]:
    """(partitions in the plan, subplans removed at run time) from EXPLAIN ANALYZE."""
    plan = "\n".join(
        r[0] for r in conn.execute(f"EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) {query}", params)
    )
    scanned = len(re.findall(r"on pr_m\d+", plan))
    removed = sum(int(n) for n in re.findall(r"Subplans Removed: (\d+)", plan))
    return scanned, removed


def pruning(conn: psycopg.Connection) -> dict[str, Any]:
    """How many of 12 monthly partitions each way of asking for March touches."""
    _monthly(conn, "pr")
    _load(conn, "pr", per_month=2_000)
    queries = {
        "created >= '2025-03-01' AND created < '2025-04-01'": "SELECT count(*) FROM pr WHERE created >= '2025-03-01' AND created < '2025-04-01'",
        "date_trunc('month', created) = '2025-03-01'": "SELECT count(*) FROM pr WHERE date_trunc('month', created) = '2025-03-01'",
        "created::date BETWEEN '2025-03-01' AND '2025-03-31'": "SELECT count(*) FROM pr WHERE created::date BETWEEN '2025-03-01' AND '2025-03-31'",
        "to_char(created, 'YYYY-MM') = '2025-03'": "SELECT count(*) FROM pr WHERE to_char(created, 'YYYY-MM') = '2025-03'",
    }
    out = {label: _scanned(conn, q)[0] for label, q in queries.items()}
    conn.execute("SET plan_cache_mode = force_generic_plan")
    conn.execute(
        "PREPARE month_q(timestamp) AS SELECT count(*) FROM pr "
        "WHERE created >= $1 AND created < $1 + interval '1 month'"
    )
    scanned, removed = _scanned(conn, "EXECUTE month_q('2025-03-01')")
    conn.execute("DEALLOCATE month_q")
    conn.execute("RESET plan_cache_mode")
    out[f"prepared statement, generic plan ({removed} pruned at run time)"] = scanned
    out["_runtime_subplans_removed"] = removed
    return out


def default_cost(conn: psycopg.Connection) -> dict[str, Any]:
    """Adding a partition when a DEFAULT partition exists."""
    _monthly(conn, "dc", months=2, default=True)
    conn.execute("INSERT INTO dc VALUES (1, '2025-03-15', 1)")  # lands in DEFAULT
    try:
        with conn.transaction():
            conn.execute(
                "CREATE TABLE dc_m03 PARTITION OF dc FOR VALUES FROM ('2025-03-01') TO ('2025-04-01')"
            )
        blocked = None
    except psycopg.Error as exc:
        blocked = exc.diag.message_primary
    conn.execute("DELETE FROM dc")
    conn.execute(
        "INSERT INTO dc SELECT g, '2030-01-01'::timestamp + (g % 1000) * interval '1 hour', 1 "
        "FROM generate_series(1, 300000) g"
    )
    start = time.perf_counter()
    conn.execute(
        "CREATE TABLE dc_m04 PARTITION OF dc FOR VALUES FROM ('2025-04-01') TO ('2025-05-01')"
    )
    with_rows_ms = (time.perf_counter() - start) * 1000
    conn.execute("CREATE TABLE dc2 (id bigint, created timestamp(0)) PARTITION BY RANGE (created)")
    conn.execute("CREATE TABLE dc2_default PARTITION OF dc2 DEFAULT")
    start = time.perf_counter()
    conn.execute(
        "CREATE TABLE dc2_m04 PARTITION OF dc2 FOR VALUES FROM ('2025-04-01') TO ('2025-05-01')"
    )
    empty_ms = (time.perf_counter() - start) * 1000
    return {
        "blocked_by_default_rows": blocked,
        "add_partition_ms_default_300k_rows": round(with_rows_ms, 1),
        "add_partition_ms_default_empty": round(empty_ms, 1),
    }


def archive(conn: psycopg.Connection, admin: str) -> dict[str, Any]:
    """Removing the oldest month: DELETE vs DETACH CONCURRENTLY + DROP."""
    _monthly(conn, "ad")
    _monthly(conn, "ar")
    _load(conn, "ad")
    _load(conn, "ar")
    start = time.perf_counter()
    deleted = conn.execute(
        "DELETE FROM ad WHERE created >= '2025-01-01' AND created < '2025-02-01'"
    ).rowcount
    delete_ms = (time.perf_counter() - start) * 1000
    # DETACH ... CONCURRENTLY cannot run inside a transaction block.
    with psycopg.connect(admin, autocommit=True) as auto:
        start = time.perf_counter()
        auto.execute("ALTER TABLE ar DETACH PARTITION ar_m01 CONCURRENTLY")
        auto.execute("DROP TABLE ar_m01")
        detach_ms = (time.perf_counter() - start) * 1000
    remaining = conn.execute("SELECT count(*) FROM ar").fetchone()[0]
    return {
        "rows_removed": deleted,
        "delete_ms": round(delete_ms, 1),
        "detach_drop_ms": round(detach_ms, 1),
        "rows_left_after_detach": remaining,
    }


def row_movement(conn: psycopg.Connection) -> dict[str, Any]:
    """UPDATE that changes the partition key."""
    _monthly(conn, "rm", months=2)
    conn.execute("INSERT INTO rm VALUES (1, '2025-01-10', 1)")
    before = conn.execute("SELECT tableoid::regclass::text FROM rm").fetchone()[0]
    conn.execute("UPDATE rm SET created = '2025-02-10' WHERE id = 1")
    after = conn.execute("SELECT tableoid::regclass::text FROM rm").fetchone()[0]
    return {"before": before, "after": after}


def run_all(admin_dsn: str) -> dict[str, dict[str, Any]]:
    """Run every experiment in a throwaway database."""
    name = f"o2pg_lab_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = make_conninfo(admin_dsn, dbname=name)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            results = {
                "null_keys": null_keys(conn),
                "global_unique": global_unique(conn),
                "pruning": pruning(conn),
                "default_cost": default_cost(conn),
                "archive": archive(conn, dsn),
                "row_movement": row_movement(conn),
            }
            results["_server"] = {"version": version}
            return results
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def markdown(results: dict[str, dict[str, Any]]) -> str:
    """Results as Markdown."""
    r = results
    pr = {k: v for k, v in r["pruning"].items() if not k.startswith("_")}
    lines = [
        f"Measured on PostgreSQL {r['_server']['version']}.",
        "",
        "| Experiment | Result |",
        "|---|---|",
        f"| NULL key, last partition `TO (MAXVALUE)` | rejected: {r['null_keys']['maxvalue_error']} |",
        f"| NULL key, last partition `DEFAULT` | accepted into {r['null_keys']['default_accepts'][-1]} |",
        f"| `PRIMARY KEY (order_id)` on a table partitioned by date | refused: {r['global_unique']['refused']} |",
        f"| `PRIMARY KEY (order_id, order_date)` | {r['global_unique']['same_id_rows']} rows with order_id 42 accepted |",
        f"| New partition while DEFAULT holds a row for its range | {r['default_cost']['blocked_by_default_rows']} |",
        f"| New partition, DEFAULT has 300k unrelated rows / is empty | {r['default_cost']['add_partition_ms_default_300k_rows']} ms / {r['default_cost']['add_partition_ms_default_empty']} ms |",
        f"| Remove one month ({r['archive']['rows_removed']:,} rows): DELETE | {r['archive']['delete_ms']} ms (and dead rows to vacuum) |",
        f"| Remove one month: DETACH CONCURRENTLY + DROP | {r['archive']['detach_drop_ms']} ms |",
        f"| UPDATE moves the key to the next month | row moved from {r['row_movement']['before']} to {r['row_movement']['after']} |",
        "",
        f"Partitions scanned (of {MONTHS}) when asking for March:",
        "",
        "| Filter | Partitions scanned |",
        "|---|---|",
        *[f"| `{label}` | {count} |" for label, count in pr.items()],
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python labs/partitioning/lab.py POSTGRES_DSN")
    print(markdown(run_all(sys.argv[1])))
