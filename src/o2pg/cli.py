"""Command line interface.

``o2pg assess | rules | sample | map-types | convert | ai-convert | equiv``.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import click

from o2pg import __version__
from o2pg.assessment import assess
from o2pg.catalog import rules_json, rules_markdown, rules_table
from o2pg.convert import ConversionResult, convert_inventory, convert_sql, split_script
from o2pg.convert.engine import (
    STATUS_BLOCKED,
    STATUS_CONVERTED,
    STATUS_NOT_CONVERTED,
    STATUS_REVIEW,
)
from o2pg.convert.lexer import ConversionError
from o2pg.convert.report import report_json, report_markdown
from o2pg.ddl import suggest_ddl
from o2pg.inventory import Inventory, InventoryError, SourceObject
from o2pg.render import RENDERERS
from o2pg.rules import Category, Severity, all_rules

_SEVERITIES = [s.value for s in Severity]
_EXTENSIONS = {"md": ".md", "json": ".json", "html": ".html"}
EXIT_FAIL_ON = 2
"""Exit status when ``--fail-on`` finds a rule at or above the given severity."""


def sample_inventory_text() -> str:
    """The bundled sample inventory (an HR / SALES schema) as JSON text."""
    return files("o2pg").joinpath("data/sample_inventory.json").read_text(encoding="utf-8")


def _load(path: Path) -> Inventory:
    try:
        return Inventory.from_path(path)
    except InventoryError as exc:
        raise click.ClickException(str(exc)) from exc


def _write(text: str, output: Path | None) -> None:
    if output is None:
        click.echo(text, nl=False)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    click.echo(f"wrote {output}", err=True)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="o2pg")
def main() -> None:
    """Offline triage of an Oracle schema before moving to Azure Database for PostgreSQL.

    Run collector/collect.sql in SQL*Plus to produce an inventory JSON file, then
    point o2pg at it. No database connection is needed.
    """


@main.command("assess")
@click.argument("inventory", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(sorted(RENDERERS)),
    default="md",
    show_default=True,
    help="Report format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the report here. A missing extension is added for you. Default: stdout.",
)
@click.option(
    "--min-severity",
    type=click.Choice(_SEVERITIES),
    default="info",
    show_default=True,
    help="Ignore rules below this severity.",
)
@click.option(
    "--fail-on",
    type=click.Choice(_SEVERITIES),
    help=f"Exit with status {EXIT_FAIL_ON} if any rule at or above this severity triggers.",
)
def assess_cmd(
    inventory: Path, fmt: str, output: Path | None, min_severity: str, fail_on: str | None
) -> None:
    """Assess INVENTORY and write a report."""
    result = assess(_load(inventory), min_severity=Severity(min_severity))
    if output is not None and not output.suffix:
        output = output.with_suffix(_EXTENSIONS[fmt])
    _write(RENDERERS[fmt](result), output)
    if fail_on is not None:
        threshold = Severity(fail_on).rank
        if any(f.rule.severity.rank <= threshold for f in result.findings):
            raise SystemExit(EXIT_FAIL_ON)


@main.command("rules")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["table", "md", "json"]),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--category",
    "-c",
    type=click.Choice([c.value for c in Category]),
    help="Only list rules in this category.",
)
def rules_cmd(fmt: str, category: str | None) -> None:
    """List the built-in rules."""
    selected = [r for r in all_rules() if category is None or r.category.value == category]
    renderers = {"table": rules_table, "md": rules_markdown, "json": rules_json}
    click.echo(renderers[fmt](selected), nl=False)


@main.command("sample")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("sample_inventory.json"),
    show_default=True,
    help="Where to write the sample inventory. Use '-' for stdout.",
)
def sample_cmd(output: Path) -> None:
    """Write the bundled sample inventory so you can try o2pg without Oracle."""
    _write(sample_inventory_text(), None if str(output) == "-" else output)


@main.command("map-types")
@click.argument("inventory", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the DDL here. Default: stdout.",
)
def map_types_cmd(inventory: Path, output: Path | None) -> None:
    """Suggest PostgreSQL DDL (types, keys, partitioning) for INVENTORY."""
    _write(suggest_ddl(_load(inventory)), output)


def _is_inventory(path: Path, text: str) -> bool:
    return path.suffix.lower() == ".json" or text.lstrip("\ufeff \t\r\n").startswith("{")


def _summary(result: ConversionResult) -> str:
    units = [u for u in result.units if u.kind != "PACKAGE STATE"]
    counts = {
        status: sum(1 for u in units if u.status == status)
        for status in (STATUS_CONVERTED, STATUS_REVIEW, STATUS_BLOCKED, STATUS_NOT_CONVERTED)
    }
    reviews = result.reviews
    return (
        f"{len(result.objects)} objects, {len(units)} units: "
        f"{counts[STATUS_CONVERTED]} converted, {counts[STATUS_REVIEW]} need review, "
        f"{counts[STATUS_BLOCKED]} blocked, {counts[STATUS_NOT_CONVERTED]} not converted; "
        f"{sum(result.rewrites.values())} rewrites, {len(reviews)} review points "
        f"({sum(1 for r in reviews if r.blocking)} blocking); "
        f"mechanical coverage {result.coverage:.1f}%"
    )


def _convert_source(
    source: Path, only: str | None, schema: str
) -> tuple[ConversionResult, list[SourceObject], Inventory | None]:
    """Mechanically convert SOURCE (inventory JSON or SQL script)."""
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise click.ClickException(f"cannot read {source}: {exc}") from exc
    inventory = None
    try:
        if _is_inventory(source, text):
            try:
                inventory = Inventory.from_json(text)
            except InventoryError as exc:
                raise click.ClickException(str(exc)) from exc
            result = convert_inventory(inventory, only=only)
            sources = list(inventory.source)
        else:
            result = convert_sql(text, default_schema=schema, only=only)
            sources = split_script(text, schema)
    except ConversionError as exc:
        raise click.ClickException(str(exc)) from exc
    if only is not None and not result.objects:
        raise click.ClickException(f"no PL/SQL object named {only} in {source}")
    return result, sources, inventory


@main.command("convert")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for one .sql file per object plus the conversion report. "
    "Default: print the converted SQL to stdout.",
)
@click.option("--object", "only", metavar="SCHEMA.NAME", help="Only convert this object.")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["md", "json", "both"]),
    default="both",
    show_default=True,
    help="Report format written next to the .sql files.",
)
@click.option(
    "--schema",
    default="PUBLIC",
    show_default=True,
    help="Owner for unqualified object names in a .sql script.",
)
def convert_cmd(source: Path, output: Path | None, only: str | None, fmt: str, schema: str) -> None:
    """Convert stored PL/SQL in SOURCE to PL/pgSQL.

    SOURCE is an inventory JSON file from collector/collect.sql, or a .sql script
    of CREATE OR REPLACE PROCEDURE / FUNCTION / PACKAGE / TRIGGER units
    separated by "/" lines. Only mechanical rewrites are made; everything else
    is left in place with an O2PG-REVIEW marker.
    """
    result, _, _ = _convert_source(source, only, schema)
    if output is None:
        click.echo("\n".join(obj.sql for obj in result.objects), nl=False)
        click.echo(_summary(result), err=True)
        return
    output.mkdir(parents=True, exist_ok=True)
    for obj in result.objects:
        (output / obj.filename).write_text(obj.sql, encoding="utf-8")
    written = len(result.objects)
    if fmt in {"md", "both"}:
        (output / "conversion-report.md").write_text(report_markdown(result), encoding="utf-8")
        written += 1
    if fmt in {"json", "both"}:
        (output / "conversion-report.json").write_text(report_json(result), encoding="utf-8")
        written += 1
    click.echo(f"wrote {written} files to {output}", err=True)
    click.echo(_summary(result), err=True)


# --------------------------------------------------------------------------------------
# ai-convert
# --------------------------------------------------------------------------------------


@main.command("ai-convert")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory for candidate .sql files, verified.sql and ai-report.md/.json.",
)
@click.option(
    "--provider",
    type=click.Choice(["azure-openai", "replay"]),
    default="azure-openai",
    show_default=True,
    help="azure-openai reads AZURE_OPENAI_* variables; replay reads saved answers.",
)
@click.option(
    "--replay",
    "replay_dir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    help="Directory of saved answers for --provider replay.",
)
@click.option(
    "--record",
    "record_dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Save every model answer here, in replay format.",
)
@click.option(
    "--deployment", help="Azure OpenAI deployment (overrides AZURE_OPENAI_CHAT_DEPLOYMENT)."
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["minimal", "low", "medium", "high"]),
    help="For gpt-5 and o-series deployments.",
)
@click.option(
    "--dsn",
    envvar="O2PG_AI_DSN",
    help="PostgreSQL DSN of a role that can CREATE DATABASE. Enables the compile and "
    "behaviour gates in a throwaway database. Without it only the static gate runs.",
)
@click.option(
    "--cases",
    "cases_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Equivalence case file (o2pg equiv). Its postgres setup files are loaded first.",
)
@click.option(
    "--setup",
    "setup_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Extra SQL files to load into the scratch database (repeatable).",
)
@click.option("--object", "only_object", metavar="SCHEMA.NAME", help="Only this Oracle object.")
@click.option("--unit", "only_unit", metavar="NAME", help="Only this routine, e.g. lab.nth_pos.")
@click.option(
    "--attempts",
    type=click.IntRange(1, 5),
    default=2,
    show_default=True,
    help="Answers per unit: a static or compile failure is sent back once per extra attempt.",
)
@click.option(
    "--blocking-only",
    is_flag=True,
    help="Skip units whose review points are all non-blocking.",
)
@click.option(
    "--behaviour-feedback",
    is_flag=True,
    help="Also send failed equivalence cases back to the model (off by default: it invites "
    "answers fitted to the tests).",
)
@click.option("--schema", default="PUBLIC", show_default=True, help="Owner for a .sql script.")
@click.option("--keep-db", is_flag=True, help="Do not drop the scratch database afterwards.")
def ai_convert_cmd(
    source: Path,
    output: Path,
    provider: str,
    replay_dir: Path | None,
    record_dir: Path | None,
    deployment: str | None,
    reasoning_effort: str | None,
    dsn: str | None,
    cases_path: Path | None,
    setup_files: tuple[Path, ...],
    only_object: str | None,
    only_unit: str | None,
    attempts: int,
    blocking_only: bool,
    behaviour_feedback: bool,
    schema: str,
    keep_db: bool,
) -> None:
    """Ask a model to finish the units o2pg convert left with review points.

    SOURCE is the same inventory JSON or .sql script you give o2pg convert. Units
    that need a design decision (autonomous transactions, package state,
    UTL_FILE, ...) are not sent. Every answer must pass a static gate; with
    --dsn it must also compile in PostgreSQL, and with --cases it must return
    what Oracle returned. Exit status is 1 if any unit sent to the model is not
    verified.
    """
    from o2pg import ai
    from o2pg.equiv import CaseError, load_cases

    result, sources, inventory = _convert_source(source, only_object, schema)
    try:
        model = ai.get_provider(
            provider, replay_dir, deployment=deployment, reasoning_effort=reasoning_effort
        )
    except ai.ProviderError as exc:
        raise click.ClickException(str(exc)) from exc
    if record_dir is not None:
        model = ai.RecordingProvider(model, record_dir)
    cases = None
    if cases_path is not None:
        try:
            cases = load_cases(cases_path)
        except CaseError as exc:
            raise click.ClickException(str(exc)) from exc
    ddl_parts = [p.read_text(encoding="utf-8") for p in (cases.postgres_setup if cases else [])]
    ddl_parts += [p.read_text(encoding="utf-8") for p in setup_files]
    inventory_ddl = suggest_ddl(inventory) if inventory is not None else ""
    prompt_ddl = "\n\n".join([*ddl_parts, inventory_ddl])
    database = None
    if dsn:
        try:
            database = ai.GateDatabase(dsn, keep=keep_db)
        except ImportError as exc:
            raise click.ClickException("--dsn needs psycopg: pip install 'o2pg[pg]'") from exc
        except Exception as exc:  # psycopg.Error, without importing psycopg here
            raise click.ClickException(f"cannot create the scratch database: {exc}") from exc
    try:
        notes = []
        if database is not None:
            try:
                notes += database.setup(
                    [*(cases.postgres_setup if cases else []), *setup_files], inventory_ddl
                )
            except Exception as exc:
                raise click.ClickException(f"setup SQL failed to load: {exc}") from exc
            failed = database.load_units(executable_pairs(result))
            if failed:
                notes.append(f"{failed} mechanical unit(s) did not load (siblings may be missing)")
        run_ = ai.run(
            result,
            ai.oracle_sources(sources),
            model,
            ddl=prompt_ddl,
            database=database,
            cases=cases,
            max_attempts=attempts,
            include_review=not blocking_only,
            behaviour_feedback=behaviour_feedback,
            only=only_unit,
            progress=lambda line: click.echo(line, err=True),
        )
        run_.setup_notes = notes
    finally:
        if database is not None:
            database.close()
    written = ai.write_outputs(run_, output)
    for note in run_.setup_notes:
        click.echo(f"note: {note}", err=True)
    click.echo(f"wrote {len(written)} files to {output}", err=True)
    click.echo(ai.summary_line(run_), err=True)
    sent = [o for o in run_.outcomes if o.sent]
    if any(o.status != ai.VERIFIED for o in sent):
        raise SystemExit(1)


def executable_pairs(result: ConversionResult) -> list:
    """Mechanical units that should CREATE as they are."""
    return [(o, u) for o in result.objects for u in o.units if u.executable]


# --------------------------------------------------------------------------------------
# equiv
# --------------------------------------------------------------------------------------


@main.group("equiv")
def equiv_group() -> None:
    """Record behaviour on Oracle and check converted code against it."""


def _cases_or_fail(path: Path):
    from o2pg.equiv import CaseError, load_cases

    try:
        return load_cases(path)
    except CaseError as exc:
        raise click.ClickException(str(exc)) from exc


@equiv_group.command("check")
@click.argument("cases_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--dsn",
    envvar="O2PG_TEST_DSN",
    required=True,
    help="PostgreSQL DSN of a role that can CREATE DATABASE (a throwaway database is used).",
)
@click.option(
    "--sql",
    "sql_files",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Converted SQL to load after the case file's setup (repeatable, loaded in order).",
)
@click.option("--case", "only", multiple=True, help="Only these case ids (repeatable).")
def equiv_check_cmd(
    cases_path: Path, dsn: str, sql_files: tuple[Path, ...], only: tuple[str, ...]
) -> None:
    """Run CASES against converted code in PostgreSQL; exit 1 if any differs from Oracle."""
    from o2pg.ai import GateDatabase
    from o2pg.equiv import results_table, run_cases

    cases = _cases_or_fail(cases_path)
    selected = [c for c in cases.cases if not only or c.id in only]
    try:
        database = GateDatabase(dsn)
    except ImportError as exc:
        raise click.ClickException("needs psycopg: pip install 'o2pg[pg]'") from exc
    try:
        database.setup(cases.postgres_setup)
        with database.conn.cursor() as cur:
            for path in sql_files:
                try:
                    cur.execute(path.read_text(encoding="utf-8"))
                    database.conn.commit()
                except Exception as exc:
                    database.conn.rollback()
                    raise click.ClickException(f"{path} did not load: {exc}") from exc
        runner = database.runner(cases.search_path)
        results = run_cases(selected, lambda c: runner.run(c, cases.data))
    finally:
        database.close()
    click.echo(results_table(results))
    if not all(r.passed for r in results):
        raise SystemExit(1)


@equiv_group.command("record")
@click.argument("cases_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--oracle-dsn",
    envvar="O2PG_ORACLE_DSN",
    required=True,
    help="python-oracledb connect string, e.g. lab/secret@localhost:1521/FREEPDB1.",
)
@click.option(
    "--setup/--no-setup",
    default=True,
    show_default=True,
    help="Run the case file's oracle setup scripts first (needs an empty schema).",
)
@click.option(
    "--write",
    is_flag=True,
    help="Rewrite the case file with what Oracle returned. Default: compare and report.",
)
def equiv_record_cmd(cases_path: Path, oracle_dsn: str, setup: bool, write: bool) -> None:
    """Replay CASES on Oracle. Exit 1 if Oracle disagrees with the file (unless --write)."""
    from o2pg.equiv import (
        CaseResult,
        OracleRunner,
        compare,
        prepare_oracle,
        results_table,
        updated_case_file,
    )

    try:
        import oracledb
    except ImportError as exc:
        raise click.ClickException("needs python-oracledb: pip install 'o2pg[oracle]'") from exc
    cases = _cases_or_fail(cases_path)
    user, _, rest = oracle_dsn.partition("/")
    password, _, dsn = rest.rpartition("@")
    if not (user and password and dsn):
        raise click.ClickException("--oracle-dsn must look like user/password@host:port/service")
    try:
        conn = oracledb.connect(user=user, password=password, dsn=dsn)
    except oracledb.Error as exc:
        raise click.ClickException(f"cannot connect to Oracle: {exc}") from exc
    try:
        if setup:
            problems = prepare_oracle(conn, cases.oracle_setup)
            if problems:
                raise click.ClickException(
                    "Oracle setup did not compile cleanly:\n  " + "\n  ".join(problems)
                )
        runner = OracleRunner(conn)
        results = []
        recorded = {}
        for case in cases.cases:
            outcome = runner.run(case, cases.data)
            if outcome.setup_error:
                raise click.ClickException(f"{case.id}: {outcome.setup_error}")
            recorded[case.id] = outcome.as_expectation(case)
            results.append(CaseResult(case, outcome, compare(case, outcome)))
    finally:
        conn.close()
    if write:
        cases_path.write_text(updated_case_file(cases, recorded), encoding="utf-8")
        click.echo(f"wrote Oracle results for {len(recorded)} cases to {cases_path}", err=True)
        return
    click.echo(results_table(results))
    if not all(r.passed for r in results):
        raise SystemExit(1)
