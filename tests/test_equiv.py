"""Equivalence cases: parsing, normalisation, comparison and the Oracle call shapes."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from o2pg.equiv import (
    Case,
    CaseError,
    Outcome,
    arg_value,
    canon,
    compare,
    data_statements,
    load_cases,
    oracle_call_sql,
    oracle_error_name,
    oracle_script_statements,
    pg_error_name,
    updated_case_file,
)

AI = Path(__file__).parent / "fixtures" / "ai"


def _case(**kw: Any) -> Case:
    base = {"id": "c", "call": "lab.f", "kind": "function", "expect": {"value": 1}}
    base.update(kw)
    return Case(
        id=base["id"],
        call=base["call"],
        kind=base["kind"],
        args=tuple(base.get("args", ())),
        returns=base.get("returns", "text"),
        expect=base["expect"],
    )


def test_bundled_case_file_loads() -> None:
    cases = load_cases(AI / "cases.json")
    assert len(cases.cases) == 34
    assert cases.search_path == "lab, public"
    assert [p.name for p in cases.oracle_setup] == ["oracle_schema.sql", "lab.sql"]
    assert [p.name for p in cases.postgres_setup] == ["pg_schema.sql"]
    assert len(cases.data) == 10
    assert len(cases.for_unit("LAB.HIGHEST_PAID")) == 3
    assert "lab.log_event" not in cases.units


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ({"id": "a", "call": "f", "kind": "function"}, "missing 'expect'"),
        ({"id": "a", "call": "f", "kind": "view", "expect": {"value": 1}}, "kind must be"),
        (
            {"id": "a", "call": "f; drop", "kind": "function", "expect": {"value": 1}},
            "routine name",
        ),
        (
            {
                "id": "a",
                "call": "f",
                "kind": "function",
                "args": [{"out": "number"}],
                "expect": {"x": 1},
            },
            "only procedures",
        ),
        (
            {"id": "a", "call": "f", "kind": "rows", "order_by": "1; drop", "expect": {"rows": []}},
            "order_by",
        ),
        ({"id": "a", "call": "f", "kind": "function", "expect": {}}, "non-empty"),
    ],
)
def test_invalid_cases_are_rejected(tmp_path: Path, case: dict, message: str) -> None:
    with pytest.raises(CaseError, match=message):
        load_cases(_write(tmp_path, {"version": 1, "cases": [case]}))


def test_duplicate_ids_and_version(tmp_path: Path) -> None:
    case = {"id": "a", "call": "f", "kind": "function", "expect": {"value": 1}}
    with pytest.raises(CaseError, match="duplicate"):
        load_cases(_write(tmp_path, {"version": 1, "cases": [case, case]}))
    with pytest.raises(CaseError, match="version"):
        load_cases(_write(tmp_path, {"version": 2, "cases": []}))


def test_canon_compares_numbers_by_value_and_keeps_types_apart() -> None:
    assert canon(Decimal("9900.00")) == canon(9900) == ("n", "9900")
    assert canon(Decimal("1.50000000000000000000")) == canon(1.5)
    assert canon(Decimal("-0.0")) == ("n", "0")
    assert canon(["n", "1"]) == [("s", "n"), ("s", "1")]
    assert canon("2") != canon(2)
    assert canon("") != canon(None)
    assert canon(datetime(2026, 1, 2, 3, 4, 5)) == canon({"timestamp": "2026-01-02T03:04:05"})


def test_arg_values() -> None:
    assert arg_value(5) == 5
    assert arg_value(4800.5) == Decimal("4800.5")
    assert arg_value({"timestamp": "2026-01-01T00:00:00"}) == datetime(2026, 1, 1)
    with pytest.raises(CaseError):
        arg_value(True)


def test_compare_value_null_and_empty_string() -> None:
    case = _case(expect={"value": None})
    assert compare(case, Outcome(value=None)) == []
    assert compare(case, Outcome(value=canon(""))) == ["returned '', expected NULL"]


def test_compare_errors() -> None:
    case = _case(expect={"error": "NO_DATA_FOUND"})
    assert compare(case, Outcome(error="NO_DATA_FOUND")) == []
    assert "succeeded" in compare(case, Outcome(value=canon(1)))[0]
    assert "got TOO_MANY_ROWS" in compare(case, Outcome(error="TOO_MANY_ROWS", message="x"))[0]
    value_case = _case(expect={"value": 1})
    assert compare(value_case, Outcome(error="ZERO_DIVIDE", message="division by zero")) == [
        "raised ZERO_DIVIDE: division by zero"
    ]
    assert compare(_case(expect={"error": "VALUE_ERROR"}), Outcome(error="INVALID_NUMBER")) == []


def test_compare_out_and_rows() -> None:
    case = _case(kind="procedure", expect={"out": [3], "rows": [[1, "a"], [2, "b"]]})
    good = Outcome(out=canon([Decimal(3)]), rows=canon([[1, "a"], [2, "b"]]))
    assert compare(case, good) == []
    bad = Outcome(out=canon([1]), rows=canon([[1, "a"]]))
    assert compare(case, bad) == ["OUT parameters (1), expected (3)", "1 rows, expected 2"]


def test_compare_reports_setup_errors() -> None:
    assert compare(_case(), Outcome(setup_error="boom")) == ["could not run: boom"]


def test_error_names() -> None:
    assert oracle_error_name(1403) == "NO_DATA_FOUND"
    assert oracle_error_name(20001) == "APPLICATION_ERROR"
    assert oracle_error_name(942) == "ORA-00942"
    assert pg_error_name("P0002") == "NO_DATA_FOUND"
    assert pg_error_name("42883") == "SQLSTATE 42883"


def test_oracle_call_sql_uses_a_plsql_block_for_functions() -> None:
    sql, names = oracle_call_sql(_case(args=(1, 2)))
    assert sql == "BEGIN :ret := lab.f(:a1, :a2); END;"
    assert names == ["a1", "a2"]
    proc = Case("p", "lab.p", "procedure", (1, {"out": "number"}), expect={"out": [1]})
    assert oracle_call_sql(proc)[0] == "BEGIN lab.p(:a1, :a2); END;"
    rows = Case("r", "lab.r", "rows", (1,), order_by="1", expect={"rows": []})
    assert oracle_call_sql(rows)[0] == "SELECT * FROM TABLE(lab.r(:a1)) ORDER BY 1"


def test_script_splitting() -> None:
    statements = oracle_script_statements((AI / "lab.sql").read_text(encoding="utf-8"))
    assert len(statements) == 14
    assert all(s.startswith("CREATE OR REPLACE") for s in statements)
    assert statements[0].rstrip().endswith("END highest_paid;")
    assert data_statements("-- c\nINSERT INTO t VALUES (1);\n\n  INSERT INTO t VALUES (2)\n") == [
        "INSERT INTO t VALUES (1)",
        "INSERT INTO t VALUES (2)",
    ]


def test_recorded_expectations_round_trip() -> None:
    cases = load_cases(AI / "cases.json")
    case = cases.cases[0]
    outcome = Outcome(value=canon(Decimal("2")))
    assert outcome.as_expectation(case) == {"value": 2}
    raise_case = next(c for c in cases.cases if c.id == "raise_dept.engineering")
    recorded = Outcome(out=canon([Decimal(3)]), rows=canon([[1, Decimal("5280.55")]]))
    assert recorded.as_expectation(raise_case) == {"out": [3], "rows": [[1, 5280.55]]}
    error = Outcome(error="NO_DATA_FOUND").as_expectation(case)
    text = updated_case_file(cases, {case.id: error})
    assert json.loads(text)["cases"][0]["expect"] == {"error": "NO_DATA_FOUND"}


# ---------------------------------------------------------------- Oracle runner, faked


class _Var:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def getvalue(self) -> Any:
        return self.value


class _Cursor:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.executed: list[tuple[str, Any]] = []
        self._rows: list[Any] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def var(self, *args: Any, **kwargs: Any) -> _Var:
        return _Var()

    def execute(self, sql: str, binds: Any = None) -> None:
        self.executed.append((sql, binds))
        action = self.script.get(sql)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            action(binds)
        elif isinstance(action, list):
            self._rows = action

    def fetchall(self) -> list[Any]:
        return self._rows


class _Conn:
    def __init__(self, script: dict[str, Any]) -> None:
        self.cursor_obj = _Cursor(script)
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.cursor_obj

    def rollback(self) -> None:
        self.rollbacks += 1


def test_oracle_runner_reads_numbers_exactly_and_maps_errors() -> None:
    oracledb = pytest.importorskip("oracledb")
    from o2pg.equiv import OracleRunner

    def set_ret(binds: dict) -> None:
        binds["ret"].value = "5280.55"

    conn = _Conn({"BEGIN :ret := lab.f(:a1); END;": set_ret})
    runner = OracleRunner(conn)
    outcome = runner.run(_case(args=(1,), returns="number"), ["INSERT INTO t VALUES (1)"])
    assert outcome.value == ("n", "5280.55")
    assert conn.rollbacks == 1
    assert conn.cursor_obj.executed[0][0] == "INSERT INTO t VALUES (1)"

    class _Err:
        code = 1403
        message = "ORA-01403: no data found\nORA-06512: at line 1"

    failing = _Conn({"BEGIN :ret := lab.f(:a1); END;": oracledb.DatabaseError(_Err())})
    outcome = OracleRunner(failing).run(_case(args=(1,), returns="number"))
    assert outcome.error == "NO_DATA_FOUND"
    assert outcome.message == "no data found"


def test_oracle_runner_out_parameters_and_then_query() -> None:
    pytest.importorskip("oracledb")
    from o2pg.equiv import OracleRunner

    def set_out(binds: dict) -> None:
        binds["a3"].value = "3"

    case = Case(
        "p",
        "lab.p",
        "procedure",
        (20, 10, {"out": "number"}),
        then="SELECT x FROM t",
        expect={"out": [3]},
    )
    conn = _Conn(
        {"BEGIN lab.p(:a1, :a2, :a3); END;": set_out, "SELECT x FROM t": [[Decimal("1.5")]]}
    )
    outcome = OracleRunner(conn).run(case)
    assert outcome.out == [("n", "3")]
    assert outcome.rows == [[("n", "1.5")]]
