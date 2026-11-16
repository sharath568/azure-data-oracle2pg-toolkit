"""Static gate: statement splitting, signatures and Oracle leftovers."""

from __future__ import annotations

import pytest

from o2pg.ai.gates import (
    code_only,
    oracle_leftovers,
    signatures,
    split_statements,
    static_gate,
)

ROUTINE = """CREATE OR REPLACE FUNCTION lab.f (p_x numeric, OUT p_y varchar)
RETURNS varchar
LANGUAGE plpgsql
SET search_path = lab, public
AS $$
BEGIN
  -- ROWNUM in a comment is fine; so is 'NVL(' in a string
  p_y := 'a;b';
  RETURN p_y;
END;
$$;"""


def test_split_statements_ignores_semicolons_in_bodies_strings_and_comments() -> None:
    text = (
        "/* a; b */ SELECT 1;\n"
        + ROUTINE
        + "\nCREATE TRIGGER t AFTER INSERT ON x EXECUTE FUNCTION f();"
    )
    parts = split_statements(text)
    assert len(parts) == 3
    assert parts[1].startswith("CREATE OR REPLACE FUNCTION")
    assert parts[1].endswith("$$;")


def test_split_statements_handles_tagged_dollar_quotes() -> None:
    text = "CREATE FUNCTION f() RETURNS void AS $body$ BEGIN EXECUTE $q$SELECT 1;$q$; END; $body$;"
    assert len(split_statements(text)) == 1


def test_code_only_blanks_comments_and_strings_but_keeps_bodies() -> None:
    code = code_only(ROUTINE)
    assert "ROWNUM" not in code
    assert "NVL" not in code
    assert "RETURN p_y" in code
    assert len(code) == len(ROUTINE)


def test_signature_parsing_normalises_types_and_modes() -> None:
    (sig,) = signatures(ROUTINE)
    assert sig.kind == "FUNCTION"
    assert sig.name == "lab.f"
    assert sig.params == (("IN", "p_x", "numeric"), ("OUT", "p_y", "varchar"))
    assert sig.returns == "varchar"
    same = signatures(
        'CREATE FUNCTION "lab"."f"(IN p_x DECIMAL, OUT p_y character varying) '
        "RETURNS character varying LANGUAGE sql AS 'x';"
    )
    assert same == [sig]


def test_signature_with_defaults_and_table_return() -> None:
    (sig,) = signatures(
        "CREATE OR REPLACE FUNCTION s.g (a integer DEFAULT 1, b timestamp(0) = now()) "
        "RETURNS TABLE (id numeric, name varchar(50)) AS $$ $$ LANGUAGE plpgsql;"
    )
    assert sig.params == (("IN", "a", "integer"), ("IN", "b", "timestamp(0)"))
    assert sig.returns == "table(id numeric,name varchar(50))"


@pytest.mark.parametrize(
    ("snippet", "found"),
    [
        ("WHERE ROWNUM = 1", "ROWNUM"),
        ("START WITH emp_id = 1 CONNECT BY PRIOR", "CONNECT BY"),
        ("WHERE a.x = b.x (+)", "(+) outer join"),
        ("SELECT id BULK COLLECT INTO ids", "BULK COLLECT"),
        ("RETURN NVL(x, 0);", "NVL()"),
        ("RETURN instr(a, b, 1, 2);", "INSTR()"),
        ("x := SYSDATE;", "SYSDATE"),
        ("NEW.x := :NEW.y;", ":NEW/:OLD"),
        ("IF SQL%NOTFOUND THEN", "cursor attribute"),
        ("SELECT 1 MINUS SELECT 2", "MINUS (use EXCEPT)"),
        ("DBMS_OUTPUT.PUT_LINE('x');", "DBMS_ package"),
    ],
)
def test_oracle_leftovers_found(snippet: str, found: str) -> None:
    hits = oracle_leftovers(f"BEGIN\n  {snippet}\nEND;")
    assert any(h.startswith(found) for h in hits), hits


def test_oracle_leftovers_ignores_comments_strings_and_casts() -> None:
    text = "/* ROWNUM */ -- NVL(\nSELECT 'CONNECT BY', x::new_type, nvl_count FROM t;"
    assert oracle_leftovers(text) == []


def _expected():
    return signatures(ROUTINE)[0]


def test_static_gate_passes_the_same_routine() -> None:
    check = static_gate(ROUTINE, _expected(), False, False, [])
    assert check.result.passed, check.result.detail
    assert not check.contract_change


def test_static_gate_rejects_extra_statements() -> None:
    check = static_gate("DROP FUNCTION lab.f;\n" + ROUTINE, _expected(), False, False, [])
    assert not check.result.passed
    assert "statement not allowed" in check.result.detail


def test_static_gate_rejects_create_extension() -> None:
    check = static_gate("CREATE EXTENSION orafce;\n" + ROUTINE, _expected(), False, False, [])
    assert "CREATE EXTENSION" in check.result.detail


def test_static_gate_signature_change_needs_a_contract_rule() -> None:
    changed = ROUTINE.replace("p_x numeric", "p_x integer")
    rejected = static_gate(changed, _expected(), False, False, [])
    assert not rejected.result.passed
    assert "signature changed" in rejected.result.detail
    allowed = static_gate(changed, _expected(), False, True, [])
    assert allowed.result.passed
    assert allowed.contract_change


def test_static_gate_create_type_only_with_contract_rule() -> None:
    text = "CREATE TYPE lab.r AS (a int);\n" + ROUTINE
    assert not static_gate(text, _expected(), False, False, []).result.passed
    assert static_gate(text, _expected(), False, True, []).result.passed


def test_static_gate_wrong_name() -> None:
    other = ROUTINE.replace("lab.f", "lab.g")
    check = static_gate(other, _expected(), False, False, [])
    assert "expected exactly one CREATE for lab.f" in check.result.detail


def test_static_gate_markers_must_be_declared() -> None:
    marked = ROUTINE.replace(
        "BEGIN\n", "BEGIN\n  /* O2PG-REVIEW O2PG-RV-034: '' is NULL in Oracle */\n", 1
    )
    undeclared = static_gate(marked, _expected(), False, False, [])
    assert "review markers left" in undeclared.result.detail
    declared = static_gate(marked, _expected(), False, False, ["RV-034 needs a person"])
    assert declared.result.passed
    assert declared.leftover_markers == ["O2PG-RV-034 at line 7"]


def test_static_gate_trigger_needs_its_create_trigger() -> None:
    fn = """CREATE OR REPLACE FUNCTION app.t_biu()
RETURNS trigger
LANGUAGE plpgsql
AS $$ BEGIN RETURN NEW; END; $$;"""
    trig = (
        "\nCREATE OR REPLACE TRIGGER t_biu BEFORE INSERT ON app.t FOR EACH ROW "
        "EXECUTE FUNCTION app.t_biu();"
    )
    expected = signatures(fn)[0]
    assert not static_gate(fn, expected, True, False, []).result.passed
    assert static_gate(fn + trig, expected, True, False, []).result.passed
    assert not static_gate(fn + trig, expected, False, False, []).result.passed
