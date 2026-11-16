"""The prompt sent for each unit, and the JSON shape the model must answer in.

The model gets three things and is told which one is the truth:

1. the **Oracle source** (the behaviour to preserve),
2. the **mechanical draft** from ``o2pg convert`` (the starting point; its
   rewrites are already checked, its header is the contract),
3. the **review points** to resolve, with the registry's explanation of each,
   plus the DDL of the tables the routine touches.

The system prompt lists the Oracle behaviours that models most often get wrong
when they "translate" rather than convert. Every rule in it exists because a
gate in this package checks it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from o2pg.convert import ConvertedUnit, get_conversion_rule

PROMPT_VERSION = "o2pg-ai-1"
MAX_SOURCE_CHARS = 16000
MAX_DDL_CHARS = 12000

SYSTEM_PROMPT = """\
You convert one Oracle PL/SQL routine to PL/pgSQL for PostgreSQL 16 on Azure Database for \
PostgreSQL Flexible Server. You are given the Oracle source, a mechanical draft made by a \
rule-based converter, and the review points the converter could not resolve.

The Oracle source defines the behaviour. The converted routine must return the same result, \
raise the same error, and leave the same data as Oracle for every input, including NULLs.

Oracle behaviours to preserve:
- '' is NULL in Oracle. Comparisons with '' are never true, and a string function whose \
result is empty returns NULL. Use NULLIF(x, '') where an empty result or input must act as NULL.
- || treats NULL as an empty string. Use concat() or COALESCE(x, '') on operands that can be NULL.
- DATE - DATE is a number of days, with a fraction for the time part. Use \
EXTRACT(EPOCH FROM (a - b)) / 86400 when the routine returns or compares a number.
- Division of two integers is not truncated. Cast one operand to numeric.
- SELECT ... INTO raises NO_DATA_FOUND for no rows and TOO_MANY_ROWS for several. Keep INTO STRICT.
- SUBSTR with a negative position counts from the end, and returns NULL when the position is \
before the start of the string.
- INSTR(s, sub, pos, n) returns the position of the n-th occurrence, or 0 when there is none.
- REGEXP_REPLACE replaces every match. Pass the 'g' flag.
- ROWNUM is assigned before ORDER BY in the same query block. Use LIMIT or FETCH FIRST on the \
ordered query.
- In an Oracle (+) outer join, a condition on the outer-joined table belongs in the ON clause \
of the LEFT JOIN, not in WHERE.
- CONNECT BY becomes WITH RECURSIVE. LEVEL starts at 1 on the START WITH rows, and a WHERE \
clause in the hierarchical query filters rows after the walk.
- SQL%ROWCOUNT after FORALL is the total number of rows across all iterations. Use \
GET DIAGNOSTICS after a single set-based statement.

Rules for the answer:
- Start from the mechanical draft. Keep its header exactly: name, parameter names, modes, order \
and types, return type, LANGUAGE, SECURITY and SET search_path clauses. Only change the \
signature when a review point makes it impossible to keep (pipelined functions, PL/SQL \
collection or object types, functions with OUT parameters). If you change it, set \
signature_changed to true and say why.
- Return only the CREATE OR REPLACE statement for this routine (for a trigger, the trigger \
function and its CREATE OR REPLACE TRIGGER). You may put a CREATE TYPE before it only when a \
changed signature needs a composite type. No other statements: no DROP, ALTER, GRANT, \
CREATE EXTENSION, DO blocks or top-level SET.
- Use only built-in PostgreSQL 16 features. Do not use extensions such as orafce, and do not \
call helper functions that do not exist yet.
- Remove each O2PG-REVIEW marker you resolve. If you cannot resolve one, leave that marker in \
place and list it in unresolved. Do not guess.
- List what you changed per review rule in changes, and anything you had to assume in assumptions.
"""

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "sql",
        "changes",
        "signature_changed",
        "signature_reason",
        "unresolved",
        "assumptions",
    ],
    "properties": {
        "sql": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule", "change"],
                "properties": {"rule": {"type": "string"}, "change": {"type": "string"}},
            },
        },
        "signature_changed": {"type": "boolean"},
        "signature_reason": {"type": "string"},
        "unresolved": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class Request:
    """Everything needed to ask for one unit (and to ask again after a failed gate)."""

    unit: str
    messages: list[dict[str, str]]
    rules: tuple[str, ...] = ()
    attempt: int = 1

    @property
    def fingerprint(self) -> str:
        """Hash of the first-attempt prompt, recorded with responses for traceability."""
        text = json.dumps(self.messages[:2], sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Answer:
    """A parsed model answer."""

    sql: str
    changes: list[dict[str, str]] = field(default_factory=list)
    signature_changed: bool = False
    signature_reason: str = ""
    unresolved: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> Answer:
        """Build from the model's JSON, tolerating missing optional fields."""
        changes = [
            {"rule": str(c.get("rule", "")), "change": str(c.get("change", ""))}
            for c in data.get("changes") or []
            if isinstance(c, dict)
        ]
        return cls(
            sql=str(data.get("sql") or "").strip(),
            changes=changes,
            signature_changed=bool(data.get("signature_changed", False)),
            signature_reason=str(data.get("signature_reason") or ""),
            unresolved=[str(x) for x in data.get("unresolved") or []],
            assumptions=[str(x) for x in data.get("assumptions") or []],
        )

    def to_json(self) -> dict:
        """The JSON form (as the model returns it)."""
        return {
            "sql": self.sql,
            "changes": self.changes,
            "signature_changed": self.signature_changed,
            "signature_reason": self.signature_reason,
            "unresolved": self.unresolved,
            "assumptions": self.assumptions,
        }


def _clip(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n-- [{what} truncated at {limit} characters]"


_DDL_SPLIT = re.compile(r";[ \t]*(?:\n|$)")
_DDL_NAME = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|TYPE|SEQUENCE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"?[\w$#]+\"?\.)?\"?([\w$#]+)\"?",
    re.IGNORECASE,
)


def relevant_ddl(ddl: str, *texts: str) -> str:
    """CREATE TABLE/TYPE/SEQUENCE/VIEW statements whose name appears in ``texts``."""
    if not ddl.strip():
        return ""
    haystack = "\n".join(texts).lower()
    keep = []
    for statement in _DDL_SPLIT.split(ddl):
        match = _DDL_NAME.search(statement)
        if match and re.search(rf"\b{re.escape(match.group(1).lower())}\b", haystack):
            keep.append(statement.strip() + ";")
    return _clip("\n\n".join(keep), MAX_DDL_CHARS, "DDL")


def build_request(
    unit: ConvertedUnit, oracle_source: str, ddl: str, rules: tuple[str, ...]
) -> Request:
    """The first-attempt messages for ``unit``."""
    review_lines = []
    for point in unit.reviews:
        rule = get_conversion_rule(point.rule)
        kind = "blocking" if point.blocking else "non-blocking"
        review_lines.append(
            f"- line {point.line}: {point.rule} {rule.title} ({kind}): {point.reason}"
        )
    tables = relevant_ddl(ddl, oracle_source, unit.sql)
    parts = [
        f"Routine: {unit.name} ({unit.kind.lower()})",
        "",
        "## Oracle source (the behaviour to preserve)",
        "```sql",
        _clip(oracle_source.strip(), MAX_SOURCE_CHARS, "Oracle source"),
        "```",
        "",
        "## Mechanical draft (start from this; keep its header)",
        "```sql",
        unit.sql.strip(),
        "```",
        "",
        "## Review points to resolve",
        *review_lines,
    ]
    if tables:
        parts += ["", "## PostgreSQL tables and types it uses", "```sql", tables, "```"]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]
    return Request(unit=unit.name, messages=messages, rules=rules)


def retry_request(request: Request, previous: Answer, gate: str, detail: str) -> Request:
    """Messages for another attempt after ``previous`` failed ``gate``."""
    feedback = (
        f"Your answer failed the {gate} check:\n{detail}\n\n"
        "Return a corrected answer in the same JSON format. Keep everything that was right."
    )
    messages = [
        *request.messages,
        {"role": "assistant", "content": json.dumps(previous.to_json())},
        {"role": "user", "content": feedback},
    ]
    return Request(
        unit=request.unit, messages=messages, rules=request.rules, attempt=request.attempt + 1
    )
