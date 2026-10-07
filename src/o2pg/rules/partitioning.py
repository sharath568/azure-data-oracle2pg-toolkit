"""Partitioning rules (O2PG-PT-xxx): ALL_PART_TABLES and related indexes/constraints."""

from __future__ import annotations

from collections.abc import Iterator

from o2pg.inventory import Inventory, PartitionedTable
from o2pg.rules.base import Category, Hit, Severity, rule

_PT = Category.PARTITIONING


def _describe(part: PartitionedTable) -> str:
    keys = ", ".join(part.partition_keys) or (
        f"via {part.ref_constraint_name}" if part.ref_constraint_name else "?"
    )
    sub = (
        f", sub {part.subpartitioning_type}"
        if part.subpartitioning_type not in {"", "NONE"}
        else ""
    )
    count = f", {part.partition_count} partitions" if part.partition_count is not None else ""
    return f"{part.partitioning_type} ({keys}){sub}{count}"


@rule(
    "O2PG-PT-001",
    "Range, list and hash partitioned tables",
    _PT,
    Severity.LOW,
    1.0,
    description=(
        "PostgreSQL declarative partitioning supports RANGE, LIST and HASH. The syntax is "
        "different and each partition is created as its own table."
    ),
    remediation=(
        "CREATE TABLE ... PARTITION BY RANGE (key) and one CREATE TABLE ... PARTITION OF per "
        "partition. Add a DEFAULT partition where Oracle used MAXVALUE or DEFAULT. pg_partman "
        "automates creating and dropping time-based partitions."
    ),
    extensions=("pg_partman",),
)
def _basic_partitioning(inventory: Inventory) -> Iterator[Hit]:
    for part in inventory.part_tables:
        kind = part.partitioning_type.upper()
        if kind in {"RANGE", "LIST", "HASH"} and not part.interval:
            yield Hit(f"{part.owner}.{part.table_name}", "TABLE", _describe(part))


@rule(
    "O2PG-PT-002",
    "Interval partitioning",
    _PT,
    Severity.MEDIUM,
    1.5,
    description=(
        "Oracle creates interval partitions automatically on insert. PostgreSQL never "
        "creates partitions on its own; rows with no matching partition fail or land in "
        "DEFAULT."
    ),
    remediation=(
        "Use pg_partman with a premake count and run its maintenance from pg_cron (both are "
        "available on Azure Flexible Server). Keep a DEFAULT partition and alert if it ever "
        "receives rows."
    ),
    extensions=("pg_partman", "pg_cron"),
)
def _interval_partitioning(inventory: Inventory) -> Iterator[Hit]:
    for part in inventory.part_tables:
        if part.interval:
            yield Hit(
                f"{part.owner}.{part.table_name}",
                "TABLE",
                f"{_describe(part)}, INTERVAL {part.interval}",
            )


@rule(
    "O2PG-PT-003",
    "Reference and system partitioning",
    _PT,
    Severity.HIGH,
    3.0,
    description=(
        "REFERENCE partitioning inherits the parent's scheme through a foreign key; SYSTEM "
        "partitioning lets the application pick the partition. Neither exists in PostgreSQL."
    ),
    remediation=(
        "For reference partitioning, copy the parent's partition key into the child table, "
        "partition the child on it and include it in the foreign key. System partitioning "
        "usually becomes HASH or LIST partitioning on an explicit column."
    ),
)
def _reference_partitioning(inventory: Inventory) -> Iterator[Hit]:
    for part in inventory.part_tables:
        if part.partitioning_type.upper() in {"REFERENCE", "SYSTEM"}:
            yield Hit(f"{part.owner}.{part.table_name}", "TABLE", _describe(part))


@rule(
    "O2PG-PT-004",
    "Composite (sub)partitioning",
    _PT,
    Severity.MEDIUM,
    1.5,
    description=(
        "Sub-partitions become partitions of partitions in PostgreSQL. Every level is a "
        "real table, so the object count grows quickly."
    ),
    remediation=(
        "Create each partition with its own PARTITION BY clause. Check that the total number "
        "of leaf partitions stays reasonable (planning time grows with it), and consider "
        "dropping the second level if it existed only for storage management."
    ),
)
def _composite_partitioning(inventory: Inventory) -> Iterator[Hit]:
    for part in inventory.part_tables:
        if part.subpartitioning_type.upper() not in {"", "NONE"}:
            yield Hit(f"{part.owner}.{part.table_name}", "TABLE", _describe(part))


@rule(
    "O2PG-PT-005",
    "Primary or unique keys that do not include the partition key",
    _PT,
    Severity.HIGH,
    1.5,
    description=(
        "PostgreSQL requires every primary key and unique constraint on a partitioned table "
        "to include all partition key columns. Oracle does not (it uses global indexes), so "
        "these constraints cannot be created as-is."
    ),
    remediation=(
        "Either add the partition key to the constraint (which weakens uniqueness to "
        "'unique per partition key value'), enforce global uniqueness another way, or "
        "change the partition key. Decide this before moving data."
    ),
)
def _keys_without_partition_key(inventory: Inventory) -> Iterator[Hit]:
    for (owner, table), part in inventory.partitioning.items():
        keys = {k.upper() for k in part.partition_keys}
        if not keys:
            continue
        for con in inventory.constraints_by_table.get((owner, table), []):
            if con.type.upper() in {"P", "U"} and not keys <= {c.upper() for c in con.columns}:
                missing = ", ".join(sorted(keys - {c.upper() for c in con.columns}))
                yield Hit(f"{owner}.{table}.{con.name}", "CONSTRAINT", f"missing {missing}")


@rule(
    "O2PG-PT-006",
    "Global indexes on partitioned tables",
    _PT,
    Severity.MEDIUM,
    0.5,
    description=(
        "A non-partitioned index on a partitioned table is a global index. PostgreSQL only "
        "has local (per-partition) indexes."
    ),
    remediation=(
        "Recreate as a regular index on the parent, which PostgreSQL builds on every "
        "partition. Queries that relied on a global index without the partition key in the "
        "WHERE clause will scan every partition's index; test them."
    ),
)
def _global_indexes(inventory: Inventory) -> Iterator[Hit]:
    for owner, table in inventory.partitioning:
        for index in inventory.indexes_by_table.get((owner, table), []):
            if not index.partitioned:
                cols = ", ".join(index.columns)
                yield Hit(f"{index.owner}.{index.name}", "INDEX", f"on {table} ({cols})")
