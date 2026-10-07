"""Performance rules (O2PG-PF-xxx): physical design that does not carry over."""

from __future__ import annotations

from collections.abc import Iterator

from o2pg.inventory import Inventory
from o2pg.rules.base import Category, Hit, Severity, rule

_PF = Category.PERFORMANCE

LARGE_TABLE_ROWS = 50_000_000


def _index_hits(inventory: Inventory, marker: str) -> Iterator[Hit]:
    for index in inventory.indexes:
        if marker in index.index_type.upper():
            cols = ", ".join(index.columns)
            yield Hit(
                f"{index.owner}.{index.name}",
                "INDEX",
                f"{index.index_type} on {index.table_name} ({cols})",
            )


@rule(
    "O2PG-PF-001",
    "Bitmap indexes",
    _PF,
    Severity.MEDIUM,
    0.5,
    description=(
        "PostgreSQL has no stored bitmap indexes. It builds bitmaps at query time from "
        "ordinary B-tree indexes and combines them, which covers most of the same queries."
    ),
    remediation=(
        "Create B-tree indexes on the same columns, or a partial index for the selective "
        "values. For very large append-only tables BRIN is worth testing. Verify the star "
        "queries that relied on bitmap AND/OR."
    ),
)
def _bitmap(inventory: Inventory) -> Iterator[Hit]:
    return _index_hits(inventory, "BITMAP")


@rule(
    "O2PG-PF-002",
    "Function-based indexes",
    _PF,
    Severity.LOW,
    0.25,
    description=(
        "PostgreSQL expression indexes are the equivalent, but every function in the "
        "expression must be IMMUTABLE and the query must use the same expression."
    ),
    remediation=(
        "CREATE INDEX ... ((upper(col))). Translate the expression and mark custom functions "
        "IMMUTABLE only if they truly are."
    ),
)
def _function_based(inventory: Inventory) -> Iterator[Hit]:
    return _index_hits(inventory, "FUNCTION-BASED")


@rule(
    "O2PG-PF-003",
    "Reverse key indexes",
    _PF,
    Severity.LOW,
    0.25,
    description=(
        "Reverse key indexes spread sequential keys across leaf blocks to reduce RAC "
        "contention. PostgreSQL has no reverse key option and no RAC block contention."
    ),
    remediation=(
        "Use a plain B-tree index. Range scans on the column start working again, which "
        "may change plans for the better."
    ),
)
def _reverse_key(inventory: Inventory) -> Iterator[Hit]:
    return _index_hits(inventory, "/REV")


@rule(
    "O2PG-PF-004",
    "Index-organized tables",
    _PF,
    Severity.MEDIUM,
    0.75,
    description=(
        "IOTs store rows in primary key order inside the index. PostgreSQL tables are heaps; "
        "CLUSTER reorders once but is not maintained."
    ),
    remediation=(
        "Create a heap table with the primary key. For small lookup tables that is all. "
        "For range-scanned IOTs, add INCLUDE columns to the primary key index to get "
        "index-only scans, and keep autovacuum healthy so the visibility map stays current."
    ),
)
def _iots(inventory: Inventory) -> Iterator[Hit]:
    for table in inventory.tables:
        if table.iot_type.upper() == "IOT":
            yield Hit(table.qualified, "TABLE", "ORGANIZATION INDEX")


@rule(
    "O2PG-PF-005",
    "Domain indexes (Oracle Text and others)",
    _PF,
    Severity.HIGH,
    2.0,
    description=(
        "Domain indexes such as Oracle Text CONTEXT/CTXCAT have no equivalent, and the "
        "CONTAINS/CATSEARCH queries that use them must be rewritten."
    ),
    remediation=(
        "Use PostgreSQL full text search: a tsvector generated column with a GIN index and "
        "@@ to_tsquery(...). Check language configuration and ranking against the Oracle "
        "results. Spatial domain indexes: see the SDO_GEOMETRY rule."
    ),
)
def _domain_indexes(inventory: Inventory) -> Iterator[Hit]:
    for index in inventory.indexes:
        if index.index_type.upper() == "DOMAIN":
            yield Hit(
                f"{index.owner}.{index.name}",
                "INDEX",
                f"{index.ityp_name or 'DOMAIN'} on {index.table_name}",
            )


@rule(
    "O2PG-PF-006",
    "Foreign keys without a supporting index",
    _PF,
    Severity.LOW,
    0.1,
    description=(
        "Neither database indexes foreign key columns automatically. Unindexed foreign keys "
        "make parent deletes and updates scan the child table, which hurts more under "
        "PostgreSQL's MVCC."
    ),
    remediation="Create an index whose leading columns are the foreign key columns.",
)
def _unindexed_fks(inventory: Inventory) -> Iterator[Hit]:
    for con in inventory.constraints:
        if con.type.upper() != "R" or not con.columns:
            continue
        wanted = [c.upper() for c in con.columns]
        indexes = inventory.indexes_by_table.get((con.owner, con.table_name), [])
        covered = any(
            sorted(c.upper() for c in index.columns[: len(wanted)]) == sorted(wanted)
            for index in indexes
        )
        if not covered:
            yield Hit(
                f"{con.owner}.{con.table_name}.{con.name}",
                "CONSTRAINT",
                f"({', '.join(con.columns)})",
            )


@rule(
    "O2PG-PF-007",
    "Compressed tables",
    _PF,
    Severity.LOW,
    0.25,
    description=(
        "Basic, OLTP/advanced and hybrid columnar compression have no PostgreSQL equivalent; "
        "only large values are compressed (TOAST). Expect the data to grow on disk."
    ),
    remediation=(
        "Size Azure storage from uncompressed volumes, not Oracle segment sizes. Consider "
        "lz4 TOAST compression (default_toast_compression) for wide text columns."
    ),
)
def _compressed(inventory: Inventory) -> Iterator[Hit]:
    for table in inventory.tables:
        if table.compression.upper() == "ENABLED":
            yield Hit(table.qualified, "TABLE", table.compress_for or "compressed")


@rule(
    "O2PG-PF-008",
    "Very large tables",
    _PF,
    Severity.MEDIUM,
    1.0,
    description=(
        f"Tables with more than {LARGE_TABLE_ROWS:,} rows according to optimizer "
        "statistics. Load time, index builds and validation for these drive the cutover "
        "window."
    ),
    remediation=(
        "Plan these separately: parallel load by key range or partition, create indexes "
        "after the load, and consider partitioning in PostgreSQL even if Oracle did not."
    ),
)
def _large_tables(inventory: Inventory) -> Iterator[Hit]:
    for table in inventory.tables:
        if table.num_rows is not None and table.num_rows > LARGE_TABLE_ROWS:
            yield Hit(table.qualified, "TABLE", f"{table.num_rows:,} rows")


@rule(
    "O2PG-PF-009",
    "Clustered tables",
    _PF,
    Severity.MEDIUM,
    0.5,
    description=(
        "Index and hash clusters store rows of several tables together. PostgreSQL has no "
        "clusters in this sense."
    ),
    remediation="Create ordinary tables with the cluster key indexed on each.",
)
def _clustered(inventory: Inventory) -> Iterator[Hit]:
    for table in inventory.tables:
        if table.cluster_name:
            yield Hit(table.qualified, "TABLE", f"cluster {table.cluster_name}")
