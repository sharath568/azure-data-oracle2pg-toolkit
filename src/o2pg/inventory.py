"""Inventory model: the JSON produced by ``collector/collect.sql``.

Every section of the inventory is a list of flat records. Keys are matched
case-insensitively against the dataclass fields below, unknown keys are
ignored, and values are coerced to the declared types. That keeps the loader
forgiving about collector versions while giving rules a typed model.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path
from typing import Any, TypeVar

from o2pg.plsql import clean_source

PLSQL_SOURCE_TYPES = frozenset(
    {"PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "TYPE", "TYPE BODY"}
)


class InventoryError(ValueError):
    """Raised when an inventory file cannot be read or has the wrong shape."""


_TRUE_STRINGS = frozenset({"Y", "YES", "TRUE", "ENABLED", "1"})


def _coerce(value: Any, hint: str) -> Any:
    """Coerce a raw JSON value to the type described by a field annotation string."""
    if hint.startswith("tuple"):
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(str(item) for item in value)
    if hint == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().upper() in _TRUE_STRINGS
    if hint.startswith("int"):
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(float(value))
    if value is None:
        return ""
    return str(value)


_R = TypeVar("_R")


def _build(cls: type[_R], raw: dict[str, Any]) -> _R:
    """Create a record dataclass from a dict with case-insensitive keys."""
    if not isinstance(raw, dict):
        raise InventoryError(f"expected an object for {cls.__name__}, got {type(raw).__name__}")
    lowered = {str(key).lower(): value for key, value in raw.items()}
    kwargs = {
        f.name: _coerce(lowered[f.name], str(f.type))
        for f in fields(cls)  # type: ignore[arg-type]
        if f.name in lowered
    }
    return cls(**kwargs)


@dataclass(frozen=True)
class Metadata:
    """Header information about the source database."""

    collector_version: str = ""
    collected_at: str = ""
    db_name: str = ""
    db_version: str = ""
    character_set: str = ""
    nchar_character_set: str = ""
    nls_length_semantics: str = ""
    schemas: tuple[str, ...] = ()


@dataclass(frozen=True)
class Table:
    """A table from ALL_TABLES."""

    owner: str = ""
    name: str = ""
    temporary: bool = False
    duration: str = ""
    iot_type: str = ""
    partitioned: bool = False
    num_rows: int | None = None
    compression: str = ""
    compress_for: str = ""
    logging: str = ""
    cluster_name: str = ""
    degree: str = ""

    @property
    def qualified(self) -> str:
        """``OWNER.NAME``."""
        return f"{self.owner}.{self.name}"


@dataclass(frozen=True)
class Column:
    """A column from ALL_TAB_COLS (hidden columns excluded by the collector)."""

    owner: str = ""
    table_name: str = ""
    name: str = ""
    data_type: str = ""
    data_type_owner: str = ""
    data_length: int | None = None
    data_precision: int | None = None
    data_scale: int | None = None
    char_length: int | None = None
    char_used: str = ""
    nullable: bool = True
    data_default: str = ""
    virtual: bool = False
    identity: bool = False

    @property
    def qualified(self) -> str:
        """``OWNER.TABLE.COLUMN``."""
        return f"{self.owner}.{self.table_name}.{self.name}"


@dataclass(frozen=True)
class Constraint:
    """A constraint from ALL_CONSTRAINTS with its columns."""

    owner: str = ""
    table_name: str = ""
    name: str = ""
    type: str = ""
    columns: tuple[str, ...] = ()
    search_condition: str = ""
    r_owner: str = ""
    r_constraint_name: str = ""
    delete_rule: str = ""
    status: str = ""
    validated: str = ""
    deferrable: str = ""


@dataclass(frozen=True)
class Index:
    """An index from ALL_INDEXES with its columns."""

    owner: str = ""
    name: str = ""
    table_owner: str = ""
    table_name: str = ""
    index_type: str = ""
    uniqueness: str = ""
    columns: tuple[str, ...] = ()
    partitioned: bool = False
    ityp_name: str = ""


@dataclass(frozen=True)
class Sequence:
    """A sequence from ALL_SEQUENCES."""

    owner: str = ""
    name: str = ""
    min_value: int | None = None
    max_value: int | None = None
    increment_by: int | None = None
    cycle: bool = False
    order: bool = False
    cache_size: int | None = None
    last_number: int | None = None


@dataclass(frozen=True)
class View:
    """A view from ALL_VIEWS (TEXT_VC, so at most 4000 characters)."""

    owner: str = ""
    name: str = ""
    text: str = ""
    text_length: int | None = None


@dataclass(frozen=True)
class Synonym:
    """A synonym from ALL_SYNONYMS."""

    owner: str = ""
    name: str = ""
    table_owner: str = ""
    table_name: str = ""
    db_link: str = ""


@dataclass(frozen=True)
class Trigger:
    """A trigger from ALL_TRIGGERS. The body lives in the source section."""

    owner: str = ""
    name: str = ""
    table_owner: str = ""
    table_name: str = ""
    trigger_type: str = ""
    triggering_event: str = ""
    base_object_type: str = ""
    status: str = ""


@dataclass(frozen=True)
class SourceObject:
    """A stored program unit assembled from ALL_SOURCE lines."""

    owner: str = ""
    name: str = ""
    type: str = ""
    text: str = ""


@dataclass(frozen=True)
class DbLink:
    """A database link from ALL_DB_LINKS."""

    owner: str = ""
    name: str = ""
    username: str = ""
    host: str = ""


@dataclass(frozen=True)
class MaterializedView:
    """A materialized view from ALL_MVIEWS."""

    owner: str = ""
    name: str = ""
    refresh_method: str = ""
    refresh_mode: str = ""
    fast_refreshable: str = ""
    build_mode: str = ""


@dataclass(frozen=True)
class PartitionedTable:
    """Partitioning details from ALL_PART_TABLES and ALL_PART_KEY_COLUMNS."""

    owner: str = ""
    table_name: str = ""
    partitioning_type: str = ""
    subpartitioning_type: str = ""
    partition_count: int | None = None
    interval: str = ""
    ref_constraint_name: str = ""
    partition_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Lob:
    """LOB segment details from ALL_LOBS."""

    owner: str = ""
    table_name: str = ""
    column_name: str = ""
    securefile: bool = False
    compression: str = ""
    deduplication: str = ""
    in_row: bool = False


@dataclass(frozen=True)
class ObjectType:
    """A user-defined type from ALL_TYPES (joined with ALL_COLL_TYPES)."""

    owner: str = ""
    name: str = ""
    typecode: str = ""
    coll_type: str = ""
    elem_type: str = ""
    methods: int | None = None


@dataclass(frozen=True)
class SchedulerJob:
    """A job from ALL_SCHEDULER_JOBS."""

    owner: str = ""
    name: str = ""
    job_type: str = ""
    job_action: str = ""
    program_name: str = ""
    repeat_interval: str = ""
    enabled: bool = False


@dataclass(frozen=True)
class Grant:
    """An aggregated object privilege: how many objects a grantee holds it on."""

    owner: str = ""
    grantee: str = ""
    privilege: str = ""
    object_count: int | None = None


@dataclass(frozen=True)
class Policy:
    """A Virtual Private Database policy from ALL_POLICIES."""

    object_owner: str = ""
    object_name: str = ""
    policy_name: str = ""
    function: str = ""
    enabled: bool = False


@dataclass(frozen=True)
class Queue:
    """An Advanced Queuing queue from ALL_QUEUES."""

    owner: str = ""
    name: str = ""
    queue_table: str = ""
    queue_type: str = ""


@dataclass(frozen=True)
class EncryptedColumn:
    """A TDE-encrypted column from ALL_ENCRYPTED_COLUMNS."""

    owner: str = ""
    table_name: str = ""
    column_name: str = ""
    encryption_alg: str = ""


@dataclass(frozen=True)
class InvalidObject:
    """An object with STATUS = 'INVALID' in ALL_OBJECTS."""

    owner: str = ""
    name: str = ""
    type: str = ""


@dataclass
class CodeUnit:
    """A piece of SQL or PL/SQL text that rules can scan.

    ``plsql`` is True for stored program units and PL/SQL scheduler blocks, and
    False for views (plain SQL).
    """

    owner: str
    name: str
    kind: str
    text: str
    plsql: bool

    @property
    def qualified(self) -> str:
        """``OWNER.NAME``."""
        return f"{self.owner}.{self.name}"

    @cached_property
    def cleaned(self) -> str:
        """Source with comments stripped and string literals masked."""
        return clean_source(self.text)


SECTION_TYPES: dict[str, type] = {
    "tables": Table,
    "columns": Column,
    "constraints": Constraint,
    "indexes": Index,
    "sequences": Sequence,
    "views": View,
    "synonyms": Synonym,
    "triggers": Trigger,
    "db_links": DbLink,
    "mviews": MaterializedView,
    "part_tables": PartitionedTable,
    "lobs": Lob,
    "types": ObjectType,
    "scheduler_jobs": SchedulerJob,
    "grants": Grant,
    "policies": Policy,
    "queues": Queue,
    "encrypted_columns": EncryptedColumn,
    "invalid_objects": InvalidObject,
}


def _assemble_source(rows: list[dict[str, Any]]) -> list[SourceObject]:
    """Group ALL_SOURCE-style rows (one per line) into whole program units.

    Rows that already carry the full text (no ``line`` key) are accepted too.
    """
    grouped: dict[tuple[str, str, str], list[tuple[int, str]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            raise InventoryError("source rows must be objects")
        lowered = {str(key).lower(): value for key, value in row.items()}
        key = (
            str(lowered.get("owner") or ""),
            str(lowered.get("name") or ""),
            str(lowered.get("type") or "").upper(),
        )
        line = int(lowered.get("line") or 0)
        grouped[key].append((line, str(lowered.get("text") or "")))
    objects = []
    for (owner, name, kind), lines in grouped.items():
        lines.sort(key=lambda item: item[0])
        text = "".join(t if t.endswith("\n") else t + "\n" for _, t in lines)
        objects.append(SourceObject(owner=owner, name=name, type=kind, text=text))
    return objects


@dataclass
class Inventory:
    """Typed view over a collected Oracle inventory."""

    metadata: Metadata = field(default_factory=Metadata)
    tables: list[Table] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    sequences: list[Sequence] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    synonyms: list[Synonym] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    source: list[SourceObject] = field(default_factory=list)
    db_links: list[DbLink] = field(default_factory=list)
    mviews: list[MaterializedView] = field(default_factory=list)
    part_tables: list[PartitionedTable] = field(default_factory=list)
    lobs: list[Lob] = field(default_factory=list)
    types: list[ObjectType] = field(default_factory=list)
    scheduler_jobs: list[SchedulerJob] = field(default_factory=list)
    grants: list[Grant] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)
    queues: list[Queue] = field(default_factory=list)
    encrypted_columns: list[EncryptedColumn] = field(default_factory=list)
    invalid_objects: list[InvalidObject] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Inventory:
        """Build an inventory from parsed collector JSON."""
        if not isinstance(data, dict):
            raise InventoryError("inventory must be a JSON object at the top level")
        lowered = {str(key).lower(): value for key, value in data.items()}
        kwargs: dict[str, Any] = {"metadata": _build(Metadata, lowered.get("metadata") or {})}
        for name, record_cls in SECTION_TYPES.items():
            rows = lowered.get(name) or []
            if not isinstance(rows, list):
                raise InventoryError(f"section '{name}' must be a list")
            kwargs[name] = [_build(record_cls, row) for row in rows]
        source_rows = lowered.get("source") or []
        if not isinstance(source_rows, list):
            raise InventoryError("section 'source' must be a list")
        kwargs["source"] = _assemble_source(source_rows)
        return cls(**kwargs)

    @classmethod
    def from_json(cls, text: str) -> Inventory:
        """Parse collector output.

        SQL*Plus wraps lines longer than LINESIZE. JSON_OBJECT escapes real
        newlines inside values, so any raw newline in the file is formatting and
        can be removed safely; we retry that way before giving up.
        """
        text = text.lstrip("﻿")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as first_error:
            try:
                data = json.loads(text.replace("\r", "").replace("\n", ""))
            except json.JSONDecodeError:
                raise InventoryError(f"not valid JSON: {first_error}") from first_error
        return cls.from_dict(data)

    @classmethod
    def from_path(cls, path: str | Path) -> Inventory:
        """Load an inventory file from disk."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise InventoryError(f"cannot read {path}: {exc}") from exc
        return cls.from_json(text)

    @cached_property
    def code_units(self) -> list[CodeUnit]:
        """All scannable code: stored PL/SQL, views and PL/SQL scheduler job actions."""
        units = [
            CodeUnit(s.owner, s.name, s.type, s.text, plsql=s.type in PLSQL_SOURCE_TYPES)
            for s in self.source
        ]
        units += [CodeUnit(v.owner, v.name, "VIEW", v.text, plsql=False) for v in self.views]
        units += [
            CodeUnit(j.owner, j.name, "SCHEDULER JOB", j.job_action, plsql=True)
            for j in self.scheduler_jobs
            if j.job_type.upper() in {"PLSQL_BLOCK", "STORED_PROCEDURE"} and j.job_action
        ]
        return units

    @cached_property
    def columns_by_table(self) -> dict[tuple[str, str], list[Column]]:
        """Columns grouped by ``(owner, table_name)`` in inventory order."""
        grouped: dict[tuple[str, str], list[Column]] = defaultdict(list)
        for column in self.columns:
            grouped[(column.owner, column.table_name)].append(column)
        return dict(grouped)

    @cached_property
    def constraints_by_table(self) -> dict[tuple[str, str], list[Constraint]]:
        """Constraints grouped by ``(owner, table_name)``."""
        grouped: dict[tuple[str, str], list[Constraint]] = defaultdict(list)
        for constraint in self.constraints:
            grouped[(constraint.owner, constraint.table_name)].append(constraint)
        return dict(grouped)

    @cached_property
    def indexes_by_table(self) -> dict[tuple[str, str], list[Index]]:
        """Indexes grouped by ``(table_owner, table_name)``."""
        grouped: dict[tuple[str, str], list[Index]] = defaultdict(list)
        for index in self.indexes:
            grouped[(index.table_owner or index.owner, index.table_name)].append(index)
        return dict(grouped)

    @cached_property
    def partitioning(self) -> dict[tuple[str, str], PartitionedTable]:
        """Partitioning details keyed by ``(owner, table_name)``."""
        return {(p.owner, p.table_name): p for p in self.part_tables}

    @cached_property
    def user_type_names(self) -> frozenset[str]:
        """Names of user-defined types in the inventory (unqualified, upper case)."""
        return frozenset(t.name.upper() for t in self.types)

    def object_counts(self) -> dict[str, int]:
        """Number of objects per inventory section, for report summaries."""
        counts = {name: len(getattr(self, name)) for name in SECTION_TYPES}
        counts["source_objects"] = len(self.source)
        counts["plsql_lines"] = sum(
            s.text.count("\n") for s in self.source if s.type in PLSQL_SOURCE_TYPES
        )
        return counts
