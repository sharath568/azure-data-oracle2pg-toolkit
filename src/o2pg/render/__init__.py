"""Report renderers. Each takes an :class:`~o2pg.assessment.Assessment` and returns text."""

from __future__ import annotations

from collections.abc import Callable

from o2pg.assessment import Assessment
from o2pg.render.htmlpage import render_html
from o2pg.render.jsonout import render_json
from o2pg.render.markdown import render_markdown

RENDERERS: dict[str, Callable[[Assessment], str]] = {
    "md": render_markdown,
    "json": render_json,
    "html": render_html,
}

__all__ = ["RENDERERS", "render_html", "render_json", "render_markdown"]
