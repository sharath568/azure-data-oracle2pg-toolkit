"""Schema object rules (O2PG-OB-xxx): synonyms, links, triggers, sequences, naming."""

from __future__ import annotations

import re
from collections.abc import Iterator

from o2pg.inventory import Inventory
from o2pg.rules.base import Category, Hit, Severity, rule

_OB = Category.OBJECTS

BIGINT_MAX = 2**63 - 1

POSTGRES_RESERVED_WORDS = frozenset(
    """
    ALL ANALYSE ANALYZE AND ANY ARRAY AS ASC ASYMMETRIC AUTHORIZATION BINARY BOTH CASE CAST
    CHECK COLLATE COLLATION COLUMN CONCURRENTLY CONSTRAINT CREATE CROSS CURRENT_CATALOG
    CURRENT_DATE CURRENT_ROLE CURRENT_SCHEMA CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER
    DEFAULT DEFERRABLE DESC DISTINCT DO ELSE END EXCEPT FALSE FETCH FOR FOREIGN FREEZE FROM
    FULL GRANT GROUP HAVING ILIKE IN INITIALLY INNER INTERSECT INTO IS ISNULL JOIN LATERAL
    LEADING LEFT LIKE LIMIT LOCALTIME LOCALTIMESTAMP NATURAL NOT NOTNULL NULL OFFSET ON ONLY
    OR ORDER OUTER OVERLAPS PLACING PRIMARY REFERENCES RETURNING RIGHT SELECT SESSION_USER
    SIMILAR SOME SYMMETRIC SYSTEM_USER TABLE TABLESAMPLE THEN TO TRAILING TRUE UNION UNIQUE
    USER USING VARIADIC VERBOSE WHEN WHERE WINDOW WITH
    """.split()
)
"""Keywords that are reserved (or reserved except as function/type names) in PostgreSQL 16."""

_SIMPLE_ORACLE_NAME = re.compile(r"^[A-Z][A-Z0-9_$]*$")


def _named_objects(inventory: Inventory) -> Iterator[tuple[str, str, str]]:
    """Yield ``(name, object_type, qualified_name)`` for every named object."""
    for t in inventory.tables:
        yield t.name, "TABLE", t.qualified
    for c in inventory.columns:
        yield c.name, "COLUMN", c.qualified
    for i in inventory.indexes:
        yield i.name, "INDEX", f"{i.owner}.{i.name}"
    for k in inventory.constraints:
        yield k.name, "CONSTRAINT", f"{k.owner}.{k.table_name}.{k.name}"
    for s in inventory.sequences:
        yield s.name, "SEQUENCE", f"{s.owner}.{s.name}"
    for v in inventory.views:
        yield v.name, "VIEW", f"{v.owner}.{v.name}"
    for m in inventory.mviews:
        yield m.name, "MATERIALIZED VIEW", f"{m.owner}.{m.name}"
    for g in inventory.triggers:
        yield g.name, "TRIGGER", f"{g.owner}.{g.name}"
    seen: set[tuple[str, str]] = set()
    for src in inventory.source:
        if src.type in {"PACKAGE BODY", "TYPE BODY", "TRIGGER"} or (src.owner, src.name) in seen:
            continue
        seen.add((src.owner, src.name))
        yield src.name, src.type, f"{src.owner}.{src.name}"


@rule(
    "O2PG-OB-001",
    "Private synonyms",
    _OB,
    Severity.LOW,
    0.25,
    description="PostgreSQL has no synonyms. Code that relies on them resolves names differently.",
    remediation=(
        "Most private synonyms just point at another schema: set search_path for the role "
        "(ALTER ROLE app SET search_path = app, hr) or qualify names. For renames, use a "
        "view, or a wrapper function for synonyms that point at code."
    ),
)
def _private_synonyms(inventory: Inventory) -> Iterator[Hit]:
    for syn in inventory.synonyms:
        if syn.owner != "PUBLIC" and not syn.db_link:
            target = f"{syn.table_owner}.{syn.table_name}"
            yield Hit(f"{syn.owner}.{syn.name}", "SYNONYM", f"-> {target}")


@rule(
    "O2PG-OB-002",
    "Public synonyms",
    _OB,
    Severity.MEDIUM,
    0.5,
    description=(
        "Public synonyms make an object visible to every user without a schema prefix. "
        "There is no global namespace like that in PostgreSQL."
    ),
    remediation=(
        "Add the owning schema to search_path for the roles that use the objects (ALTER "
        "ROLE ... SET search_path, or ALTER DATABASE ... SET search_path)."
    ),
)
def _public_synonyms(inventory: Inventory) -> Iterator[Hit]:
    for syn in inventory.synonyms:
        if syn.owner == "PUBLIC" and not syn.db_link:
            yield Hit(f"PUBLIC.{syn.name}", "SYNONYM", f"-> {syn.table_owner}.{syn.table_name}")


@rule(
    "O2PG-OB-003",
    "Database links",
    _OB,
    Severity.HIGH,
    3.0,
    description=(
        "Database links (and synonyms that reach through them) let SQL query another "
        "database. On Azure Flexible Server postgres_fdw can reach other PostgreSQL "
        "servers, but there is no supported way to query a remaining Oracle database from "
        "the PostgreSQL side."
    ),
    remediation=(
        "Links to databases that also move to PostgreSQL -> postgres_fdw with a foreign "
        "server and user mapping (check outbound networking from the server's VNet or "
        "firewall). Links to systems that stay on Oracle -> move the integration into the "
        "application or a data pipeline, and plan the cutover order around it. Credentials "
        "stored in links become user mappings; rotate them."
    ),
    extensions=("postgres_fdw",),
)
def _db_links(inventory: Inventory) -> Iterator[Hit]:
    for link in inventory.db_links:
        detail = f"host {link.host or '?'}"
        if link.username:
            detail += f", stored user {link.username}"
        yield Hit(f"{link.owner}.{link.name}", "DATABASE LINK", detail)
    for syn in inventory.synonyms:
        if syn.db_link:
            target = f"{syn.table_owner}.{syn.table_name}@{syn.db_link}"
            yield Hit(f"{syn.owner}.{syn.name}", "SYNONYM", f"-> {target}")


@rule(
    "O2PG-OB-004",
    "Row-level DML triggers",
    _OB,
    Severity.MEDIUM,
    1.0,
    description=(
        "Row triggers carry business logic that does not show up in table DDL: audit "
        "columns, derived values, cascades into other tables. PostgreSQL triggers call a "
        "separate trigger function and use NEW/OLD instead of :NEW/:OLD."
    ),
    remediation=(
        "Create a trigger function (RETURNS trigger) per trigger and a CREATE TRIGGER that "
        "calls it. Replace :NEW/:OLD with NEW/OLD, INSERTING/UPDATING/DELETING with TG_OP, "
        "and remember BEFORE row triggers must RETURN NEW. Sequence-filling triggers can "
        "usually be replaced by identity columns."
    ),
    ai_risk=(
        "Triggers change data the DDL does not mention. Give the model the trigger bodies "
        "along with the table, or it will produce tables that silently lose those side effects."
    ),
)
def _row_triggers(inventory: Inventory) -> Iterator[Hit]:
    for trg in inventory.triggers:
        kind = trg.trigger_type.upper()
        if "EACH ROW" in kind and trg.base_object_type.upper() in {"TABLE", ""}:
            detail = f"{trg.trigger_type} {trg.triggering_event} on {trg.table_name}"
            yield Hit(f"{trg.owner}.{trg.name}", "TRIGGER", detail)


@rule(
    "O2PG-OB-005",
    "Compound triggers",
    _OB,
    Severity.HIGH,
    2.0,
    description=(
        "Compound triggers combine statement and row timing points with shared state, "
        "usually to work around mutating-table errors."
    ),
    remediation=(
        "Split into separate row and statement triggers. In PostgreSQL 10+ AFTER statement "
        "triggers with transition tables (REFERENCING NEW TABLE AS ...) often replace the "
        "whole pattern, since PostgreSQL has no mutating-table restriction."
    ),
)
def _compound_triggers(inventory: Inventory) -> Iterator[Hit]:
    for trg in inventory.triggers:
        if trg.trigger_type.upper() == "COMPOUND":
            yield Hit(f"{trg.owner}.{trg.name}", "TRIGGER", f"on {trg.table_name}")


@rule(
    "O2PG-OB-006",
    "Schema and database event triggers",
    _OB,
    Severity.HIGH,
    2.0,
    description=(
        "DDL, logon and startup triggers defined on SCHEMA or DATABASE. PostgreSQL event "
        "triggers cover DDL only; there is no logon trigger."
    ),
    remediation=(
        "DDL auditing -> event triggers or Azure server logs. Logon logic -> role settings "
        "(ALTER ROLE ... SET), connection pool init SQL or the application."
    ),
)
def _system_triggers(inventory: Inventory) -> Iterator[Hit]:
    for trg in inventory.triggers:
        if trg.base_object_type.upper() in {"SCHEMA", "DATABASE"}:
            detail = f"{trg.trigger_type} {trg.triggering_event}".strip()
            yield Hit(f"{trg.owner}.{trg.name}", "TRIGGER", detail)


@rule(
    "O2PG-OB-007",
    "Sequences with MAXVALUE beyond bigint",
    _OB,
    Severity.LOW,
    0.1,
    description=(
        "Oracle's default sequence MAXVALUE is 10^28 - 1. PostgreSQL sequences are bigint, "
        "so CREATE SEQUENCE fails if the value is copied as-is."
    ),
    remediation="Drop MAXVALUE (defaults to bigint max) or cap it at 9223372036854775807.",
)
def _big_sequences(inventory: Inventory) -> Iterator[Hit]:
    for seq in inventory.sequences:
        if seq.max_value is not None and seq.max_value > BIGINT_MAX:
            yield Hit(f"{seq.owner}.{seq.name}", "SEQUENCE", f"MAXVALUE {seq.max_value}")


@rule(
    "O2PG-OB-008",
    "Sequences must be re-synced after the data load",
    _OB,
    Severity.INFO,
    0.05,
    description=(
        "Loading data does not move sequences. After cutover each sequence must start "
        "above the highest value already used, or inserts start failing with duplicates."
    ),
    remediation=(
        "After the final load run setval('seq', (SELECT max(id) FROM t)) per sequence, or "
        "use the Oracle LAST_NUMBER captured at cutover plus a safety margin."
    ),
)
def _sequence_resync(inventory: Inventory) -> Iterator[Hit]:
    for seq in inventory.sequences:
        detail = f"last_number {seq.last_number}" if seq.last_number is not None else ""
        yield Hit(f"{seq.owner}.{seq.name}", "SEQUENCE", detail)


@rule(
    "O2PG-OB-009",
    "Materialized views refreshed ON COMMIT",
    _OB,
    Severity.HIGH,
    3.0,
    description=(
        "PostgreSQL materialized views are only refreshed by an explicit REFRESH, which is "
        "always a full rebuild. Nothing keeps them in sync on commit."
    ),
    remediation=(
        "Replace with a plain table maintained by triggers on the base tables, or accept "
        "staleness and refresh on a schedule. If the view exists only for query rewrite, "
        "a normal view plus indexes may be enough."
    ),
)
def _mv_on_commit(inventory: Inventory) -> Iterator[Hit]:
    for mv in inventory.mviews:
        if mv.refresh_mode.upper() == "COMMIT":
            yield Hit(
                f"{mv.owner}.{mv.name}", "MATERIALIZED VIEW", f"{mv.refresh_method} ON COMMIT"
            )


@rule(
    "O2PG-OB-010",
    "Materialized views with fast (incremental) refresh",
    _OB,
    Severity.MEDIUM,
    1.5,
    description=(
        "Fast refresh relies on materialized view logs. PostgreSQL has no incremental "
        "refresh; REFRESH MATERIALIZED VIEW CONCURRENTLY still recomputes everything, it "
        "just avoids blocking readers."
    ),
    remediation=(
        "Schedule REFRESH MATERIALIZED VIEW CONCURRENTLY (needs a unique index) with pg_cron "
        "and check that the refresh time fits the window, or maintain a summary table "
        "incrementally with triggers."
    ),
    extensions=("pg_cron",),
)
def _mv_fast(inventory: Inventory) -> Iterator[Hit]:
    for mv in inventory.mviews:
        if mv.refresh_method.upper() in {"FAST", "FORCE"} and mv.refresh_mode.upper() != "COMMIT":
            yield Hit(
                f"{mv.owner}.{mv.name}",
                "MATERIALIZED VIEW",
                f"{mv.refresh_method} ON {mv.refresh_mode}",
            )


@rule(
    "O2PG-OB-011",
    "Global temporary tables",
    _OB,
    Severity.MEDIUM,
    1.0,
    description=(
        "Oracle GTTs are permanent definitions with per-session data. PostgreSQL temporary "
        "tables are created per session, so code that expects the table to already exist "
        "breaks."
    ),
    remediation=(
        "Create the temp table at the start of the session or procedure (CREATE TEMP TABLE "
        "IF NOT EXISTS ... ON COMMIT DELETE ROWS), or use an unlogged table keyed by "
        "session/transaction id. Watch catalog bloat if temp tables are created very often."
    ),
)
def _gtts(inventory: Inventory) -> Iterator[Hit]:
    for table in inventory.tables:
        if table.temporary:
            scope = (
                "ON COMMIT DELETE ROWS"
                if "TRANSACTION" in table.duration.upper()
                else ("ON COMMIT PRESERVE ROWS")
            )
            yield Hit(table.qualified, "TABLE", scope)


@rule(
    "O2PG-OB-012",
    "User-defined object and collection types",
    _OB,
    Severity.HIGH,
    1.5,
    description=(
        "Object types, VARRAYs and nested table types. PostgreSQL composite types have no "
        "methods, inheritance or constructors with defaults."
    ),
    remediation=(
        "Object types used only in PL/SQL -> composite types. Member methods -> plain "
        "functions taking the composite as the first argument. Collections -> arrays. "
        "Types used as column types: see O2PG-DT-016."
    ),
)
def _object_types(inventory: Inventory) -> Iterator[Hit]:
    for typ in inventory.types:
        detail = typ.typecode
        if typ.coll_type:
            detail = f"{typ.coll_type} OF {typ.elem_type}".strip()
        if typ.methods:
            detail += f", {typ.methods} method(s)"
        yield Hit(f"{typ.owner}.{typ.name}", "TYPE", detail)


@rule(
    "O2PG-OB-013",
    "Invalid objects",
    _OB,
    Severity.MEDIUM,
    0.25,
    description=(
        "Objects that do not compile in Oracle today. Converting them costs time and they "
        "may be dead code."
    ),
    remediation=(
        "Recompile in Oracle (UTL_RECOMP or ALTER ... COMPILE). Anything that still fails "
        "and is not referenced should be dropped from scope, not migrated."
    ),
)
def _invalid_objects(inventory: Inventory) -> Iterator[Hit]:
    for obj in inventory.invalid_objects:
        yield Hit(f"{obj.owner}.{obj.name}", obj.type, "STATUS = INVALID")


@rule(
    "O2PG-OB-014",
    "Tables without a primary key",
    _OB,
    Severity.MEDIUM,
    0.5,
    description=(
        "Logical replication, most change data capture tools and row-by-row validation all "
        "need a key. Without one, online migration and data comparison get much harder."
    ),
    remediation=(
        "Add a primary key where a natural or surrogate key exists. Otherwise set REPLICA "
        "IDENTITY FULL on the PostgreSQL side for replication, and expect slower apply."
    ),
)
def _tables_without_pk(inventory: Inventory) -> Iterator[Hit]:
    with_pk = {(c.owner, c.table_name) for c in inventory.constraints if c.type.upper() == "P"}
    for table in inventory.tables:
        if not table.temporary and (table.owner, table.name) not in with_pk:
            yield Hit(table.qualified, "TABLE", "no primary key")


@rule(
    "O2PG-OB-015",
    "Identifiers longer than 63 bytes",
    _OB,
    Severity.HIGH,
    0.5,
    description=(
        "Oracle 12.2+ allows 128-byte names. PostgreSQL truncates identifiers to 63 bytes, "
        "so two long names with the same prefix collide, and generated DDL can fail."
    ),
    remediation=(
        "Rename before conversion and keep a rename map so code and the application can be "
        "updated consistently."
    ),
)
def _long_identifiers(inventory: Inventory) -> Iterator[Hit]:
    for name, kind, qualified in _named_objects(inventory):
        size = len(name.encode("utf-8"))
        if size > 63:
            yield Hit(qualified, kind, f"{size} bytes")


@rule(
    "O2PG-OB-016",
    "Quoted, mixed-case or special-character identifiers",
    _OB,
    Severity.MEDIUM,
    0.25,
    description=(
        "Oracle folds unquoted names to upper case; PostgreSQL folds them to lower case. "
        "Names created with quotes in mixed case or with characters such as # or spaces "
        "will need quotes forever in PostgreSQL."
    ),
    remediation=(
        "Convert to lower-case snake_case names during migration and keep a rename map. "
        "Do not carry the quotes over unless the application cannot change."
    ),
    ai_risk=(
        'Upper-case Oracle names copied with quotes ("EMPLOYEES") force quoting in every '
        "PostgreSQL query. Tell the model to emit unquoted lower-case names and list the "
        "identifiers that really are case-sensitive."
    ),
)
def _quoted_identifiers(inventory: Inventory) -> Iterator[Hit]:
    for name, kind, qualified in _named_objects(inventory):
        if not _SIMPLE_ORACLE_NAME.match(name):
            yield Hit(qualified, kind, f'needs quoting: "{name}"')


@rule(
    "O2PG-OB-017",
    "Names that are reserved words in PostgreSQL",
    _OB,
    Severity.MEDIUM,
    0.25,
    description=(
        "Some names that are legal unquoted in Oracle (LIMIT, OFFSET, ANALYZE, WINDOW, "
        "RETURNING, ...) are reserved in PostgreSQL and fail in DDL and queries unless quoted."
    ),
    remediation=(
        "Rename the table or column (for example limit -> credit_limit), or quote it everywhere."
    ),
)
def _reserved_words(inventory: Inventory) -> Iterator[Hit]:
    for name, kind, qualified in _named_objects(inventory):
        if kind in {"TABLE", "COLUMN", "VIEW", "MATERIALIZED VIEW", "SEQUENCE"} and (
            name.upper() in POSTGRES_RESERVED_WORDS
        ):
            yield Hit(qualified, kind, f"{name.lower()} is reserved")
