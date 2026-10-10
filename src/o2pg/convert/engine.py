"""Convert stored PL/SQL objects to PL/pgSQL files plus a structured result.

One :class:`ConvertedObject` is produced per Oracle object (a package spec and
body together count as one). Each object holds one or more
:class:`ConvertedUnit`: a routine, a trigger, a package's declarations, or an
object kept as a comment because it is not converted. Units carry their own
SQL, rewrite counts and statement counts so the report can say exactly which
routines are ready to try in PostgreSQL.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from o2pg import __version__
from o2pg.convert.body import BlockRewriter, Scope
from o2pg.convert.lexer import (
    IDENT,
    MARKER_PREFIX,
    TokenPool,
    W,
    find_markers,
    first_word,
    matching_paren,
    split_top_level,
    statements,
)
from o2pg.convert.registry import get_conversion_rule
from o2pg.convert.structure import (
    PackageItem,
    StructureError,
    Subprogram,
    TriggerParts,
    parse_package,
    parse_subprogram,
    parse_trigger,
    referencing_aliases,
    split_events,
)
from o2pg.convert.types import TYPE_PATTERN, map_plsql_type
from o2pg.ddl import ident
from o2pg.inventory import Inventory, SourceObject, Trigger

_I = re.IGNORECASE
_IS = re.IGNORECASE | re.DOTALL

STATUS_CONVERTED = "converted"
STATUS_REVIEW = "review"
STATUS_BLOCKED = "blocked"
STATUS_NOT_CONVERTED = "not converted"
_STATUS_ORDER = [STATUS_CONVERTED, STATUS_REVIEW, STATUS_BLOCKED, STATUS_NOT_CONVERTED]

KIND_STATE = "PACKAGE STATE"
KIND_NOT_CONVERTED = "NOT CONVERTED"


@dataclass(frozen=True)
class ReviewPoint:
    """One ``O2PG-REVIEW`` marker in an output file (``line`` is 1-based)."""

    line: int
    rule: str
    reason: str
    blocking: bool


@dataclass
class ConvertedUnit:
    """A routine, trigger, package state section or unconverted object."""

    name: str
    kind: str
    sql: str
    rewrites: Counter[str] = field(default_factory=Counter)
    statements: int = 0
    clean_statements: int = 0
    reviews: list[ReviewPoint] = field(default_factory=list)

    @property
    def status(self) -> str:
        """converted, review, blocked or not converted."""
        if self.kind == KIND_NOT_CONVERTED:
            return STATUS_NOT_CONVERTED
        if any(r.blocking for r in self.reviews):
            return STATUS_BLOCKED
        if self.reviews:
            return STATUS_REVIEW
        return STATUS_CONVERTED

    @property
    def executable(self) -> bool:
        """True for a routine or trigger with no blocking review points."""
        return self.kind not in {KIND_STATE, KIND_NOT_CONVERTED} and self.status in {
            STATUS_CONVERTED,
            STATUS_REVIEW,
        }


@dataclass
class ConvertedObject:
    """All output for one Oracle object."""

    owner: str
    name: str
    source_types: tuple[str, ...]
    preamble: list[str] = field(default_factory=list)
    units: list[ConvertedUnit] = field(default_factory=list)
    rewrites: Counter[str] = field(default_factory=Counter)
    sql: str = ""

    @property
    def qualified(self) -> str:
        """``OWNER.NAME`` as in Oracle."""
        return f"{self.owner}.{self.name}" if self.owner else self.name

    @property
    def kind(self) -> str:
        """PACKAGE, PROCEDURE, FUNCTION, TRIGGER, TYPE ..."""
        return self.source_types[0] if self.source_types else "UNKNOWN"

    @property
    def filename(self) -> str:
        """File name for the converted SQL, e.g. ``hr.emp_mgmt.sql``."""
        base = f"{self.owner}.{self.name}" if self.owner else self.name
        return re.sub(r"[^a-z0-9_.$#-]", "_", base.lower()) + ".sql"

    @property
    def status(self) -> str:
        """The worst status of any unit."""
        if not self.units:
            return STATUS_CONVERTED
        return max((u.status for u in self.units), key=_STATUS_ORDER.index)

    @property
    def all_rewrites(self) -> Counter[str]:
        """Rewrite counts for the object and all its units."""
        total = Counter(self.rewrites)
        for unit in self.units:
            total.update(unit.rewrites)
        return total

    @property
    def reviews(self) -> list[ReviewPoint]:
        """Every review point in the object's file, in line order."""
        return sorted((r for u in self.units for r in u.reviews), key=lambda r: r.line)

    @property
    def statements(self) -> int:
        """Statements counted for coverage."""
        return sum(u.statements for u in self.units)

    @property
    def clean_statements(self) -> int:
        """Statements without a review marker."""
        return sum(u.clean_statements for u in self.units)


@dataclass
class ConversionResult:
    """Everything converted from one inventory or SQL file."""

    source: str
    objects: list[ConvertedObject] = field(default_factory=list)

    @property
    def units(self) -> list[ConvertedUnit]:
        """All units across objects."""
        return [u for o in self.objects for u in o.units]

    @property
    def rewrites(self) -> Counter[str]:
        """Rewrite counts across every object."""
        total: Counter[str] = Counter()
        for obj in self.objects:
            total.update(obj.all_rewrites)
        return total

    @property
    def reviews(self) -> list[ReviewPoint]:
        """Every review point across every object."""
        return [r for o in self.objects for r in o.reviews]

    @property
    def statements(self) -> int:
        """Statements counted for coverage."""
        return sum(o.statements for o in self.objects)

    @property
    def clean_statements(self) -> int:
        """Statements that needed no review."""
        return sum(o.clean_statements for o in self.objects)

    @property
    def coverage(self) -> float:
        """Mechanical coverage: share of statements converted without a review marker."""
        return 100.0 * self.clean_statements / self.statements if self.statements else 100.0


# --------------------------------------------------------------------------------------
# Context shared across objects
# --------------------------------------------------------------------------------------


@dataclass
class _Catalog:
    """Facts gathered from every object before converting any of them."""

    qualified_package_vars: frozenset[str] = frozenset()
    triggers: dict[tuple[str, str], Trigger] = field(default_factory=dict)


_VAR_DECL = re.compile(
    rf"^{W}*(?P<name>{IDENT}){W}+(?P<const>CONSTANT{W}+)?(?P<type>{TYPE_PATTERN})", _IS
)
_NOT_VARIABLE_WORDS = frozenset({"TYPE", "SUBTYPE", "CURSOR", "PRAGMA", "PROCEDURE", "FUNCTION"})


def _classify_declaration(text: str) -> tuple[str, str]:
    """``(category, name)`` for a package-level declaration in masked text.

    Categories: constant, variable, exception, refcursor, type, cursor, pragma, other.
    """
    word = first_word(text)
    if word == "PRAGMA":
        return "pragma", ""
    if word == "CURSOR":
        name = re.match(rf"^{W}*CURSOR{W}+({IDENT})", text, _I)
        return "cursor", name.group(1) if name else ""
    if word in {"TYPE", "SUBTYPE"}:
        ref = re.match(rf"^{W}*TYPE{W}+({IDENT}){W}+IS{W}+REF{W}+CURSOR\b", text, _I)
        if ref:
            return "refcursor", ref.group(1)
        name = re.match(rf"^{W}*(?:SUB)?TYPE{W}+({IDENT})", text, _I)
        return "type", name.group(1) if name else ""
    match = _VAR_DECL.match(text)
    if not match or match.group("name").upper() in _NOT_VARIABLE_WORDS:
        return "other", ""
    if match.group("type").upper() == "EXCEPTION":
        return "exception", match.group("name")
    if match.group("const"):
        return "constant", match.group("name")
    return "variable", match.group("name")


def _package_variables(source: SourceObject) -> list[str]:
    pool = TokenPool()
    masked = pool.mask(source.text)
    try:
        package = parse_package(masked)
    except StructureError:
        return []
    names = []
    for item in package.items:
        if item.kind == "declaration":
            category, name = _classify_declaration(masked[item.start : item.end])
            if category == "variable":
                names.append(name.lower())
    return names


def _catalog(sources: list[SourceObject], triggers: list[Trigger]) -> _Catalog:
    qualified = set()
    for source in sources:
        if source.type in {"PACKAGE", "PACKAGE BODY"}:
            for var in _package_variables(source):
                qualified.add(f"{source.name.lower()}.{var}")
    return _Catalog(frozenset(qualified), {(t.owner.upper(), t.name.upper()): t for t in triggers})


# --------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------


def convert_inventory(inventory: Inventory, only: str | None = None) -> ConversionResult:
    """Convert every stored program unit in ``inventory`` (or just ``only``, OWNER.NAME)."""
    catalog = _catalog(inventory.source, inventory.triggers)
    result = ConversionResult(source="inventory")
    for owner, name, parts in _group(inventory.source):
        if only is not None and not _matches(only, owner, name):
            continue
        result.objects.append(_convert_object(owner, name, parts, catalog))
    return result


def convert_sql(
    text: str, default_schema: str = "PUBLIC", only: str | None = None
) -> ConversionResult:
    """Convert a SQL*Plus-style script of ``CREATE OR REPLACE`` PL/SQL units."""
    sources = split_script(text, default_schema)
    catalog = _catalog(sources, [])
    result = ConversionResult(source="sql")
    for owner, name, parts in _group(sources):
        if only is not None and not _matches(only, owner, name):
            continue
        result.objects.append(_convert_object(owner, name, parts, catalog))
    return result


def _matches(only: str, owner: str, name: str) -> bool:
    wanted = only.strip().upper()
    return wanted in {f"{owner}.{name}".upper(), name.upper()}


def _group(sources: list[SourceObject]) -> list[tuple[str, str, dict[str, SourceObject]]]:
    grouped: dict[tuple[str, str], dict[str, SourceObject]] = {}
    for source in sources:
        grouped.setdefault((source.owner, source.name), {})[source.type] = source
    return [(owner, name, parts) for (owner, name), parts in grouped.items()]


_SLASH_LINE = re.compile(r"^[ \t]*/[ \t]*$", re.MULTILINE)
_CREATE = re.compile(
    rf"^(?P<lead>{W}*)CREATE{W}+(?:OR{W}+REPLACE{W}+)?(?:(?:NON)?EDITIONABLE{W}+)?"
    rf"(?P<kind>PACKAGE{W}+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE{W}+BODY|TYPE)\b"
    rf"{W}+(?P<name>[\w$#\".\U00100000-\U0010FFFD]+)",
    _I,
)
_CREATE_LINE = re.compile(rf"^(?={W}*CREATE\b)", _I | re.MULTILINE)


def split_script(text: str, default_schema: str = "PUBLIC") -> list[SourceObject]:
    """Split a script into source objects (``/`` lines, or one CREATE per unit)."""
    pool = TokenPool()
    masked = pool.mask(text)
    chunks = _SLASH_LINE.split(masked)
    if len(chunks) == 1:
        starts = [m.start() for m in _CREATE_LINE.finditer(masked)] or [0]
        if starts[0] != 0:
            starts.insert(0, 0)
        chunks = [masked[a:b] for a, b in zip(starts, [*starts[1:], len(masked)], strict=True)]
    sources = []
    for index, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        match = _CREATE.match(chunk)
        if not match:
            restored = pool.restore(chunk)
            if restored.strip().startswith("--") and not re.search(
                r"\w", _strip_comments(chunk, pool)
            ):
                continue
            sources.append(SourceObject(default_schema, f"SCRIPT_{index}", "SCRIPT", restored))
            continue
        kind = " ".join(match.group("kind").upper().split())
        owner, name = _split_name(pool.restore(match.group("name")), default_schema)
        body = chunk[match.start("kind") :]
        lead = pool.restore(match.group("lead"))
        sources.append(SourceObject(owner, name, kind, lead + pool.restore(body)))
    return sources


def _strip_comments(chunk: str, pool: TokenPool) -> str:
    return "".join(ch for ch in chunk if pool.kind(ch) is None)


def _split_name(qualified: str, default_owner: str) -> tuple[str, str]:
    parts = [p.strip().strip('"') for p in qualified.split(".") if p.strip()]
    if len(parts) >= 2:
        return parts[-2].upper(), parts[-1].upper()
    return default_owner.upper(), (parts[0] if parts else "").upper()


# --------------------------------------------------------------------------------------
# Objects
# --------------------------------------------------------------------------------------


def _convert_object(
    owner: str, name: str, parts: dict[str, SourceObject], catalog: _Catalog
) -> ConvertedObject:
    types = tuple(sorted(parts, key=lambda t: (len(t), t)))
    obj = ConvertedObject(owner, name, types)
    try:
        if "PACKAGE" in parts or "PACKAGE BODY" in parts:
            _convert_package(obj, parts.get("PACKAGE"), parts.get("PACKAGE BODY"), catalog)
        elif "PROCEDURE" in parts or "FUNCTION" in parts:
            _convert_standalone(obj, parts.get("PROCEDURE") or parts["FUNCTION"], catalog)
        elif "TRIGGER" in parts:
            _convert_trigger_object(obj, parts["TRIGGER"], catalog)
        else:
            reason = _unsupported_reason(types)
            obj.units.append(_not_converted(f"{owner}.{name}", parts, reason))
    except StructureError as exc:
        obj.preamble = []
        obj.units = [_not_converted(f"{owner}.{name}", parts, f"could not parse: {exc}")]
    _assemble(obj)
    return obj


def _unsupported_reason(types: tuple[str, ...]) -> str:
    if any(t.startswith("TYPE") for t in types):
        return (
            "object type: create a composite type (CREATE TYPE ... AS (...)) and turn member "
            "methods into functions that take the type as a parameter"
        )
    if "SCRIPT" in types:
        return "not a CREATE PROCEDURE/FUNCTION/PACKAGE/TRIGGER unit"
    return f"{'/'.join(types)} is not converted"


def _not_converted(name: str, parts: dict[str, SourceObject], reason: str) -> ConvertedUnit:
    pool = TokenPool()
    lines = [f"{MARKER_PREFIX}O2PG-RV-001: {reason} */"]
    total = 0
    for source in parts.values():
        total += max(1, pool.mask(source.text).count(";"))
        lines += ["-- " + line if line else "--" for line in source.text.rstrip("\n").split("\n")]
    return ConvertedUnit(name, KIND_NOT_CONVERTED, "\n".join(lines), statements=total)


def _schema_path(*schemas: str) -> str:
    seen: list[str] = []
    for schema in (*schemas, "public"):
        if schema and schema not in seen:
            seen.append(schema)
    return ", ".join(seen)


def _convert_standalone(obj: ConvertedObject, source: SourceObject, catalog: _Catalog) -> None:
    pool = TokenPool()
    masked = pool.mask(source.text)
    start = re.search(r"\b(?:PROCEDURE|FUNCTION)\b", masked, _I)
    if not start:
        raise StructureError("no PROCEDURE or FUNCTION keyword")
    sub = parse_subprogram(masked, start.start())
    schema = ident(obj.owner)
    obj.preamble = [f"SET search_path = {_schema_path(schema)};"]
    lead = _trivia(pool, masked[: start.start()])
    unit = _convert_subprogram(
        pool,
        masked,
        sub,
        schema=schema,
        search_path=_schema_path(schema),
        catalog=catalog,
        oracle_name=obj.qualified,
    )
    unit.sql = lead + unit.sql
    obj.units.append(unit)


def _trivia(pool: TokenPool, masked: str) -> str:
    """Comments found in ``masked`` (between items), restored, as a prefix block."""
    comments = [pool.restore(ch) for ch in masked if pool.kind(ch) in {"comment"}]
    return "".join(c if c.endswith("\n") else c + "\n" for c in comments)


@dataclass
class _PackageContext:
    variables: frozenset[str] = frozenset()
    constants: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    exceptions: frozenset[str] = frozenset()
    invoker: bool = False


def _convert_package(
    obj: ConvertedObject,
    spec: SourceObject | None,
    body: SourceObject | None,
    catalog: _Catalog,
) -> None:
    pool = TokenPool()
    parsed = []
    for source in (spec, body):
        if source is None:
            continue
        masked = pool.mask(source.text)
        package = parse_package(masked)
        if package.wrapped:
            parts = {s.type: s for s in (spec, body) if s is not None}
            obj.units.append(
                _not_converted(
                    obj.qualified,
                    parts,
                    "wrapped (obfuscated) PL/SQL cannot be converted; convert the original "
                    "source instead",
                )
            )
            return
        parsed.append((source, masked, package))

    schema = ident(obj.name)
    owner_schema = ident(obj.owner)
    obj.preamble = [
        f"CREATE SCHEMA IF NOT EXISTS {schema};",
        f"SET search_path = {_schema_path(schema, owner_schema)};",
    ]
    obj.rewrites["O2PG-CV-012"] += 1
    context = _PackageContext()
    state_lines: list[str] = []
    state = ConvertedUnit(f"{schema} (package declarations)", KIND_STATE, "")
    variables: set[str] = set()
    exceptions: set[str] = set()
    routines: list[tuple[str, PackageItem, str]] = []
    for source, masked, package in parsed:
        if source.type == "PACKAGE" and re.search(
            rf"\bAUTHID{W}+CURRENT_USER\b", masked[: package.header_end], _I
        ):
            context.invoker = True
        cursor = 0
        for item in package.items:
            comments = _trivia(pool, masked[cursor : item.start]).rstrip("\n")
            cursor = item.end + (1 if item.kind == "declaration" else 0)
            if item.kind == "subprogram":
                sub = item.subprogram
                assert sub is not None
                if source.type == "PACKAGE BODY" and not sub.forward:
                    routines.append((masked, item, comments))
                    continue
                if comments:
                    state_lines.append(comments)
                text = masked[item.start : item.end]
                if source.type == "PACKAGE BODY":
                    state_lines.append(
                        _comment_block(pool, text, "forward declaration, not needed")
                    )
                    state.rewrites["O2PG-CV-014"] += 1
                else:
                    state_lines.append(_comment_block(pool, text, "spec, implemented below"))
                continue
            if comments:
                state_lines.append(comments)
            if item.kind == "initialization":
                state_lines.append(
                    _marked_block(pool, masked[item.start : item.end], "O2PG-RV-020")
                )
                state.statements += 1
                continue
            text = masked[item.start : item.end]
            category, name = _classify_declaration(text)
            state.statements += 1
            if category == "constant":
                context.constants[name.lower()] = text.strip()
                state_lines.append(_comment_block(pool, text + ";", "inlined where used"))
                state.clean_statements += 1
            elif category == "refcursor":
                context.aliases[name.upper()] = "refcursor"
                state_lines.append(_comment_block(pool, text + ";", "now refcursor"))
                state.rewrites["O2PG-CV-011"] += 1
                state.clean_statements += 1
            elif category == "variable":
                variables.add(name.lower())
                state_lines.append(_marked_block(pool, text + ";", "O2PG-RV-011"))
            elif category == "exception":
                exceptions.add(name.lower())
                state_lines.append(_marked_block(pool, text + ";", "O2PG-RV-012"))
            elif category == "type":
                state_lines.append(_marked_block(pool, text + ";", "O2PG-RV-009"))
            elif category == "pragma":
                state_lines.append(_marked_block(pool, text + ";", "O2PG-RV-013"))
            else:
                reason = (
                    "package-level cursor: declare it in each routine that uses it"
                    if category == "cursor"
                    else "package-level declaration not converted"
                )
                state_lines.append(_marked_block(pool, text + ";", "O2PG-RV-029", reason))
        tail = _trivia(pool, masked[cursor:]).rstrip("\n")
        if tail:
            state_lines.append(tail)
    context.variables = frozenset(variables)
    context.exceptions = frozenset(exceptions)
    if state_lines:
        state.sql = "-- Package-level declarations (Oracle source kept as comments)\n" + "\n".join(
            state_lines
        )
        obj.units.append(state)
    for masked, item, comments in routines:
        sub = item.subprogram
        assert sub is not None
        unit = _convert_subprogram(
            pool,
            masked,
            sub,
            schema=schema,
            search_path=_schema_path(schema, owner_schema),
            catalog=catalog,
            oracle_name=f"{obj.qualified}.{sub.name.upper()}",
            package=context,
        )
        unit.sql = (comments + "\n" if comments else "") + unit.sql
        obj.units.append(unit)


def _comment_block(pool: TokenPool, masked: str, note: str) -> str:
    original = _dedent(pool.restore(masked).strip("\n"))
    lines = original.split("\n")
    lines[-1] += f"  -- o2pg: {note}"
    return "\n".join("-- " + line for line in lines)


def _marked_block(pool: TokenPool, masked: str, rule: str, reason: str | None = None) -> str:
    text = reason or get_conversion_rule(rule).description
    original = _dedent(pool.restore(masked).strip("\n"))
    marker = pool.restore(pool.marker(rule, text))
    return marker + "\n" + "\n".join("-- " + line for line in original.split("\n"))


def _dedent(text: str) -> str:
    lines = text.split("\n")
    widths = [len(line) - len(line.lstrip(" ")) for line in lines[1:] if line.strip()]
    first = len(lines[0]) - len(lines[0].lstrip(" "))
    width = min([first, *widths]) if widths else first
    return "\n".join(line[width:] if line[:width].strip() == "" else line for line in lines)


# --------------------------------------------------------------------------------------
# Routines
# --------------------------------------------------------------------------------------

_PARAM = re.compile(
    rf"^(?P<lead>{W}*)(?P<name>{IDENT}){W}+(?:(?P<mode>IN{W}+OUT|IN|OUT)\b{W}*)?"
    rf"(?:NOCOPY\b{W}*)?(?P<type>{TYPE_PATTERN})"
    rf"(?:(?P<defkw>{W}*(?::=|\bDEFAULT\b)){W}*(?P<expr>.*?))?(?P<trail>{W}*)$",
    _IS,
)
_FUNCTION_TAIL = re.compile(
    rf"^{W}*RETURN{W}+(?P<rtype>{TYPE_PATTERN})(?P<attrs>.*?)(?P<isas>\b(?:IS|AS))$", _IS
)
_PROCEDURE_TAIL = re.compile(r"^(?P<attrs>.*?)(?P<isas>\b(?:IS|AS))$", _IS)
_TRANSACTION = re.compile(r"(?<![\w$#.])(?:COMMIT|ROLLBACK)\b", _I)


@dataclass
class _Header:
    params: str = ""
    families: dict[str, str] = field(default_factory=dict)
    markers: list[tuple[str, str | None]] = field(default_factory=list)
    has_out: bool = False


def _convert_params(
    pool: TokenPool, text: str, scope_aliases: dict[str, str], rewrites: Counter[str]
) -> _Header:
    header = _Header()
    if not text:
        return header
    converted = []
    seen_default = False
    for part in split_top_level(text):
        match = _PARAM.match(part)
        if not match:
            header.markers.append(("O2PG-RV-029", "parameter not recognised: rewrite by hand"))
            converted.append(part)
            continue
        name = match.group("name")
        mode = " ".join((match.group("mode") or "").upper().split())
        type_text = match.group("type")
        mapped = map_plsql_type(type_text, scope_aliases)
        if mapped is None:
            reason = f"no automatic mapping for parameter type {pool.restore(type_text).strip()}"
            header.markers.append(("O2PG-RV-010", reason))
            pg_type = type_text
        else:
            pg_type = mapped.text
            header.families[name.lower()] = mapped.family
            if pg_type != " ".join(type_text.split()):
                rewrites["O2PG-CV-004"] += 1
        pg_mode = {"": "", "IN": "", "OUT": "OUT ", "IN OUT": "INOUT "}[mode]
        if mode:
            rewrites["O2PG-CV-003"] += 1
        if mode in {"OUT", "IN OUT"}:
            header.has_out = True
        default = ""
        if match.group("defkw"):
            default = f" DEFAULT {match.group('expr').strip()}"
            if ":=" in match.group("defkw"):
                rewrites["O2PG-CV-005"] += 1
            seen_default = seen_default or mode != "OUT"
        elif seen_default and mode != "OUT":
            header.markers.append(("O2PG-RV-025", None))
        converted.append(
            f"{match.group('lead')}{pg_mode}{name} {pg_type}{default}{match.group('trail')}"
        )
    header.params = ",".join(converted)
    return header


def _convert_subprogram(
    pool: TokenPool,
    masked: str,
    sub: Subprogram,
    *,
    schema: str,
    search_path: str,
    catalog: _Catalog,
    oracle_name: str,
    package: _PackageContext | None = None,
) -> ConvertedUnit:
    package = package or _PackageContext()
    pg_name = f"{schema}.{ident(sub.name.split('.')[-1].strip(chr(34)))}"
    kind = sub.kind
    rewrites: Counter[str] = Counter()
    if sub.call_spec:
        return ConvertedUnit(
            pg_name,
            KIND_NOT_CONVERTED,
            _marked_block(
                pool,
                masked[sub.start : sub.end],
                "O2PG-RV-001",
                "call specification (LANGUAGE JAVA/C or EXTERNAL): no PL/pgSQL equivalent on Azure",
            ),
            statements=1,
        )
    indent = _line_indent(masked, sub.start)
    header_text = masked[sub.start : sub.header_end]
    head = re.match(rf"^(?:PROCEDURE|FUNCTION){W}+[^\s(]+(?P<gap>{W}*)", header_text, _I)
    assert head is not None
    cursor = head.end()
    params_text = ""
    if cursor < len(header_text) and header_text[cursor] == "(":
        close = matching_paren(header_text, cursor)
        assert close is not None
        params_text = header_text[cursor + 1 : close]
        cursor = close + 1
    tail = header_text[cursor:]
    header = _convert_params(pool, _undent(params_text, indent), package.aliases, rewrites)
    lines: list[str] = []
    returns = ""
    if kind == "FUNCTION":
        match = _FUNCTION_TAIL.match(tail)
        if not match:
            raise StructureError(f"function {sub.name}: RETURN clause not recognised")
        rtype = match.group("rtype")
        mapped = map_plsql_type(rtype, package.aliases)
        if mapped is None:
            header.markers.append(
                (
                    "O2PG-RV-010",
                    f"no automatic mapping for return type {pool.restore(rtype).strip()}",
                )
            )
            returns = rtype.strip()
        else:
            returns = mapped.text
            if mapped.text != " ".join(rtype.split()):
                rewrites["O2PG-CV-004"] += 1
        rewrites["O2PG-CV-002"] += 1
        attrs = match.group("attrs")
        if header.has_out:
            header.markers.append(("O2PG-RV-024", None))
    else:
        match = _PROCEDURE_TAIL.match(tail)
        attrs = match.group("attrs") if match else ""
    if re.search(r"\bPIPELINED\b", attrs, _I):
        header.markers.append(("O2PG-RV-008", None))
    invoker = package.invoker or bool(re.search(rf"\bAUTHID{W}+CURRENT_USER\b", attrs, _I))
    options = re.sub(rf"\bAUTHID{W}+\w+|\bPIPELINED\b", "", attrs, flags=_I)
    option_text = " ".join(pool.restore(options).split())
    if option_text:
        rewrites["O2PG-CV-009"] += 1
    rewrites["O2PG-CV-008"] += 1

    decls = _undent(masked[sub.header_end : sub.begin], indent)
    body = _undent(masked[sub.begin : sub.end], indent)
    constants = [
        text
        for name, text in package.constants.items()
        if re.search(rf"(?<![\w$#.]){re.escape(name)}\b", decls + body, _I)
    ]
    if constants:
        rewrites["O2PG-CV-013"] += len(constants)
        decls = "".join(f"\n  {c};" for c in constants) + decls
    region = ("DECLARE" + decls if decls.strip() else "") + body.lstrip()
    commits = kind == "PROCEDURE" and bool(_TRANSACTION.search(body))
    scope = Scope(
        pool=pool,
        routine_kind=kind,
        package_vars=package.variables,
        qualified_package_vars=catalog.qualified_package_vars,
        type_aliases=dict(package.aliases),
        variables=header.families,
        exceptions=package.exceptions,
    )
    rewriter = BlockRewriter(region, scope)
    region = rewriter.run()
    rewrites.update(rewriter.applied)
    if sub.nested:
        header.markers.append(("O2PG-RV-019", None))
    rewrites["O2PG-CV-001"] += 1

    marker_lines = [
        pool.marker(rule, reason or get_conversion_rule(rule).description)
        for rule, reason in header.markers
    ]
    lines = [*marker_lines]
    gap = " " if head.group("gap") else ""
    lines.append(f"CREATE OR REPLACE {kind} {pg_name}{gap}({header.params})")
    if returns:
        lines.append(f"RETURNS {returns}")
    lines.append("LANGUAGE plpgsql")
    if option_text:
        lines.append(f"-- Oracle options (no effect in PostgreSQL): {option_text}")
    if invoker:
        lines.append("SECURITY INVOKER")
    if not commits:
        lines.append(f"SET search_path = {search_path}")
        rewrites["O2PG-CV-007"] += 1
    head_sql = pool.restore("\n".join(lines), rewrites)
    body_sql = pool.restore(region, rewrites)
    tag = "$o2pg$" if "$$" in body_sql else "$$"
    sql = f"{head_sql}\nAS {tag}\n{body_sql.strip(chr(10))}\n{tag};"
    total, clean = _coverage(pool, region)
    header_clean = 0 if header.markers else 1
    return ConvertedUnit(
        pg_name,
        kind,
        sql,
        rewrites,
        statements=total + 1,
        clean_statements=clean + header_clean,
    )


def _line_indent(text: str, index: int) -> int:
    start = text.rfind("\n", 0, index) + 1
    prefix = text[start:index]
    return len(prefix) if not prefix.strip() else 0


def _undent(text: str, width: int) -> str:
    """Remove up to ``width`` leading spaces from every line after the first."""
    if width <= 0:
        return text
    return re.sub(rf"\n[ ]{{1,{width}}}", "\n", text)


def _coverage(pool: TokenPool, region: str) -> tuple[int, int]:
    total = clean = 0
    for stmt in statements(region):
        body = region[stmt.start : stmt.end]
        word = first_word(body)
        if not word or word == "END":
            continue
        total += 1
        if not pool.has_marker(region[stmt.chunk_start : stmt.end]):
            clean += 1
    return total, clean


# --------------------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------------------


def _convert_trigger_object(obj: ConvertedObject, source: SourceObject, catalog: _Catalog) -> None:
    pool = TokenPool()
    masked = pool.mask(source.text)
    parts = parse_trigger(masked)
    if isinstance(parts, str):
        obj.units.append(_not_converted(obj.qualified, {source.type: source}, parts))
        return
    info = catalog.triggers.get((obj.owner.upper(), obj.name.upper()))
    schema = ident(obj.owner)
    obj.preamble = [f"SET search_path = {_schema_path(schema)};"]
    start = re.search(r"\bTRIGGER\b", masked, _I)
    lead = _trivia(pool, masked[: start.start()]) if start else ""
    unit = _convert_trigger(pool, parts, obj, info, schema, catalog)
    unit.sql = lead + unit.sql
    obj.units.append(unit)


def _convert_trigger(
    pool: TokenPool,
    parts: TriggerParts,
    obj: ConvertedObject,
    info: Trigger | None,
    schema: str,
    catalog: _Catalog,
) -> ConvertedUnit:
    rewrites: Counter[str] = Counter({"O2PG-CV-032": 1})
    trigger_name = ident(pool.restore(parts.name).split(".")[-1].strip('"'))
    function = f"{schema}.{trigger_name}"
    table_owner, table_name = _split_name(
        pool.restore(parts.table), (info.table_owner if info and info.table_owner else obj.owner)
    )
    table = f"{ident(table_owner)}.{ident(table_name)}"
    events = split_events(parts.events)
    pg_events = []
    kinds = set()
    for event in events:
        word = first_word(event)
        kinds.add(word)
        if word == "UPDATE" and re.search(r"\bOF\b", event, _I):
            cols = re.split(r"\bOF\b", event, maxsplit=1, flags=_I)[1]
            names = [ident(c.strip().strip('"')) for c in pool.restore(cols).split(",")]
            pg_events.append("UPDATE OF " + ", ".join(names))
        else:
            pg_events.append(word)
    new_aliases, old_aliases = referencing_aliases(parts.referencing)
    row = parts.for_each_row
    timing = parts.timing
    if row and timing in {"BEFORE", "INSTEAD OF"}:
        if kinds == {"DELETE"}:
            final = "RETURN OLD;"
            bare: str | None = "RETURN OLD;"
        elif "DELETE" in kinds:
            delete = pool.literal("DELETE")
            final = f"IF TG_OP = {delete} THEN\n    RETURN OLD;\n  END IF;\n  RETURN NEW;"
            bare = None
        else:
            final = "RETURN NEW;"
            bare = "RETURN NEW;"
    else:
        final = "RETURN NULL;"
        bare = "RETURN NULL;"
    scope = Scope(
        pool=pool,
        routine_kind="TRIGGER",
        new_aliases=("NEW", *new_aliases),
        old_aliases=("OLD", *old_aliases),
        trigger_return=bare,
        qualified_package_vars=catalog.qualified_package_vars,
    )
    region = parts.body
    rewriter = BlockRewriter(region, scope)
    region = rewriter.run()
    rewrites.update(rewriter.applied)
    when_sql = ""
    if parts.when is not None:
        when = parts.when
        for alias in new_aliases:
            when = re.sub(rf"(?<![\w$#.:]){re.escape(alias)}{W}*\.", "NEW.", when, flags=_I)
        for alias in old_aliases:
            when = re.sub(rf"(?<![\w$#.:]){re.escape(alias)}{W}*\.", "OLD.", when, flags=_I)
        when_rewriter = BlockRewriter(when, scope)
        when = when_rewriter.run()
        rewrites.update(when_rewriter.applied)
        when_sql = f"WHEN ({pool.restore(when, rewrites).strip()})\n"
    options = pool.restore(parts.options, rewrites)
    extra = []
    disabled = bool(re.search(r"\bDISABLE\b", options, _I)) or bool(
        info and info.status.upper() == "DISABLED"
    )
    if parts.referencing:
        refs = " ".join(pool.restore(parts.referencing).split())
        extra.append(f"-- Oracle: REFERENCING {refs} (aliases rewritten to NEW and OLD)")
    follows = re.search(r"\b(?:FOLLOWS|PRECEDES)\b[^\n]*", options, _I)
    if follows:
        extra.append(
            f"-- Oracle: {follows.group(0).strip()} (PostgreSQL fires triggers in name order)"
        )
    body_sql = pool.restore(region, rewrites).strip("\n")
    final_sql = pool.restore(final, rewrites)
    indented = "\n".join(("  " + line) if line.strip() else line for line in body_sql.split("\n"))
    tag = "$o2pg$" if "$$" in body_sql else "$$"
    sql = (
        f"CREATE OR REPLACE FUNCTION {function}()\n"
        "RETURNS trigger\n"
        "LANGUAGE plpgsql\n"
        f"SET search_path = {_schema_path(schema, ident(table_owner))}\n"
        f"AS {tag}\n"
        "BEGIN\n"
        f"{indented}\n"
        f"  {final_sql}\n"
        "END;\n"
        f"{tag};\n"
        "\n"
        + "".join(line + "\n" for line in extra)
        + f"CREATE OR REPLACE TRIGGER {trigger_name}\n"
        f"{timing} {' OR '.join(pg_events)} ON {table}\n"
        f"FOR EACH {'ROW' if row else 'STATEMENT'}\n"
        f"{when_sql}"
        f"EXECUTE FUNCTION {function}();"
    )
    rewrites["O2PG-CV-007"] += 1
    if disabled:
        sql += f"\nALTER TABLE {table} DISABLE TRIGGER {trigger_name};"
    total, clean = _coverage(pool, region)
    return ConvertedUnit(
        f"{function} (trigger on {table})",
        "TRIGGER",
        sql,
        rewrites,
        statements=total + 1,
        clean_statements=clean + 1,
    )


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def _assemble(obj: ConvertedObject) -> None:
    marker_count = sum(len(find_markers(u.sql)) for u in obj.units)
    blocking = sum(
        1
        for u in obj.units
        for _, rule, _ in find_markers(u.sql)
        if get_conversion_rule(rule).blocking
    )
    kinds = " + ".join(obj.source_types)
    header = [
        f"-- {obj.qualified} ({kinds}) converted to PL/pgSQL by o2pg {__version__}",
        "-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic",
        "-- against real data before relying on it.",
        f"-- Review points: {marker_count} ({blocking} blocking). See conversion-report.md.",
        "",
        "",
    ]
    text = "\n".join(header)
    if obj.preamble:
        text += "\n".join(obj.preamble) + "\n\n"
    for index, unit in enumerate(obj.units):
        start_line = text.count("\n") + 1
        unit.reviews = [
            ReviewPoint(start_line + line - 1, rule, reason, get_conversion_rule(rule).blocking)
            for line, rule, reason in find_markers(unit.sql)
        ]
        text += unit.sql.rstrip("\n") + "\n"
        if index < len(obj.units) - 1:
            text += "\n"
    obj.sql = text
