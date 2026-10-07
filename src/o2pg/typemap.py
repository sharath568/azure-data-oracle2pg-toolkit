"""Oracle to PostgreSQL data type mapping.

The mapping is deliberately conservative: when there is a choice between a type
that is always correct and one that is usually correct, it picks the former and
says why in ``note``. Rules use :func:`map_column` and :func:`base_type`; the
``map-types`` command uses it to suggest DDL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from o2pg.inventory import Column

ORACLE_MAINTAINED_TYPE_OWNERS = frozenset({"SYS", "PUBLIC", "MDSYS", "XDB", "ORDSYS", "CTXSYS"})

CHARACTER_TYPES = frozenset({"VARCHAR2", "VARCHAR", "NVARCHAR2", "CHAR", "NCHAR"})

_PRECISION = re.compile(r"\(\s*\d+\s*\)")


@dataclass(frozen=True)
class TypeMapping:
    """Suggested PostgreSQL type for an Oracle column.

    ``pg_type`` is None when there is no sensible automatic mapping. ``exact`` is
    False when the suggestion changes semantics in a way someone should review.
    """

    oracle_type: str
    pg_type: str | None
    exact: bool = True
    note: str = ""


def base_type(data_type: str) -> str:
    """Normalise an ALL_TAB_COLS data type by dropping precisions and upper-casing.

    ``TIMESTAMP(6) WITH TIME ZONE`` becomes ``TIMESTAMP WITH TIME ZONE``.
    """
    return " ".join(_PRECISION.sub("", data_type).upper().split())


def _number(column: Column) -> TypeMapping:
    precision, scale = column.data_precision, column.data_scale
    label = "NUMBER"
    if precision is None and scale is None:
        return TypeMapping(
            label,
            "numeric",
            exact=True,
            note=(
                "NUMBER without precision; numeric is safe, pick a narrower type only after "
                "profiling the data"
            ),
        )
    if precision is None:
        return TypeMapping(
            f"{label}(*,{scale})",
            f"numeric(38,{scale})" if scale else "numeric(38)",
            note="declared as INTEGER or NUMBER(*,s); Oracle allows up to 38 digits",
        )
    if scale is None or scale == 0:
        oracle = f"{label}({precision})"
        if precision <= 4:
            return TypeMapping(oracle, "smallint")
        if precision <= 9:
            return TypeMapping(oracle, "integer")
        if precision <= 18:
            return TypeMapping(oracle, "bigint")
        return TypeMapping(oracle, f"numeric({precision})")
    oracle = f"{label}({precision},{scale})"
    if scale < 0:
        return TypeMapping(
            oracle,
            f"numeric({precision},{scale})",
            exact=False,
            note="negative scale needs PostgreSQL 15 or later",
        )
    if scale > precision:
        return TypeMapping(
            oracle,
            f"numeric({scale},{scale})",
            exact=False,
            note="scale greater than precision is legal in Oracle; widened for PostgreSQL",
        )
    return TypeMapping(oracle, f"numeric({precision},{scale})")


def _character(column: Column, kind: str) -> TypeMapping:
    length = column.char_length or column.data_length or 1
    byte = column.char_used.upper() == "B"
    oracle = f"{kind}({length}{' BYTE' if byte else ' CHAR' if column.char_used else ''})"
    pg = f"varchar({length})" if kind in {"VARCHAR2", "VARCHAR", "NVARCHAR2"} else f"char({length})"
    note = ""
    if byte:
        note = "Oracle length is in bytes, PostgreSQL length is in characters"
    if kind in {"CHAR", "NCHAR"}:
        note = (note + "; " if note else "") + "blank-padded comparison rules differ"
    return TypeMapping(oracle, pg, exact=kind not in {"CHAR", "NCHAR"}, note=note)


def _timestamp(data_type: str, column: Column) -> TypeMapping:
    upper = data_type.upper()
    scale = column.data_scale if column.data_scale is not None else 6
    pg_scale = min(scale, 6)
    note = "fractional seconds above 6 digits are truncated" if scale > 6 else ""
    if "LOCAL TIME ZONE" in upper:
        return TypeMapping(
            data_type,
            f"timestamptz({pg_scale})",
            exact=False,
            note=(note + "; " if note else "") + "session time zone conversion rules differ",
        )
    if "TIME ZONE" in upper:
        return TypeMapping(
            data_type,
            f"timestamptz({pg_scale})",
            exact=False,
            note=(note + "; " if note else "")
            + "PostgreSQL normalises to UTC and does not keep the original offset",
        )
    return TypeMapping(data_type, f"timestamp({pg_scale})", exact=not note, note=note)


_SIMPLE: dict[str, TypeMapping] = {
    "DATE": TypeMapping("DATE", "timestamp(0)", True, "Oracle DATE carries a time to the second"),
    "BINARY_FLOAT": TypeMapping("BINARY_FLOAT", "real"),
    "BINARY_DOUBLE": TypeMapping("BINARY_DOUBLE", "double precision"),
    "FLOAT": TypeMapping(
        "FLOAT",
        "double precision",
        False,
        "Oracle FLOAT is decimal; use numeric if exact values matter",
    ),
    "CLOB": TypeMapping("CLOB", "text", True, "1 GB limit per value in PostgreSQL"),
    "NCLOB": TypeMapping("NCLOB", "text", True, "1 GB limit per value in PostgreSQL"),
    "LONG": TypeMapping("LONG", "text"),
    "BLOB": TypeMapping("BLOB", "bytea", True, "1 GB limit per value in PostgreSQL"),
    "LONG RAW": TypeMapping("LONG RAW", "bytea"),
    "BFILE": TypeMapping(
        "BFILE",
        "text",
        False,
        "no equivalent; store a path or URL and move the file to object storage",
    ),
    "ROWID": TypeMapping("ROWID", "text", False, "no stable physical row address in PostgreSQL"),
    "UROWID": TypeMapping("UROWID", "text", False, "no stable physical row address in PostgreSQL"),
    "XMLTYPE": TypeMapping("XMLTYPE", "xml", False, "XPath and XQuery functions differ"),
    "SDO_GEOMETRY": TypeMapping(
        "SDO_GEOMETRY", "geometry", False, "requires the postgis extension"
    ),
    "INTERVAL YEAR TO MONTH": TypeMapping("INTERVAL YEAR TO MONTH", "interval year to month"),
    "INTERVAL DAY TO SECOND": TypeMapping("INTERVAL DAY TO SECOND", "interval day to second"),
    "ANYDATA": TypeMapping("ANYDATA", None, False, "no equivalent; consider jsonb"),
}


def map_column(column: Column) -> TypeMapping:
    """Return the suggested PostgreSQL type for an Oracle column."""
    data_type = column.data_type.upper().strip()
    base = base_type(data_type)
    if base == "NUMBER":
        return _number(column)
    if base in CHARACTER_TYPES:
        return _character(column, base)
    if base.startswith("TIMESTAMP"):
        return _timestamp(data_type, column)
    if base == "RAW":
        length = column.data_length or 0
        if length == 16:
            return TypeMapping("RAW(16)", "uuid", False, "use uuid only if the column holds GUIDs")
        return TypeMapping(f"RAW({length})", "bytea")
    if base in _SIMPLE:
        return _SIMPLE[base]
    return TypeMapping(data_type, None, False, "no automatic mapping")


def is_user_defined(column: Column, user_types: frozenset[str] = frozenset()) -> bool:
    """True when the column's type is a user-defined object or collection type."""
    if column.data_type.upper() in user_types:
        return True
    owner = column.data_type_owner.upper()
    return bool(owner) and owner not in ORACLE_MAINTAINED_TYPE_OWNERS
