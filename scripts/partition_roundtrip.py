"""Oracle -> collector -> o2pg -> PostgreSQL round trip for partitioned tables.

Used by the ``partitions`` CI job:

    python scripts/partition_roundtrip.py setup --oracle-dsn part/pw@localhost:1521/FREEPDB1
    # ... run collector/collect.sql with SQL*Plus as PART, writing collected.json ...
    python scripts/partition_roundtrip.py check collected.json \\
        --oracle-dsn part/pw@localhost:1521/FREEPDB1 --pg-dsn postgresql://...

``check`` does four things and exits 1 if any fails:

1. The collected partition structure matches tests/fixtures/partitions/inventory.json
   (the hand-written inventory the unit tests use), position by position:
   same HIGH_VALUE text, subpartitions, keys and interval. System-generated
   interval partition names (SYS_Pnnn) are not compared.
2. ``o2pg map-types`` output for the collected inventory loads into a fresh
   PostgreSQL database.
3. Every row is copied from Oracle into PostgreSQL.
4. Rows per partition match: Oracle ``SELECT COUNT(*) FROM t PARTITION (p)``
   (or SUBPARTITION) against the PostgreSQL partition that o2pg created for p.
   Hash and reference tables compare totals only.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "partitions"
sys.path.insert(0, str(ROOT / "src"))

from o2pg.ddl import suggest_ddl  # noqa: E402
from o2pg.equiv import data_statements, oracle_script_statements  # noqa: E402
from o2pg.inventory import Inventory  # noqa: E402
from o2pg.partitions import plan_partitions, report_markdown  # noqa: E402


def _oracle(dsn: str):
    import oracledb

    oracledb.defaults.fetch_decimals = True
    user, _, rest = dsn.partition("/")
    password, _, target = rest.rpartition("@")
    return oracledb.connect(user=user, password=password, dsn=target)


def setup(args: argparse.Namespace) -> int:
    """Create the fixture tables in Oracle and load the rows."""
    with _oracle(args.oracle_dsn) as conn, conn.cursor() as cur:
        for statement in oracle_script_statements((FIXTURES / "oracle_schema.sql").read_text()):
            cur.execute(statement)
        for statement in data_statements((FIXTURES / "data.sql").read_text()):
            cur.execute(statement)
        conn.commit()
        cur.execute(
            "SELECT table_name, partition_name FROM user_tab_partitions ORDER BY 1, partition_position"
        )
        for table, partition in cur.fetchall():
            print(f"oracle: {table}.{partition}")
    return 0


def _structure(inv: Inventory) -> dict:
    out = {}
    subs = {}
    for s in inv.subpartitions:
        subs.setdefault((s.table_name, s.partition_name), []).append(
            (s.subpartition_position, s.subpartition_name, " ".join(s.high_value.split()))
        )
    for part in inv.part_tables:
        parts = sorted(
            (p for p in inv.partitions if p.table_name == part.table_name),
            key=lambda p: p.partition_position or 0,
        )
        out[part.table_name] = {
            "type": part.partitioning_type,
            "subtype": part.subpartitioning_type,
            "keys": list(part.partition_keys),
            "subkeys": list(part.subpartition_keys),
            "interval": " ".join((part.interval or "").replace(" ", "").split()),
            "partitions": [
                [
                    p.partition_position,
                    None if p.partition_name.startswith("SYS_P") else p.partition_name,
                    " ".join(p.high_value.split()),
                    sorted(subs.get((p.table_name, p.partition_name), [])),
                ]
                for p in parts
            ],
        }
    return out


def check(args: argparse.Namespace) -> int:
    """Compare structure, load into PostgreSQL, copy rows, compare counts."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    failures: list[str] = []
    collected = Inventory.from_path(args.inventory)
    fixture = Inventory.from_path(FIXTURES / "inventory.json")

    got, want = _structure(collected), _structure(fixture)
    for table in sorted(set(got) | set(want)):
        if got.get(table) != want.get(table):
            failures.append(
                f"structure of {table} differs from the fixture:\n  collected {got.get(table)}\n"
                f"  fixture   {want.get(table)}"
            )
    print(report_markdown(collected))

    name = f"o2pg_rt_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(args.pg_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    pg_dsn = make_conninfo(args.pg_dsn, dbname=name)
    plans = {p.table: p for p in plan_partitions(collected)}
    try:
        with psycopg.connect(pg_dsn, autocommit=True) as pg, _oracle(args.oracle_dsn) as ora:
            try:
                pg.execute(suggest_ddl(collected))
            except psycopg.Error as exc:
                failures.append(f"map-types DDL did not load: {exc}")
                raise SystemExit(_finish(failures)) from exc
            cur = ora.cursor()
            for table in collected.tables:
                columns = [c.name for c in collected.columns_by_table[(table.owner, table.name)]]
                cur.execute(f"SELECT {', '.join(columns)} FROM {table.name}")
                rows = cur.fetchall()
                target = f"{table.owner.lower()}.{table.name.lower()}"
                placeholders = ", ".join(["%s"] * len(columns))
                with pg.cursor() as pcur:
                    pcur.executemany(
                        f"INSERT INTO {target} ({', '.join(c.lower() for c in columns)}) "
                        f"VALUES ({placeholders})",
                        rows,
                    )
                print(f"copied {len(rows)} rows into {target}")
            for table, plan in plans.items():
                target = f"{plan.owner.lower()}.{table.lower()}"
                pg_counts = {
                    n.split(".")[-1]: c
                    for n, c in pg.execute(
                        f"SELECT tableoid::regclass::text, count(*) FROM {target} GROUP BY 1"
                    ).fetchall()
                }
                if plan.pg_kind in {"", "HASH"}:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    total = cur.fetchone()[0]
                    if sum(pg_counts.values()) != total:
                        failures.append(f"{table}: {sum(pg_counts.values())} rows, Oracle {total}")
                    continue
                for child in plan.children:
                    leaves = child.children or [child]
                    for leaf in leaves:
                        clause = "SUBPARTITION" if child.children else "PARTITION"
                        cur.execute(f'SELECT COUNT(*) FROM {table} {clause} ("{leaf.oracle_name}")')
                        oracle_count = cur.fetchone()[0]
                        pg_count = pg_counts.get(leaf.name, 0)
                        mark = "ok" if oracle_count == pg_count else "DIFFERENT"
                        print(
                            f"{table}.{leaf.oracle_name}: Oracle {oracle_count}, {leaf.name} {pg_count} {mark}"
                        )
                        if oracle_count != pg_count:
                            failures.append(
                                f"{table}.{leaf.oracle_name}: Oracle {oracle_count}, PostgreSQL {pg_count}"
                            )
    finally:
        with psycopg.connect(args.pg_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
    return _finish(failures)


def _finish(failures: list[str]) -> int:
    if failures:
        print("\nFAILED:\n" + "\n".join(failures))
        return 1
    print("\nround trip ok: structure, DDL, rows and per-partition counts match")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_setup = sub.add_parser("setup")
    p_setup.add_argument("--oracle-dsn", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("inventory", type=Path)
    p_check.add_argument("--oracle-dsn", required=True)
    p_check.add_argument("--pg-dsn", required=True)
    args = parser.parse_args()
    return setup(args) if args.command == "setup" else check(args)


if __name__ == "__main__":
    raise SystemExit(main())
