"""Oracle to PostgreSQL type mapping."""

from __future__ import annotations

import pytest

from o2pg.inventory import Column
from o2pg.typemap import base_type, is_user_defined, map_column


def _col(data_type: str, **fields: object) -> Column:
    return Column(owner="A", table_name="T", name="C", data_type=data_type, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (_col("NUMBER"), "numeric"),
        (_col("NUMBER", data_scale=0), "numeric(38)"),
        (_col("NUMBER", data_scale=2), "numeric(38,2)"),
        (_col("NUMBER", data_precision=1, data_scale=0), "smallint"),
        (_col("NUMBER", data_precision=4, data_scale=0), "smallint"),
        (_col("NUMBER", data_precision=5, data_scale=0), "integer"),
        (_col("NUMBER", data_precision=9, data_scale=0), "integer"),
        (_col("NUMBER", data_precision=10, data_scale=0), "bigint"),
        (_col("NUMBER", data_precision=18, data_scale=0), "bigint"),
        (_col("NUMBER", data_precision=19, data_scale=0), "numeric(19)"),
        (_col("NUMBER", data_precision=10, data_scale=2), "numeric(10,2)"),
        (_col("NUMBER", data_precision=5, data_scale=-2), "numeric(5,-2)"),
        (_col("NUMBER", data_precision=2, data_scale=4), "numeric(4,4)"),
        (_col("VARCHAR2", char_length=30, char_used="B"), "varchar(30)"),
        (_col("NVARCHAR2", char_length=30, char_used="C"), "varchar(30)"),
        (_col("CHAR", char_length=2, char_used="B"), "char(2)"),
        (_col("NCHAR", char_length=3), "char(3)"),
        (_col("DATE"), "timestamp(0)"),
        (_col("TIMESTAMP(6)", data_scale=6), "timestamp(6)"),
        (_col("TIMESTAMP(9)", data_scale=9), "timestamp(6)"),
        (_col("TIMESTAMP(3) WITH TIME ZONE", data_scale=3), "timestamptz(3)"),
        (_col("TIMESTAMP(6) WITH LOCAL TIME ZONE", data_scale=6), "timestamptz(6)"),
        (_col("INTERVAL YEAR(2) TO MONTH"), "interval year to month"),
        (_col("INTERVAL DAY(2) TO SECOND(6)"), "interval day to second"),
        (_col("FLOAT"), "double precision"),
        (_col("BINARY_FLOAT"), "real"),
        (_col("BINARY_DOUBLE"), "double precision"),
        (_col("CLOB"), "text"),
        (_col("NCLOB"), "text"),
        (_col("LONG"), "text"),
        (_col("BLOB"), "bytea"),
        (_col("LONG RAW"), "bytea"),
        (_col("RAW", data_length=16), "uuid"),
        (_col("RAW", data_length=32), "bytea"),
        (_col("ROWID"), "text"),
        (_col("XMLTYPE"), "xml"),
        (_col("SDO_GEOMETRY"), "geometry"),
        (_col("BFILE"), "text"),
        (_col("ANYDATA"), None),
        (_col("SOMETHING_ELSE"), None),
    ],
)
def test_map_column(column: Column, expected: str | None) -> None:
    assert map_column(column).pg_type == expected


@pytest.mark.parametrize(
    ("column", "exact"),
    [
        (_col("VARCHAR2", char_length=10, char_used="B"), True),
        (_col("CHAR", char_length=10, char_used="B"), False),
        (_col("TIMESTAMP(9)", data_scale=9), False),
        (_col("BFILE"), False),
        (_col("DATE"), True),
    ],
)
def test_exactness(column: Column, exact: bool) -> None:
    assert map_column(column).exact is exact


def test_byte_semantics_note() -> None:
    assert "bytes" in map_column(_col("VARCHAR2", char_length=10, char_used="B")).note


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TIMESTAMP(6) WITH TIME ZONE", "TIMESTAMP WITH TIME ZONE"),
        ("interval day(2) to second(6)", "INTERVAL DAY TO SECOND"),
        ("NUMBER", "NUMBER"),
    ],
)
def test_base_type(raw: str, expected: str) -> None:
    assert base_type(raw) == expected


def test_is_user_defined() -> None:
    assert is_user_defined(_col("ADDR_T", data_type_owner="APP"))
    assert is_user_defined(_col("ADDR_T"), frozenset({"ADDR_T"}))
    assert not is_user_defined(_col("XMLTYPE", data_type_owner="SYS"))
    assert not is_user_defined(_col("SDO_GEOMETRY", data_type_owner="MDSYS"))
    assert not is_user_defined(_col("DATE"))
