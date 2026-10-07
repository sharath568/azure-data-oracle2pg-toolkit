"""Run rules against an inventory and collect everything a report needs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from o2pg import __version__
from o2pg.inventory import Inventory
from o2pg.rules import Finding, Rule, Severity, all_rules
from o2pg.scoring import (
    AiReadiness,
    ComplexityScore,
    ObjectScore,
    ai_readiness,
    complexity,
    object_scores,
)


@dataclass(frozen=True)
class Assessment:
    """The result of assessing one inventory."""

    metadata: dict[str, Any]
    object_counts: dict[str, int]
    findings: list[Finding]
    complexity: ComplexityScore
    top_objects: list[ObjectScore]
    ai: AiReadiness
    extensions: dict[str, list[str]]
    rules_evaluated: int
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    tool_version: str = __version__

    def to_dict(self) -> dict[str, Any]:
        """A JSON-friendly representation with stable key order."""
        return {
            "tool": {"name": "o2pg", "version": self.tool_version},
            "generated_at": self.generated_at,
            "source": self.metadata,
            "object_counts": self.object_counts,
            "complexity": asdict(self.complexity),
            "ai_readiness": {
                "score": self.ai.score,
                "verdict": self.ai.verdict,
                "risks": [asdict(r) for r in self.ai.risks],
            },
            "extensions": self.extensions,
            "rules_evaluated": self.rules_evaluated,
            "findings": [
                {
                    "rule_id": f.rule.id,
                    "title": f.rule.title,
                    "category": f.rule.category.value,
                    "severity": f.rule.severity.value,
                    "effort_points": f.effort_points,
                    "description": f.rule.description,
                    "remediation": f.rule.remediation,
                    "ai_risk": f.rule.ai_risk,
                    "occurrences": len(f.hits),
                    "objects": [asdict(h) for h in f.hits],
                }
                for f in self.findings
            ],
            "top_objects": [asdict(o) for o in self.top_objects],
        }


def assess(
    inventory: Inventory,
    rules: Iterable[Rule] | None = None,
    min_severity: Severity = Severity.INFO,
) -> Assessment:
    """Evaluate ``rules`` (all built-in rules by default) and score the results."""
    selected = list(all_rules() if rules is None else rules)
    findings = [
        finding
        for rule in selected
        if rule.severity.rank <= min_severity.rank and (finding := rule.evaluate(inventory))
    ]
    findings.sort(key=lambda f: (f.rule.severity.rank, -len(f.hits), f.rule.id))
    extensions: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        for extension in finding.rule.extensions:
            extensions[extension].append(finding.rule.id)
    meta = inventory.metadata
    return Assessment(
        metadata={
            "db_name": meta.db_name,
            "db_version": meta.db_version,
            "character_set": meta.character_set,
            "schemas": list(meta.schemas),
            "collected_at": meta.collected_at,
            "collector_version": meta.collector_version,
        },
        object_counts=inventory.object_counts(),
        findings=findings,
        complexity=complexity(findings, inventory),
        top_objects=object_scores(findings, inventory),
        ai=ai_readiness(findings),
        extensions={name: sorted(ids) for name, ids in sorted(extensions.items())},
        rules_evaluated=len(selected),
    )
