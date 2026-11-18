"""Oracle partitions to PostgreSQL declarative partitions (``map-types`` and ``o2pg partitions``).

The collector (0.4+) records every partition and subpartition with its
``HIGH_VALUE`` text. This module turns those into ``CREATE TABLE ... PARTITION
OF`` statements, and records every place where the two databases behave
differently so a person can decide:

* **NULL partition keys.** Oracle puts a row whose range key is NULL in the
  ``MAXVALUE`` partition. PostgreSQL range partitions never accept NULL; only a
  DEFAULT partition does. When a key column is nullable, the ``MAXVALUE``
  partition becomes the DEFAULT partition, which accepts the same rows.
* **Interval partitioning.** Oracle creates partitions on insert. PostgreSQL
  never does, so interval tables get a small procedure that creates partitions
  ahead of time (schedule it with pg_cron), or use pg_partman.
* **Global indexes.** A unique index that does not include the partition key
  cannot be enforced across partitions in PostgreSQL.
* **Reference, system and multi-column list partitioning** have no PostgreSQL
  equivalent and are reported, not converted.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field

from o2pg.ddl import ident, qualified
from o2pg.inventory import (
    Column,
    Index,
    Inventory,
    PartitionedTable,
    TablePartition,
    TableSubpartition,
)
from o2pg.typemap import base_type

MAX_IDENT = 63

TODO = "todo"
CHANGED = "changed"
NOTE = "note"

# --------------------------------------------------------------------------------------
# HIGH_VALUE parsing
# --------------------------------------------------------------------------------------

LITERAL = "literal"
MAXVALUE = "maxvalue"
DEFAULT = "default"
NULL = "null"
UNKNOWN = "unknown"

_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_STRING = re.compile(r"^[nN]?'((?:[^']|'')*)'$")
_TO_DATE = re.compile(r"^TO_DATE\s*\(\s*'([^']*)'", re.IGNORECASE)
_TIMESTAMP = re.compile(r"^TIMESTAMP\s*'([^']*)'$", re.IGNORECASE)
_DATE_TEXT = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?(\.\d+)?\s*$")


@dataclass(frozen=True)
class Value:
    """One bound value: a PostgreSQL literal, MAXVALUE, DEFAULT, NULL, or not understood."""

    kind: str
    sql: str = ""
    raw: str = ""


def split_top_level(text: str) -> list[str]:
    """Split on commas outside quotes and parentheses."""
    parts: list[str] = []
    depth, quoted, current = 0, False, []
    for ch in text:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def parse_value(token: str) -> Value:
    """Parse one HIGH_VALUE item."""
    text = token.strip()
    upper = text.upper()
    if upper == "MAXVALUE":
        return Value(MAXVALUE, raw=text)
    if upper == "DEFAULT":
        return Value(DEFAULT, raw=text)
    if upper == "NULL":
        return Value(NULL, "NULL", raw=text)
    if _NUMBER.match(text):
        return Value(LITERAL, text.lstrip("+"), raw=text)
    string = _STRING.match(text)
    if string:
        return Value(LITERAL, "'" + string.group(1) + "'", raw=text)
    for pattern in (_TO_DATE, _TIMESTAMP):
        match = pattern.match(text)
        if match:
            date = _DATE_TEXT.match(match.group(1))
            if date:  # years BC (a leading '-') are not supported and fall through
                time = date.group(2) or "00:00:00"
                return Value(LITERAL, f"'{date.group(1)} {time}'", raw=text)
    return Value(UNKNOWN, raw=text)


def parse_range_bound(text: str) -> list[Value]:
    """One value per range key column."""
    return [parse_value(t) for t in split_top_level(text)]


def parse_list_values(text: str) -> list[list[Value]]:
    """Each list item as a tuple of values (one value per key column)."""
    items = []
    for token in split_top_level(text):
        stripped = token.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            items.append([parse_value(t) for t in split_top_level(stripped[1:-1])])
        else:
            items.append([parse_value(stripped)])
    return items


def interval_to_pg(expression: str) -> tuple[str, str] | None:
    """Oracle INTERVAL text to ``("interval", "1 month")``, or ``("number", "1000")``."""
    text = " ".join(expression.split())
    match = re.match(
        r"^NUMTO(?:YM|DS)INTERVAL\s*\(\s*(\d+)\s*,\s*'(\w+)'\s*\)$", text, re.IGNORECASE
    )
    if match:
        unit = match.group(2).lower()
        if unit in {"year", "month", "day", "hour", "minute", "second"}:
            return "interval", f"{match.group(1)} {unit}"
        return None
    match = re.match(
        r"^INTERVAL\s*'(\d+)'\s*(YEAR|MONTH|DAY|HOUR|MINUTE|SECOND)\b", text, re.IGNORECASE
    )
    if match:
        return "interval", f"{match.group(1)} {match.group(2).lower()}"
    if _NUMBER.match(text):
        return "number", text
    return None


# --------------------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """Something a person should know or decide about one table."""

    level: str
    text: str


@dataclass
class Child:
    """One PostgreSQL partition (and its own partitions, for composite partitioning)."""

    name: str
    oracle_name: str
    bound: str
    partition_by: str = ""
    children: list[Child] = field(default_factory=list)
    skipped: str = ""


@dataclass
class TablePlan:
    """What happens to one Oracle partitioned table."""

    owner: str
    table: str
    oracle_kind: str
    oracle_sub_kind: str
    keys: tuple[str, ...]
    sub_keys: tuple[str, ...]
    pg_kind: str = ""
    children: list[Child] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    interval: tuple[str, str] | None = None
    oracle_partitions: int = 0
    oracle_subpartitions: int = 0

    @property
    def qualified(self) -> str:
        """``schema.table`` in PostgreSQL form."""
        return qualified(self.owner, self.table)

    @property
    def converted(self) -> bool:
        """True when PostgreSQL partitions the table the same way."""
        return bool(self.pg_kind)

    def add(self, level: str, text: str) -> None:
        """Record a finding."""
        self.findings.append(Finding(level, text))

    def all_children(self) -> list[Child]:
        """Every child and grandchild."""
        out = []
        for child in self.children:
            out.append(child)
            out.extend(child.children)
        return out


def child_name(table: str, partition: str) -> str:
    """PostgreSQL table name for a partition: ``orders_p2024_01``, kept within 63 bytes."""
    base = re.sub(r"[^a-z0-9_]", "_", f"{table}_{partition}".lower())
    if len(base) <= MAX_IDENT:
        return base
    digest = hashlib.sha1(base.encode()).hexdigest()[:8]
    return base[: MAX_IDENT - 9] + "_" + digest


def _partman_name(table: str, lower: Value) -> str | None:
    """``orders_p20240101`` for a date lower bound (pg_partman 5 naming)."""
    match = re.match(r"^'(\d{4})-(\d{2})-(\d{2})", lower.sql) if lower.kind == LITERAL else None
    if not match:
        return None
    return child_name(table, "p" + "".join(match.groups()))


def _join(values: list[Value]) -> str:
    return ", ".join(
        v.sql if v.kind == LITERAL or v.kind == NULL else v.kind.upper() for v in values
    )


class _Planner:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self.partitions: dict[tuple[str, str], list[TablePartition]] = defaultdict(list)
        for p in inventory.partitions:
            self.partitions[(p.owner, p.table_name)].append(p)
        self.subpartitions: dict[tuple[str, str, str], list[TableSubpartition]] = defaultdict(list)
        for s in inventory.subpartitions:
            self.subpartitions[(s.owner, s.table_name, s.partition_name)].append(s)
        self.columns = inventory.columns_by_table
        self.part_indexes = {(i.owner, i.index_name): i for i in inventory.part_indexes}

    def column(self, owner: str, table: str, name: str) -> Column | None:
        for col in self.columns.get((owner, table), []):
            if col.name.upper() == name.upper():
                return col
        return None

    def nullable(self, owner: str, table: str, keys: tuple[str, ...]) -> list[str]:
        out = []
        for key in keys:
            col = self.column(owner, table, key)
            if col is None or col.nullable:
                out.append(key)
        return out

    # -- per table -----------------------------------------------------------------

    def plan(self, part: PartitionedTable) -> TablePlan:
        kind = part.partitioning_type.upper()
        sub_kind = (part.subpartitioning_type or "NONE").upper()
        plan = TablePlan(
            part.owner,
            part.table_name,
            kind,
            sub_kind,
            tuple(part.partition_keys),
            tuple(part.subpartition_keys),
        )
        partitions = sorted(
            self.partitions.get((part.owner, part.table_name), []),
            key=lambda p: p.partition_position or 0,
        )
        plan.oracle_partitions = len(partitions)
        plan.oracle_subpartitions = sum(
            len(self.subpartitions.get((part.owner, part.table_name, p.partition_name), []))
            for p in partitions
        )
        if kind == "REFERENCE":
            plan.add(
                TODO,
                f"Reference partitioning (through {part.ref_constraint_name or 'a foreign key'}) "
                "has no PostgreSQL equivalent. Either copy the parent's partition key into this "
                "table and partition it the same way (so old parents and their children can be "
                "dropped together), or leave it unpartitioned. It is created unpartitioned.",
            )
            return plan
        if kind not in {"RANGE", "LIST", "HASH"}:
            plan.add(
                TODO, f"{kind} partitioning has no PostgreSQL equivalent; table left unpartitioned."
            )
            return plan
        if kind == "LIST" and len(plan.keys) > 1:
            plan.add(
                TODO,
                "Multi-column LIST partitioning is not supported by PostgreSQL. Partition by the "
                "first column and sub-partition by the second, or use RANGE.",
            )
            return plan
        if not plan.keys:
            plan.add(TODO, "No partition key columns were collected.")
            return plan
        plan.pg_kind = kind
        if part.interval:
            plan.interval = interval_to_pg(part.interval)
            what = f"every {plan.interval[1]}" if plan.interval else f"by {part.interval}"
            plan.add(
                CHANGED,
                f"Interval partitioning ({what}): Oracle creates partitions on insert, "
                "PostgreSQL never does. A row past the last partition fails unless partitions "
                "are created ahead of time: map-types adds a procedure, "
                f"{plan.owner.lower()}.{plan.table.lower()}_add_partitions, to run on a "
                "schedule (pg_cron), or use pg_partman.",
            )
        if not partitions:
            plan.add(
                TODO,
                "No partitions in the inventory (collected before o2pg 0.4?). Re-run "
                "collector/collect.sql to get partition bounds; only the parent is created.",
            )
            return plan
        sub_plan = sub_kind if sub_kind in {"RANGE", "LIST", "HASH"} else ""
        if sub_kind not in {"NONE", ""} and not sub_plan:
            plan.add(
                TODO, f"{sub_kind} subpartitioning is not supported; partitions are not subdivided."
            )
        if sub_plan == "LIST" and len(plan.sub_keys) > 1:
            plan.add(
                TODO,
                "Multi-column LIST subpartitioning is not supported; partitions are not "
                "subdivided.",
            )
            sub_plan = ""
        if sub_plan and not plan.sub_keys:
            plan.add(
                TODO, "No subpartition key columns were collected; partitions are not subdivided."
            )
            sub_plan = ""
        builder = {"RANGE": self._range, "LIST": self._list, "HASH": self._hash}[kind]
        plan.children = builder(plan, plan.keys, partitions, top=True)
        if sub_plan:
            for child, oracle_part in zip(plan.children, partitions, strict=False):
                if child.skipped:
                    continue
                subs = sorted(
                    self.subpartitions.get(
                        (plan.owner, plan.table, oracle_part.partition_name), []
                    ),
                    key=lambda s: s.subpartition_position or 0,
                )
                if not subs:
                    continue
                child.partition_by = f"{sub_plan} ({', '.join(ident(k) for k in plan.sub_keys)})"
                sub_builder = {"RANGE": self._range, "LIST": self._list, "HASH": self._hash}[
                    sub_plan
                ]
                child.children = sub_builder(plan, plan.sub_keys, subs, top=False)
        self._indexes(plan)
        return plan

    def _range(self, plan, keys, items, top: bool) -> list[Child]:
        children: list[Child] = []
        lower = [Value(LITERAL, "MINVALUE")] * len(keys)
        nullable = self.nullable(plan.owner, plan.table, keys)
        for item in items:
            oracle_name = _item_name(item)
            values = parse_range_bound(item.high_value)
            name = child_name(plan.table, oracle_name)
            if len(values) != len(keys) or any(v.kind in {UNKNOWN, DEFAULT, NULL} for v in values):
                children.append(
                    Child(
                        name,
                        oracle_name,
                        "",
                        skipped=f"HIGH_VALUE not understood: {item.high_value}",
                    )
                )
                plan.add(TODO, f"{oracle_name}: bound {item.high_value!r} was not converted.")
                continue
            if top and plan.interval and len(keys) == 1:
                name = _partman_name(plan.table, lower[0]) or name
            if all(v.kind == MAXVALUE for v in values) and nullable:
                children.append(Child(name, oracle_name, "DEFAULT"))
                plan.add(
                    CHANGED,
                    f"{oracle_name} (MAXVALUE) becomes the DEFAULT partition, because "
                    f"{', '.join(nullable)} can be NULL: Oracle stores NULL keys in the MAXVALUE "
                    "partition, and in PostgreSQL only a DEFAULT partition accepts them. Creating "
                    "a new partition later scans the DEFAULT partition and fails if it holds rows "
                    "for the new range.",
                )
            else:
                children.append(
                    Child(
                        name, oracle_name, f"FOR VALUES FROM ({_join(lower)}) TO ({_join(values)})"
                    )
                )
            lower = values
        return children

    def _list(self, plan, keys, items, top: bool) -> list[Child]:
        children: list[Child] = []
        for item in items:
            oracle_name = _item_name(item)
            name = child_name(plan.table, oracle_name)
            values = parse_list_values(item.high_value)
            flat = [v[0] for v in values if len(v) == 1]
            if len(flat) != len(values) or any(v.kind == UNKNOWN for v in flat) or not flat:
                children.append(
                    Child(
                        name,
                        oracle_name,
                        "",
                        skipped=f"HIGH_VALUE not understood: {item.high_value}",
                    )
                )
                plan.add(TODO, f"{oracle_name}: values {item.high_value!r} were not converted.")
                continue
            if any(v.kind == DEFAULT for v in flat):
                children.append(Child(name, oracle_name, "DEFAULT"))
                continue
            children.append(Child(name, oracle_name, f"FOR VALUES IN ({_join(flat)})"))
        return children

    def _hash(self, plan, keys, items, top: bool) -> list[Child]:
        count = len(items)
        if top:
            plan.add(
                NOTE,
                f"HASH into {count} partitions: PostgreSQL uses its own hash function, so rows "
                "land in different partitions than in Oracle (harmless unless something depends "
                "on which partition a row is in)."
                + (
                    ""
                    if count & (count - 1) == 0
                    else " Oracle spreads rows evenly only with a power of two; PostgreSQL "
                    "does not care."
                ),
            )
        return [
            Child(
                child_name(plan.table, _item_name(item)),
                _item_name(item),
                f"FOR VALUES WITH (MODULUS {count}, REMAINDER {i})",
            )
            for i, item in enumerate(items)
        ]

    def _indexes(self, plan: TablePlan) -> None:
        required = plan.keys + (plan.sub_keys if any(c.partition_by for c in plan.children) else ())
        keys = {k.upper() for k in required}
        key_list = ", ".join(required)
        index_list: list[Index] = self.inventory.indexes_by_table.get((plan.owner, plan.table), [])
        for index in index_list:
            columns = {c.upper() for c in index.columns}
            part_index = self.part_indexes.get((plan.owner, index.name))
            locality = (
                part_index.locality if part_index else ("LOCAL" if index.partitioned else "GLOBAL")
            ).upper()
            unique = index.uniqueness.upper() == "UNIQUE"
            if unique and not keys <= columns:
                plan.add(
                    TODO,
                    f"Unique index {index.name} ({', '.join(index.columns)}) does not include "
                    f"every partitioning column ({key_list}). PostgreSQL cannot enforce it "
                    "across partitions: "
                    "include the key (uniqueness then only holds per key value), generate the "
                    "values from one sequence, or enforce it in a separate unpartitioned table.",
                )
            elif locality == "GLOBAL" and not index.partitioned:
                plan.add(
                    NOTE,
                    f"Global index {index.name} becomes one index per partition. A query that "
                    "does not filter on the partition key probes every partition's index.",
                )


def _item_name(item: TablePartition | TableSubpartition) -> str:
    return item.subpartition_name if isinstance(item, TableSubpartition) else item.partition_name


def plan_partitions(inventory: Inventory) -> list[TablePlan]:
    """A plan for every partitioned table in the inventory."""
    planner = _Planner(inventory)
    return [planner.plan(p) for p in inventory.part_tables]


# --------------------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------------------


def child_ddl(plan: TablePlan) -> list[str]:
    """``CREATE TABLE ... PARTITION OF`` statements for one table, parents first."""
    lines: list[str] = []
    parent = plan.qualified

    def emit(child: Child, of: str) -> None:
        target = qualified(plan.owner, child.name)
        if child.skipped:
            lines.append(f"-- TODO {child.oracle_name}: {child.skipped}")
            return
        bound = "DEFAULT" if child.bound == "DEFAULT" else child.bound
        tail = f" PARTITION BY {child.partition_by}" if child.partition_by else ""
        rename = (
            "" if child.name == child.oracle_name.lower() else f"  -- Oracle {child.oracle_name}"
        )
        lines.append(f"CREATE TABLE {target} PARTITION OF {of} {bound}{tail};{rename}")
        for grandchild in child.children:
            emit(grandchild, target)

    for child in plan.children:
        emit(child, parent)
    return lines


def maintenance_sql(plan: TablePlan) -> list[str]:
    """A procedure that creates partitions ahead of time for an interval table."""
    if not plan.interval or not plan.children or len(plan.keys) != 1:
        return []
    kind, step = plan.interval
    proc = qualified(plan.owner, f"{plan.table}_add_partitions")
    parent = plan.qualified
    table = plan.table.lower()
    if kind == "interval":
        return [
            f"-- Interval partitioning: create {step} partitions ahead of time.",
            f"-- Run it now, then on a schedule, e.g. SELECT cron.schedule('{table}-partitions',",
            f"--   '0 3 * * *', $$CALL {proc}()$$);  (pg_cron, allow-listed on Azure)",
            f"CREATE OR REPLACE PROCEDURE {proc}(p_ahead integer DEFAULT 3)",
            "LANGUAGE plpgsql",
            "AS $o2pg$",
            "DECLARE",
            "  v_upper timestamp;",
            "  v_next  timestamp;",
            "BEGIN",
            "  SELECT max(substring(pg_get_expr(c.relpartbound, c.oid)",
            "                       FROM 'TO \\(''([^'']+)''\\)')::timestamp)",
            "    INTO v_upper",
            "    FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid",
            f"   WHERE i.inhparent = '{parent}'::regclass;",
            "  IF v_upper IS NULL THEN",
            f"    RAISE EXCEPTION '{parent} has no range partition with an upper bound';",
            "  END IF;",
            f"  WHILE v_upper < LOCALTIMESTAMP + p_ahead * interval '{step}' LOOP",
            f"    v_next := v_upper + interval '{step}';",
            "    EXECUTE format('CREATE TABLE %I.%I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',",
            f"                   '{plan.owner.lower()}',",
            f"                   '{table}_p' || to_char(v_upper, 'YYYYMMDD'),",
            f"                   '{parent}', v_upper, v_next);",
            "    v_upper := v_next;",
            "  END LOOP;",
            "END;",
            "$o2pg$;",
        ]
    return [
        f"-- Interval partitioning by {step}: create the next partitions ahead of time.",
        f"CREATE OR REPLACE PROCEDURE {proc}(p_count integer DEFAULT 3)",
        "LANGUAGE plpgsql",
        "AS $o2pg$",
        "DECLARE",
        "  v_upper numeric;",
        "BEGIN",
        "  SELECT max(substring(pg_get_expr(c.relpartbound, c.oid)",
        "                       FROM 'TO \\(([-0-9.]+)\\)')::numeric)",
        "    INTO v_upper",
        "    FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid",
        f"   WHERE i.inhparent = '{parent}'::regclass;",
        "  FOR n IN 1 .. p_count LOOP",
        "    EXECUTE format('CREATE TABLE %I.%I PARTITION OF %s FOR VALUES FROM (%s) TO (%s)',",
        f"                   '{plan.owner.lower()}',",
        f"                   '{table}_p' || replace(v_upper::text, '.', '_'),",
        f"                   '{parent}', v_upper, v_upper + {step});",
        f"    v_upper := v_upper + {step};",
        "  END LOOP;",
        "END;",
        "$o2pg$;",
    ]


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------

_LEVEL_TITLE = {TODO: "Decide", CHANGED: "Behaves differently", NOTE: "Good to know"}


def _key_types(inventory: Inventory, plan: TablePlan) -> str:
    out = []
    for key in plan.keys:
        col = next(
            (
                c
                for c in inventory.columns_by_table.get((plan.owner, plan.table), [])
                if c.name.upper() == key.upper()
            ),
            None,
        )
        out.append(
            f"{key} {base_type(col.data_type) if col else '?'}"
            + ("" if col is None or col.nullable else " NOT NULL")
        )
    return ", ".join(out)


def _made(plan: TablePlan) -> int:
    return len([c for c in plan.all_children() if not c.skipped])


def _todo(plan: TablePlan) -> int:
    return sum(1 for f in plan.findings if f.level == TODO)


def report_markdown(inventory: Inventory, plans: list[TablePlan] | None = None) -> str:
    """The ``o2pg partitions`` report."""
    from o2pg import __version__

    plans = plan_partitions(inventory) if plans is None else plans
    todo = sum(1 for p in plans for f in p.findings if f.level == TODO)
    out = [
        "# o2pg partitioning report",
        "",
        f"Generated by o2pg {__version__}. `o2pg map-types` creates the partitions listed here; "
        "this report explains each decision.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Partitioned tables | {len(plans)} |",
        f"| Partitioned the same way in PostgreSQL | {sum(p.converted for p in plans)} |",
        f"| Oracle partitions / subpartitions | {sum(p.oracle_partitions for p in plans)} / "
        f"{sum(p.oracle_subpartitions for p in plans)} |",
        f"| PostgreSQL partitions created | {sum(_made(p) for p in plans)} |",
        f"| Decisions needed | {todo} |",
        "",
        "| Table | Oracle | PostgreSQL | Partitions | Decisions |",
        "|---|---|---|---|---|",
    ]
    for p in plans:
        oracle = p.oracle_kind + (
            f" / {p.oracle_sub_kind}" if p.oracle_sub_kind not in {"NONE", ""} else ""
        )
        if p.interval:
            oracle += " (interval)"
        pg = (
            p.pg_kind
            + (
                f" / {p.children[0].partition_by.split(' ')[0]}"
                if p.children and p.children[0].partition_by
                else ""
            )
            if p.converted
            else "not partitioned"
        )
        made = len([c for c in p.all_children() if not c.skipped])
        out.append(f"| `{p.qualified}` | {oracle} | {pg} | {made} | {_todo(p)} |")
    for p in plans:
        out += ["", f"## `{p.qualified}`", ""]
        if p.keys:
            out.append(
                f"Key: {_key_types(inventory, p)}"
                + (f"; subpartition key: {', '.join(p.sub_keys)}" if p.sub_keys else "")
                + "."
            )
            out.append("")
        if not p.findings:
            out.append("Converted as is. No differences that need attention.")
        for level in (TODO, CHANGED, NOTE):
            items = [f.text for f in p.findings if f.level == level]
            if items:
                out.append(f"**{_LEVEL_TITLE[level]}**")
                out.append("")
                out += [f"- {text}" for text in items]
                out.append("")
    out += [
        "",
        "## Differences that apply to every partitioned table",
        "",
        "- Updating the partition key moves the row to the right partition in PostgreSQL. Oracle "
        "raises ORA-14402 unless ROW MOVEMENT is enabled.",
        "- Partition pruning needs a condition on the key column itself. "
        "`order_date >= '2024-03-01'` prunes; `date_trunc('month', order_date) = '2024-03-01'` "
        "and `order_date::date = ...` scan "
        "every partition.",
        "- Archive old data with `ALTER TABLE ... DETACH PARTITION ... CONCURRENTLY` and "
        "`DROP TABLE`, "
        "not DELETE.",
        "- A DEFAULT partition makes every new partition more expensive to add: PostgreSQL "
        "scans it "
        "to prove it holds no rows for the new range.",
        "",
    ]
    return "\n".join(out)
