"""Behaviour equivalence cases: record results on Oracle, check them on PostgreSQL.

A case file (JSON) lists calls to stored routines and what Oracle returned for
each one: a value, OUT parameter values, rows, or a named error. ``o2pg equiv
record`` replays the cases on Oracle (to create or verify the expectations) and
``o2pg equiv check`` replays them on PostgreSQL against converted code.

Every case runs in its own transaction, which is rolled back afterwards, so the
case data (the file named by ``data``) is loaded fresh each time and routines
that write leave nothing behind.

Values are compared after normalisation: numbers by decimal value (``9900`` and
``9900.00`` are equal), timestamps to the second, strings exactly. An empty
string and NULL are *different* values; Oracle never returns ``''``, so a
PostgreSQL routine that does is reported.

Two Oracle details the harness takes care of:

* A function that raises NO_DATA_FOUND inside a SQL query returns NULL in
  Oracle instead of an error. Functions are therefore called from a PL/SQL
  block (``BEGIN :ret := f(...); END;``), where the error surfaces.
* Binding ``''`` sends NULL, exactly as an application would experience it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

KINDS = ("function", "procedure", "rows")
RETURN_TYPES = ("number", "text", "timestamp")
_CALL_NAME = re.compile(r"^[A-Za-z_][\w$#]*(\.[A-Za-z_][\w$#]*){0,2}$")
_ORDER_BY = re.compile(r"^[\w\s,.]+$")


class CaseError(ValueError):
    """A case file that cannot be used."""


# --------------------------------------------------------------------------------------
# Case files
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One call and the result Oracle gave for it."""

    id: str
    call: str
    kind: str
    args: tuple[Any, ...] = ()
    returns: str = "text"
    then: str = ""
    order_by: str = ""
    expect: dict[str, Any] = field(default_factory=dict)

    @property
    def unit(self) -> str:
        """Lower-case routine name, matching ``ConvertedUnit.name``."""
        return self.call.lower()

    @property
    def out_count(self) -> int:
        """Number of OUT parameters in ``args``."""
        return sum(1 for a in self.args if _is_out(a))


@dataclass
class CaseFile:
    """A parsed case file plus the directory its relative paths resolve against."""

    path: Path
    cases: list[Case]
    search_path: str = "public"
    oracle_setup: list[Path] = field(default_factory=list)
    postgres_setup: list[Path] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def for_unit(self, unit: str) -> list[Case]:
        """Cases that call ``unit`` (case-insensitive)."""
        return [c for c in self.cases if c.unit == unit.lower()]

    @property
    def units(self) -> set[str]:
        """Every routine some case calls."""
        return {c.unit for c in self.cases}


def _is_out(arg: Any) -> bool:
    return isinstance(arg, dict) and "out" in arg


def load_cases(path: Path) -> CaseFile:
    """Read and validate a case file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise CaseError(f'{path}: expected a JSON object with "version": 1')
    base = path.parent
    seen: set[str] = set()
    cases = []
    for index, item in enumerate(raw.get("cases") or []):
        case = _parse_case(item, f"{path}: case {index + 1}")
        if case.id in seen:
            raise CaseError(f"{path}: duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    data_file = raw.get("data")
    return CaseFile(
        path=path,
        cases=cases,
        search_path=str(raw.get("search_path") or "public"),
        oracle_setup=[base / p for p in (raw.get("oracle") or {}).get("setup", [])],
        postgres_setup=[base / p for p in (raw.get("postgres") or {}).get("setup", [])],
        data=data_statements((base / data_file).read_text(encoding="utf-8")) if data_file else [],
        raw=raw,
    )


def _parse_case(item: Any, where: str) -> Case:
    if not isinstance(item, dict):
        raise CaseError(f"{where}: not an object")
    for key in ("id", "call", "kind", "expect"):
        if key not in item:
            raise CaseError(f"{where}: missing {key!r}")
    kind = item["kind"]
    if kind not in KINDS:
        raise CaseError(f"{where}: kind must be one of {', '.join(KINDS)}")
    call = str(item["call"])
    if not _CALL_NAME.match(call):
        raise CaseError(f"{where}: call must be a routine name such as schema.name")
    returns = item.get("returns", "text")
    if returns not in RETURN_TYPES:
        raise CaseError(f"{where}: returns must be one of {', '.join(RETURN_TYPES)}")
    order_by = str(item.get("order_by", ""))
    if order_by and not _ORDER_BY.match(order_by):
        raise CaseError(f"{where}: order_by may only hold column names or positions")
    args = tuple(item.get("args") or [])
    if kind != "procedure" and any(_is_out(a) for a in args):
        raise CaseError(f"{where}: only procedures take OUT arguments")
    expect = item["expect"]
    if not isinstance(expect, dict) or not expect:
        raise CaseError(f"{where}: expect must be a non-empty object")
    return Case(
        id=str(item["id"]),
        call=call,
        kind=kind,
        args=args,
        returns=returns,
        then=str(item.get("then", "")),
        order_by=order_by,
        expect=expect,
    )


def data_statements(text: str) -> list[str]:
    """One statement per non-blank line, ignoring ``--`` comment lines."""
    out = []
    for line in text.splitlines():
        stripped = line.strip().rstrip(";").strip()
        if stripped and not stripped.startswith("--"):
            out.append(stripped)
    return out


def oracle_script_statements(text: str) -> list[str]:
    """Split an Oracle script on lines holding only ``/``; drop leading comments."""
    chunks = re.split(r"^[ \t]*/[ \t]*$", text, flags=re.MULTILINE)
    out = []
    for chunk in chunks:
        lines = chunk.strip().splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)
        body = "\n".join(lines).strip()
        if body:
            out.append(body)
    return out


# --------------------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------------------


def arg_value(arg: Any) -> Any:
    """Turn a JSON argument into the Python value to bind."""
    if isinstance(arg, dict):
        if "timestamp" in arg:
            return datetime.fromisoformat(str(arg["timestamp"]))
        if "number" in arg:
            return Decimal(str(arg["number"]))
        if "text" in arg:
            return str(arg["text"])
        raise CaseError(f"unknown argument form {arg!r}")
    if isinstance(arg, bool):
        raise CaseError("booleans are not portable between Oracle and PostgreSQL; use 1 or 0")
    if isinstance(arg, float):
        return Decimal(str(arg))
    return arg


def canon(value: Any) -> Any:
    """A comparable form: ``("n", "3.5")``, ``("s", "x")``, ``("t", iso)``, None; lists for rows."""
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2 and value[0] in _TAGS:
        return value
    if isinstance(value, bool):
        return ("n", "1" if value else "0")
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value)).normalize()
        except InvalidOperation:
            return ("s", str(value))
        text = format(number, "f")
        return ("n", "0" if text in {"-0", "0"} else text)
    if isinstance(value, datetime):
        return ("t", value.replace(tzinfo=None).isoformat(timespec="seconds"))
    if isinstance(value, date):
        return ("t", datetime(value.year, value.month, value.day).isoformat(timespec="seconds"))
    if isinstance(value, dict) and "timestamp" in value:
        return canon(datetime.fromisoformat(str(value["timestamp"])))
    if isinstance(value, (list, tuple)):
        return [canon(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ("s", bytes(value).hex())
    return ("s", str(value))


_TAGS = ("n", "s", "t")


def _scalar(value: Any) -> bool:
    return isinstance(value, tuple) and len(value) == 2 and value[0] in _TAGS


def show(value: Any) -> str:
    """Human-readable form of a canonical value."""
    if value is None:
        return "NULL"
    if _scalar(value):
        kind, text = value
        return repr(text) if kind == "s" else text
    if isinstance(value, list):
        return "(" + ", ".join(show(v) for v in value) + ")"
    return repr(value)


def plain(value: Any) -> Any:
    """Canonical value back to plain JSON for writing expectations."""
    if value is None:
        return None
    if _scalar(value):
        kind, text = value
        if kind == "n":
            number = Decimal(text)
            return int(number) if number == number.to_integral_value() else float(text)
        if kind == "t":
            return {"timestamp": text}
        return text
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------

ORACLE_ERRORS = {
    1403: "NO_DATA_FOUND",
    1422: "TOO_MANY_ROWS",
    1476: "ZERO_DIVIDE",
    1: "DUP_VAL_ON_INDEX",
    6502: "VALUE_ERROR",
    1722: "INVALID_NUMBER",
    1400: "NOT_NULL_VIOLATION",
    2291: "FOREIGN_KEY_VIOLATION",
    2292: "FOREIGN_KEY_VIOLATION",
}

PG_ERRORS = {
    "P0002": "NO_DATA_FOUND",
    "P0003": "TOO_MANY_ROWS",
    "22012": "ZERO_DIVIDE",
    "23505": "DUP_VAL_ON_INDEX",
    "22P02": "INVALID_NUMBER",
    "22001": "VALUE_ERROR",
    "22003": "VALUE_ERROR",
    "23502": "NOT_NULL_VIOLATION",
    "23503": "FOREIGN_KEY_VIOLATION",
    "P0001": "APPLICATION_ERROR",
}

# Oracle reports a bad number conversion as INVALID_NUMBER in SQL and VALUE_ERROR in
# PL/SQL; PostgreSQL has one SQLSTATE for both. Treat the two names as the same error.
_EQUIVALENT_ERRORS = [{"VALUE_ERROR", "INVALID_NUMBER"}]


def oracle_error_name(code: int) -> str:
    """Canonical name for an ORA- error number."""
    if 20000 <= code <= 20999:
        return "APPLICATION_ERROR"
    return ORACLE_ERRORS.get(code, f"ORA-{code:05d}")


def pg_error_name(sqlstate: str | None) -> str:
    """Canonical name for a PostgreSQL SQLSTATE."""
    if not sqlstate:
        return "UNKNOWN"
    return PG_ERRORS.get(sqlstate, f"SQLSTATE {sqlstate}")


def same_error(a: str, b: str) -> bool:
    """True when two canonical error names mean the same thing."""
    return a == b or any(a in group and b in group for group in _EQUIVALENT_ERRORS)


# --------------------------------------------------------------------------------------
# Outcomes and comparison
# --------------------------------------------------------------------------------------


@dataclass
class Outcome:
    """What one run of a case produced (values already canonical)."""

    value: Any = None
    out: list[Any] | None = None
    rows: list[Any] | None = None
    error: str | None = None
    message: str = ""
    setup_error: str | None = None

    def as_expectation(self, case: Case) -> dict[str, Any]:
        """The ``expect`` object this outcome would be recorded as."""
        if self.error:
            expect: dict[str, Any] = {"error": self.error}
            if self.error == "APPLICATION_ERROR":
                expect["message"] = self.message
            return expect
        expect = {}
        if case.kind == "function":
            expect["value"] = plain(self.value)
        if self.out is not None and case.out_count:
            expect["out"] = plain(self.out)
        if self.rows is not None:
            expect["rows"] = plain(self.rows)
        return expect


def compare(case: Case, outcome: Outcome) -> list[str]:
    """Differences between ``case.expect`` and ``outcome``; empty means equivalent."""
    if outcome.setup_error:
        return [f"could not run: {outcome.setup_error}"]
    expect = case.expect
    if "error" in expect:
        wanted = str(expect["error"])
        if outcome.error is None:
            return [f"expected error {wanted}, but the call succeeded"]
        diffs = []
        if not same_error(wanted, outcome.error):
            diffs.append(f"expected error {wanted}, got {outcome.error}: {outcome.message}")
        if "message" in expect and str(expect["message"]) not in outcome.message:
            diffs.append(f"expected message {expect['message']!r}, got {outcome.message!r}")
        return diffs
    if outcome.error is not None:
        return [f"raised {outcome.error}: {outcome.message}"]
    diffs = []
    if "value" in expect:
        want, got = canon(expect["value"]), outcome.value
        if want != got:
            diffs.append(f"returned {show(got)}, expected {show(want)}")
    if "out" in expect:
        want_out, got_out = canon(expect["out"]), outcome.out
        if want_out != got_out:
            got_text = show(got_out) if got_out is not None else "nothing"
            diffs.append(f"OUT parameters {got_text}, expected {show(want_out)}")
    if "rows" in expect:
        want_rows, got_rows = canon(expect["rows"]), outcome.rows
        if want_rows != got_rows:
            diffs.append(_rows_diff(want_rows, got_rows or []))
    return diffs


def _rows_diff(want: list[Any], got: list[Any]) -> str:
    if len(want) != len(got):
        return f"{len(got)} rows, expected {len(want)}"
    for index, (w, g) in enumerate(zip(want, got, strict=True)):
        if w != g:
            return f"row {index + 1} is {show(g)}, expected {show(w)}"
    return "rows differ"


@dataclass
class CaseResult:
    """A case, what happened, and the differences (if any)."""

    case: Case
    outcome: Outcome
    diffs: list[str]

    @property
    def passed(self) -> bool:
        """True when the outcome matches the expectation."""
        return not self.diffs


# --------------------------------------------------------------------------------------
# PostgreSQL
# --------------------------------------------------------------------------------------


def _pg_call_sql(case: Case) -> str:
    placeholders = ", ".join("NULL" if _is_out(a) else "%s" for a in case.args)
    if case.kind == "function":
        return f"SELECT {case.call}({placeholders})"
    if case.kind == "procedure":
        return f"CALL {case.call}({placeholders})"
    order = f" ORDER BY {case.order_by}" if case.order_by else ""
    return f"SELECT * FROM {case.call}({placeholders}){order}"


def _bind_args(case: Case) -> list[Any]:
    return [arg_value(a) for a in case.args if not _is_out(a)]


class PostgresRunner:
    """Run cases on PostgreSQL through an open psycopg connection (autocommit off)."""

    def __init__(self, conn: Any, search_path: str = "public", timeout_ms: int = 5000) -> None:
        self.conn = conn
        self.search_path = search_path
        self.timeout_ms = timeout_ms

    def run(self, case: Case, data: Iterable[str] = (), before: Iterable[str] = ()) -> Outcome:
        """Run one case. ``before`` is SQL to execute first (e.g. a candidate routine)."""
        import psycopg

        conn = self.conn
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
                cur.execute(f"SET LOCAL search_path = {self.search_path}")
                try:
                    for statement in before:
                        cur.execute(statement)
                    for statement in data:
                        cur.execute(statement)
                except psycopg.Error as exc:
                    return Outcome(setup_error=_pg_message(exc))
                try:
                    cur.execute(_pg_call_sql(case), _bind_args(case))
                    outcome = self._read(case, cur)
                except psycopg.Error as exc:
                    return Outcome(error=pg_error_name(exc.sqlstate), message=_pg_message(exc))
                if case.then:
                    try:
                        cur.execute(case.then)
                        outcome.rows = canon([list(r) for r in cur.fetchall()])
                    except psycopg.Error as exc:
                        outcome.setup_error = f"then query failed: {_pg_message(exc)}"
                return outcome
        finally:
            conn.rollback()

    @staticmethod
    def _read(case: Case, cur: Any) -> Outcome:
        if case.kind == "function":
            row = cur.fetchone()
            return Outcome(value=canon(row[0] if row else None))
        if case.kind == "procedure":
            if not case.out_count:
                return Outcome()
            row = cur.fetchone() if cur.description else None
            return Outcome(out=canon(list(row)) if row else None)
        return Outcome(rows=canon([list(r) for r in cur.fetchall()]))


def _pg_message(exc: Any) -> str:
    diag = getattr(exc, "diag", None)
    primary = getattr(diag, "message_primary", None) if diag is not None else None
    return (primary or str(exc)).strip().splitlines()[0]


def prepare_postgres(conn: Any, files: Iterable[Path], extra_sql: Iterable[str] = ()) -> None:
    """Run setup files (whole-file execute) and extra SQL, committing each."""
    with conn.cursor() as cur:
        for path in files:
            cur.execute(path.read_text(encoding="utf-8"))
            conn.commit()
        for text in extra_sql:
            cur.execute(text)
            conn.commit()


# --------------------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------------------


def oracle_call_sql(case: Case) -> tuple[str, list[str]]:
    """The SQL or PL/SQL that runs ``case`` on Oracle, and its bind names in order."""
    names = [f"a{i + 1}" for i in range(len(case.args))]
    binds = ", ".join(f":{n}" for n in names)
    if case.kind == "function":
        return f"BEGIN :ret := {case.call}({binds}); END;", names
    if case.kind == "procedure":
        return f"BEGIN {case.call}({binds}); END;", names
    order = f" ORDER BY {case.order_by}" if case.order_by else ""
    return f"SELECT * FROM TABLE({case.call}({binds})){order}", names


class OracleRunner:
    """Run cases on Oracle through an open python-oracledb connection.

    NUMBER results are read as text and parsed with Decimal, so values such as
    5280.55 are compared exactly (never through a float).
    """

    def __init__(self, conn: Any) -> None:
        import oracledb

        oracledb.defaults.fetch_decimals = True
        self.conn = conn

    @staticmethod
    def _var(cur: Any, kind: str) -> tuple[Any, Callable[[Any], Any]]:
        import oracledb

        if kind == "number":
            return cur.var(oracledb.DB_TYPE_VARCHAR, 200), _to_decimal
        if kind == "timestamp":
            return cur.var(oracledb.DB_TYPE_TIMESTAMP), _same
        return cur.var(oracledb.DB_TYPE_VARCHAR, 32767), _same

    def run(self, case: Case, data: Iterable[str] = ()) -> Outcome:
        """Run one case and roll back."""
        import oracledb

        conn = self.conn
        try:
            with conn.cursor() as cur:
                try:
                    for statement in data:
                        cur.execute(statement)
                except oracledb.DatabaseError as exc:
                    return Outcome(setup_error=_ora_message(exc))
                sql_text, names = oracle_call_sql(case)
                binds: dict[str, Any] = {}
                outs: list[tuple[Any, Callable[[Any], Any]]] = []
                for name, arg in zip(names, case.args, strict=True):
                    if _is_out(arg):
                        pair = self._var(cur, str(arg["out"]))
                        outs.append(pair)
                        binds[name] = pair[0]
                    else:
                        binds[name] = arg_value(arg)
                ret = None
                if case.kind == "function":
                    ret = self._var(cur, case.returns)
                    binds["ret"] = ret[0]
                try:
                    cur.execute(sql_text, binds)
                    if case.kind == "rows":
                        outcome = Outcome(rows=canon([list(r) for r in cur.fetchall()]))
                    elif ret is not None:
                        outcome = Outcome(value=canon(ret[1](ret[0].getvalue())))
                    else:
                        values = [convert(var.getvalue()) for var, convert in outs]
                        outcome = Outcome(out=canon(values) if outs else None)
                except oracledb.DatabaseError as exc:
                    (error,) = exc.args
                    return Outcome(
                        error=oracle_error_name(int(getattr(error, "code", 0) or 0)),
                        message=_ora_message(exc),
                    )
                if case.then:
                    cur.execute(case.then)
                    outcome.rows = canon([list(r) for r in cur.fetchall()])
                return outcome
        finally:
            conn.rollback()


def _same(value: Any) -> Any:
    return value


def _to_decimal(value: Any) -> Any:
    """NUMBER read as text (``'1.5'``, ``'.5'``, ``'-1.5'``) to Decimal; NULL stays None."""
    if value is None:
        return None
    return Decimal(str(value).strip().replace(",", "."))


def _ora_message(exc: Any) -> str:
    error = exc.args[0] if exc.args else exc
    text = str(getattr(error, "message", error)).strip().splitlines()[0]
    return re.sub(r"^ORA-\d+:\s*", "", text)


def prepare_oracle(conn: Any, files: Iterable[Path]) -> list[str]:
    """Run Oracle setup scripts. Returns compilation errors (empty when all is well)."""
    problems = []
    with conn.cursor() as cur:
        for path in files:
            for statement in oracle_script_statements(path.read_text(encoding="utf-8")):
                cur.execute(statement)
                if getattr(cur, "warning", None):
                    problems.append(f"{path.name}: {cur.warning} in: {statement.splitlines()[0]}")
        cur.execute("SELECT name, type, line, text FROM user_errors ORDER BY name, type, sequence")
        for name, kind, line, text in cur.fetchall():
            problems.append(f"{kind} {name} line {line}: {text.strip()}")
    return problems


# --------------------------------------------------------------------------------------
# Running a whole file
# --------------------------------------------------------------------------------------


def run_cases(cases: Iterable[Case], run: Callable[[Case], Outcome]) -> list[CaseResult]:
    """Run each case with ``run`` and compare against its expectation."""
    results = []
    for case in cases:
        outcome = run(case)
        results.append(CaseResult(case, outcome, compare(case, outcome)))
    return results


def results_table(results: list[CaseResult]) -> str:
    """A plain-text summary, one line per case."""
    lines = []
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        detail = "" if result.passed else "  " + "; ".join(result.diffs)
        lines.append(f"{mark}  {result.case.id}{detail}")
    passed = sum(1 for r in results if r.passed)
    lines.append(f"{passed} of {len(results)} cases match Oracle")
    return "\n".join(lines)


def updated_case_file(case_file: CaseFile, recorded: dict[str, dict[str, Any]]) -> str:
    """The case file's JSON with expectations replaced by ``recorded`` (by case id)."""
    raw = json.loads(json.dumps(case_file.raw))
    for item in raw.get("cases", []):
        if item.get("id") in recorded:
            item["expect"] = recorded[item["id"]]
    return json.dumps(raw, indent=2) + "\n"
