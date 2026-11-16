"""ai-convert and equiv against a real PostgreSQL server (O2PG_TEST_DSN).

These tests pin down three things:

* the mechanical v0.2 output of the hard corpus matches Oracle on only 6 of
  34 cases, and the units with non-blocking review points run but answer
  differently, which is why behaviour has to be tested;
* the hand-written reference answers pass every gate and every case;
* each flaw in the hand-written flawed answers is caught by the gate meant to
  catch it, and the two recoverable ones pass after feedback.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from o2pg import ai
from o2pg.cli import main
from o2pg.convert import convert_sql
from o2pg.convert.engine import split_script
from o2pg.equiv import load_cases, run_cases

pytestmark = pytest.mark.integration

DSN = os.environ.get("O2PG_TEST_DSN", "")
AI = Path(__file__).parent / "fixtures" / "ai"
CASES_PATH = AI / "cases.json"
LAB_SQL = (AI / "lab.sql").read_text(encoding="utf-8")


@pytest.fixture
def database():
    pytest.importorskip("psycopg")
    if not DSN:
        pytest.skip("O2PG_TEST_DSN is not set")
    db = ai.GateDatabase(DSN)
    try:
        yield db
    finally:
        db.close()


def _run(database: ai.GateDatabase, directory: str, **kwargs) -> ai.AiRun:
    cases = load_cases(CASES_PATH)
    database.setup(cases.postgres_setup)
    result = convert_sql(LAB_SQL)
    return ai.run(
        result,
        ai.oracle_sources(split_script(LAB_SQL)),
        ai.ReplayProvider(AI / "responses" / directory),
        ddl=(AI / "pg_schema.sql").read_text(encoding="utf-8"),
        database=database,
        cases=cases,
        **kwargs,
    )


def test_mechanical_baseline(database: ai.GateDatabase) -> None:
    cases = load_cases(CASES_PATH)
    database.setup(cases.postgres_setup)
    result = convert_sql(LAB_SQL)
    units = {u.name: (o, u) for o in result.objects for u in o.units}
    runner = database.runner(cases.search_path)

    def run_one(case):
        obj, unit = units[case.unit]
        return runner.run(case, cases.data, before=[*obj.preamble, unit.sql])

    results = {r.case.id: r for r in run_cases(cases.cases, run_one)}
    assert sum(r.passed for r in results.values()) == 6
    # Non-blocking review points: the code runs and gives Oracle-incompatible answers.
    assert results["contact_line.null_email"].diffs == ["returned NULL, expected 'Bob <>'"]
    assert results["code_kind.empty_string_is_null"].diffs == [
        "returned 'empty', expected 'missing'"
    ]
    assert results["avg_split.fraction_kept"].diffs == ["returned 3, expected 3.5"]
    assert results["digits_only.no_digits_is_null"].diffs == ["returned '/a', expected NULL"]
    # check_function_bodies does not catch an unknown column: only running it does.
    assert database.compile([], units["lab.highest_paid"][1].sql).passed
    assert "rownum" in results["highest_paid.engineering"].diffs[0]


def test_reference_answers_are_verified(database: ai.GateDatabase) -> None:
    run = _run(database, "reference")
    statuses = {o.name: o.status for o in run.outcomes}
    assert statuses.pop("lab.log_event") == ai.SKIPPED
    assert set(statuses.values()) == {ai.VERIFIED}
    cases = [c for o in run.outcomes for c in o.cases]
    assert len(cases) == 34
    assert all(c.passed for c in cases)


def test_flawed_answers_are_caught_by_the_right_gate(database: ai.GateDatabase) -> None:
    run = _run(database, "flawed")
    by_name = {o.name: o for o in run.outcomes}
    assert {n for n, o in by_name.items() if o.status == ai.VERIFIED} == {
        "lab.chain_of_command",
        "lab.contact_line",
        "lab.hired_since",
    }
    assert by_name["lab.code_kind"].status == ai.UNRESOLVED
    static = {"lab.highest_paid", "lab.nth_pos"}
    behaviour = {
        "lab.headcount_under",
        "lab.depts_without_earner",
        "lab.raise_dept",
        "lab.days_between",
        "lab.avg_split",
        "lab.last_chars",
        "lab.digits_only",
    }
    for name in static:
        assert by_name[name].status == ai.REJECTED
        assert by_name[name].failed_gate == "static"
    for name in behaviour:
        assert by_name[name].status == ai.REJECTED, name
        assert by_name[name].failed_gate == "behaviour", name
    chain = by_name["lab.chain_of_command"]
    assert [(g.gate, g.passed) for g in chain.gates] == [
        ("static", True),
        ("compile", False),
        ("static", True),
        ("compile", True),
        ("behaviour", True),
    ]
    raise_dept = by_name["lab.raise_dept"]
    assert {tuple(c.diffs) for c in raise_dept.cases} == {
        ("OUT parameters (1), expected (3)",),
        ("OUT parameters (1), expected (2)",),
    }
    depts = by_name["lab.depts_without_earner"]
    assert depts.cases[0].diffs == ["returned 0, expected 1"]


def test_behaviour_feedback_retries_a_failed_case(database: ai.GateDatabase) -> None:
    run = _run(database, "flawed", behaviour_feedback=True, only="lab.avg_split")
    (outcome,) = run.outcomes
    # The flawed set has one answer for avg_split, so the retry finds nothing to replay.
    assert outcome.status == ai.ERROR
    assert outcome.attempts == 2
    assert outcome.gates[-1].gate == "behaviour"
    assert "attempt 2" in outcome.reason


def test_cli_ai_convert_and_equiv_check(tmp_path: Path) -> None:
    pytest.importorskip("psycopg")
    if not DSN:
        pytest.skip("O2PG_TEST_DSN is not set")
    out = tmp_path / "ai"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "ai-convert",
            str(AI / "lab.sql"),
            "-o",
            str(out),
            "--provider",
            "replay",
            "--replay",
            str(AI / "responses" / "reference"),
            "--cases",
            str(CASES_PATH),
            "--dsn",
            DSN,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "13 verified" in result.output
    assert "Equivalence cases matching Oracle | 34 of 34" in (out / "ai-report.md").read_text()
    check = runner.invoke(
        main, ["equiv", "check", str(CASES_PATH), "--dsn", DSN, "--sql", str(out / "verified.sql")]
    )
    assert check.exit_code == 0, check.output
    assert "34 of 34 cases match Oracle" in check.output
    flawed = runner.invoke(
        main,
        [
            "ai-convert",
            str(AI / "lab.sql"),
            "-o",
            str(tmp_path / "flawed"),
            "--provider",
            "replay",
            "--replay",
            str(AI / "responses" / "flawed"),
            "--cases",
            str(CASES_PATH),
            "--dsn",
            DSN,
        ],
    )
    assert flawed.exit_code == 1
    assert "3 verified, 1 unresolved, 9 rejected" in flawed.output
