"""Builders for small inventories used across the test suite."""

from __future__ import annotations

from typing import Any

from o2pg.inventory import Inventory

Section = dict[str, list[dict[str, Any]]]


def inventory(*parts: Section | dict[str, Any], **sections: Any) -> Inventory:
    """Merge section dicts (lists are concatenated) and build an Inventory."""
    merged: dict[str, Any] = {}
    for part in (*parts, sections):
        for key, value in part.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged[key] = value
    return Inventory.from_dict(merged)


def column(
    data_type: str = "VARCHAR2",
    name: str = "C1",
    table: str = "T1",
    owner: str = "APP",
    **fields: Any,
) -> Section:
    """One column, wrapped in a ``columns`` section."""
    row = {
        "owner": owner,
        "table_name": table,
        "name": name,
        "data_type": data_type,
        "data_length": fields.pop("data_length", 10),
        "char_length": fields.pop("char_length", 10),
        "nullable": fields.pop("nullable", "Y"),
    }
    row.update(fields)
    return {"columns": [row]}


def table(name: str = "T1", owner: str = "APP", **fields: Any) -> Section:
    """One table, wrapped in a ``tables`` section."""
    return {"tables": [{"owner": owner, "name": name, **fields}]}


def code(text: str, kind: str = "PACKAGE BODY", name: str = "P1", owner: str = "APP") -> Section:
    """Source text as ALL_SOURCE lines, or a view when ``kind`` is VIEW."""
    if kind == "VIEW":
        return {"views": [{"owner": owner, "name": name, "text": text}]}
    lines = text.splitlines(keepends=True) or [""]
    return {
        "source": [
            {"owner": owner, "name": name, "type": kind, "line": i, "text": line}
            for i, line in enumerate(lines, 1)
        ]
    }


def constraint(
    name: str, ctype: str, columns: str, table: str = "T1", owner: str = "APP", **fields: Any
) -> Section:
    """One constraint, wrapped in a ``constraints`` section."""
    return {
        "constraints": [
            {
                "owner": owner,
                "table_name": table,
                "name": name,
                "type": ctype,
                "columns": columns,
                **fields,
            }
        ]
    }


def index(name: str, columns: str, table: str = "T1", owner: str = "APP", **fields: Any) -> Section:
    """One index, wrapped in an ``indexes`` section."""
    return {
        "indexes": [
            {
                "owner": owner,
                "name": name,
                "table_owner": owner,
                "table_name": table,
                "columns": columns,
                **fields,
            }
        ]
    }


def partitioned(
    table_name: str = "T1",
    ptype: str = "RANGE",
    keys: str = "D",
    owner: str = "APP",
    **fields: Any,
) -> Section:
    """One partitioned table entry, wrapped in a ``part_tables`` section."""
    return {
        "part_tables": [
            {
                "owner": owner,
                "table_name": table_name,
                "partitioning_type": ptype,
                "partition_keys": keys,
                **fields,
            }
        ]
    }


def section(section_name: str, /, **row: Any) -> Section:
    """A single-row section with arbitrary fields."""
    return {section_name: [row]}
