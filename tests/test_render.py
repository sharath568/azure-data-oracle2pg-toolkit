"""Markdown, JSON and HTML renderers."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest

from helpers import code, column, inventory
from o2pg.assessment import Assessment, assess
from o2pg.inventory import Inventory
from o2pg.render import RENDERERS, render_html, render_json, render_markdown
from o2pg.render.common import MAX_OBJECTS_PER_FINDING


@pytest.fixture
def small() -> Assessment:
    inv = inventory(
        column("DATE"),
        code("SELECT a || b, NVL(c, '') FROM t WHERE x = '<script>'", "VIEW"),
        metadata={"db_name": "DB1", "db_version": "19.0.0.0.0", "schemas": "APP"},
    )
    return assess(inv)


@pytest.fixture
def many_hits() -> Assessment:
    inv = inventory(*(column("DATE", name=f"C{i}") for i in range(MAX_OBJECTS_PER_FINDING + 5)))
    return assess(inv)


def test_renderer_registry() -> None:
    assert set(RENDERERS) == {"md", "json", "html"}


def test_markdown_sections(small: Assessment) -> None:
    text = render_markdown(small)
    for heading in (
        "# o2pg assessment: DB1",
        "## Summary",
        "## AI readiness",
        "## Azure extensions to allow-list",
        "## Findings",
        "### O2PG-DT-003: DATE columns carry a time of day",
        "## Most complex objects",
        "## About the numbers",
    ):
        assert heading in text
    assert "Oracle 19.0.0.0.0; schemas APP" in text


def test_markdown_escapes_pipes_in_cells() -> None:
    inv = inventory(column("VARCHAR2", virtual="YES", data_default="A || B"))
    text = render_markdown(assess(inv))
    assert "expression: A \\|\\| B" in text
    for line in text.splitlines():
        if line.startswith("|"):
            assert "||" not in line.replace("\\|", ""), line


def test_markdown_truncates_long_hit_lists(many_hits: Assessment) -> None:
    assert "...and 5 more." in render_markdown(many_hits)


def test_markdown_empty_assessment() -> None:
    text = render_markdown(assess(Inventory()))
    assert "No rules triggered." in text
    assert "o2pg assessment: Oracle inventory" in text
    assert "no metadata in inventory" in text


def test_json_round_trip(small: Assessment) -> None:
    data = json.loads(render_json(small))
    assert data["tool"]["name"] == "o2pg"
    assert data["source"]["db_name"] == "DB1"
    assert data["complexity"]["score"] == small.complexity.score
    ids = [f["rule_id"] for f in data["findings"]]
    assert "O2PG-DT-003" in ids
    first = data["findings"][0]
    assert set(first) >= {"severity", "objects", "remediation", "occurrences", "effort_points"}
    assert data["ai_readiness"]["risks"]


def test_json_is_valid_for_empty_assessment() -> None:
    data = json.loads(render_json(assess(Inventory())))
    assert data["findings"] == []


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        for name, value in attrs:
            if name in {"src", "href"} and value and not value.startswith("#"):
                self.external.append(value)


def test_html_is_self_contained(small: Assessment) -> None:
    page = render_html(small)
    parser = _Collector()
    parser.feed(page)
    assert page.startswith("<!doctype html>")
    assert "script" not in parser.tags
    assert parser.external == []
    assert "<style>" in page
    assert "prefers-color-scheme: dark" in page


def test_html_escapes_user_content(small: Assessment) -> None:
    page = render_html(small)
    assert "<script>" not in page


def test_html_has_finding_details(small: Assessment) -> None:
    page = render_html(small)
    assert '<details id="O2PG-DT-003">' in page
    assert re.search(r'class="sev sev-high"', page)


def test_html_truncates_long_hit_lists(many_hits: Assessment) -> None:
    assert "...and 5 more." in render_html(many_hits)


def test_html_empty_assessment() -> None:
    assert "No rules triggered." in render_html(assess(Inventory()))
