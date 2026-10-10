"""Map PL/SQL type names (parameters, variables, return types) to PostgreSQL.

Column-style types go through :func:`o2pg.typemap.map_column`, so a
``VARCHAR2(30)`` variable gets the same type as a ``VARCHAR2(30)`` column in
``o2pg map-types``. PL/SQL-only types are handled here. PostgreSQL spellings
that the converter itself produces map to themselves, so running the converter
on its own output changes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from o2pg.inventory import Column
from o2pg.typemap import map_column

_TYPE_ATOM = (
    r"(?:"
    r"DOUBLE\s+PRECISION"
    r"|LONG\s+RAW"
    r"|INTERVAL\s+(?:YEAR|DAY)(?:\s*\(\s*\d+\s*\))?\s+TO\s+(?:MONTH|SECOND)(?:\s*\(\s*\d+\s*\))?"
    r"|TIMESTAMP(?:\s*\(\s*\d+\s*\))?(?:\s+WITH(?:\s+LOCAL)?\s+TIME\s+ZONE)?"
    r"|CHARACTER\s+VARYING(?:\s*\([^()]*\))?"
    r"|[A-Za-z_][\w$#]*(?:\s*\.\s*[A-Za-z_][\w$#]*)*(?:\s*%\s*(?:ROW)?TYPE\b)?(?:\s*\([^()]*\))?"
    r")"
)
TYPE_PATTERN = _TYPE_ATOM
"""Regex (no groups) for one PL/SQL or PostgreSQL type reference."""

_ANCHORED = re.compile(r"%\s*(?:ROW)?TYPE$", re.IGNORECASE)
_NAME_ARGS = re.compile(r"^(?P<name>[A-Z_][A-Z0-9_$# ]*?)\s*(?:\((?P<args>[^()]*)\))?$")

_INTEGER_TYPES = frozenset(
    {
        "PLS_INTEGER",
        "BINARY_INTEGER",
        "SIMPLE_INTEGER",
        "NATURAL",
        "NATURALN",
        "POSITIVE",
        "POSITIVEN",
        "SIGNTYPE",
        "INTEGER",
        "INT",
        "INT4",
    }
)
_FIXED: dict[str, str] = {
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "SMALLINT": "smallint",
    "INT2": "smallint",
    "BIGINT": "bigint",
    "INT8": "bigint",
    "REAL": "real",
    "FLOAT4": "real",
    "FLOAT8": "double precision",
    "DOUBLE PRECISION": "double precision",
    "TEXT": "text",
    "BYTEA": "bytea",
    "REFCURSOR": "refcursor",
    "SYS_REFCURSOR": "refcursor",
    "RECORD": "record",
    "UUID": "uuid",
    "JSON": "json",
    "JSONB": "jsonb",
    "XML": "xml",
    "XMLTYPE": "xml",
    "SYS.XMLTYPE": "xml",
    "TIMESTAMPTZ": "timestamptz",
    "INTERVAL": "interval",
    "CLOB": "text",
    "NCLOB": "text",
    "LONG": "text",
    "BLOB": "bytea",
    "LONG RAW": "bytea",
    "BINARY_FLOAT": "real",
    "BINARY_DOUBLE": "double precision",
}
_CHARACTER = frozenset(
    {
        "VARCHAR2",
        "VARCHAR",
        "NVARCHAR2",
        "CHAR",
        "NCHAR",
        "CHARACTER",
        "STRING",
        "CHARACTER VARYING",
    }
)
_NUMERIC = frozenset({"NUMBER", "NUMERIC", "DECIMAL", "DEC"})


@dataclass(frozen=True)
class PgType:
    """A mapped type. ``family`` is ``date``, ``integer``, ``numeric`` or ``other``."""

    text: str
    family: str = "other"


def _family(pg: str) -> str:
    lowered = pg.lower()
    if lowered.startswith(("timestamp", "date")):
        return "date"
    if lowered in {"integer", "smallint", "bigint"}:
        return "integer"
    if lowered.startswith(("numeric", "real", "double")):
        return "numeric"
    return "other"


def map_plsql_type(text: str, aliases: dict[str, str] | None = None) -> PgType | None:
    """Return the PostgreSQL type for a PL/SQL type reference, or None if unknown.

    ``aliases`` maps upper-case user type names (REF CURSOR types) to a
    PostgreSQL type. Anchored types (``%TYPE``, ``%ROWTYPE``) are returned as is.
    """
    raw = " ".join(text.split())
    if not raw:
        return None
    if _ANCHORED.search(raw):
        return PgType(re.sub(r"\s*%\s*", "%", raw))
    upper = raw.upper()
    if aliases and upper in aliases:
        return PgType(aliases[upper])
    if upper in _FIXED:
        return PgType(_FIXED[upper], _family(_FIXED[upper]))
    if upper in _INTEGER_TYPES:
        return PgType("integer", "integer")
    if upper == "DATE":
        return PgType("timestamp(0)", "date")
    if upper.startswith(("TIMESTAMP", "INTERVAL")):
        return _timestamp_or_interval(upper)
    match = _NAME_ARGS.match(upper)
    if not match:
        return None
    name = " ".join(match.group("name").split())
    args = match.group("args")
    if name in _CHARACTER:
        return _character(name, args)
    if name in _NUMERIC:
        return _numeric(args)
    if name == "FLOAT":
        return PgType("double precision", "numeric")
    if name == "RAW":
        return PgType("bytea")
    if name == "BYTEA" or name == "BIT":
        return PgType(name.lower() + (f"({args})" if args else ""))
    return None


def _character(name: str, args: str | None) -> PgType:
    kind = "CHAR" if name in {"CHAR", "NCHAR", "CHARACTER"} else "VARCHAR2"
    if not args:
        return PgType("char" if kind == "CHAR" else "varchar")
    parts = args.replace(",", " ").split()
    try:
        length = int(parts[0])
    except (ValueError, IndexError):
        return PgType("char" if kind == "CHAR" else "varchar")
    used = "B" if "BYTE" in parts[1:] else "C"
    mapping = map_column(
        Column(data_type=kind, char_length=length, data_length=length, char_used=used)
    )
    return PgType(mapping.pg_type or "varchar")


def _numeric(args: str | None) -> PgType:
    if not args:
        return PgType("numeric", "numeric")
    parts = [p.strip() for p in args.split(",")]
    try:
        precision = None if parts[0] == "*" else int(parts[0])
        scale = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return PgType("numeric", "numeric")
    mapping = map_column(Column(data_type="NUMBER", data_precision=precision, data_scale=scale))
    pg = mapping.pg_type or "numeric"
    return PgType(pg, _family(pg))


def _timestamp_or_interval(upper: str) -> PgType | None:
    if upper.startswith("INTERVAL"):
        if "YEAR" in upper:
            return PgType("interval year to month")
        if "DAY" in upper:
            return PgType("interval day to second")
        return PgType("interval")
    if upper.startswith("TIMESTAMPTZ"):
        return PgType(upper.lower(), "date")
    scale_match = re.search(r"\(\s*(\d+)\s*\)", upper)
    scale = int(scale_match.group(1)) if scale_match else None
    base = re.sub(r"\s*\(\s*\d+\s*\)", "", upper)
    mapping = map_column(Column(data_type=base, data_scale=scale))
    if mapping.pg_type is None:
        return None
    return PgType(mapping.pg_type, "date")
