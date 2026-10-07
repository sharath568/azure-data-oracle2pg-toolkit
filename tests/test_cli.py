"""Command line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from o2pg import __version__
from o2pg.cli import EXIT_FAIL_ON, main, sample_inventory_text
from o2pg.rules import all_rules


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "inv.json"
    path.write_text(sample_inventory_text(), encoding="utf-8")
    return path


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("assess", "rules", "sample", "map-types"):
        assert command in result.output


def test_assess_markdown_to_stdout(runner: CliRunner, sample_file: Path) -> None:
    result = runner.invoke(main, ["assess", str(sample_file)])
    assert result.exit_code == 0
    assert result.output.startswith("# o2pg assessment: ORCLPDB1")


@pytest.mark.parametrize(("fmt", "suffix"), [("md", ".md"), ("json", ".json"), ("html", ".html")])
def test_assess_writes_file_and_adds_extension(
    runner: CliRunner, sample_file: Path, tmp_path: Path, fmt: str, suffix: str
) -> None:
    target = tmp_path / "out" / "report"
    result = runner.invoke(main, ["assess", str(sample_file), "-f", fmt, "-o", str(target)])
    assert result.exit_code == 0
    written = target.with_suffix(suffix)
    assert written.exists()
    assert written.read_text(encoding="utf-8")


def test_assess_keeps_explicit_extension(
    runner: CliRunner, sample_file: Path, tmp_path: Path
) -> None:
    target = tmp_path / "report.txt"
    result = runner.invoke(main, ["assess", str(sample_file), "-o", str(target)])
    assert result.exit_code == 0
    assert target.exists()


def test_assess_json_is_parseable(runner: CliRunner, sample_file: Path) -> None:
    result = runner.invoke(main, ["assess", str(sample_file), "--format", "json"])
    assert json.loads(result.output)["source"]["db_name"] == "ORCLPDB1"


def test_assess_min_severity(runner: CliRunner, sample_file: Path) -> None:
    result = runner.invoke(
        main, ["assess", str(sample_file), "-f", "json", "--min-severity", "blocker"]
    )
    severities = {f["severity"] for f in json.loads(result.output)["findings"]}
    assert severities == {"blocker"}


def test_assess_fail_on(runner: CliRunner, sample_file: Path) -> None:
    result = runner.invoke(main, ["assess", str(sample_file), "--fail-on", "blocker"])
    assert result.exit_code == EXIT_FAIL_ON


def test_assess_fail_on_passes_when_clean(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("{}", encoding="utf-8")
    result = runner.invoke(main, ["assess", str(path), "--fail-on", "low"])
    assert result.exit_code == 0


def test_assess_bad_json_is_a_clean_error(runner: CliRunner, tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{oops", encoding="utf-8")
    result = runner.invoke(main, ["assess", str(path)])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output
    assert "Traceback" not in result.output


def test_assess_missing_file(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["assess", str(tmp_path / "nope.json")])
    assert result.exit_code == 2


def test_rules_table(runner: CliRunner) -> None:
    result = runner.invoke(main, ["rules"])
    assert result.exit_code == 0
    assert "O2PG-DT-001" in result.output
    assert f"{len(all_rules())} rules" in result.output


def test_rules_category_filter(runner: CliRunner) -> None:
    result = runner.invoke(main, ["rules", "--category", "partitioning", "-f", "json"])
    data = json.loads(result.output)
    assert data
    assert {r["category"] for r in data} == {"partitioning"}


def test_rules_markdown(runner: CliRunner) -> None:
    result = runner.invoke(main, ["rules", "-f", "md"])
    assert result.output.startswith("# o2pg rule reference")


def test_sample_writes_file(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "sample.json"
    result = runner.invoke(main, ["sample", "-o", str(target)])
    assert result.exit_code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["metadata"]["db_name"] == "ORCLPDB1"


def test_sample_default_path(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["sample"])
    assert result.exit_code == 0
    assert (tmp_path / "sample_inventory.json").exists()


def test_sample_to_stdout(runner: CliRunner) -> None:
    result = runner.invoke(main, ["sample", "-o", "-"])
    assert json.loads(result.output)["metadata"]["schemas"] == "HR,SALES"


def test_map_types(runner: CliRunner, sample_file: Path, tmp_path: Path) -> None:
    result = runner.invoke(main, ["map-types", str(sample_file)])
    assert result.exit_code == 0
    assert "CREATE TABLE hr.employees (" in result.output
    target = tmp_path / "schema.sql"
    result = runner.invoke(main, ["map-types", str(sample_file), "-o", str(target)])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8").startswith("-- Suggested PostgreSQL DDL")


def test_python_dash_m(sample_file: Path) -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "o2pg", "assess", str(sample_file), "--min-severity", "blocker"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "O2PG-DT-010" in result.stdout
