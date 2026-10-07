"""Command line interface: ``o2pg assess | rules | sample | map-types``."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import click

from o2pg import __version__
from o2pg.assessment import assess
from o2pg.catalog import rules_json, rules_markdown, rules_table
from o2pg.ddl import suggest_ddl
from o2pg.inventory import Inventory, InventoryError
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
