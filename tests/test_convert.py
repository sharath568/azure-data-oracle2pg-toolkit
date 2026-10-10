"""PL/SQL to PL/pgSQL conversion: rewrites, review points, structure and guarantees."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from o2pg.cli import sample_inventory_text
from o2pg.convert import (
    conversion_rules,
    convert_inventory,
    convert_sql,
    get_conversion_rule,
    split_script,
)
from o2pg.convert.body import rewrite_block
from o2pg.convert.lexer import TokenPool, block_end, find_markers, statements
from o2pg.convert.registry import CONVERSION_RULE_ID
from o2pg.convert.report import report_dict, report_json, report_markdown
from o2pg.convert.structure import parse_package, parse_subprogram, parse_trigger
from o2pg.convert.types import map_plsql_type
from o2pg.inventory import Inventory

FIXTURES = Path(__file__).parent / "fixtures" / "plsql"
SRC = Path(__file__).resolve().parents[1] / "src" / "o2pg" / "convert"


def rewrite(text: str, kind: str = "FUNCTION", **scope: object) -> tuple[str, Counter[str]]:
    return rewrite_block(text, kind, **scope)


def markers(sql: str) -> list[str]:
    return [rule for _, rule, _ in find_markers(sql)]


# --------------------------------------------------------------------------------------
# Mechanical rewrites: (rule, routine kind, Oracle input, expected PL/pgSQL fragment)
# --------------------------------------------------------------------------------------

REWRITES = [
    ("O2PG-CV-004", "FUNCTION", "DECLARE a VARCHAR2(10 CHAR); BEGIN NULL; END;", "a varchar(10);"),
    ("O2PG-CV-004", "FUNCTION", "DECLARE b NUMBER(10,2); BEGIN NULL; END;", "b numeric(10,2);"),
    ("O2PG-CV-004", "FUNCTION", "DECLARE d DATE; BEGIN NULL; END;", "d timestamp(0);"),
    ("O2PG-CV-004", "FUNCTION", "DECLARE e PLS_INTEGER; BEGIN NULL; END;", "e integer;"),
    ("O2PG-CV-004", "FUNCTION", "DECLARE g CLOB; BEGIN NULL; END;", "g text;"),
    ("O2PG-CV-004", "FUNCTION", "DECLARE h SYS_REFCURSOR; BEGIN NULL; END;", "h refcursor;"),
    (
        "O2PG-CV-004",
        "FUNCTION",
        "BEGIN x := CAST(a AS VARCHAR2(10)); END;",
        "CAST(a AS varchar(10))",
    ),
    ("O2PG-CV-006", "FUNCTION", "BEGIN NULL; END my_func;", "BEGIN NULL; END;"),
    (
        "O2PG-CV-010",
        "FUNCTION",
        "DECLARE CURSOR c (p NUMBER) IS SELECT a FROM t WHERE b = p; BEGIN NULL; END;",
        "c CURSOR (p numeric) FOR SELECT a FROM t WHERE b = p;",
    ),
    (
        "O2PG-CV-011",
        "FUNCTION",
        "DECLARE TYPE rc IS REF CURSOR; c rc; BEGIN NULL; END;",
        "c refcursor;",
    ),
    ("O2PG-CV-015", "FUNCTION", "BEGIN x := NVL(a, 0); END;", "x := COALESCE(a, 0);"),
    (
        "O2PG-CV-016",
        "FUNCTION",
        "BEGIN x := NVL2(a, b, c); END;",
        "x := CASE WHEN a IS NOT NULL THEN b ELSE c END;",
    ),
    (
        "O2PG-CV-017",
        "FUNCTION",
        "BEGIN x := DECODE(s, 1, 'one', 2, 'two', 'many'); END;",
        "CASE s WHEN 1 THEN 'one' WHEN 2 THEN 'two' ELSE 'many' END",
    ),
    (
        "O2PG-CV-017",
        "FUNCTION",
        "BEGIN x := DECODE(s, NULL, 'none', 'some'); END;",
        "CASE WHEN s IS NULL THEN 'none' ELSE 'some' END",
    ),
    (
        "O2PG-CV-017",
        "FUNCTION",
        "BEGIN x := DECODE(s, '', 'empty', 'full'); END;",
        "CASE WHEN s IS NULL THEN 'empty' ELSE 'full' END",
    ),
    (
        "O2PG-CV-017",
        "FUNCTION",
        "BEGIN x := DECODE(s, p_a, 1, 0); END;",
        "CASE WHEN s IS NOT DISTINCT FROM p_a THEN 1 ELSE 0 END",
    ),
    ("O2PG-CV-018", "FUNCTION", "BEGIN d := SYSDATE; END;", "d := LOCALTIMESTAMP(0);"),
    ("O2PG-CV-018", "FUNCTION", "BEGIN d := CURRENT_DATE; END;", "d := LOCALTIMESTAMP(0);"),
    ("O2PG-CV-019", "FUNCTION", "BEGIN t := SYSTIMESTAMP; END;", "t := clock_timestamp();"),
    ("O2PG-CV-020", "FUNCTION", "BEGIN x := emp_seq.NEXTVAL; END;", "x := nextval('emp_seq');"),
    ("O2PG-CV-020", "FUNCTION", "BEGIN x := hr.emp_seq.nextval; END;", "nextval('hr.emp_seq')"),
    ("O2PG-CV-020", "FUNCTION", "BEGIN x := emp_seq.CURRVAL; END;", "x := currval('emp_seq');"),
    (
        "O2PG-CV-021",
        "FUNCTION",
        "BEGIN SELECT COUNT(*) INTO n FROM (SELECT 1 AS a FROM dual); END;",
        "(SELECT 1 AS a)",
    ),
    ("O2PG-CV-022", "FUNCTION", "BEGIN SELECT 1 INTO x FROM dual; END;", "BEGIN x := 1; END;"),
    (
        "O2PG-CV-023",
        "FUNCTION",
        "BEGIN SELECT a INTO x FROM t WHERE id = 1; END;",
        "SELECT a INTO STRICT x FROM t",
    ),
    (
        "O2PG-CV-023",
        "FUNCTION",
        "BEGIN WITH q AS (SELECT 1 a FROM t) SELECT a INTO x FROM q; END;",
        "SELECT a INTO STRICT x FROM q",
    ),
    (
        "O2PG-CV-024",
        "PROCEDURE",
        "BEGIN DBMS_OUTPUT.PUT_LINE(msg); END;",
        "RAISE NOTICE '%', msg;",
    ),
    (
        "O2PG-CV-025",
        "PROCEDURE",
        "BEGIN RAISE_APPLICATION_ERROR(-20042, 'bad'); END;",
        "RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'bad', "
        "HINT = 'Oracle error code -20042';",
    ),
    (
        "O2PG-CV-026",
        "PROCEDURE",
        "BEGIN NULL; EXCEPTION WHEN DUP_VAL_ON_INDEX THEN NULL; END;",
        "WHEN unique_violation THEN",
    ),
    (
        "O2PG-CV-026",
        "PROCEDURE",
        "BEGIN NULL; EXCEPTION WHEN ZERO_DIVIDE THEN NULL; END;",
        "WHEN division_by_zero THEN",
    ),
    (
        "O2PG-CV-027",
        "PROCEDURE",
        "BEGIN NULL; EXCEPTION WHEN OTHERS THEN x := SQLCODE; END;",
        "x := SQLSTATE;",
    ),
    (
        "O2PG-CV-028",
        "PROCEDURE",
        "BEGIN EXECUTE IMMEDIATE 'select 1' INTO x; END;",
        "EXECUTE 'select 1' INTO STRICT x;",
    ),
    ("O2PG-CV-029", "PROCEDURE", "BEGIN UPDATE t SET a = 1; COMMIT; END;", "COMMIT;"),
    ("O2PG-CV-030", "TRIGGER", "BEGIN :NEW.a := :old.b; END;", "NEW.a := OLD.b;"),
    (
        "O2PG-CV-031",
        "TRIGGER",
        "BEGIN IF INSERTING OR DELETING THEN NULL; END IF; END;",
        "IF (TG_OP = 'INSERT') OR (TG_OP = 'DELETE') THEN",
    ),
    (
        "O2PG-CV-033",
        "FUNCTION",
        "BEGIN FOR r IN (SELECT a FROM t) LOOP NULL; END LOOP; END;",
        "r record;",
    ),
    (
        "O2PG-CV-034",
        "FUNCTION",
        "BEGIN FOR i IN REVERSE 1 .. n LOOP NULL; END LOOP; END;",
        "REVERSE n .. 1",
    ),
    ("O2PG-CV-035", "FUNCTION", "BEGIN EXIT WHEN c%NOTFOUND; END;", "EXIT WHEN NOT FOUND;"),
    ("O2PG-CV-035", "FUNCTION", "BEGIN IF SQL%FOUND THEN NULL; END IF; END;", "IF FOUND THEN"),
    (
        "O2PG-CV-035",
        "PROCEDURE",
        "BEGIN UPDATE t SET a = 1; n := SQL%ROWCOUNT; END;",
        "GET DIAGNOSTICS n = ROW_COUNT;",
    ),
    ("O2PG-CV-036", "PROCEDURE", "BEGIN do_it(1, 'a'); END;", "CALL do_it(1, 'a');"),
    ("O2PG-CV-036", "PROCEDURE", "BEGIN pkg.run; END;", "CALL pkg.run();"),
    (
        "O2PG-CV-037",
        "FUNCTION",
        "BEGIN d := TRUNC(SYSDATE); END;",
        "date_trunc('day', LOCALTIMESTAMP(0))",
    ),
    (
        "O2PG-CV-037",
        "FUNCTION",
        "DECLARE d DATE; BEGIN m := TRUNC(d, 'MM'); END;",
        "date_trunc('month', d)",
    ),
    (
        "O2PG-CV-038",
        "FUNCTION",
        "BEGIN d := ADD_MONTHS(SYSDATE, -1); END;",
        "(LOCALTIMESTAMP(0) + interval '1 month' * (-1))",
    ),
    (
        "O2PG-CV-039",
        "FUNCTION",
        "BEGIN d := SYSDATE - 1; END;",
        "LOCALTIMESTAMP(0) - interval '1 day'",
    ),
    (
        "O2PG-CV-039",
        "FUNCTION",
        "DECLARE p_days NUMBER; BEGIN d := SYSDATE + p_days; END;",
        "LOCALTIMESTAMP(0) + interval '1 day' * p_days",
    ),
    (
        "O2PG-CV-040",
        "FUNCTION",
        "BEGIN d := TO_DATE(s, 'YYYY-MM-DD'); END;",
        "to_timestamp(s, 'YYYY-MM-DD')::timestamp(0)",
    ),
    ("O2PG-CV-041", "FUNCTION", "BEGIN s := TO_CHAR(n); END;", "s := CAST(n AS text);"),
    ("O2PG-CV-041", "FUNCTION", "BEGIN n := TO_NUMBER(s); END;", "n := CAST(s AS numeric);"),
    (
        "O2PG-CV-042",
        "FUNCTION",
        "BEGIN s := DBMS_LOB.SUBSTR(c, 10, 5); END;",
        "s := substr(c, 5, 10);",
    ),
    ("O2PG-CV-042", "FUNCTION", "BEGIN n := DBMS_LOB.GETLENGTH(c); END;", "n := length(c);"),
    ("O2PG-CV-043", "PROCEDURE", "BEGIN DBMS_LOCK.SLEEP(2); END;", "PERFORM pg_sleep(2);"),
    ("O2PG-CV-044", "FUNCTION", "BEGIN g := SYS_GUID(); END;", "g := gen_random_uuid();"),
    ("O2PG-CV-045", "FUNCTION", "BEGIN n := INSTR(s, 'x'); END;", "n := strpos(s, 'x');"),
    (
        "O2PG-CV-046",
        "FUNCTION",
        "BEGIN SELECT COUNT(*) INTO n FROM (SELECT a FROM t MINUS SELECT a FROM u); END;",
        "SELECT a FROM t EXCEPT SELECT a FROM u",
    ),
    (
        "O2PG-CV-047",
        "PROCEDURE",
        "BEGIN MERGE INTO t x USING s ON (x.id = s.id) WHEN MATCHED THEN UPDATE SET x.a = s.a, "
        "x.b = s.b WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id); END;",
        "UPDATE SET a = s.a, b = s.b",
    ),
    ("O2PG-CV-048", "PROCEDURE", "BEGIN INSERT INTO t VALUES r; END;", "VALUES (r.*);"),
    (
        "O2PG-CV-049",
        "FUNCTION",
        'BEGIN SELECT "NAME" INTO x FROM "EMPLOYEES" WHERE 1 = 1; END;',
        "SELECT name INTO STRICT x FROM employees",
    ),
    ("O2PG-CV-050", "FUNCTION", "BEGIN x := q'[it's]'; END;", "x := 'it''s';"),
    ("O2PG-CV-050", "FUNCTION", "BEGIN x := N'abc'; END;", "x := 'abc';"),
    ("O2PG-CV-051", "FUNCTION", "BEGIN /* a /* b */ NULL; END;", "/* a / * b */"),
    ("O2PG-CV-052", "FUNCTION", "BEGIN s := SUBSTR(s, 0, 3); END;", "s := substr(s, 1, 3);"),
    ("O2PG-CV-053", "FUNCTION", "BEGIN s := REPLACE(s, 'x'); END;", "s := replace(s, 'x', '');"),
]


@pytest.mark.parametrize(
    ("rule", "kind", "source", "expected"),
    REWRITES,
    ids=[f"{r[0]}-{i}" for i, r in enumerate(REWRITES)],
)
def test_rewrite(rule: str, kind: str, source: str, expected: str) -> None:
    sql, applied = rewrite(source, kind)
    assert expected in sql
    assert applied[rule] >= 1


# Constructs that must survive untouched inside comments and string literals.
TOKENS = {
    "O2PG-CV-006": "END my_label;",
    "O2PG-CV-010": "CURSOR c IS SELECT 1 FROM t;",
    "O2PG-CV-011": "TYPE rc IS REF CURSOR;",
    "O2PG-CV-015": "NVL(a, 0)",
    "O2PG-CV-016": "NVL2(a, b, c)",
    "O2PG-CV-017": "DECODE(s, 1, 'x', 'y')",
    "O2PG-CV-018": "SYSDATE",
    "O2PG-CV-019": "SYSTIMESTAMP",
    "O2PG-CV-020": "emp_seq.NEXTVAL",
    "O2PG-CV-021": "SELECT 1 FROM dual",
    "O2PG-CV-022": "SELECT 1 INTO x FROM dual;",
    "O2PG-CV-023": "SELECT a INTO x FROM t;",
    "O2PG-CV-024": "DBMS_OUTPUT.PUT_LINE(x);",
    "O2PG-CV-025": "RAISE_APPLICATION_ERROR(-20001, 'm');",
    "O2PG-CV-026": "WHEN DUP_VAL_ON_INDEX THEN",
    "O2PG-CV-027": "SQLCODE",
    "O2PG-CV-028": "EXECUTE IMMEDIATE 'x';",
    "O2PG-CV-029": "COMMIT;",
    "O2PG-CV-030": ":NEW.a",
    "O2PG-CV-031": "INSERTING",
    "O2PG-CV-033": "FOR r IN (SELECT 1 FROM t) LOOP",
    "O2PG-CV-034": "FOR i IN REVERSE 1 .. 3 LOOP",
    "O2PG-CV-035": "c%NOTFOUND",
    "O2PG-CV-036": "do_it(1);",
    "O2PG-CV-037": "TRUNC(SYSDATE)",
    "O2PG-CV-038": "ADD_MONTHS(SYSDATE, 1)",
    "O2PG-CV-039": "SYSDATE + 1",
    "O2PG-CV-040": "TO_DATE(s, 'YYYY')",
    "O2PG-CV-041": "TO_CHAR(n)",
    "O2PG-CV-042": "DBMS_LOB.GETLENGTH(c)",
    "O2PG-CV-043": "DBMS_LOCK.SLEEP(1);",
    "O2PG-CV-044": "SYS_GUID()",
    "O2PG-CV-045": "INSTR(s, 'x')",
    "O2PG-CV-046": "a MINUS b",
    "O2PG-CV-047": "MERGE INTO t x USING s ON (1 = 1) WHEN MATCHED THEN UPDATE SET x.a = 1;",
    "O2PG-CV-048": "INSERT INTO t VALUES r;",
    "O2PG-CV-052": "SUBSTR(s, 0, 2)",
    "O2PG-CV-053": "REPLACE(s, 'x')",
}


def _negative_sources(token: str) -> list[str]:
    literal = "'" + token.replace("'", "''") + "'"
    return [
        f"BEGIN\n  -- {token}\n  NULL;\nEND;",
        f"BEGIN\n  /* {token} */\n  NULL;\nEND;",
        f"BEGIN\n  x := {literal};\nEND;",
    ]


@pytest.mark.parametrize(("rule", "token"), sorted(TOKENS.items()))
@pytest.mark.parametrize("variant", ["line comment", "block comment", "string"])
def test_rewrite_ignores_comments_and_strings(rule: str, token: str, variant: str) -> None:
    source = _negative_sources(token)[["line comment", "block comment", "string"].index(variant)]
    kind = "TRIGGER" if rule in {"O2PG-CV-030", "O2PG-CV-031"} else "PROCEDURE"
    sql, applied = rewrite(source, kind)
    assert sql == source
    assert not applied


# --------------------------------------------------------------------------------------
# Review points
# --------------------------------------------------------------------------------------

REVIEWS = [
    ("O2PG-RV-002", "PROCEDURE", "DECLARE PRAGMA AUTONOMOUS_TRANSACTION; BEGIN NULL; END;"),
    ("O2PG-RV-003", "FUNCTION", "BEGIN SELECT a BULK COLLECT INTO l FROM t; END;"),
    ("O2PG-RV-004", "PROCEDURE", "BEGIN FORALL i IN 1 .. n DELETE FROM t WHERE id = l(i); END;"),
    ("O2PG-RV-005", "FUNCTION", "BEGIN SELECT a INTO x FROM t WHERE ROWNUM = 1; END;"),
    ("O2PG-RV-006", "FUNCTION", "BEGIN SELECT a INTO x FROM t CONNECT BY PRIOR a = b; END;"),
    ("O2PG-RV-007", "FUNCTION", "BEGIN SELECT a INTO x FROM t, u WHERE t.id = u.id(+); END;"),
    ("O2PG-RV-008", "FUNCTION", "BEGIN PIPE ROW (r); END;"),
    ("O2PG-RV-009", "FUNCTION", "DECLARE TYPE t IS TABLE OF NUMBER; BEGIN NULL; END;"),
    ("O2PG-RV-010", "FUNCTION", "DECLARE x foo_t; BEGIN NULL; END;"),
    ("O2PG-RV-012", "FUNCTION", "DECLARE e_x EXCEPTION; BEGIN RAISE e_x; END;"),
    ("O2PG-RV-013", "FUNCTION", "DECLARE PRAGMA EXCEPTION_INIT(e, -1); BEGIN NULL; END;"),
    ("O2PG-RV-014", "PROCEDURE", "BEGIN UTL_FILE.FCLOSE(f); END;"),
    ("O2PG-RV-014", "PROCEDURE", "BEGIN DBMS_SCHEDULER.RUN_JOB('J'); END;"),
    ("O2PG-RV-015", "FUNCTION", "BEGIN x := MONTHS_BETWEEN(a, b); END;"),
    ("O2PG-RV-015", "FUNCTION", "BEGIN x := LISTAGG(a, ',') WITHIN GROUP (ORDER BY a); END;"),
    ("O2PG-RV-016", "FUNCTION", "BEGIN COMMIT; RETURN 1; END;"),
    ("O2PG-RV-016", "TRIGGER", "BEGIN ROLLBACK; END;"),
    ("O2PG-RV-017", "PROCEDURE", "BEGIN SAVEPOINT s1; ROLLBACK TO s1; END;"),
    ("O2PG-RV-018", "FUNCTION", "BEGIN GOTO done; <<done>> NULL; END;"),
    ("O2PG-RV-019", "PROCEDURE", "DECLARE PROCEDURE p IS BEGIN NULL; END; BEGIN NULL; END;"),
    ("O2PG-RV-021", "FUNCTION", "BEGIN n := c%ROWCOUNT; END;"),
    ("O2PG-RV-022", "FUNCTION", "BEGIN x := DECODE(f(s), p_a, 1, 0); END;"),
    ("O2PG-RV-023", "TRIGGER", "BEGIN IF UPDATING('A') THEN NULL; END IF; END;"),
    ("O2PG-RV-026", "FUNCTION", "BEGIN SELECT ROWID INTO r FROM t WHERE 1 = 1; END;"),
    ("O2PG-RV-027", "FUNCTION", "BEGIN SELECT a INTO r FROM t AS OF TIMESTAMP x; END;"),
    ("O2PG-RV-028", "FUNCTION", "BEGIN SELECT a INTO r FROM dual d; END;"),
    ("O2PG-RV-030", "FUNCTION", "BEGIN s := SUBSTR(s, -2); END;"),
    (
        "O2PG-RV-031",
        "PROCEDURE",
        "BEGIN MERGE INTO t USING s ON (t.id = s.id) WHEN MATCHED THEN UPDATE SET a = s.a; END;",
    ),
    (
        "O2PG-RV-032",
        "PROCEDURE",
        "BEGIN MERGE INTO t USING s ON (t.id = s.id) "
        "WHEN MATCHED THEN UPDATE SET a = s.a WHERE s.a > 0; END;",
    ),
    ("O2PG-RV-033", "PROCEDURE", "BEGIN EXECUTE IMMEDIATE 'TRUNCATE TABLE t'; END;"),
    ("O2PG-RV-034", "FUNCTION", "BEGIN IF a = '' THEN NULL; END IF; END;"),
    ("O2PG-RV-035", "FUNCTION", "BEGIN x := a || b; END;"),
    ("O2PG-RV-036", "FUNCTION", "BEGIN NULL; EXCEPTION WHEN OTHERS THEN x := SQLCODE; END;"),
    ("O2PG-RV-037", "FUNCTION", "BEGIN x := SQLERRM(-1); END;"),
    ("O2PG-RV-038", "FUNCTION", "BEGIN x := TRUNC(unknown_value); END;"),
    ("O2PG-RV-039", "FUNCTION", "DECLARE d DATE; BEGIN x := d - SYSDATE; END;"),
    ("O2PG-RV-040", "FUNCTION", "BEGIN x := 7 / 2; END;"),
    ("O2PG-RV-041", "FUNCTION", "BEGIN x := GREATEST(a, b); END;"),
    ("O2PG-RV-042", "FUNCTION", "BEGIN x := REGEXP_REPLACE(s, 'a', 'b'); END;"),
    ("O2PG-RV-043", "FUNCTION", "BEGIN d := TO_DATE(s); END;"),
    ("O2PG-RV-044", "FUNCTION", "BEGIN n := INSTR(s, 'x', 1, 2); END;"),
]


@pytest.mark.parametrize(
    ("rule", "kind", "source"), REVIEWS, ids=[f"{r[0]}-{i}" for i, r in enumerate(REVIEWS)]
)
def test_review_marker(rule: str, kind: str, source: str) -> None:
    sql, _ = rewrite(source, kind)
    assert rule in markers(sql)
    assert get_conversion_rule(rule).description


@pytest.mark.parametrize(
    ("rule", "kind", "source"), REVIEWS, ids=[f"{r[0]}-{i}" for i, r in enumerate(REVIEWS)]
)
def test_review_marker_keeps_the_oracle_code(rule: str, kind: str, source: str) -> None:
    """A review point never removes code: every word of the input survives."""
    sql, _ = rewrite(source, kind)
    replaced = {
        "SYSDATE", "INTO", "IMMEDIATE", "SQLCODE", "NUMBER", "DATE", "MINUS",
    }  # fmt: skip
    for word in re.findall(r"[A-Za-z_]\w*", source):
        if word.upper() not in replaced:
            assert re.search(rf"\b{re.escape(word)}\b", sql, re.IGNORECASE), word


REVIEW_TOKENS = {
    "O2PG-RV-002": "PRAGMA AUTONOMOUS_TRANSACTION;",
    "O2PG-RV-003": "BULK COLLECT",
    "O2PG-RV-005": "ROWNUM",
    "O2PG-RV-006": "CONNECT BY PRIOR a = b",
    "O2PG-RV-007": "u.id(+)",
    "O2PG-RV-014": "UTL_FILE.FOPEN('D', 'f', 'w')",
    "O2PG-RV-034": "a = ''",
    "O2PG-RV-035": "a || b",
}


@pytest.mark.parametrize(("rule", "token"), sorted(REVIEW_TOKENS.items()))
@pytest.mark.parametrize("variant", [0, 1, 2])
def test_review_ignores_comments_and_strings(rule: str, token: str, variant: int) -> None:
    source = _negative_sources(token)[variant]
    sql, _ = rewrite(source, "PROCEDURE")
    assert sql == source


def test_marker_goes_on_its_own_line_above_the_statement() -> None:
    sql, _ = rewrite("BEGIN\n  x := 1;\n  SELECT a INTO y FROM t WHERE ROWNUM = 1;\nEND;")
    lines = sql.splitlines()
    index = next(i for i, line in enumerate(lines) if "ROWNUM =" in line)
    assert lines[index - 1].strip().startswith("/* O2PG-REVIEW O2PG-RV-005:")
    assert lines[index - 1].startswith("  ")


def test_one_marker_per_rule_and_line() -> None:
    sql, _ = rewrite("BEGIN x := a || b || c || d; END;")
    assert markers(sql) == ["O2PG-RV-035"]


def test_package_variables_are_flagged() -> None:
    sql, _ = rewrite(
        "BEGIN g_count := g_count + 1; emp_pkg.g_last := SYSDATE; END;",
        package_vars=frozenset({"g_count"}),
        qualified_package_vars=frozenset({"emp_pkg.g_last"}),
    )
    assert markers(sql) == ["O2PG-RV-011"]


def test_local_variable_shadowing_a_package_variable_is_not_flagged() -> None:
    sql, _ = rewrite(
        "DECLARE g_count NUMBER; BEGIN g_count := 1; END;", package_vars=frozenset({"g_count"})
    )
    assert "O2PG-RV-011" not in markers(sql)


def test_trigger_bare_return() -> None:
    sql, _ = rewrite(
        "BEGIN IF x THEN RETURN; END IF; END;", "TRIGGER", trigger_return="RETURN NEW;"
    )
    assert "RETURN NEW;" in sql
    sql, _ = rewrite("BEGIN IF x THEN RETURN; END IF; END;", "TRIGGER", trigger_return=None)
    assert "O2PG-RV-029" in markers(sql)


def test_referencing_aliases() -> None:
    sql, _ = rewrite("BEGIN :n.a := :o.a; END;", "TRIGGER", new_aliases=("NEW", "n"),
                     old_aliases=("OLD", "o"))  # fmt: skip
    assert "NEW.a := OLD.a;" in sql


def test_date_arithmetic_on_unknown_operand_is_flagged_not_rewritten() -> None:
    sql, applied = rewrite("BEGIN d := SYSDATE - some_value; END;")
    assert "O2PG-RV-039" in markers(sql)
    assert "O2PG-CV-039" not in applied


def test_decode_with_complex_subject_and_literal_searches_uses_simple_case() -> None:
    sql, _ = rewrite("BEGIN x := DECODE(UPPER(s), 'A', 1, 0); END;")
    assert "CASE UPPER(s) WHEN 'A' THEN 1 ELSE 0 END" in sql


def test_nested_functions_are_all_rewritten() -> None:
    sql, applied = rewrite("BEGIN x := NVL(DECODE(a, 1, NVL(b, 2), 3), 4); END;")
    assert "NVL" not in sql.upper().replace("NVL2", "")
    assert "DECODE" not in sql.upper()
    assert applied["O2PG-CV-015"] == 2


def test_statement_level_rewrites_respect_merge_when_then() -> None:
    sql, _ = rewrite(
        "BEGIN MERGE INTO t x USING s ON (x.id = s.id) WHEN MATCHED THEN UPDATE SET x.a = 1 "
        "WHEN NOT MATCHED THEN INSERT (id) VALUES (s.id); do_it; END;",
        "PROCEDURE",
    )
    assert "UPDATE SET a = 1" in sql
    assert "CALL do_it();" in sql
    assert "CALL INSERT" not in sql


def test_end_loop_label_is_not_a_call() -> None:
    source = "BEGIN <<outer>> FOR i IN 1 .. 3 LOOP EXIT outer WHEN i = 2; END LOOP outer; END;"
    sql, applied = rewrite(source, "PROCEDURE")
    assert sql == source
    assert not applied


def test_case_statement_and_nested_block() -> None:
    sql, applied = rewrite(
        "BEGIN CASE m WHEN 'A' THEN x := NVL(y, 1); WHEN 'B' THEN DECLARE z NUMBER; "
        "BEGIN go(z); END; ELSE NULL; END CASE; END;",
        "PROCEDURE",
    )
    assert "x := COALESCE(y, 1);" in sql
    assert "DECLARE z numeric;" in sql
    assert "CALL go(z);" in sql
    assert applied["O2PG-CV-036"] == 1


def test_collection_methods_are_not_turned_into_calls() -> None:
    sql, _ = rewrite("DECLARE TYPE t IS TABLE OF NUMBER; l t; BEGIN l.EXTEND; END;", "PROCEDURE")
    assert "CALL" not in sql


# --------------------------------------------------------------------------------------
# Idempotence and no dropped code
# --------------------------------------------------------------------------------------

# REPLACE(s, x) becomes replace(s, x, ''): on a second run that '' is indistinguishable
# from an Oracle empty-string literal, so it (rightly) gets an O2PG-RV-034 marker.
NOT_IDEMPOTENT = {"O2PG-CV-053"}


@pytest.mark.parametrize(
    ("rule", "kind", "source", "expected"),
    [r for r in REWRITES if r[0] not in NOT_IDEMPOTENT],
    ids=[f"{r[0]}-{i}" for i, r in enumerate(REWRITES) if r[0] not in NOT_IDEMPOTENT],
)
def test_rewrites_are_idempotent(rule: str, kind: str, source: str, expected: str) -> None:
    once, _ = rewrite(source, kind)
    twice, applied = rewrite(once, kind)
    assert twice == once
    # CV-029 only records that COMMIT was kept; it never changes the text.
    assert not applied.get(rule) or rule == "O2PG-CV-029"


@pytest.mark.parametrize(("rule", "kind", "source"), REVIEWS[:20])
def test_review_markers_are_idempotent(rule: str, kind: str, source: str) -> None:
    once, _ = rewrite(source, kind)
    twice, _ = rewrite(once, kind)
    assert twice == once


def test_fixture_bodies_are_idempotent() -> None:
    pool = TokenPool()
    for path in sorted(FIXTURES.glob("*.sql")):
        for source in split_script(path.read_text(encoding="utf-8")):
            masked = pool.mask(source.text)
            begin = re.search(r"\bBEGIN\b", masked, re.IGNORECASE)
            if begin is None or source.type.startswith("PACKAGE"):
                continue
            end = block_end(masked, begin.start())
            assert end is not None
            block = pool.restore(masked[begin.start() : end])
            kind = "TRIGGER" if source.type == "TRIGGER" else source.type
            once, _ = rewrite(block, kind)
            twice, _ = rewrite(once, kind)
            assert twice == once, source.name


# Words that a documented rewrite replaces or removes. Everything else in the Oracle
# source (code, comments and literals) must still be somewhere in the output.
REPLACED_WORDS = frozenset(
    """
    NVL NVL2 DECODE SYSDATE SYSTIMESTAMP CURRENT_DATE DUAL FROM INTO SELECT IMMEDIATE
    NEXTVAL CURRVAL DBMS_OUTPUT PUT_LINE RAISE_APPLICATION_ERROR DUP_VAL_ON_INDEX ZERO_DIVIDE
    INVALID_NUMBER VALUE_ERROR CURSOR_ALREADY_OPEN INVALID_CURSOR TIMEOUT_ON_RESOURCE SQLCODE
    IN IS AS NOCOPY INSERTING UPDATING DELETING TRUNC ADD_MONTHS TO_DATE TO_CHAR TO_NUMBER
    DBMS_LOB GETLENGTH SYS_GUID INSTR MINUS AUTHID CURRENT_USER DEFINER SQL ROWCOUNT
    NOTFOUND ISOPEN DBMS_LOCK DBMS_SESSION SLEEP NUMBER VARCHAR2 NVARCHAR2 DATE PLS_INTEGER
    BINARY_INTEGER CLOB NCLOB BLOB RAW SYS_REFCURSOR MM DD YYYY HH24 MI Q IW
    """.split()
)
_WORD = re.compile(r"[A-Za-z_][\w$#]*")


def _words(text: str) -> set[str]:
    return {w.upper() for w in _WORD.findall(text)}


def _assert_nothing_dropped(original: str, converted: str, label: str) -> None:
    missing = _words(original) - _words(converted) - REPLACED_WORDS
    assert not missing, f"{label}: {sorted(missing)}"


def test_no_code_dropped_from_sample() -> None:
    inventory = Inventory.from_json(sample_inventory_text())
    result = convert_inventory(inventory)
    for obj in result.objects:
        original = "\n".join(
            s.text for s in inventory.source if (s.owner, s.name) == (obj.owner, obj.name)
        )
        _assert_nothing_dropped(original, obj.sql, obj.qualified)


def test_no_code_dropped_from_fixtures() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(FIXTURES.glob("*.sql")))
    sources = split_script(text)
    for obj in convert_sql(text).objects:
        original = "\n".join(s.text for s in sources if (s.owner, s.name) == (obj.owner, obj.name))
        _assert_nothing_dropped(original, obj.sql, obj.qualified)


def test_string_literals_and_comments_survive() -> None:
    inventory = Inventory.from_json(sample_inventory_text())
    result = convert_inventory(inventory)
    pool = TokenPool()
    for obj in result.objects:
        for source in inventory.source:
            if (source.owner, source.name) != (obj.owner, obj.name):
                continue
            for char in pool.mask(source.text):
                token = pool.token(char)
                if token and token.kind in {"string", "comment"} and len(token.text) > 2:
                    assert token.text in obj.sql, (obj.qualified, token.text)


# --------------------------------------------------------------------------------------
# Whole objects
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_result():  # type: ignore[no-untyped-def]
    return convert_inventory(Inventory.from_json(sample_inventory_text()))


def test_sample_unit_statuses(sample_result) -> None:  # type: ignore[no-untyped-def]
    statuses = {u.name: u.status for u in sample_result.units if u.kind != "PACKAGE STATE"}
    assert statuses == {
        "hr.employees_biu (trigger on hr.employees)": "blocked",
        "hr.update_job_history (trigger on hr.employees)": "converted",
        "HR.HR_DDL_AUDIT": "not converted",
        "hr.add_job_history": "converted",
        "emp_mgmt.log_error": "blocked",
        "emp_mgmt.hire": "blocked",
        "emp_mgmt.raise_salary": "blocked",
        "emp_mgmt.employees_in": "converted",
        "order_proc.upsert_customer": "review",
        "order_proc.export_daily": "blocked",
        "order_proc.notify_big_order": "blocked",
        "order_proc.recent_orders": "blocked",
        "order_proc.purge_stage": "review",
        "SALES.PRICING": "not converted",
        "sales.orders_bi (trigger on sales.orders)": "converted",
        "SALES.ORDERS_AUDIT_CT": "not converted",
        "sales.publish_order_event": "blocked",
        "sales.get_order_total": "converted",
        "sales.product_snippet": "review",
        "sales.refresh_rollups": "blocked",
        "SALES.ADDRESS_T": "not converted",
    }


def test_sample_coverage_is_reported(sample_result) -> None:  # type: ignore[no-untyped-def]
    assert 0 < sample_result.clean_statements < sample_result.statements
    assert sample_result.coverage == pytest.approx(
        100 * sample_result.clean_statements / sample_result.statements
    )


def test_review_lines_point_at_markers(sample_result) -> None:  # type: ignore[no-untyped-def]
    for obj in sample_result.objects:
        lines = obj.sql.splitlines()
        for review in obj.reviews:
            assert f"O2PG-REVIEW {review.rule}:" in lines[review.line - 1]


def test_package_becomes_schema_and_functions(sample_result) -> None:  # type: ignore[no-untyped-def]
    pkg = next(o for o in sample_result.objects if o.name == "EMP_MGMT")
    assert pkg.preamble[0] == "CREATE SCHEMA IF NOT EXISTS emp_mgmt;"
    assert "CREATE OR REPLACE FUNCTION emp_mgmt.hire" in pkg.sql
    assert "SET search_path = emp_mgmt, hr, public" in pkg.sql
    assert "c_max_salary          CONSTANT numeric := 250000;" in pkg.sql
    assert "-- session-level state used by triggers and reports" in pkg.sql
    assert pkg.all_rewrites["O2PG-CV-012"] == 1


def test_procedure_that_commits_gets_no_set_clause(sample_result) -> None:  # type: ignore[no-untyped-def]
    unit = next(u for u in sample_result.units if u.name == "sales.refresh_rollups")
    assert "SET search_path" not in unit.sql
    assert unit.rewrites["O2PG-CV-029"] == 1


def test_wrapped_compound_ddl_and_type_objects_are_kept_as_comments(sample_result) -> None:  # type: ignore[no-untyped-def]
    for name in ("PRICING", "ORDERS_AUDIT_CT", "HR_DDL_AUDIT", "ADDRESS_T"):
        obj = next(o for o in sample_result.objects if o.name == name)
        assert obj.status == "not converted"
        assert "O2PG-RV-001" in markers(obj.sql)
        body = [line for line in obj.sql.splitlines()[5:] if line and "O2PG-REVIEW" not in line]
        assert all(line.startswith("--") for line in body)


def test_trigger_conversion_shape() -> None:
    result = convert_sql((FIXTURES / "triggers.sql").read_text(encoding="utf-8"))
    sql = next(o for o in result.objects if o.name == "ORDERS_BI").sql
    assert "RETURNS trigger" in sql
    assert "NEW.order_id := nextval('order_seq');" in sql
    assert "RETURN NEW;" in sql
    assert "BEFORE INSERT ON app.orders" in sql
    assert "WHEN (new.order_id IS NULL)" in sql
    assert "EXECUTE FUNCTION app.orders_bi();" in sql
    audit = next(o for o in result.objects if o.name == "ORDERS_AUDIT").sql
    assert "RETURN NULL;" in audit
    assert "REFERENCING NEW AS n OLD AS o" in audit


def test_before_trigger_on_delete_and_insert_returns_by_operation() -> None:
    result = convert_sql(
        "CREATE OR REPLACE TRIGGER app.t_bid BEFORE INSERT OR DELETE ON app.t FOR EACH ROW\n"
        "BEGIN NULL; END;\n/\n"
    )
    sql = result.objects[0].sql
    assert "IF TG_OP = 'DELETE' THEN" in sql
    assert "RETURN OLD;" in sql


def test_disabled_trigger_stays_disabled() -> None:
    result = convert_sql(
        "CREATE OR REPLACE TRIGGER app.t_ai AFTER INSERT ON app.t FOR EACH ROW DISABLE\n"
        "BEGIN NULL; END;\n/\n"
    )
    assert "ALTER TABLE app.t DISABLE TRIGGER t_ai;" in result.objects[0].sql


def test_function_header_parameters_and_return() -> None:
    result = convert_sql(
        "CREATE OR REPLACE FUNCTION app.f (a IN NUMBER, b OUT VARCHAR2, c IN OUT NOCOPY DATE, "
        "d IN NUMBER := 5) RETURN NUMBER IS BEGIN RETURN 1; END f;\n/\n"
    )
    unit = result.units[0]
    assert "(a numeric, OUT b varchar, INOUT c timestamp(0), d numeric DEFAULT 5)" in unit.sql
    assert "RETURNS numeric" in unit.sql
    assert "O2PG-RV-024" in markers(unit.sql)
    assert unit.rewrites["O2PG-CV-003"] == 4
    assert unit.rewrites["O2PG-CV-005"] == 1


def test_parameter_default_order_is_flagged() -> None:
    result = convert_sql(
        "CREATE OR REPLACE PROCEDURE app.p (a NUMBER DEFAULT 1, b NUMBER) IS BEGIN NULL; END;\n/\n"
    )
    assert "O2PG-RV-025" in markers(result.units[0].sql)


def test_authid_and_oracle_options() -> None:
    result = convert_sql(
        "CREATE OR REPLACE FUNCTION app.f RETURN NUMBER AUTHID CURRENT_USER DETERMINISTIC IS "
        "BEGIN RETURN 1; END;\n/\n"
    )
    sql = result.units[0].sql
    assert "SECURITY INVOKER" in sql
    assert "-- Oracle options (no effect in PostgreSQL): DETERMINISTIC" in sql


def test_dollar_quote_tag_changes_when_body_contains_double_dollar() -> None:
    result = convert_sql(
        "CREATE OR REPLACE FUNCTION app.f RETURN VARCHAR2 IS BEGIN RETURN 'a$$b'; END;\n/\n"
    )
    sql = result.units[0].sql
    assert "AS $o2pg$" in sql
    assert sql.rstrip().endswith("$o2pg$;")


def test_call_spec_is_not_converted() -> None:
    result = convert_sql(
        "CREATE OR REPLACE PROCEDURE app.j (x NUMBER) AS LANGUAGE JAVA NAME 'A.b(int)';\n/\n"
    )
    assert result.units[0].status == "not converted"


def test_unparseable_unit_is_kept_as_comment() -> None:
    result = convert_sql("CREATE OR REPLACE PROCEDURE app.broken IS BEGIN NULL;\n/\n")
    assert result.objects[0].status == "not converted"
    assert "could not parse" in result.objects[0].sql


def test_anonymous_block_in_script_is_not_converted() -> None:
    result = convert_sql("BEGIN NULL; END;\n/\n")
    assert result.objects[0].name == "SCRIPT_1"
    assert result.objects[0].status == "not converted"


# --------------------------------------------------------------------------------------
# Structure helpers
# --------------------------------------------------------------------------------------


def test_split_script_with_and_without_slashes() -> None:
    with_slash = split_script(
        "CREATE OR REPLACE PROCEDURE a IS BEGIN NULL; END;\n/\n"
        "CREATE OR REPLACE EDITIONABLE FUNCTION hr.b RETURN NUMBER IS BEGIN RETURN 1; END;\n/\n"
    )
    assert [(s.owner, s.name, s.type) for s in with_slash] == [
        ("PUBLIC", "A", "PROCEDURE"),
        ("HR", "B", "FUNCTION"),
    ]
    assert with_slash[1].text.lstrip().startswith("FUNCTION hr.b")
    without = split_script(
        "CREATE PROCEDURE a IS BEGIN NULL; END;\nCREATE PROCEDURE b IS BEGIN NULL; END;\n",
        default_schema="app",
    )
    assert [s.name for s in without] == ["A", "B"]
    assert {s.owner for s in without} == {"APP"}


def test_slash_inside_a_comment_does_not_split() -> None:
    sources = split_script("CREATE PROCEDURE a IS BEGIN\n/* \n/\n */ NULL; END;\n/\n")
    assert len(sources) == 1


def test_parse_subprogram_with_nested_block_and_case() -> None:
    pool = TokenPool()
    text = pool.mask(
        "PROCEDURE p (x NUMBER) IS\n  y NUMBER;\nBEGIN\n  BEGIN NULL; END;\n"
        "  y := CASE WHEN x > 1 THEN 1 ELSE 0 END;\n  CASE y WHEN 1 THEN NULL; END CASE;\n"
        "  IF y = 1 THEN NULL; END IF;\n  LOOP EXIT; END LOOP;\nEND p;\n-- trailing"
    )
    sub = parse_subprogram(text, 0)
    assert sub.kind == "PROCEDURE"
    assert text[sub.begin : sub.begin + 5] == "BEGIN"
    assert text[: sub.end].rstrip().endswith("END p;")


def test_parse_package_items() -> None:
    pool = TokenPool()
    text = pool.mask(
        "PACKAGE BODY p AS\n  g NUMBER;\n  PROCEDURE a;\n  PROCEDURE a IS BEGIN NULL; END a;\n"
        "BEGIN\n  g := 1;\nEND p;\n"
    )
    package = parse_package(text)
    kinds = [item.kind for item in package.items]
    assert kinds == ["declaration", "subprogram", "subprogram", "initialization"]
    assert package.items[1].subprogram is not None
    assert package.items[1].subprogram.forward


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("TRIGGER t FOR INSERT ON x COMPOUND TRIGGER AFTER STATEMENT IS BEGIN NULL; END "
         "AFTER STATEMENT; END;", "compound"),
        ("TRIGGER t AFTER CREATE ON SCHEMA BEGIN NULL; END;", "event trigger"),
        ("TRIGGER t AFTER LOGON ON DATABASE BEGIN NULL; END;", "event trigger"),
        ("TRIGGER t BEFORE INSERT ON x FOR EACH ROW CALL p", "CALL"),
    ],
)  # fmt: skip
def test_unsupported_triggers(source: str, reason: str) -> None:
    pool = TokenPool()
    result = parse_trigger(pool.mask(source))
    assert isinstance(result, str)
    assert reason in result


def test_statements_and_block_end() -> None:
    text = "BEGIN a := 1; IF x THEN b := 2; ELSE c := 3; END IF; END;"
    found = [text[s.start : s.end] for s in statements(text)]
    assert found == ["a := 1", "b := 2", "c := 3", "END IF", "END"]
    assert block_end(text, 0) == len(text)


def test_token_pool_round_trip_is_exact() -> None:
    inventory = Inventory.from_json(sample_inventory_text())
    for source in inventory.source:
        pool = TokenPool()
        assert pool.restore(pool.mask(source.text)) == source.text


@pytest.mark.parametrize(
    ("oracle", "pg"),
    [
        ("VARCHAR2(30)", "varchar(30)"),
        ("VARCHAR2(30 BYTE)", "varchar(30)"),
        ("VARCHAR2", "varchar"),
        ("CHAR(1)", "char(1)"),
        ("NUMBER", "numeric"),
        ("NUMBER(4)", "smallint"),
        ("NUMBER(9)", "integer"),
        ("NUMBER(18)", "bigint"),
        ("NUMBER(12,2)", "numeric(12,2)"),
        ("INTEGER", "integer"),
        ("PLS_INTEGER", "integer"),
        ("BOOLEAN", "boolean"),
        ("DATE", "timestamp(0)"),
        ("TIMESTAMP", "timestamp(6)"),
        ("TIMESTAMP(3) WITH TIME ZONE", "timestamptz(3)"),
        ("CLOB", "text"),
        ("BLOB", "bytea"),
        ("RAW(16)", "bytea"),
        ("SYS_REFCURSOR", "refcursor"),
        ("employees.salary%TYPE", "employees.salary%TYPE"),
        ("employees%ROWTYPE", "employees%ROWTYPE"),
        ("timestamp(0)", "timestamp(0)"),
        ("numeric(10,2)", "numeric(10,2)"),
        ("varchar(20)", "varchar(20)"),
    ],
)
def test_map_plsql_type(oracle: str, pg: str) -> None:
    mapped = map_plsql_type(oracle)
    assert mapped is not None
    assert mapped.text == pg


@pytest.mark.parametrize("oracle", ["UTL_FILE.FILE_TYPE", "my_tab_t", "ROWID"])
def test_map_plsql_type_unknown(oracle: str) -> None:
    assert map_plsql_type(oracle) is None


def test_map_plsql_type_aliases() -> None:
    mapped = map_plsql_type("emp_cur_t", {"EMP_CUR_T": "refcursor"})
    assert mapped is not None
    assert mapped.text == "refcursor"


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


def test_conversion_rule_ids_are_valid_unique_and_sorted() -> None:
    ids = [r.id for r in conversion_rules()]
    assert ids == sorted(set(ids))
    assert all(CONVERSION_RULE_ID.match(i) for i in ids)


def test_rewrites_are_never_blocking() -> None:
    assert not [r.id for r in conversion_rules() if r.is_rewrite and r.blocking]


def test_every_conversion_rule_is_used_by_the_converter() -> None:
    code = "\n".join(
        p.read_text(encoding="utf-8") for p in SRC.glob("*.py") if p.name != "registry.py"
    )
    unused = [r.id for r in conversion_rules() if r.id not in code]
    assert not unused


def test_every_conversion_rule_has_a_test() -> None:
    covered = {r[0] for r in REWRITES} | {r[0] for r in REVIEWS}
    tested_elsewhere = {
        "O2PG-CV-001", "O2PG-CV-002", "O2PG-CV-003", "O2PG-CV-005", "O2PG-CV-007",
        "O2PG-CV-008", "O2PG-CV-009", "O2PG-CV-012", "O2PG-CV-013", "O2PG-CV-014",
        "O2PG-CV-032", "O2PG-RV-001", "O2PG-RV-011", "O2PG-RV-020", "O2PG-RV-024",
        "O2PG-RV-025", "O2PG-RV-029",
    }  # fmt: skip
    missing = {r.id for r in conversion_rules()} - covered - tested_elsewhere
    assert not missing


def test_rule_texts_are_marker_safe() -> None:
    for rule in conversion_rules():
        assert rule.title
        assert rule.description
        assert "*/" not in rule.description


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


def test_markdown_report(sample_result) -> None:  # type: ignore[no-untyped-def]
    text = report_markdown(sample_result)
    assert text.startswith("# o2pg conversion report")
    assert "| Mechanical coverage |" in text
    assert "## Rewrites applied" in text
    assert "## Review points by rule" in text
    assert "### HR.EMP_MGMT (PACKAGE + PACKAGE BODY)" in text
    assert "O2PG-RV-011 (blocking): package variable" in text


def test_json_report_matches_result(sample_result) -> None:  # type: ignore[no-untyped-def]
    import json

    data = json.loads(report_json(sample_result))
    summary = data["summary"]
    assert summary["statements"] == sample_result.statements
    assert summary["clean_statements"] == sample_result.clean_statements
    assert summary["mechanical_coverage_pct"] == round(sample_result.coverage, 1)
    assert summary["review_points"] == len(sample_result.reviews)
    assert summary["rewrites_applied"] == sum(sample_result.rewrites.values())
    assert len(data["objects"]) == len(sample_result.objects)
    first = data["objects"][0]
    assert {"object", "file", "status", "units", "review_points", "rewrites"} <= set(first)


def test_report_counts_units_by_status(sample_result) -> None:  # type: ignore[no-untyped-def]
    summary = report_dict(sample_result)["summary"]
    assert summary["units_converted"] == 5
    assert summary["units_review"] == 3
    assert summary["units_blocked"] == 9
    assert summary["units_not_converted"] == 4
