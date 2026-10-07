"""Data type rules (O2PG-DT-xxx): column-level issues found in ALL_TAB_COLS."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from o2pg.inventory import Column, Inventory
from o2pg.rules.base import Category, Hit, Severity, rule
from o2pg.typemap import CHARACTER_TYPES, base_type, is_user_defined, map_column

_DT = Category.DATATYPES


def _columns(inventory: Inventory, predicate: Callable[[Column], bool]) -> Iterator[Hit]:
    """Yield a hit with the suggested PostgreSQL type for each matching column."""
    for column in inventory.columns:
        if predicate(column):
            mapping = map_column(column)
            suggestion = f"suggest {mapping.pg_type}" if mapping.pg_type else "no direct mapping"
            yield Hit(column.qualified, "COLUMN", f"{column.data_type} -> {suggestion}")


def _base_in(*types: str) -> Callable[[Column], bool]:
    wanted = frozenset(types)
    return lambda column: base_type(column.data_type) in wanted


@rule(
    "O2PG-DT-001",
    "NUMBER without declared precision",
    _DT,
    Severity.MEDIUM,
    0.1,
    description=(
        "Bare NUMBER (and INTEGER, which Oracle stores as NUMBER(*,0)) accepts up to 38 "
        "significant digits. There is no single right PostgreSQL type without knowing the data."
    ),
    remediation=(
        "Map to numeric to stay safe. Profile MAX/MIN and scale per column, then narrow to "
        "integer, bigint or numeric(p,s) where the data allows. Keys joined to other tables "
        "should end up with the same type on both sides."
    ),
    ai_risk=(
        "Bare NUMBER is not an integer and not a float. Tell the model to map it to numeric "
        "unless you give it a profiled precision and scale."
    ),
)
def _number_without_precision(inventory: Inventory) -> Iterator[Hit]:
    return _columns(
        inventory,
        lambda c: base_type(c.data_type) == "NUMBER" and c.data_precision is None,
    )


@rule(
    "O2PG-DT-002",
    "Binary and approximate numeric types",
    _DT,
    Severity.LOW,
    0.1,
    description=(
        "FLOAT in Oracle is a decimal type with binary precision; BINARY_FLOAT and "
        "BINARY_DOUBLE are IEEE types with their own NaN and infinity literals."
    ),
    remediation=(
        "BINARY_FLOAT -> real, BINARY_DOUBLE -> double precision. For FLOAT use numeric when "
        "values must round-trip exactly, otherwise double precision. Replace BINARY_FLOAT_NAN "
        "style literals with 'NaN'::float8."
    ),
)
def _float_types(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"))


@rule(
    "O2PG-DT-003",
    "DATE columns carry a time of day",
    _DT,
    Severity.HIGH,
    0.25,
    description=(
        "Oracle DATE stores year to second. PostgreSQL date has no time at all, so a "
        "name-for-name mapping silently drops the time portion."
    ),
    remediation=(
        "Map to timestamp(0). Only use date after checking that every row has a midnight time "
        "(e.g. COUNT(*) WHERE col <> TRUNC(col) = 0). Review TRUNC(col) and col + n arithmetic "
        "in code at the same time."
    ),
    ai_risk=(
        "Oracle DATE includes hours, minutes and seconds. Tell the model to map DATE to "
        "timestamp(0), not date."
    ),
)
def _date_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("DATE"))


@rule(
    "O2PG-DT-004",
    "Time zone aware timestamps",
    _DT,
    Severity.MEDIUM,
    0.25,
    description=(
        "TIMESTAMP WITH TIME ZONE keeps the original offset or region in Oracle. "
        "TIMESTAMP WITH LOCAL TIME ZONE converts to the session zone. PostgreSQL timestamptz "
        "stores UTC and renders in the session TimeZone, so the original zone is lost."
    ),
    remediation=(
        "Map both to timestamptz. If the original zone matters, add a separate text column "
        "holding the IANA zone name. Set the server TimeZone parameter on Azure explicitly "
        "rather than relying on the default (UTC)."
    ),
)
def _tz_timestamps(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, lambda c: "TIME ZONE" in c.data_type.upper())


@rule(
    "O2PG-DT-005",
    "Character columns with BYTE length semantics",
    _DT,
    Severity.LOW,
    0.05,
    description=(
        "VARCHAR2(n BYTE) limits bytes, not characters. PostgreSQL varchar(n) limits "
        "characters, so the limit effectively grows for multibyte data."
    ),
    remediation=(
        "Usually harmless for the database, but application-side validation that mirrors the "
        "byte limit may now disagree with the column. Decide on character limits explicitly."
    ),
    ai_risk=(
        "VARCHAR2 lengths here are in bytes. Tell the model whether you want character "
        "lengths kept as-is or recalculated."
    ),
)
def _byte_semantics(inventory: Inventory) -> Iterator[Hit]:
    return _columns(
        inventory,
        lambda c: base_type(c.data_type) in CHARACTER_TYPES and c.char_used.upper() == "B",
    )


@rule(
    "O2PG-DT-006",
    "NOT NULL character columns will start accepting empty strings",
    _DT,
    Severity.MEDIUM,
    0.1,
    description=(
        "Oracle treats '' as NULL, so NOT NULL on a VARCHAR2 also rejects empty strings. In "
        "PostgreSQL '' is a real value and passes NOT NULL. The constraint gets weaker after "
        "migration without anyone touching it."
    ),
    remediation=(
        "Where the business rule is 'must have a value', add CHECK (col <> '') or "
        "CHECK (btrim(col) <> '') next to NOT NULL."
    ),
    ai_risk=(
        "In Oracle '' IS NULL. Tell the model that NOT NULL character columns must also "
        "reject empty strings, and to add CHECK (col <> '') where that matters."
    ),
)
def _not_null_strings(inventory: Inventory) -> Iterator[Hit]:
    return _columns(
        inventory,
        lambda c: base_type(c.data_type) in CHARACTER_TYPES and not c.nullable and not c.virtual,
    )


@rule(
    "O2PG-DT-007",
    "CHAR columns and blank padding",
    _DT,
    Severity.LOW,
    0.1,
    description=(
        "CHAR(n) pads with blanks in both databases, but comparison and concatenation rules "
        "differ. In PostgreSQL trailing blanks of character(n) are insignificant and are "
        "dropped when cast to text, which changes LENGTH() and || results."
    ),
    remediation=(
        "Prefer varchar(n) or text unless the fixed width is genuinely required. If you keep "
        "char(n), test any code that compares CHAR with VARCHAR2 values or relies on padding."
    ),
)
def _char_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(
        inventory,
        lambda c: base_type(c.data_type) in {"CHAR", "NCHAR"} and (c.char_length or 1) > 1,
    )


@rule(
    "O2PG-DT-008",
    "LONG and LONG RAW columns",
    _DT,
    Severity.HIGH,
    1.0,
    description=(
        "LONG types have been deprecated for decades and carry many restrictions. Migration "
        "tools often need special handling to extract them, and code that reads them "
        "piecewise must change."
    ),
    remediation=(
        "LONG -> text, LONG RAW -> bytea. Check that your data movement tool supports LONG "
        "extraction and test with the largest values."
    ),
)
def _long_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("LONG", "LONG RAW"))


@rule(
    "O2PG-DT-009",
    "LOB columns",
    _DT,
    Severity.MEDIUM,
    0.5,
    description=(
        "CLOB and BLOB map to text and bytea, which are limited to 1 GB per value and are "
        "read in one piece. DBMS_LOB code and locator-based APIs do not carry over."
    ),
    remediation=(
        "Map CLOB/NCLOB -> text and BLOB -> bytea. Check the largest value per column. For "
        "very large binaries consider Azure Blob Storage with a URL column instead of storing "
        "bytes in the database. Data loads of LOB-heavy tables are usually the slowest part."
    ),
)
def _lob_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("CLOB", "NCLOB", "BLOB"))


@rule(
    "O2PG-DT-010",
    "BFILE columns",
    _DT,
    Severity.BLOCKER,
    3.0,
    description=(
        "BFILE points at a file on the database server's file system. Azure Database for "
        "PostgreSQL Flexible Server gives you no file system access, so there is nothing to "
        "point at."
    ),
    remediation=(
        "Move the files to Azure Blob Storage and store the blob URL or path in a text column. "
        "File reads move to the application."
    ),
)
def _bfile_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("BFILE"))


@rule(
    "O2PG-DT-011",
    "XMLTYPE columns",
    _DT,
    Severity.MEDIUM,
    1.0,
    description=(
        "PostgreSQL has an xml type with xpath() and XMLTABLE, but no XQuery, no XML indexes "
        "and no XML schema validation. Storage options (binary XML, object-relational) do not "
        "carry over."
    ),
    remediation=(
        "Map to xml if you query it with XPath, or to jsonb if you control the format and "
        "mostly query it. Rewrite EXTRACT/EXTRACTVALUE/XMLQUERY calls."
    ),
)
def _xml_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("XMLTYPE"))


@rule(
    "O2PG-DT-012",
    "SDO_GEOMETRY (Oracle Spatial) columns",
    _DT,
    Severity.HIGH,
    2.0,
    description=(
        "Oracle Spatial geometries need PostGIS. SRIDs, spatial indexes and SDO_* functions "
        "all need translation."
    ),
    remediation=(
        "Allow-list postgis in azure.extensions, CREATE EXTENSION postgis, map to "
        "geometry(<type>, <srid>), replace spatial indexes with GiST, and rewrite SDO_RELATE / "
        "SDO_WITHIN_DISTANCE as ST_Intersects / ST_DWithin."
    ),
    extensions=("postgis",),
)
def _spatial_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("SDO_GEOMETRY"))


@rule(
    "O2PG-DT-013",
    "ROWID and UROWID columns",
    _DT,
    Severity.MEDIUM,
    0.5,
    description=(
        "Stored ROWIDs are physical addresses into Oracle. PostgreSQL's ctid changes on every "
        "update and VACUUM FULL, so it cannot replace them."
    ),
    remediation=(
        "Find what the stored ROWID points at and replace it with that row's primary key."
    ),
)
def _rowid_columns(inventory: Inventory) -> Iterator[Hit]:
    return _columns(inventory, _base_in("ROWID", "UROWID"))


@rule(
    "O2PG-DT-014",
    "Virtual columns",
    _DT,
    Severity.LOW,
    0.25,
    description=(
        "PostgreSQL generated columns are computed on write and stored. The expression must "
        "use immutable functions only, which rules out SYSDATE and most NLS-dependent calls."
    ),
    remediation=(
        "Use GENERATED ALWAYS AS (expr) STORED and translate the expression. If it is not "
        "immutable, use a view or a trigger instead."
    ),
)
def _virtual_columns(inventory: Inventory) -> Iterator[Hit]:
    for column in inventory.columns:
        if column.virtual:
            yield Hit(column.qualified, "COLUMN", f"expression: {column.data_default.strip()}")


_ORACLE_DEFAULT = re.compile(
    r"\b(SYSDATE|SYSTIMESTAMP|SYS_GUID|USER|USERENV|SYS_CONTEXT|CURRENT_DATE)\b|\.\s*\"?NEXTVAL\b",
    re.IGNORECASE,
)


@rule(
    "O2PG-DT-015",
    "Column defaults that use Oracle functions",
    _DT,
    Severity.LOW,
    0.1,
    description=(
        "Defaults such as SYSDATE, SYS_GUID(), USER or seq.NEXTVAL do not exist in PostgreSQL."
    ),
    remediation=(
        "SYSDATE -> LOCALTIMESTAMP(0) or clock_timestamp() (see the SYSDATE rule for the "
        "difference), SYS_GUID() -> gen_random_uuid(), USER -> current_user, "
        "seq.NEXTVAL -> nextval('seq') or an identity column."
    ),
)
def _oracle_defaults(inventory: Inventory) -> Iterator[Hit]:
    for column in inventory.columns:
        if column.virtual or column.identity or not column.data_default:
            continue
        if _ORACLE_DEFAULT.search(column.data_default):
            yield Hit(column.qualified, "COLUMN", f"default {column.data_default.strip()}")


@rule(
    "O2PG-DT-016",
    "Columns of user-defined object or collection types",
    _DT,
    Severity.HIGH,
    1.5,
    description=(
        "Columns typed as Oracle object types, VARRAYs or nested tables. PostgreSQL has "
        "composite types and arrays, but no methods, no nested table storage and different "
        "constructor syntax."
    ),
    remediation=(
        "Usually the cleanest answer is to normalise: nested tables and VARRAYs become child "
        "tables, object types become plain columns. Arrays or jsonb are reasonable when the "
        "data is only ever read as a whole."
    ),
)
def _udt_columns(inventory: Inventory) -> Iterator[Hit]:
    for column in inventory.columns:
        if is_user_defined(column, inventory.user_type_names):
            owner = f"{column.data_type_owner}." if column.data_type_owner else ""
            yield Hit(column.qualified, "COLUMN", f"type {owner}{column.data_type}")


_KNOWN_BUILTINS = frozenset({"SDO_GEOMETRY", "XMLTYPE"})


@rule(
    "O2PG-DT-017",
    "Data types with no automatic mapping",
    _DT,
    Severity.HIGH,
    1.0,
    description=(
        "Oracle-supplied types o2pg does not know how to map (ANYDATA, URITYPE, ORDImage, "
        "media types and so on)."
    ),
    remediation=(
        "Decide per column: jsonb for self-describing payloads, text for URIs, Blob Storage "
        "for media. Check whether the column is still used before investing in it."
    ),
)
def _unmapped(inventory: Inventory) -> Iterator[Hit]:
    for column in inventory.columns:
        if is_user_defined(column, inventory.user_type_names):
            continue
        if base_type(column.data_type) in _KNOWN_BUILTINS:
            continue
        if map_column(column).pg_type is None:
            yield Hit(column.qualified, "COLUMN", f"type {column.data_type}")
