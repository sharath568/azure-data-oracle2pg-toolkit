"""Lexer and package-state heuristics."""

from __future__ import annotations

import pytest

from o2pg.plsql import HINT_MARKER, MASKED_LITERAL, clean_source, line_of, package_state_variables


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x := 1; -- ROWNUM here", "x := 1; "),
        ("a /* ROWNUM\nCONNECT BY */ b", "a \n b"),
        ("SELECT /*+ FULL(t) */ 1", f"SELECT {HINT_MARKER} 1"),
        ("x := 'ROWNUM';", f"x := {MASKED_LITERAL};"),
        ("x := '';", "x := '';"),
        ("x := 'it''s';", f"x := {MASKED_LITERAL};"),
        ("x := '''';", f"x := {MASKED_LITERAL};"),
        ("x := q'[it's ROWNUM]';", f"x := {MASKED_LITERAL};"),
        ("x := Q'{a}';", f"x := {MASKED_LITERAL};"),
        ("x := q'!a'b!';", f"x := {MASKED_LITERAL};"),
        ("x := nq'<a>';", f"x := {MASKED_LITERAL};"),
        ('SELECT "ROWNUM" FROM t', 'SELECT "ROWNUM" FROM t'),
        ("seq'", f"seq{MASKED_LITERAL}"),
    ],
)
def test_clean_source(source: str, expected: str) -> None:
    assert clean_source(source) == expected


def test_clean_source_keeps_line_numbers_through_multiline_literals() -> None:
    text = "a := 'line1\nline2';\nb := NVL(c, 0);"
    cleaned = clean_source(text)
    assert cleaned.count("\n") == text.count("\n")
    assert line_of(cleaned, cleaned.index("NVL")) == 3


def test_clean_source_handles_unterminated_comment() -> None:
    assert clean_source("x /* never closed") == "x "


def test_empty_literal_next_to_concatenation() -> None:
    assert clean_source("x := '' || 'a';") == f"x := '' || {MASKED_LITERAL};"


def test_line_of() -> None:
    assert line_of("a\nb\nc", 0) == 1
    assert line_of("a\nb\nc", 4) == 3


def test_package_state_spec() -> None:
    spec = (
        "PACKAGE p AS\n"
        "  g_a NUMBER;\n"
        "  g_b employees.salary%TYPE := 0;\n"
        "  c_x CONSTANT NUMBER := 1;\n"
        "  CURSOR c IS SELECT * FROM t;\n"
        "  SUBTYPE s_t IS NUMBER;\n"
        "  PROCEDURE run;\n"
        "END p;\n"
    )
    assert package_state_variables(clean_source(spec), is_body=False) == ["g_a", "g_b"]


def test_package_state_ignores_cursor_query_lines() -> None:
    spec = "PACKAGE p AS\n  CURSOR c IS\n    SELECT a\n    FROM employees;\nEND p;\n"
    assert package_state_variables(clean_source(spec), is_body=False) == []


def test_package_state_body_with_authid_header() -> None:
    body = "PACKAGE BODY p AUTHID DEFINER AS\n  g_x DATE;\n  FUNCTION f RETURN NUMBER;\nEND p;\n"
    assert package_state_variables(clean_source(body), is_body=True) == ["g_x"]


def test_package_state_body_without_globals() -> None:
    body = "PACKAGE BODY p AS\n  PROCEDURE run IS\n    l_x NUMBER;\n  BEGIN NULL; END;\nEND p;\n"
    assert package_state_variables(clean_source(body), is_body=True) == []
