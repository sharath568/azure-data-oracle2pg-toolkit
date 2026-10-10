"""Find the pieces of a masked program unit: subprograms, package items, trigger parts.

Everything here works on masked text (see :mod:`o2pg.convert.lexer`) and only
locates spans; it never rewrites. It understands just enough PL/SQL structure
to find where a routine's declarations, body and final ``END`` are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from o2pg.convert.lexer import IDENT, W, block_end, matching_paren

_I = re.IGNORECASE
_IS = re.IGNORECASE | re.DOTALL

_QNAME = r"[\w$#\".\U00100000-\U0010FFFD]+"
_SKIP = re.compile(rf"{W}*")
_WORD = re.compile(IDENT)
_SUB_HEAD = re.compile(rf"(?P<kind>PROCEDURE|FUNCTION){W}+(?P<name>{_QNAME})", _I)
_IS_AS = re.compile(r"\b(?:IS|AS)\b", _I)
_CALL_SPEC = re.compile(rf"{W}*(?:LANGUAGE|EXTERNAL)\b", _I)


class StructureError(ValueError):
    """Raised when a unit does not have the shape the converter expects."""


@dataclass
class Subprogram:
    """A procedure or function inside masked text.

    ``header_end`` is just after IS/AS (or after ``;`` for a forward declaration),
    ``begin`` is the index of the body's ``BEGIN`` and ``end`` is just after the
    final ``;``. ``nested`` is True when the declarations contain subprograms.
    """

    kind: str
    name: str
    start: int
    header_end: int
    begin: int = -1
    end: int = -1
    forward: bool = False
    call_spec: bool = False
    nested: bool = False


def _skip(text: str, pos: int) -> int:
    match = _SKIP.match(text, pos)
    return match.end() if match else pos


def _word_at(text: str, pos: int) -> str:
    match = _WORD.match(text, pos)
    return match.group(0).upper() if match else ""


def parse_subprogram(text: str, pos: int) -> Subprogram:
    """Parse the procedure or function that starts at ``pos``."""
    head = _SUB_HEAD.match(text, pos)
    if not head:
        raise StructureError("expected PROCEDURE or FUNCTION")
    cursor = _skip(text, head.end())
    if cursor < len(text) and text[cursor] == "(":
        close = matching_paren(text, cursor)
        if close is None:
            raise StructureError("unbalanced parameter list")
        cursor = close + 1
    is_as = _IS_AS.search(text, cursor)
    semicolon = text.find(";", cursor)
    sub = Subprogram(head.group("kind").upper(), head.group("name"), pos, -1)
    if is_as is None or (0 <= semicolon < is_as.start()):
        if semicolon < 0:
            raise StructureError(f"no IS/AS or ; after {sub.name}")
        sub.header_end = sub.end = semicolon + 1
        sub.forward = True
        return sub
    sub.header_end = is_as.end()
    if _CALL_SPEC.match(text, sub.header_end):
        end = text.find(";", sub.header_end)
        sub.end = len(text) if end < 0 else end + 1
        sub.call_spec = True
        return sub
    cursor = sub.header_end
    while True:
        cursor = _skip(text, cursor)
        if cursor >= len(text):
            raise StructureError(f"no BEGIN found for {sub.name}")
        word = _word_at(text, cursor)
        if word in {"PROCEDURE", "FUNCTION"}:
            nested = parse_subprogram(text, cursor)
            sub.nested = sub.nested or not nested.forward
            cursor = nested.end
            continue
        if word == "BEGIN":
            sub.begin = cursor
            end = block_end(text, cursor)
            if end is None:
                raise StructureError(f"no END found for {sub.name}")
            sub.end = end
            return sub
        semicolon = text.find(";", cursor)
        if semicolon < 0:
            raise StructureError(f"unterminated declaration in {sub.name}")
        cursor = semicolon + 1


@dataclass
class PackageItem:
    """A top-level item of a package spec or body.

    ``kind`` is ``declaration``, ``subprogram`` or ``initialization``.
    """

    kind: str
    start: int
    end: int
    subprogram: Subprogram | None = None


@dataclass
class Package:
    """Parsed package spec or body."""

    name: str
    header_end: int
    items: list[PackageItem] = field(default_factory=list)
    wrapped: bool = False


_PACKAGE_HEAD = re.compile(
    rf"^{W}*PACKAGE{W}+(?P<body>BODY{W}+)?(?P<name>{_QNAME})(?P<rest>.*?)\b(?:IS|AS)\b", _IS
)
_WRAPPED = re.compile(rf"^{W}*PACKAGE{W}+(?:BODY{W}+)?{_QNAME}{W}+wrapped\b", _I)


def parse_package(text: str) -> Package:
    """Parse a masked package spec or body (starting at ``PACKAGE``)."""
    if _WRAPPED.match(text):
        name = re.match(rf"^{W}*PACKAGE{W}+(?:BODY{W}+)?({_QNAME})", text, _I)
        return Package(name.group(1) if name else "", 0, wrapped=True)
    head = _PACKAGE_HEAD.match(text)
    if not head:
        raise StructureError("expected PACKAGE [BODY] name IS|AS")
    package = Package(head.group("name"), head.end())
    cursor = head.end()
    while True:
        cursor = _skip(text, cursor)
        if cursor >= len(text):
            return package
        word = _word_at(text, cursor)
        if word in {"PROCEDURE", "FUNCTION"}:
            sub = parse_subprogram(text, cursor)
            package.items.append(PackageItem("subprogram", cursor, sub.end, sub))
            cursor = sub.end
            continue
        if word == "BEGIN":
            end = block_end(text, cursor)
            if end is None:
                raise StructureError("unterminated package initialisation section")
            package.items.append(PackageItem("initialization", cursor, end))
            return package
        if word == "END":
            return package
        semicolon = text.find(";", cursor)
        if semicolon < 0:
            raise StructureError("unterminated package declaration")
        package.items.append(PackageItem("declaration", cursor, semicolon))
        cursor = semicolon + 1


@dataclass
class TriggerParts:
    """The parts of a masked trigger definition."""

    name: str
    timing: str
    events: str
    table: str
    referencing: str
    for_each_row: bool
    when: str | None
    options: str
    body_start: int
    body_end: int
    body: str = ""


_TRIGGER_HEAD = re.compile(
    rf"^{W}*TRIGGER{W}+(?P<name>{_QNAME}){W}+"
    rf"(?P<timing>BEFORE|AFTER|INSTEAD{W}+OF|FOR){W}+(?P<events>.+?){W}+ON{W}+"
    rf"(?P<table>{_QNAME})(?P<rest>.*?)(?=\b(?:DECLARE|BEGIN|COMPOUND{W}+TRIGGER|CALL)\b)",
    _IS,
)
_DML_EVENT = re.compile(rf"^{W}*(?:INSERT|DELETE|UPDATE(?:{W}+OF{W}+.+)?){W}*$", _IS)


def split_events(events: str) -> list[str]:
    """Split ``INSERT OR UPDATE OF a, b OR DELETE`` into its events."""
    return [part.strip() for part in re.split(rf"{W}+OR{W}+", events, flags=_I)]


def parse_trigger(text: str) -> TriggerParts | str:
    """Parse a masked trigger. Returns the parts, or a reason string if unsupported."""
    head = _TRIGGER_HEAD.match(text)
    if not head:
        return "trigger header not recognised"
    timing = " ".join(head.group("timing").upper().split())
    rest = head.group("rest")
    body_start = head.end()
    word = _word_at(text, body_start)
    if timing == "FOR" or word == "COMPOUND":
        return (
            "compound trigger: split it into row-level and statement-level triggers, using "
            "transition tables (REFERENCING NEW TABLE AS ...) for the statement part"
        )
    if word == "CALL":
        return "trigger body is a CALL statement: write a trigger function that calls it"
    table = head.group("table")
    if table.upper() in {"SCHEMA", "DATABASE"} or table.upper().endswith(".SCHEMA"):
        return (
            "schema or database event trigger: use an event trigger (CREATE EVENT TRIGGER), "
            "which needs elevated privileges; check what your Azure server allows"
        )
    events = split_events(head.group("events"))
    if not all(_DML_EVENT.match(event) for event in events):
        return "trigger on a non-DML event: use an event trigger if PostgreSQL has one"
    if word == "DECLARE":
        begin = re.compile(r"\bBEGIN\b", _I).search(text, body_start)
        if not begin:
            return "trigger body not recognised"
        end = block_end(text, begin.start())
    else:
        end = block_end(text, body_start)
    if end is None:
        return "trigger body not recognised"
    when = None
    when_match = re.search(rf"\bWHEN{W}*\(", rest, _I)
    options = rest
    if when_match:
        open_index = when_match.end() - 1
        close = matching_paren(rest, open_index)
        if close is None:
            return "trigger WHEN clause not recognised"
        when = rest[open_index + 1 : close]
        options = rest[: when_match.start()] + rest[close + 1 :]
    referencing = ""
    ref = re.search(
        rf"\bREFERENCING{W}+(?P<refs>(?:(?:NEW|OLD|PARENT){W}+(?:AS{W}+)?{IDENT}{W}*)+)",
        options,
        _I,
    )
    if ref:
        referencing = ref.group("refs")
        options = options[: ref.start()] + options[ref.end() :]
    for_each_row = bool(re.search(rf"\bFOR{W}+EACH{W}+ROW\b", options, _I))
    options = re.sub(rf"\bFOR{W}+EACH{W}+ROW\b", "", options, flags=_I)
    return TriggerParts(
        name=head.group("name"),
        timing=timing,
        events=head.group("events"),
        table=table,
        referencing=referencing,
        for_each_row=for_each_row,
        when=when,
        options=options,
        body_start=body_start,
        body_end=end,
        body=text[body_start:end],
    )


def referencing_aliases(referencing: str) -> tuple[list[str], list[str]]:
    """``(new_aliases, old_aliases)`` from a REFERENCING clause body."""
    new: list[str] = []
    old: list[str] = []
    for match in re.finditer(rf"\b(NEW|OLD){W}+(?:AS{W}+)?({IDENT})", referencing, _I):
        (new if match.group(1).upper() == "NEW" else old).append(match.group(2))
    return new, old
