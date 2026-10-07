"""JSON report renderer."""

from __future__ import annotations

import json

from o2pg.assessment import Assessment


def render_json(assessment: Assessment) -> str:
    """Render the assessment as pretty-printed JSON."""
    return json.dumps(assessment.to_dict(), indent=2, ensure_ascii=False) + "\n"
