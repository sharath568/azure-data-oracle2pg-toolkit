"""Inventory loading and coercion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import code, column, inventory, section
from o2pg.inventory import Inventory, InventoryError


def test_keys_are_case_insensitive_and_unknown_keys_ignored() -> None:
    inv = Inventory.from_dict(
        {"TABLES": [{"OWNER": "HR", "NAME": "EMP", "NUM_ROWS": "12", "SOMETHING": 1}]}
    )
    assert inv.tables[0].qualified == "HR.EMP"
    assert inv.tables[0].num_rows == 12


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Y", True), ("YES", True), ("ENABLED", True), (True, True), ("N", False), ("NO", False)],
)
def test_bool_coercion(raw: object, expected: bool) -> None:
    inv = Inventory.from_dict({"tables": [{"owner": "A", "name": "T", "partitioned": raw}]})
    assert inv.tables[0].partitioned is expected


def test_int_coercion_handles_floats_and_blanks() -> None:
    inv = inventory(column("NUMBER", data_precision="10.0", data_scale=""))
    assert inv.columns[0].data_precision == 10
    assert inv.columns[0].data_scale is None


def test_list_fields_accept_comma_strings_and_arrays() -> None:
    inv = Inventory.from_dict(
        {
            "constraints": [
                {"owner": "A", "name": "PK", "columns": "A, B"},
                {"owner": "A", "name": "UK", "columns": ["C"]},
            ]
        }
    )
    assert inv.constraints[0].columns == ("A", "B")
    assert inv.constraints[1].columns == ("C",)


def test_null_strings_become_empty() -> None:
    inv = inventory(section("synonyms", owner="A", name="S", db_link=None))
    assert inv.synonyms[0].db_link == ""


def test_source_lines_are_assembled_in_order() -> None:
    inv = Inventory.from_dict(
        {
            "source": [
                {"owner": "A", "name": "P", "type": "procedure", "line": 2, "text": "END;\n"},
                {"owner": "A", "name": "P", "type": "procedure", "line": 1, "text": "BEGIN"},
            ]
        }
    )
    assert inv.source[0].type == "PROCEDURE"
    assert inv.source[0].text == "BEGIN\nEND;\n"


def test_code_units_cover_source_views_and_plsql_jobs() -> None:
    inv = inventory(
        code("BEGIN NULL; END;", kind="PROCEDURE"),
        code("SELECT 1 FROM dual", kind="VIEW", name="V"),
        section("scheduler_jobs", owner="A", name="J1", job_type="PLSQL_BLOCK", job_action="x;"),
        section("scheduler_jobs", owner="A", name="J2", job_type="EXECUTABLE", job_action="/x"),
    )
    kinds = {(u.kind, u.plsql) for u in inv.code_units}
    assert kinds == {("PROCEDURE", True), ("VIEW", False), ("SCHEDULER JOB", True)}


def test_object_counts_include_plsql_lines() -> None:
    inv = inventory(code("A\nB\nC\n", kind="PROCEDURE"), column("DATE"))
    counts = inv.object_counts()
    assert counts["plsql_lines"] == 3
    assert counts["columns"] == 1
    assert counts["source_objects"] == 1


def test_from_json_recovers_from_sqlplus_line_wrapping() -> None:
    payload = json.dumps({"tables": [{"owner": "HR", "name": "EMPLOYEES"}]})
    wrapped = payload[:20] + "\n" + payload[20:]
    assert Inventory.from_json(wrapped).tables[0].name == "EMPLOYEES"


def test_from_json_strips_bom() -> None:
    assert Inventory.from_json('﻿{"tables": []}').tables == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "top level"),
        ('{"tables": {"a": 1}}', "must be a list"),
        ('{"source": "x"}', "must be a list"),
        ('{"tables": [1]}', "expected an object"),
        ('{"source": [1]}', "must be objects"),
    ],
)
def test_bad_inventories_raise(text: str, message: str) -> None:
    with pytest.raises(InventoryError, match=message):
        Inventory.from_json(text)


def test_from_path_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InventoryError, match="cannot read"):
        Inventory.from_path(tmp_path / "missing.json")


def test_metadata_schemas_split() -> None:
    inv = Inventory.from_dict({"metadata": {"schemas": "HR,SALES", "db_name": "X"}})
    assert inv.metadata.schemas == ("HR", "SALES")
