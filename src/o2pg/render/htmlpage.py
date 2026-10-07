"""Self-contained HTML report renderer (inline CSS, no scripts, no external assets)."""

from __future__ import annotations

from html import escape

from o2pg.assessment import Assessment
from o2pg.render.common import (
    COUNT_LABELS,
    MAX_OBJECTS_PER_FINDING,
    SCORING_NOTE,
    SEVERITY_LABELS,
    source_line,
    title_of,
)

_CSS = """
:root { --bg:#ffffff; --fg:#1d2330; --muted:#5b6475; --line:#d9dee7; --card:#f5f7fa;
  --blocker:#b42318; --high:#c4320a; --medium:#b54708; --low:#175cd3; --info:#475467;
  --accent:#0b6bcb; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --fg:#e6e9ef; --muted:#9aa4b2;
  --line:#2c323c; --card:#1c2027; --blocker:#f97066; --high:#fd853a; --medium:#fdb022;
  --low:#84adff; --info:#98a2b3; --accent:#6cb4ff; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:1080px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:1.6rem; margin:0 0 4px; }
h2 { margin-top:40px; border-bottom:1px solid var(--line); padding-bottom:6px; }
.muted { color:var(--muted); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px;
  margin-top:20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:12px 14px; }
.card b { display:block; font-size:1.5rem; } .card span { color:var(--muted); font-size:.85rem; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; margin:8px 0 16px; font-size:.9rem; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-weight:600; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.85em; }
.sev { display:inline-block; padding:1px 8px; border-radius:999px; font-size:.75rem;
  font-weight:600; color:var(--bg); }
.sev-blocker { background:var(--blocker); } .sev-high { background:var(--high); }
.sev-medium { background:var(--medium); } .sev-low { background:var(--low); }
.sev-info { background:var(--info); }
details { border:1px solid var(--line); border-radius:8px; margin:10px 0; background:var(--card); }
summary { cursor:pointer; padding:10px 14px; font-weight:600; }
details > div { padding:0 14px 8px; }
ol li { margin-bottom:6px; } a { color:var(--accent); }
"""


def _sev(value: str) -> str:
    return f'<span class="sev sev-{escape(value)}">{escape(SEVERITY_LABELS[value])}</span>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Build a table; cells are expected to be escaped already."""
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return f'<div class="scroll">{table}</div>'


def _cards(a: Assessment) -> str:
    c = a.complexity
    items = [
        (f"{c.score}/100", f"Complexity ({c.level})"),
        (c.effort_band, f"Effort: {c.effort_range}"),
        (str(c.blockers), "Blockers"),
        (f"{a.ai.score}/100", "AI readiness"),
        (f"{len(a.findings)}/{a.rules_evaluated}", "Rules triggered"),
    ]
    cards = "".join(
        f'<div class="card"><b>{escape(value)}</b><span>{escape(label)}</span></div>'
        for value, label in items
    )
    return f'<div class="cards">{cards}</div>'


def _ai(a: Assessment) -> str:
    out = [f"<h2>AI readiness</h2><p><b>{a.ai.score}/100.</b> {escape(a.ai.verdict)}</p>"]
    if a.ai.risks:
        out.append(
            "<p>These Oracle behaviours are implicit in the schema and code. Put this list in "
            "your prompt or conversion spec:</p><ol>"
        )
        out += [
            f"<li><b>{escape(r.title)}</b> <code>{escape(r.rule_id)}</code>, "
            f"{r.occurrences} object(s): {escape(r.guidance)}</li>"
            for r in a.ai.risks
        ]
        out.append("</ol>")
    return "".join(out)


def _extensions(a: Assessment) -> str:
    if not a.extensions:
        return ""
    rows = [
        [f"<code>{escape(n)}</code>", escape(", ".join(ids))] for n, ids in a.extensions.items()
    ]
    return (
        "<h2>Azure extensions to allow-list</h2><p>Add these to the <code>azure.extensions</code> "
        "server parameter before <code>CREATE EXTENSION</code>. <code>pg_cron</code> also needs "
        "<code>shared_preload_libraries</code> and a restart.</p>"
        + _table(["Extension", "Suggested by"], rows)
    )


def _findings(a: Assessment) -> str:
    if not a.findings:
        return "<h2>Findings</h2><p>No rules triggered.</p>"
    rows = [
        [
            _sev(f.rule.severity.value),
            f"<code>{escape(f.rule.id)}</code>",
            escape(f.rule.title),
            str(len(f.hits)),
            str(f.effort_points),
        ]
        for f in a.findings
    ]
    out = ["<h2>Findings</h2>", _table(["Severity", "Rule", "Title", "Objects", "Effort"], rows)]
    for f in a.findings:
        r = f.rule
        shown = f.hits[:MAX_OBJECTS_PER_FINDING]
        hit_rows = [[escape(h.object_name), escape(h.object_type), escape(h.detail)] for h in shown]
        more = (
            f'<p class="muted">...and {len(f.hits) - len(shown)} more.</p>'
            if len(f.hits) > len(shown)
            else ""
        )
        out.append(
            f'<details id="{escape(r.id)}"><summary>{_sev(r.severity.value)} '
            f"<code>{escape(r.id)}</code> {escape(r.title)} ({len(f.hits)})</summary><div>"
            f'<p class="muted">Category: {escape(r.category.value)} · Effort points: '
            f"{f.effort_points}</p><p>{escape(r.description)}</p>"
            f"<p><b>What to do:</b> {escape(r.remediation)}</p>"
            f"{_table(['Object', 'Type', 'Detail'], hit_rows)}{more}</div></details>"
        )
    return "".join(out)


def _top_objects(a: Assessment) -> str:
    if not a.top_objects:
        return ""
    rows = [
        [escape(o.name), escape(o.object_type), str(o.score), escape(", ".join(o.rule_ids))]
        for o in a.top_objects
    ]
    return "<h2>Most complex objects</h2>" + _table(["Object", "Type", "Score", "Rules"], rows)


def _counts(a: Assessment) -> str:
    rows = [[escape(COUNT_LABELS.get(k, k)), str(v)] for k, v in a.object_counts.items() if v]
    return "<h2>Objects in the inventory</h2>" + _table(["Object", "Count"], rows)


def render_html(assessment: Assessment) -> str:
    """Render the assessment as a single self-contained HTML page."""
    title = escape(title_of(assessment))
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{title}</h1>"
        f'<p class="muted">Generated {escape(assessment.generated_at)} by o2pg '
        f"{escape(assessment.tool_version)}. Source: {escape(source_line(assessment))}.</p>"
        f"{_cards(assessment)}{_ai(assessment)}{_extensions(assessment)}"
        f"{_findings(assessment)}{_top_objects(assessment)}{_counts(assessment)}"
        f'<h2>About the numbers</h2><p class="muted">{escape(SCORING_NOTE)}</p>'
        "</main></body></html>\n"
    )
