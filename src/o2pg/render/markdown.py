"""Markdown report renderer (GitHub-flavoured tables)."""

from __future__ import annotations

from o2pg.assessment import Assessment
from o2pg.render.common import (
    COUNT_LABELS,
    MAX_OBJECTS_PER_FINDING,
    SCORING_NOTE,
    SEVERITY_LABELS,
    source_line,
    title_of,
)


def _cell(value: object) -> str:
    """Make a value safe for a Markdown table cell."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def _summary(a: Assessment) -> list[str]:
    c = a.complexity
    rows: list[list[object]] = [
        ["Migration complexity", f"{c.score}/100 ({c.level})"],
        ["Effort band", f"{c.effort_band}: {c.effort_range} ({c.effort_points} points)"],
        ["Blockers", c.blockers],
        ["AI readiness", f"{a.ai.score}/100"],
        ["Rules triggered", f"{len(a.findings)} of {a.rules_evaluated}"],
    ]
    out = ["## Summary", "", *_table(["Metric", "Value"], rows), ""]
    sev_rows = [[SEVERITY_LABELS[k], v] for k, v in c.findings_by_severity.items()]
    out += ["Triggered rules by severity:", "", *_table(["Severity", "Rules"], sev_rows), ""]
    counts = [[COUNT_LABELS.get(k, k), v] for k, v in a.object_counts.items() if v]
    out += ["Objects in the inventory:", "", *_table(["Object", "Count"], counts), ""]
    return out


def _ai(a: Assessment) -> list[str]:
    out = ["## AI readiness", "", f"**{a.ai.score}/100.** {a.ai.verdict}", ""]
    if a.ai.risks:
        out += [
            "These Oracle behaviours are implicit in the schema and code. A model converting "
            "it will not know about them unless you say so. Put this list in your prompt or "
            "conversion spec:",
            "",
        ]
        for i, risk in enumerate(a.ai.risks, 1):
            out.append(
                f"{i}. **{risk.title}** ({risk.rule_id}, {risk.occurrences} object(s)): "
                f"{risk.guidance}"
            )
        out.append("")
    return out


def _extensions(a: Assessment) -> list[str]:
    if not a.extensions:
        return []
    rows = [[f"`{name}`", ", ".join(ids)] for name, ids in a.extensions.items()]
    return [
        "## Azure extensions to allow-list",
        "",
        "Add these to the `azure.extensions` server parameter before `CREATE EXTENSION`. "
        "`pg_cron` also needs `shared_preload_libraries` and a restart.",
        "",
        *_table(["Extension", "Suggested by"], rows),
        "",
    ]


def _findings(a: Assessment) -> list[str]:
    if not a.findings:
        return ["## Findings", "", "No rules triggered.", ""]
    rows = [
        [
            SEVERITY_LABELS[f.rule.severity.value],
            f.rule.id,
            f.rule.title,
            len(f.hits),
            f.effort_points,
        ]
        for f in a.findings
    ]
    out = ["## Findings", "", *_table(["Severity", "Rule", "Title", "Objects", "Effort"], rows), ""]
    for f in a.findings:
        r = f.rule
        out += [
            f"### {r.id}: {r.title}",
            "",
            f"**Severity:** {SEVERITY_LABELS[r.severity.value]} · **Category:** "
            f"{r.category.value} · "
            f"**Objects:** {len(f.hits)} · **Effort points:** {f.effort_points}",
            "",
            r.description,
            "",
            f"**What to do:** {r.remediation}",
            "",
        ]
        shown = f.hits[:MAX_OBJECTS_PER_FINDING]
        out += _table(
            ["Object", "Type", "Detail"], [[h.object_name, h.object_type, h.detail] for h in shown]
        )
        if len(f.hits) > len(shown):
            out.append(f"\n...and {len(f.hits) - len(shown)} more.")
        out.append("")
    return out


def _top_objects(a: Assessment) -> list[str]:
    if not a.top_objects:
        return []
    rows = [[o.name, o.object_type, o.score, ", ".join(o.rule_ids)] for o in a.top_objects]
    return [
        "## Most complex objects",
        "",
        *_table(["Object", "Type", "Score", "Rules"], rows),
        "",
    ]


def render_markdown(assessment: Assessment) -> str:
    """Render the assessment as a Markdown document."""
    lines = [
        f"# {title_of(assessment)}",
        "",
        f"Generated {assessment.generated_at} by o2pg {assessment.tool_version}. "
        f"Source: {source_line(assessment)}.",
        "",
        *_summary(assessment),
        *_ai(assessment),
        *_extensions(assessment),
        *_findings(assessment),
        *_top_objects(assessment),
        "## About the numbers",
        "",
        SCORING_NOTE,
        "",
    ]
    return "\n".join(lines)
