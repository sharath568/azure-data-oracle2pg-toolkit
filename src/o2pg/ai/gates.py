"""Checks every model answer must pass before anyone is asked to look at it.

1. **static**: the answer holds only the routine (and, for a contract change,
   a composite type), keeps the routine's name, keeps its signature unless a
   review point allows a change, contains no Oracle-only syntax or functions
   outside comments and strings, and has no review marker left that it did not
   list as unresolved.
2. **compile**: the routine is created in a scratch PostgreSQL database with
   ``check_function_bodies = on``, inside a transaction that is rolled back.
   PL/pgSQL only checks syntax at this point: a missing column or function is
   found when the code runs, which is what the behaviour gate is for.
3. **behaviour**: the equivalence cases for the routine (``o2pg equiv``) run
   against the candidate and must match what Oracle returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from o2pg.convert.lexer import find_markers

FORMAT = "format"
STATIC = "static"
COMPILE = "compile"
BEHAVIOUR = "behaviour"

_I = re.IGNORECASE


@dataclass
class GateResult:
    """Outcome of one gate."""

    gate: str
    passed: bool
    detail: str = ""
    attempt: int = 0


# --------------------------------------------------------------------------------------
# Lexing PostgreSQL text
# --------------------------------------------------------------------------------------

_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def _scan(sql: str) -> list[tuple[str, str]]:
    """Split ``sql`` into ``(kind, text)`` chunks.

    Kinds: ``code``, ``comment``, ``string`` (single-quoted or E''), ``ident``
    (double-quoted), ``dollar`` (a dollar-quote delimiter). Dollar-quoted bodies
    are scanned as code, because that is where PL/pgSQL lives.
    """
    chunks: list[tuple[str, str]] = []
    i, n, start = 0, len(sql), 0

    def flush(end: int) -> None:
        if end > start:
            chunks.append(("code", sql[start:end]))

    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            flush(i)
            end = sql.find("\n", i)
            end = n if end == -1 else end
            chunks.append(("comment", sql[i:end]))
            i = start = end
            continue
        if sql.startswith("/*", i):
            flush(i)
            depth, j = 0, i
            while j < n:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            chunks.append(("comment", sql[i:j]))
            i = start = j
            continue
        if ch == "'":
            flush(i)
            j = i + 1
            backslash = i > 0 and sql[i - 1] in "eE" and (i < 2 or not sql[i - 2].isalnum())
            while j < n:
                if backslash and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            chunks.append(("string", sql[i:j]))
            i = start = j
            continue
        if ch == '"':
            flush(i)
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            chunks.append(("ident", sql[i:j]))
            i = start = j
            continue
        if ch == "$" and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            match = _DOLLAR.match(sql, i)
            if match:
                flush(i)
                chunks.append(("dollar", match.group(0)))
                i = start = match.end()
                continue
        i += 1
    flush(n)
    return chunks


def code_only(sql: str) -> str:
    """``sql`` with comments and string literals blanked out (lengths kept)."""
    out = []
    for kind, text in _scan(sql):
        if kind in {"comment", "string"}:
            out.append(re.sub(r"[^\n]", " ", text))
        elif kind == "dollar":
            out.append(" " * len(text))
        else:
            out.append(text)
    return "".join(out)


def split_statements(sql: str) -> list[str]:
    """Top-level statements (``;`` outside strings, comments and dollar quotes)."""
    statements: list[str] = []
    current: list[str] = []
    open_tag: str | None = None
    for kind, text in _scan(sql):
        if kind == "dollar":
            if open_tag is None:
                open_tag = text
            elif text == open_tag:
                open_tag = None
            current.append(text)
            continue
        if kind != "code" or open_tag is not None:
            current.append(text)
            continue
        parts = text.split(";")
        for index, part in enumerate(parts):
            current.append(part)
            if index < len(parts) - 1:
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement + ";")
                current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


# --------------------------------------------------------------------------------------
# Headers and signatures
# --------------------------------------------------------------------------------------

_ROUTINE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?P<kind>FUNCTION|PROCEDURE)\s+"
    r"(?P<name>(?:\"?[\w$]+\"?\.)?\"?[\w$]+\"?)\s*\(",
    _I,
)
_STATEMENT_HEAD = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?P<what>FUNCTION|PROCEDURE|TRIGGER|TYPE|"
    r"CONSTRAINT\s+TRIGGER)\b",
    _I,
)
_RETURNS = re.compile(
    r"\bRETURNS\s+(?P<ret>.+?)\s*(?=\bLANGUAGE\b|\bAS\b|\bSET\b|\bSECURITY\b|\bIMMUTABLE\b|"
    r"\bSTABLE\b|\bVOLATILE\b|\bSTRICT\b|\bCALLED\b|\bPARALLEL\b|\bCOST\b|\bROWS\b|"
    r"\bWINDOW\b|\bLEAKPROOF\b|\bNOT\b|$)",
    _I | re.DOTALL,
)
_MODES = ("INOUT", "IN", "OUT", "VARIADIC")
_TYPE_SYNONYMS = [
    (r"\bcharacter varying\b", "varchar"),
    (r"\bdecimal\b", "numeric"),
    (r"\bint4\b|\bint\b", "integer"),
    (r"\bint8\b", "bigint"),
    (r"\bint2\b", "smallint"),
    (r"\bfloat8\b|\bdouble precision\b", "float8"),
    (r"\btimestamp without time zone\b", "timestamp"),
    (r"\btimestamp with time zone\b|\btimestamptz\b", "timestamptz"),
    (r"\bbool\b", "boolean"),
    (r"\s*\(\s*", "("),
    (r"\s*\)\s*", ")"),
    (r"\s*,\s*", ","),
]


@dataclass(frozen=True)
class Signature:
    """A routine's name, parameters and return type, normalised for comparison."""

    kind: str
    name: str
    params: tuple[tuple[str, str, str], ...]
    returns: str = ""

    def show(self) -> str:
        """Readable form, e.g. ``lab.f(IN p_x numeric) RETURNS numeric``."""
        params = ", ".join(" ".join(x for x in p if x) for p in self.params)
        ret = f" RETURNS {self.returns}" if self.returns else ""
        return f"{self.kind.lower()} {self.name}({params}){ret}"


def _norm_name(name: str) -> str:
    return name.replace('"', "").lower()


def _norm_type(text: str) -> str:
    out = " ".join(text.lower().split())
    for pattern, repl in _TYPE_SYNONYMS:
        out = re.sub(pattern, repl, out)
    return out


def _split_top(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _param(text: str) -> tuple[str, str, str]:
    text = re.split(r"\s+DEFAULT\s+|\s*:=\s*|\s*=\s*", text.strip(), maxsplit=1, flags=_I)[0]
    words = text.split()
    mode = "IN"
    if words and words[0].upper() in _MODES:
        mode = words.pop(0).upper()
    if len(words) >= 2:
        return mode, _norm_name(words[0]), _norm_type(" ".join(words[1:]))
    return mode, "", _norm_type(" ".join(words))


def signatures(sql: str) -> list[Signature]:
    """Every CREATE FUNCTION/PROCEDURE header in ``sql`` (comments and strings ignored)."""
    code = code_only(sql)
    found = []
    for match in _ROUTINE.finditer(code):
        open_at = match.end() - 1
        depth, close = 0, None
        for index in range(open_at, len(code)):
            if code[index] == "(":
                depth += 1
            elif code[index] == ")":
                depth -= 1
                if depth == 0:
                    close = index
                    break
        if close is None:
            continue
        params = tuple(_param(p) for p in _split_top(code[open_at + 1 : close]) if p.strip())
        returns = ""
        if match.group("kind").upper() == "FUNCTION":
            ret = _RETURNS.match(code[close + 1 :].lstrip())
            returns = _norm_type(ret.group("ret")) if ret else ""
        found.append(
            Signature(match.group("kind").upper(), _norm_name(match.group("name")), params, returns)
        )
    return found


# --------------------------------------------------------------------------------------
# Oracle-only syntax
# --------------------------------------------------------------------------------------

ORACLE_ONLY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bROWNUM\b", _I), "ROWNUM"),
    (re.compile(r"\bCONNECT\s+BY\b", _I), "CONNECT BY"),
    (re.compile(r"\bSTART\s+WITH\b", _I), "START WITH"),
    (re.compile(r"\(\s*\+\s*\)"), "(+) outer join"),
    (re.compile(r"\bBULK\s+COLLECT\b", _I), "BULK COLLECT"),
    (re.compile(r"\bFORALL\b", _I), "FORALL"),
    (re.compile(r"\bPIPE\s+ROW\b", _I), "PIPE ROW"),
    (re.compile(r"\bPIPELINED\b", _I), "PIPELINED"),
    (re.compile(r"\bPRAGMA\b", _I), "PRAGMA"),
    (re.compile(r"\bEXECUTE\s+IMMEDIATE\b", _I), "EXECUTE IMMEDIATE"),
    (re.compile(r"\bRAISE_APPLICATION_ERROR\b", _I), "RAISE_APPLICATION_ERROR"),
    (re.compile(r"\bSYSDATE\b", _I), "SYSDATE"),
    (re.compile(r"\bSYSTIMESTAMP\b", _I), "SYSTIMESTAMP"),
    (re.compile(r"\bFROM\s+DUAL\b", _I), "FROM DUAL"),
    (re.compile(r"\bVARCHAR2\b", _I), "VARCHAR2"),
    (re.compile(r"\bDBMS_\w+", _I), "DBMS_ package"),
    (re.compile(r"\bUTL_\w+", _I), "UTL_ package"),
    (re.compile(r"(?<![\w.:]):(?:NEW|OLD)\b", _I), ":NEW/:OLD"),
    (re.compile(r"%(?:ROWCOUNT|FOUND|NOTFOUND|ISOPEN|BULK_ROWCOUNT)\b", _I), "cursor attribute"),
    (re.compile(r"\bMINUS\b", _I), "MINUS (use EXCEPT)"),
    (
        re.compile(
            r"\b(NVL2?|DECODE|INSTR|LISTAGG|ADD_MONTHS|MONTHS_BETWEEN|LAST_DAY|NEXT_DAY|"
            r"SYS_CONNECT_BY_PATH|SYS_GUID|EMPTY_CLOB|EMPTY_BLOB|TO_CLOB)\s*\(",
            _I,
        ),
        "Oracle-only function",
    ),
]


def oracle_leftovers(sql: str) -> list[str]:
    """Oracle-only constructs in the code part of ``sql`` (``name at line n``)."""
    code = code_only(sql)
    found = []
    for pattern, label in ORACLE_ONLY:
        for match in pattern.finditer(code):
            line = code.count("\n", 0, match.start()) + 1
            what = label if label != "Oracle-only function" else f"{match.group(1).upper()}()"
            found.append(f"{what} at line {line}")
    return found


# --------------------------------------------------------------------------------------
# The static gate
# --------------------------------------------------------------------------------------


@dataclass
class StaticCheck:
    """What the static gate found, beyond pass/fail."""

    result: GateResult
    contract_change: bool = False
    leftover_markers: list[str] = field(default_factory=list)


def static_gate(
    candidate: str,
    expected: Signature | None,
    is_trigger: bool,
    contract_change_allowed: bool,
    unresolved: list[str],
) -> StaticCheck:
    """Check a candidate's statements, name, signature, syntax and markers."""
    problems: list[str] = []
    statements = split_statements(candidate)
    routines = triggers = types = 0
    for statement in statements:
        code = code_only(statement).strip().rstrip(";").strip()
        if not code:
            continue  # a comment-only chunk
        head = _STATEMENT_HEAD.match(code)
        what = head.group("what").upper() if head else ""
        if what in {"FUNCTION", "PROCEDURE"}:
            routines += 1
        elif what == "TRIGGER":
            triggers += 1
        elif what == "TYPE":
            types += 1
        else:
            first = " ".join(code.split()[:3])
            problems.append(f"statement not allowed in an answer: {first} ...")
    if types and not contract_change_allowed:
        problems.append("CREATE TYPE is only allowed when a review point requires a new type")
    if is_trigger and triggers != 1:
        problems.append(f"expected the trigger function and one CREATE TRIGGER, found {triggers}")
    if not is_trigger and triggers:
        problems.append("the answer creates a trigger, but this unit is not a trigger")

    sigs = signatures(candidate)
    contract_change = False
    if expected is not None:
        same_name = [s for s in sigs if s.name == expected.name]
        if routines != 1 or len(same_name) != 1:
            problems.append(
                f"expected exactly one CREATE for {expected.name}, found {routines} routine(s)"
                + (f" named {', '.join(s.name for s in sigs)}" if sigs else "")
            )
        else:
            got = same_name[0]
            if got != expected:
                contract_change = True
                if not contract_change_allowed:
                    problems.append(
                        "signature changed without a review point that requires it: "
                        f"expected {expected.show()}, got {got.show()}"
                    )
    problems += [f"Oracle-only syntax left: {x}" for x in oracle_leftovers(candidate)]
    markers = [f"{rule} at line {line}" for line, rule, _ in find_markers(candidate)]
    if markers and not unresolved:
        problems.append("review markers left but not listed as unresolved: " + ", ".join(markers))
    result = GateResult(STATIC, not problems, "; ".join(problems))
    return StaticCheck(result, contract_change, markers)
