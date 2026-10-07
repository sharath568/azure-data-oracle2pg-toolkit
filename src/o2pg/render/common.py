"""Small helpers shared by the Markdown and HTML renderers."""

from __future__ import annotations

from o2pg.assessment import Assessment

MAX_OBJECTS_PER_FINDING = 15

SEVERITY_LABELS = {
    "blocker": "Blocker",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Info",
}

COUNT_LABELS = {
    "tables": "Tables",
    "columns": "Columns",
    "constraints": "Constraints",
    "indexes": "Indexes",
    "sequences": "Sequences",
    "views": "Views",
    "mviews": "Materialized views",
    "synonyms": "Synonyms",
    "triggers": "Triggers",
    "source_objects": "PL/SQL and other source objects",
    "plsql_lines": "Lines of PL/SQL",
    "db_links": "Database links",
    "part_tables": "Partitioned tables",
    "lobs": "LOB columns",
    "types": "User-defined types",
    "scheduler_jobs": "Scheduler jobs",
    "grants": "Grant summary rows",
    "policies": "VPD policies",
    "queues": "AQ queues",
    "encrypted_columns": "TDE encrypted columns",
    "invalid_objects": "Invalid objects",
}

SCORING_NOTE = (
    "Scores are heuristics for ranking work and comparing schemas, not a quote. "
    "Complexity weights each triggered rule by severity (blocker 25, high 8, medium 3, "
    "low 1) and counts repeated hits logarithmically. Effort points add each rule's "
    "per-object effort, a baseline per table and one point per 150 lines of PL/SQL. "
    "AI readiness starts at 100 and decays with every triggered rule that hides "
    "behaviour an LLM is likely to miss (blocker 30, high 20, medium 10, low 5 penalty points)."
)


def source_line(assessment: Assessment) -> str:
    """One-line description of where the inventory came from."""
    meta = assessment.metadata
    parts = []
    if meta.get("db_version"):
        parts.append(f"Oracle {meta['db_version']}")
    if meta.get("schemas"):
        parts.append("schemas " + ", ".join(meta["schemas"]))
    if meta.get("character_set"):
        parts.append(f"character set {meta['character_set']}")
    if meta.get("collected_at"):
        parts.append(f"collected {meta['collected_at']}")
    return "; ".join(parts) or "no metadata in inventory"


def title_of(assessment: Assessment) -> str:
    """Report title."""
    name = assessment.metadata.get("db_name") or "Oracle inventory"
    return f"o2pg assessment: {name}"
