"""Rewrite one masked PL/SQL block (``[DECLARE ...] BEGIN ... END;``) into PL/pgSQL.

:class:`BlockRewriter` runs a fixed sequence of passes over masked text (see
:mod:`o2pg.convert.lexer`), so nothing inside a comment or string literal can be
touched. Each pass either makes a mechanical edit, recorded against an
``O2PG-CV`` rule, or leaves the code alone and drops an ``O2PG-REVIEW`` marker on
the line above it. Markers are deduplicated per line and rule, which keeps the
passes idempotent: running the rewriter on its own output changes nothing.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from o2pg.convert.lexer import (
    IDENT,
    KIND_EMPTY,
    KIND_MARKER,
    KIND_STRING,
    LIT,
    TokenPool,
    W,
    first_word,
    indent_at,
    line_start,
    matching_paren,
    split_top_level,
    statements,
    top_level,
)
from o2pg.convert.registry import get_conversion_rule
from o2pg.convert.types import TYPE_PATTERN, map_plsql_type
from o2pg.ddl import ident

_I = re.IGNORECASE
_IS = re.IGNORECASE | re.DOTALL


@dataclass
class Scope:
    """What the rewriter needs to know about the routine around the block.

    ``routine_kind`` is ``PROCEDURE``, ``FUNCTION`` or ``TRIGGER``.
    ``variables`` maps lower-case parameter names to a type family (``date``,
    ``integer``, ``numeric`` or ``other``). ``trigger_return`` is the statement
    that replaces a bare ``RETURN;`` in a trigger body, or None when it cannot be
    replaced mechanically.
    """

    pool: TokenPool
    routine_kind: str = "PROCEDURE"
    new_aliases: tuple[str, ...] = ("NEW",)
    old_aliases: tuple[str, ...] = ("OLD",)
    trigger_return: str | None = None
    package_vars: frozenset[str] = frozenset()
    qualified_package_vars: frozenset[str] = frozenset()
    type_aliases: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)
    exceptions: frozenset[str] = frozenset()

    @property
    def is_trigger(self) -> bool:
        """True when the block is a trigger body."""
        return self.routine_kind == "TRIGGER"


@dataclass(frozen=True)
class Flag:
    """A review point to mark at ``offset``. ``reason`` defaults to the rule text."""

    offset: int
    rule: str
    reason: str | None = None


@dataclass
class Edit:
    """Replace ``text[start:end]`` with ``new``; ``flags`` offsets are relative to ``new``."""

    start: int
    end: int
    new: str
    rule: str | None = None
    flags: list[tuple[int, str, str | None]] = field(default_factory=list)


_ORACLE_PACKAGE = re.compile(
    rf"(?<![\w$#.])(?:SYS{W}*\.{W}*)?(?P<pkg>DBMS_\w+|UTL_\w+|OWA_\w+|HTP|HTF|APEX_\w+|CTX_\w+"
    rf"|ORD_\w+|SDO_\w+){W}*\.",
    _I,
)
_PACKAGE_ADVICE = {
    "UTL_FILE": "UTL_FILE: there is no server file system on Azure Flexible Server; write files "
    "from the application or Azure Storage instead",
    "UTL_HTTP": "network call from the database: move it to the application, an Azure Function "
    "or a queue consumer",
    "UTL_SMTP": "sending mail from the database: move it to the application or a queue consumer",
    "UTL_MAIL": "sending mail from the database: move it to the application or a queue consumer",
    "UTL_TCP": "network call from the database: move it to the application",
    "DBMS_SCHEDULER": "scheduling: use pg_cron",
    "DBMS_JOB": "scheduling: use pg_cron",
    "DBMS_AQ": "Advanced Queuing: use a queue table (SELECT ... FOR UPDATE SKIP LOCKED) or an "
    "Azure messaging service",
    "DBMS_AQADM": "Advanced Queuing: use a queue table or an Azure messaging service",
    "DBMS_LOB": "DBMS_LOB call: text and bytea functions (substr, length, position, overlay) "
    "cover most uses",
    "DBMS_MVIEW": "use REFRESH MATERIALIZED VIEW [CONCURRENTLY] (EXECUTE format(...) if the "
    "name is a parameter)",
    "DBMS_STATS": "use ANALYZE; autovacuum usually keeps statistics current",
    "DBMS_OUTPUT": "only DBMS_OUTPUT.PUT_LINE is converted (to RAISE NOTICE)",
    "DBMS_SQL": "rewrite with EXECUTE ... USING or EXECUTE format(...)",
    "DBMS_CRYPTO": "use pgcrypto",
    "DBMS_RANDOM": "use random() or gen_random_uuid()",
    "DBMS_UTILITY": "use GET STACKED DIAGNOSTICS for error stacks and backtraces",
    "DBMS_APPLICATION_INFO": "use SET application_name or set_config()",
    "DBMS_SESSION": "use set_config() / current_setting()",
    "DBMS_LOCK": "use advisory locks (pg_advisory_xact_lock)",
}
_ORACLE_FUNCTIONS = re.compile(
    rf"(?<![\w$#.])(?P<name>MONTHS_BETWEEN|LAST_DAY|NEXT_DAY|NUMTODSINTERVAL|NUMTOYMINTERVAL"
    r"|SYS_CONTEXT|USERENV|LISTAGG|WM_CONCAT|TO_CLOB|TO_BLOB|EMPTY_CLOB|EMPTY_BLOB|RAWTOHEX"
    r"|HEXTORAW|BITAND|TO_TIMESTAMP_TZ|SYS_EXTRACT_UTC|LNNVL|NANVL|INSTRB|SUBSTRB|LENGTHB"
    r"|ORA_HASH|STANDARD_HASH|SYS_CONNECT_BY_PATH|TO_DSINTERVAL|TO_YMINTERVAL|FROM_TZ|NEW_TIME"
    rf"|MEDIAN|VSIZE|NLS_UPPER|NLS_LOWER|NLSSORT|RATIO_TO_REPORT){W}*\("
    r"|(?<![\w$#.])(?P<bare>DBTIMEZONE|SESSIONTIMEZONE)\b",
    _I,
)
_FUNCTION_ADVICE = {
    "LISTAGG": "LISTAGG: use string_agg(expr, sep ORDER BY ...)",
    "WM_CONCAT": "WM_CONCAT: use string_agg(expr, ',')",
    "SYS_CONTEXT": "SYS_CONTEXT: use current_user, current_setting() or inet_client_addr()",
    "USERENV": "USERENV: use current_user, current_setting() or inet_client_addr()",
    "MONTHS_BETWEEN": "MONTHS_BETWEEN: use age() and EXTRACT, or orafce",
    "LAST_DAY": "LAST_DAY: date_trunc('month', d) + interval '1 month - 1 day', or orafce",
    "BITAND": "BITAND: use the & operator on integers",
    "RAWTOHEX": "RAWTOHEX: use encode(x, 'hex')",
    "HEXTORAW": "HEXTORAW: use decode(x, 'hex')",
    "EMPTY_CLOB": "EMPTY_CLOB(): use ''",
    "EMPTY_BLOB": "EMPTY_BLOB(): use ''::bytea",
}
_DETECT: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bPRAGMA{W}+AUTONOMOUS_TRANSACTION\b", _I), "O2PG-RV-002"),
    (re.compile(rf"\bPRAGMA{W}+(?!AUTONOMOUS_TRANSACTION\b)", _I), "O2PG-RV-013"),
    (re.compile(rf"\bBULK{W}+COLLECT\b", _I), "O2PG-RV-003"),
    (re.compile(r"\bFORALL\b", _I), "O2PG-RV-004"),
    (re.compile(r"(?<![\w$#.])ROWNUM\b", _I), "O2PG-RV-005"),
    (re.compile(rf"\bCONNECT{W}+BY\b", _I), "O2PG-RV-006"),
    (re.compile(rf"\({W}*\+{W}*\)"), "O2PG-RV-007"),
    (re.compile(rf"\bPIPE{W}+ROW\b|\bPIPELINED\b", _I), "O2PG-RV-008"),
    (re.compile(r"\bGOTO\b", _I), "O2PG-RV-018"),
    (re.compile(r"\b(?:PROCEDURE|FUNCTION)\b", _I), "O2PG-RV-019"),
    (
        re.compile(rf"%{W}*(?:ROWCOUNT|ISOPEN|BULK_ROWCOUNT|BULK_EXCEPTIONS)\b", _I),
        "O2PG-RV-021",
    ),
    (re.compile(r"(?<![\w$#.])(?:ROWID|UROWID|ROWIDTOCHAR|CHARTOROWID)\b", _I), "O2PG-RV-026"),
    (re.compile(rf"\bAS{W}+OF{W}+(?:TIMESTAMP|SCN)\b", _I), "O2PG-RV-027"),
    (re.compile(r"(?<![\w$#.])DUAL\b", _I), "O2PG-RV-028"),
    (re.compile(rf"\bSQLERRM{W}*\(", _I), "O2PG-RV-037"),
    (re.compile(rf"(?<![\w$#.])(?:LEAST|GREATEST){W}*\(", _I), "O2PG-RV-041"),
    (re.compile(r"(?<![\w$#.])REGEXP_(?:REPLACE|LIKE|SUBSTR|INSTR|COUNT)\b", _I), "O2PG-RV-042"),
]

_EXCEPTION_NAMES = {
    "DUP_VAL_ON_INDEX": "unique_violation",
    "ZERO_DIVIDE": "division_by_zero",
    "INVALID_NUMBER": "invalid_text_representation",
    "VALUE_ERROR": "data_exception",
    "CURSOR_ALREADY_OPEN": "duplicate_cursor",
    "INVALID_CURSOR": "invalid_cursor_state",
    "TIMEOUT_ON_RESOURCE": "lock_not_available",
    "NO_DATA_FOUND": "no_data_found",
    "TOO_MANY_ROWS": "too_many_rows",
}
_TRUNC_UNITS = {
    "DD": "day",
    "DDD": "day",
    "J": "day",
    "DAY": "week",
    "DY": "week",
    "D": "week",
    "IW": "week",
    "MM": "month",
    "MON": "month",
    "MONTH": "month",
    "RM": "month",
    "Q": "quarter",
    "YYYY": "year",
    "YEAR": "year",
    "YYY": "year",
    "YY": "year",
    "Y": "year",
    "SYYYY": "year",
    "HH": "hour",
    "HH12": "hour",
    "HH24": "hour",
    "MI": "minute",
    "CC": "century",
    "SCC": "century",
}
# 'DAY'/'DY'/'D' truncate to the first day of the week in Oracle, which depends on NLS
# territory; 'IW' is the ISO week, which is what date_trunc('week') does. Only 'IW' is safe.
_UNSAFE_TRUNC_UNITS = frozenset({"DAY", "DY", "D"})

_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_INTEGER = re.compile(r"^\d+$")
_SIMPLE_NAME = re.compile(rf"^{IDENT}(?:\.{IDENT})*$")
_TIME_ATOM = re.compile(
    rf"^(?:LOCALTIMESTAMP(?:{W}*\({W}*\d*{W}*\))?|CURRENT_TIMESTAMP(?:{W}*\({W}*\d*{W}*\))?"
    rf"|clock_timestamp{W}*\({W}*\)|now{W}*\({W}*\)|statement_timestamp{W}*\({W}*\)"
    rf"|SYSDATE|SYSTIMESTAMP)$",
    _I,
)
_KEYWORD_STATEMENTS = frozenset(
    """
    NULL RAISE RETURN COMMIT ROLLBACK EXIT CONTINUE END BEGIN CLOSE OPEN FETCH GOTO SAVEPOINT
    PERFORM CALL LOOP ELSE ELSIF IF WHILE FOR FORALL CASE WHEN THEN DECLARE EXCEPTION SELECT
    INSERT UPDATE DELETE MERGE EXECUTE LOCK SET PIPE GET MOVE ASSERT
    """.split()
)
_COLLECTION_METHODS = frozenset({"EXTEND", "DELETE", "TRIM"})


class BlockRewriter:
    """Apply every mechanical rewrite and review check to one masked block."""

    def __init__(self, text: str, scope: Scope) -> None:
        self.text = text
        self.scope = scope
        self.pool = scope.pool
        self.applied: Counter[str] = Counter()
        self.variables: dict[str, str] = dict(scope.variables)
        self.declared: set[str] = set(self.variables)
        self.cursors: set[str] = set()
        self.exceptions: set[str] = set(scope.exceptions)
        self.aliases: dict[str, str] = dict(scope.type_aliases)
        self.extra_decls: list[str] = []

    # ------------------------------------------------------------------ driver

    def run(self) -> str:
        """Run every pass and return the rewritten masked text."""
        self._declarations()
        if self.scope.is_trigger:
            self._trigger_references()
        self._sequences()
        self._from_dual()
        self._time_functions()
        self._repeat(self._functions)
        self._date_arithmetic()
        self._cast_types()
        self._statement_pass(self._dbms_output)
        self._statement_pass(self._raise_application_error)
        self._statement_pass(self._sleep)
        self._statement_pass(self._rowcount_assignment)
        self._statement_pass(self._insert_record)
        self._statement_pass(self._select_into)
        self._statement_pass(self._execute_immediate)
        self._statement_pass(self._transaction_control)
        self._statement_pass(self._merge)
        self._statement_pass(self._call)
        self._cursor_attributes()
        self._for_loops()
        self._exception_names()
        self._sqlcode()
        self._minus()
        self._national_literals()
        self._end_labels()
        self._detect()
        self._add_extra_declarations()
        return self.text

    def _repeat(self, step: Callable[[], int], limit: int = 8) -> None:
        for _ in range(limit):
            if not step():
                return

    # ------------------------------------------------------------- edit engine

    def _apply(self, edits: list[Edit], flags: list[Flag] | None = None) -> int:
        """Apply non-overlapping edits, then insert markers for flags. Returns edit count."""
        edits = sorted(edits, key=lambda e: e.start)
        kept: list[Edit] = []
        last_end = -1
        for edit in edits:
            if edit.start < last_end:
                continue
            kept.append(edit)
            last_end = edit.end
        pieces: list[str] = []
        pos = 0
        out_len = 0
        new_flags: list[Flag] = []
        shifts: list[tuple[int, int, int, int]] = []
        delta = 0
        for edit in kept:
            pieces.append(self.text[pos : edit.start])
            out_len += edit.start - pos
            for offset, rule, reason in edit.flags:
                new_flags.append(Flag(out_len + offset, rule, reason))
            pieces.append(edit.new)
            out_len += len(edit.new)
            before = delta
            delta += len(edit.new) - (edit.end - edit.start)
            shifts.append((edit.start, edit.end, before, delta))
            if edit.rule:
                self.applied[edit.rule] += 1
            pos = edit.end
        pieces.append(self.text[pos:])
        for flag in flags or []:
            new_flags.append(Flag(_shift(flag.offset, shifts), flag.rule, flag.reason))
        self.text = "".join(pieces)
        self._insert_markers(new_flags)
        return len(kept)

    def _sub(self, pattern: re.Pattern[str], make: Callable[[re.Match[str]], Edit | None]) -> int:
        edits = [e for m in pattern.finditer(self.text) if (e := make(m)) is not None]
        return self._apply(edits)

    def _flag(self, flags: list[Flag]) -> None:
        self._apply([], flags)

    def _insert_markers(self, flags: list[Flag]) -> None:
        if not flags:
            return
        by_line: dict[int, list[tuple[str, str | None]]] = {}
        for flag in flags:
            start = line_start(self.text, flag.offset)
            entries = by_line.setdefault(start, [])
            if all(rule != flag.rule for rule, _ in entries):
                entries.append((flag.rule, flag.reason))
        for start in sorted(by_line, reverse=True):
            existing = self._markers_above(start)
            indent = indent_at(self.text, start)
            insert = ""
            for rule, reason in by_line[start]:
                if rule in existing:
                    continue
                text = reason or get_conversion_rule(rule).description
                insert += indent + self.pool.marker(rule, text) + "\n"
            if insert:
                self.text = self.text[:start] + insert + self.text[start:]

    def _markers_above(self, start: int) -> set[str]:
        """Rules of the marker lines directly above (and on) the line at ``start``."""
        rules: set[str] = set()
        end = self.text.find("\n", start)
        line = self.text[start : end if end >= 0 else len(self.text)]
        rules.update(self.pool.marker_rules(line))
        cursor = start
        while cursor > 0:
            previous = line_start(self.text, cursor - 1)
            line = self.text[previous : cursor - 1]
            if not line.strip() or any(
                not ch.isspace() and self.pool.kind(ch) != KIND_MARKER for ch in line
            ):
                break
            rules.update(self.pool.marker_rules(line))
            cursor = previous
        return rules

    # ---------------------------------------------------------- declarations

    def _declare_sections(self) -> list[tuple[int, int]]:
        """``(start, end)`` of each DECLARE section's items (after DECLARE, before BEGIN)."""
        sections = []
        for match in re.finditer(r"\bDECLARE\b", self.text, _I):
            begin = re.compile(r"\bBEGIN\b", _I).search(self.text, match.end())
            if begin:
                sections.append((match.end(), begin.start()))
        return sections

    def _declarations(self) -> None:
        sections = self._declare_sections()
        spans: list[tuple[int, int]] = []
        for start, end in sections:
            for stmt in statements(self.text[start:end]):
                spans.append((start + stmt.start, start + stmt.end))
        # REF CURSOR types first, so variables declared with them can be mapped.
        edits: list[Edit] = []
        flags: list[Flag] = []
        for start, end in spans:
            item = self.text[start:end]
            word = first_word(item)
            if word == "TYPE":
                self._type_declaration(start, end, item, edits, flags)
            elif word == "SUBTYPE":
                flags.append(Flag(start, "O2PG-RV-009"))
        for start, end in spans:
            item = self.text[start:end]
            word = first_word(item)
            if word in {"TYPE", "SUBTYPE", "PRAGMA", "PROCEDURE", "FUNCTION", "END", "BEGIN"}:
                continue
            if word == "CURSOR":
                self._cursor_declaration(start, end, item, edits, flags)
                continue
            self._variable_declaration(start, end, item, edits, flags)
        self._apply(edits, flags)

    def _type_declaration(
        self, start: int, end: int, item: str, edits: list[Edit], flags: list[Flag]
    ) -> None:
        ref = re.match(rf"^TYPE{W}+(?P<name>{IDENT}){W}+IS{W}+REF{W}+CURSOR\b", item, _I)
        if ref:
            self.aliases[ref.group("name").upper()] = "refcursor"
            original = self.pool.restore(item)
            edits.append(
                Edit(
                    start,
                    end + 1,
                    self.pool.comment(f"o2pg: {original}; (now refcursor)"),
                    "O2PG-CV-011",
                )
            )
            return
        flags.append(Flag(start, "O2PG-RV-009"))

    def _cursor_declaration(
        self, start: int, end: int, item: str, edits: list[Edit], flags: list[Flag]
    ) -> None:
        match = re.match(
            rf"^CURSOR{W}+(?P<name>{IDENT}){W}*(?P<params>\(.*?\))?{W}*"
            rf"(?P<ret>RETURN{W}+.*?)?\bIS\b(?P<query>.*)$",
            item,
            _IS,
        )
        if not match:
            return
        self.cursors.add(match.group("name").lower())
        self.declared.add(match.group("name").lower())
        if match.group("ret"):
            flags.append(
                Flag(start, "O2PG-RV-029", "cursor with a RETURN clause: declare it without RETURN")
            )
            return
        params = ""
        if match.group("params"):
            converted = []
            for part in split_top_level(match.group("params")[1:-1]):
                param = re.match(
                    rf"^{W}*(?P<name>{IDENT}){W}+(?:IN{W}+)?(?P<type>{TYPE_PATTERN}){W}*$",
                    part,
                    _IS,
                )
                if not param:
                    flags.append(
                        Flag(
                            start,
                            "O2PG-RV-029",
                            "cursor parameter with a default or unusual form: rewrite by hand",
                        )
                    )
                    return
                mapped = map_plsql_type(param.group("type"), self.aliases)
                if mapped is None:
                    flags.append(Flag(start, "O2PG-RV-010"))
                    return
                converted.append(f"{param.group('name')} {mapped.text}")
            params = " (" + ", ".join(converted) + ")"
        new = f"{match.group('name')} CURSOR{params} FOR{match.group('query')}"
        edits.append(Edit(start, end, new, "O2PG-CV-010"))

    def _variable_declaration(
        self, start: int, end: int, item: str, edits: list[Edit], flags: list[Flag]
    ) -> None:
        pg_cursor = re.match(rf"^(?P<name>{IDENT}){W}+(?:NO{W}+)?(?:SCROLL{W}+)?CURSOR\b", item, _I)
        if pg_cursor:  # already in PL/pgSQL form: name CURSOR [(args)] FOR query
            self.cursors.add(pg_cursor.group("name").lower())
            self.declared.add(pg_cursor.group("name").lower())
            return
        match = re.match(
            rf"^(?P<name>{IDENT}){W}+(?P<const>CONSTANT{W}+)?(?P<type>{TYPE_PATTERN})(?P<rest>.*)$",
            item,
            _IS,
        )
        if not match:
            return
        name = match.group("name").lower()
        type_text = match.group("type")
        self.declared.add(name)
        if type_text.upper() == "EXCEPTION":
            self.exceptions.add(name)
            flags.append(Flag(start, "O2PG-RV-012"))
            return
        if type_text.upper() == "CURSOR":
            self.cursors.add(name)
            return
        mapped = map_plsql_type(type_text, self.aliases)
        if mapped is None:
            reason = f"no automatic mapping for type {self.pool.restore(type_text).strip()}"
            flags.append(Flag(start, "O2PG-RV-010", reason))
            return
        self.variables[name] = mapped.family
        if mapped.text != " ".join(type_text.split()):
            offset = start + match.start("type")
            edits.append(Edit(offset, offset + len(type_text), mapped.text, "O2PG-CV-004"))

    def _add_extra_declarations(self) -> None:
        if not self.extra_decls:
            return
        lines = "".join(f"  {decl}\n" for decl in self.extra_decls)
        first = re.match(rf"^{W}*DECLARE\b", self.text, _I)
        if first:
            self.text = (
                self.text[: first.end()] + "\n" + lines.rstrip("\n") + self.text[first.end() :]
            )
        else:
            self.text = "DECLARE\n" + lines + self.text.lstrip("\n")

    # ------------------------------------------------------------- triggers

    def _trigger_references(self) -> None:
        names = {n.upper(): "NEW" for n in self.scope.new_aliases}
        names.update({n.upper(): "OLD" for n in self.scope.old_aliases})

        def colon(match: re.Match[str]) -> Edit | None:
            target = names.get(match.group("name").upper())
            if target is None:
                return None
            return Edit(match.start(), match.end(), target, "O2PG-CV-030")

        self._sub(re.compile(rf":{W}*(?P<name>{IDENT})(?={W}*\.)", _I), colon)

        ops = {"INSERTING": "INSERT", "UPDATING": "UPDATE", "DELETING": "DELETE"}

        def predicate(match: re.Match[str]) -> Edit | None:
            word = match.group(1).upper()
            after = re.match(rf"{W}*\(", self.text[match.end() :])
            if after:
                return Edit(match.start(), match.start(), "", None, [(0, "O2PG-RV-023", None)])
            literal = self.pool.literal(ops[word])
            return Edit(match.start(), match.end(), f"(TG_OP = {literal})", "O2PG-CV-031")

        self._sub(re.compile(r"(?<![\w$#.])(INSERTING|UPDATING|DELETING)\b", _I), predicate)

        def bare_return(match: re.Match[str]) -> Edit | None:
            if self.scope.trigger_return is None:
                return Edit(
                    match.start(),
                    match.start(),
                    "",
                    None,
                    [
                        (
                            0,
                            "O2PG-RV-029",
                            "bare RETURN in a trigger that fires for DELETE and "
                            "other events: return NEW or OLD explicitly",
                        )
                    ],
                )
            return Edit(match.start(), match.end(), self.scope.trigger_return, "O2PG-CV-032")

        self._sub(re.compile(rf"\bRETURN{W}*;", _I), bare_return)

    # ---------------------------------------------------- expression rewrites

    def _sequences(self) -> None:
        pattern = re.compile(
            rf"(?<![\w$#.])(?:(?P<owner>{IDENT}){W}*\.{W}*)?(?P<seq>{IDENT}){W}*\.{W}*"
            r"(?P<op>NEXTVAL|CURRVAL)\b",
            _I,
        )

        def make(match: re.Match[str]) -> Edit:
            owner, seq = match.group("owner"), match.group("seq")
            name = f"{ident(owner)}.{ident(seq)}" if owner else ident(seq)
            func = match.group("op").lower()
            return Edit(
                match.start(), match.end(), f"{func}({self.pool.literal(name)})", "O2PG-CV-020"
            )

        self._sub(pattern, make)

    def _from_dual(self) -> None:
        pattern = re.compile(
            rf"{W}+FROM{W}+(?:SYS{W}*\.{W}*)?DUAL\b(?!{W}*\.)(?={W}*(?:;|\)|$|WHERE\b|UNION\b"
            r"|INTERSECT\b|EXCEPT\b|MINUS\b|ORDER\b|GROUP\b|INTO\b))",
            _I,
        )
        self._sub(pattern, lambda m: Edit(m.start(), m.end(), "", "O2PG-CV-021"))

    def _time_functions(self) -> None:
        def make(match: re.Match[str]) -> Edit:
            word = match.group(1).upper()
            if word == "SYSTIMESTAMP":
                return Edit(match.start(), match.end(), "clock_timestamp()", "O2PG-CV-019")
            return Edit(match.start(), match.end(), "LOCALTIMESTAMP(0)", "O2PG-CV-018")

        self._sub(
            re.compile(rf"(?<![\w$#.])(SYSDATE|SYSTIMESTAMP|CURRENT_DATE)\b(?!{W}*\()", _I), make
        )

    def _calls(self, names: str) -> list[tuple[re.Match[str], int, int, list[str]]]:
        """``(match, open_paren, close_paren, args)`` for calls to any of ``names``."""
        found = []
        pattern = re.compile(rf"(?<![\w$#.])(?P<name>{names}){W}*\(", _I)
        for match in pattern.finditer(self.text):
            open_index = match.end() - 1
            close = matching_paren(self.text, open_index)
            if close is None:
                continue
            args = split_top_level(self.text[open_index + 1 : close])
            found.append((match, open_index, close, args))
        return found

    def _functions(self) -> int:
        edits: list[Edit] = []
        flags: list[Flag] = []
        for match, _open, close, args in self._calls(
            r"NVL|NVL2|DECODE|TRUNC|ADD_MONTHS|TO_DATE|TO_CHAR|TO_NUMBER|DBMS_LOB\s*\.\s*SUBSTR"
            r"|DBMS_LOB\s*\.\s*GETLENGTH|SYS_GUID|INSTR|SUBSTR|REPLACE"
        ):
            name = re.sub(r"\s+", "", match.group("name").upper())
            edit = self._function(name, match, close, args, flags)
            if edit is not None:
                edits.append(edit)
        return self._apply(edits, flags) if edits else (self._flag(flags) or 0)

    def _function(
        self, name: str, match: re.Match[str], close: int, args: list[str], flags: list[Flag]
    ) -> Edit | None:
        start, end = match.start(), close + 1
        stripped = [a.strip() for a in args]
        if name == "NVL" and len(args) == 2:
            return Edit(start, match.end() - 1, "COALESCE", "O2PG-CV-015")
        if name == "NVL2" and len(args) == 3:
            a, b, c = stripped
            return Edit(
                start, end, f"CASE WHEN {a} IS NOT NULL THEN {b} ELSE {c} END", "O2PG-CV-016"
            )
        if name == "DECODE" and len(args) >= 3:
            return self._decode(start, end, stripped, flags)
        if name == "TRUNC":
            return self._trunc(start, end, stripped, flags)
        if name == "ADD_MONTHS" and len(args) == 2:
            d, n = stripped
            return Edit(
                start, end, f"({d} + interval {self._lit('1 month')} * ({n}))", "O2PG-CV-038"
            )
        if name == "TO_DATE":
            if len(args) == 1:
                flags.append(Flag(start, "O2PG-RV-043"))
                return None
            if len(args) == 2:
                return Edit(
                    start,
                    end,
                    f"to_timestamp({stripped[0]}, {stripped[1]})::timestamp(0)",
                    "O2PG-CV-040",
                )
            return None
        if name in {"TO_CHAR", "TO_NUMBER"} and len(args) == 1 and stripped[0]:
            target = "text" if name == "TO_CHAR" else "numeric"
            return Edit(start, end, f"CAST({stripped[0]} AS {target})", "O2PG-CV-041")
        if name == "DBMS_LOB.SUBSTR" and 1 <= len(args) <= 3:
            lob = stripped[0]
            amount = stripped[1] if len(args) > 1 else "32767"
            offset = stripped[2] if len(args) > 2 else "1"
            return Edit(start, end, f"substr({lob}, {offset}, {amount})", "O2PG-CV-042")
        if name == "DBMS_LOB.GETLENGTH" and len(args) == 1:
            return Edit(start, end, f"length({stripped[0]})", "O2PG-CV-042")
        if name == "SYS_GUID" and not stripped[0]:
            return Edit(start, end, "gen_random_uuid()", "O2PG-CV-044")
        if name == "INSTR":
            if len(args) == 2:
                return Edit(start, end, f"strpos({stripped[0]}, {stripped[1]})", "O2PG-CV-045")
            flags.append(Flag(start, "O2PG-RV-044"))
            return None
        if name == "SUBSTR" and len(args) in {2, 3}:
            position = stripped[1]
            if position == "0":
                rest = f", {stripped[2]}" if len(args) == 3 else ""
                return Edit(start, end, f"substr({stripped[0]}, 1{rest})", "O2PG-CV-052")
            if position.startswith("-"):
                flags.append(Flag(start, "O2PG-RV-030"))
            return None
        if name == "REPLACE" and len(args) == 2:
            return Edit(
                start, end, f"replace({stripped[0]}, {stripped[1]}, {self._lit('')})", "O2PG-CV-053"
            )
        return None

    def _lit(self, value: str) -> str:
        return self.pool.literal(value)

    def _is_literal(self, text: str) -> bool:
        return bool(_NUMBER.match(text.lstrip("-"))) or self.pool.is_string(text)

    def _is_null(self, text: str) -> bool:
        return text.upper() == "NULL" or (len(text) == 1 and self.pool.kind(text) == KIND_EMPTY)

    def _decode(self, start: int, end: int, args: list[str], flags: list[Flag]) -> Edit | None:
        subject, rest = args[0], args[1:]
        default = rest.pop() if len(rest) % 2 == 1 else None
        pairs = list(zip(rest[0::2], rest[1::2], strict=True))
        else_part = f" ELSE {default}" if default is not None else ""
        if all(self._is_literal(s) and not self._is_null(s) for s, _ in pairs):
            whens = " ".join(f"WHEN {s} THEN {r}" for s, r in pairs)
            return Edit(start, end, f"CASE {subject} {whens}{else_part} END", "O2PG-CV-017")
        if not (_SIMPLE_NAME.match(subject) or self._is_literal(subject)):
            flags.append(Flag(start, "O2PG-RV-022"))
            return None
        whens = []
        for search, result in pairs:
            if self._is_null(search):
                whens.append(f"WHEN {subject} IS NULL THEN {result}")
            elif self._is_literal(search):
                whens.append(f"WHEN {subject} = {search} THEN {result}")
            else:
                whens.append(f"WHEN {subject} IS NOT DISTINCT FROM {search} THEN {result}")
        return Edit(start, end, f"CASE {' '.join(whens)}{else_part} END", "O2PG-CV-017")

    def _is_dateish(self, expr: str) -> bool:
        expr = expr.strip()
        if not expr:
            return False
        if _TIME_ATOM.match(expr):
            return True
        if (
            re.match(rf"^date_trunc{W}*\(", expr, _I)
            and matching_paren(expr, expr.index("(")) == len(expr) - 1
        ):
            return True
        if expr.startswith("(") and matching_paren(expr, 0) == len(expr) - 1:
            inner = expr[1:-1].strip()
            head = re.match(rf"^(.*?){W}*[+-]{W}*interval\b", inner, _IS)
            return self._is_dateish(head.group(1) if head else inner)
        return self.variables.get(expr.lower()) == "date"

    def _trunc(self, start: int, end: int, args: list[str], flags: list[Flag]) -> Edit | None:
        if len(args) not in {1, 2} or not args[0]:
            return None
        value = args[0]
        if not self._is_dateish(value):
            family = self.variables.get(value.lower())
            numeric = family in {"numeric", "integer"} or bool(_NUMBER.match(value))
            arithmetic = bool(re.search(r"[*/]", value))
            if len(args) == 1 and not numeric and not arithmetic:
                flags.append(Flag(start, "O2PG-RV-038"))
            return None
        unit = "day"
        if len(args) == 2:
            fmt = self.pool.string_value(args[1])
            if fmt is None or fmt.upper() not in _TRUNC_UNITS or fmt.upper() in _UNSAFE_TRUNC_UNITS:
                flags.append(
                    Flag(
                        start,
                        "O2PG-RV-038",
                        "TRUNC with this format model: use date_trunc() with the matching field",
                    )
                )
                return None
            unit = _TRUNC_UNITS[fmt.upper()]
        return Edit(start, end, f"date_trunc({self._lit(unit)}, {value})", "O2PG-CV-037")

    def _cast_types(self) -> None:
        edits: list[Edit] = []
        flags: list[Flag] = []
        for match, open_index, close, _args in self._calls("CAST"):
            inner = self.text[open_index + 1 : close]
            found = top_level(re.compile(r"\bAS\b", _I), inner)
            if not found:
                continue
            type_start = open_index + 1 + found[-1].end()
            type_text = self.text[type_start:close]
            stripped = type_text.strip()
            mapped = map_plsql_type(stripped, self.aliases)
            if mapped is None:
                flags.append(
                    Flag(
                        match.start(),
                        "O2PG-RV-010",
                        f"no automatic mapping for CAST type {self.pool.restore(stripped)}",
                    )
                )
                continue
            if mapped.text != " ".join(stripped.split()):
                edits.append(Edit(type_start, close, f" {mapped.text}", "O2PG-CV-004"))
        self._apply(edits, flags)

    # ------------------------------------------------------ date arithmetic

    def _date_arithmetic(self) -> None:
        edits: list[Edit] = []
        flags: list[Flag] = []
        text = self.text
        for match in re.finditer(r"(?<![+\-*/<>=!|])([+\-])(?![+\-=>])", text):
            op_index = match.start()
            left = _left_operand(text, op_index)
            if left is None or not self._is_dateish(text[left:op_index]):
                continue
            right = _right_operand(text, op_index + 1)
            if right is None:
                continue
            r_start, r_end = right
            operand = text[r_start:r_end].strip()
            if re.match(r"^interval\b", operand, _I):
                continue
            if self._is_dateish(operand):
                flags.append(Flag(op_index, "O2PG-RV-039"))
                continue
            family = self.variables.get(operand.lower())
            parenthesised = operand.startswith("(")
            if (
                _NUMBER.match(operand)
                or family in {"numeric", "integer"}
                or (parenthesised and "interval" not in operand.lower())
            ):
                day = self._lit("1 day")
                new = f"interval {day}" if operand == "1" else f"interval {day} * {operand}"
                edits.append(Edit(r_start, r_end, new, "O2PG-CV-039"))
            else:
                flags.append(Flag(op_index, "O2PG-RV-039"))
        self._apply(edits, flags)

    # --------------------------------------------------------- statements

    def _statement_pass(self, handler: Callable[[int, int, str], Edit | None]) -> None:
        edits: list[Edit] = []
        for stmt in statements(self.text):
            if stmt.start >= stmt.end:
                continue
            edit = handler(stmt.start, stmt.end, self.text[stmt.start : stmt.end])
            if edit is not None:
                edits.append(edit)
        self._apply(edits)

    def _dbms_output(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(rf"^DBMS_OUTPUT{W}*\.{W}*PUT_LINE{W}*\(", stmt, _I)
        if not match:
            return None
        close = matching_paren(stmt, match.end() - 1)
        if close is None or stmt[close + 1 :].strip():
            return None
        arg = stmt[match.end() : close].strip() or self._lit("")
        return Edit(start, end, f"RAISE NOTICE {self._lit('%')}, {arg}", "O2PG-CV-024")

    def _raise_application_error(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(rf"^RAISE_APPLICATION_ERROR{W}*\(", stmt, _I)
        if not match:
            return None
        close = matching_paren(stmt, match.end() - 1)
        if close is None or stmt[close + 1 :].strip():
            return None
        args = [a.strip() for a in split_top_level(stmt[match.end() : close])]
        if len(args) not in {2, 3}:
            return None
        code, message = args[0], args[1]
        compact = re.sub(r"\s+", "", code)
        if re.match(r"^-?\d+$", compact):
            hint = self._lit(f"Oracle error code {compact}")
        else:
            hint = f"{self._lit('Oracle error code ')} || ({code})"
        new = (
            f"RAISE EXCEPTION USING ERRCODE = {self._lit('P0001')}, MESSAGE = {message}, "
            f"HINT = {hint}"
        )
        return Edit(start, end, new, "O2PG-CV-025")

    def _sleep(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(rf"^DBMS_(?:LOCK|SESSION){W}*\.{W}*SLEEP{W}*\(", stmt, _I)
        if not match:
            return None
        close = matching_paren(stmt, match.end() - 1)
        if close is None or stmt[close + 1 :].strip():
            return None
        return Edit(
            start, end, f"PERFORM pg_sleep({stmt[match.end() : close].strip()})", "O2PG-CV-043"
        )

    def _rowcount_assignment(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(
            rf"^(?P<target>{IDENT}(?:\.{IDENT})?){W}*:={W}*SQL{W}*%{W}*ROWCOUNT{W}*$", stmt, _I
        )
        if not match:
            return None
        return Edit(
            start, end, f"GET DIAGNOSTICS {match.group('target')} = ROW_COUNT", "O2PG-CV-035"
        )

    def _insert_record(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(
            rf"^(?P<head>INSERT{W}+INTO{W}+.+?{W}VALUES{W}+)(?P<rec>{IDENT}){W}*$", stmt, _IS
        )
        if not match or match.group("rec").upper() in {"NULL", "DEFAULT"}:
            return None
        return Edit(start, end, f"{match.group('head')}({match.group('rec')}.*)", "O2PG-CV-048")

    def _select_into(self, start: int, end: int, stmt: str) -> Edit | None:
        if first_word(stmt) not in {"SELECT", "WITH"}:
            return None
        into = top_level(re.compile(r"\bINTO\b", _I), stmt)
        if not into:
            return None
        at = into[0]
        before = stmt[: at.start()]
        if re.search(rf"\bBULK{W}+COLLECT{W}*$", before, _I):
            return None
        after = stmt[at.end() :]
        if re.match(rf"{W}+STRICT\b", after, _I):
            return None
        has_from = top_level(re.compile(r"\bFROM\b", _I), stmt, at.end())
        if has_from:
            return Edit(start + at.end(), start + at.end(), " STRICT", "O2PG-CV-023")
        select = re.match(rf"^SELECT{W}+(?P<expr>.+?){W}+$", before, _IS)
        target = re.match(rf"^{W}+(?P<target>{IDENT}(?:\.{IDENT})?){W}*$", after, _I)
        if select and target and len(split_top_level(select.group("expr"))) == 1:
            expr = select.group("expr").strip()
            if not re.search(r"\b(?:DISTINCT|UNIQUE|ALL)\b", expr[:9], _I):
                return Edit(start, end, f"{target.group('target')} := {expr}", "O2PG-CV-022")
        return None

    def _execute_immediate(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(rf"^EXECUTE{W}+IMMEDIATE\b", stmt, _I)
        if not match:
            return None
        rest = stmt[match.end() :]
        flags: list[tuple[int, str, str | None]] = [(0, "O2PG-RV-033", None)]
        using = re.search(r"\bUSING\b", rest, _I)
        if using and re.search(rf"\b(?:OUT|IN{W}+OUT)\b", rest[using.end() :], _I):
            flags.append((0, "O2PG-RV-033", None))
        into = top_level(re.compile(r"\bINTO\b", _I), rest)
        if (
            into
            and not re.match(rf"{W}+STRICT\b", rest[into[0].end() :], _I)
            and not re.search(rf"\bBULK{W}+COLLECT{W}*$", rest[: into[0].start()], _I)
        ):
            rest = rest[: into[0].end()] + " STRICT" + rest[into[0].end() :]
            self.applied["O2PG-CV-023"] += 1
        return Edit(start, end, "EXECUTE" + rest, "O2PG-CV-028", flags)

    def _transaction_control(self, start: int, end: int, stmt: str) -> Edit | None:
        word = first_word(stmt)
        if word == "SAVEPOINT" or (
            word == "ROLLBACK" and re.search(rf"^ROLLBACK(?:{W}+WORK)?{W}+TO\b", stmt, _I)
        ):
            return Edit(start, start, "", None, [(0, "O2PG-RV-017", None)])
        if word == "SET" and re.match(rf"^SET{W}+TRANSACTION\b", stmt, _I):
            return Edit(start, start, "", None, [(0, "O2PG-RV-017", None)])
        if word not in {"COMMIT", "ROLLBACK"}:
            return None
        if not re.match(rf"^(?:COMMIT|ROLLBACK)(?:{W}+WORK)?{W}*$", stmt, _I):
            return Edit(start, start, "", None, [(0, "O2PG-RV-017", None)])
        if self.scope.routine_kind != "PROCEDURE":
            return Edit(start, start, "", None, [(0, "O2PG-RV-016", None)])
        self.applied["O2PG-CV-029"] += 1
        if re.search(r"\bWORK\b", stmt, _I):
            return Edit(start, end, word)
        return None

    def _merge(self, start: int, end: int, stmt: str) -> Edit | None:
        if first_word(stmt) != "MERGE":
            return None
        flags: list[tuple[int, str, str | None]] = [(0, "O2PG-RV-031", None)]
        head = re.match(
            rf"^MERGE{W}+INTO{W}+(?:{IDENT}{W}*\.{W}*)?{IDENT}(?:{W}+(?!USING\b)(?P<alias>{IDENT}))?",
            stmt,
            _I,
        )
        new = stmt
        alias = head.group("alias") if head else None
        set_clause = re.search(rf"\bUPDATE{W}+SET\b", stmt, _I)
        if set_clause:
            stop = re.compile(r"\bWHEN\b|\bWHERE\b|\bDELETE\b", _I)
            stops = top_level(stop, stmt, set_clause.end())
            seg_end = stops[0].start() if stops else len(stmt)
            tail = stmt[seg_end:]
            conditional = re.match(r"WHERE\b|DELETE\b", tail, _I) or re.search(
                rf"\bTHEN{W}+DELETE{W}+WHERE\b", tail, _I
            )
            if conditional:
                flags.append((0, "O2PG-RV-032", None))
            if alias:
                segment = stmt[set_clause.end() : seg_end]
                pattern = re.compile(
                    rf"(?P<lead>(?:^|,){W}*){re.escape(alias)}{W}*\.{W}*(?P<col>{IDENT})(?={W}*=)",
                    _I,
                )
                fixed, count = pattern.subn(r"\g<lead>\g<col>", segment)
                if count:
                    new = stmt[: set_clause.end()] + fixed + stmt[seg_end:]
                    self.applied["O2PG-CV-047"] += count
        if re.search(rf"\bTHEN{W}+DELETE{W}+WHERE\b|\bDELETE{W}+WHERE\b", stmt, _I) and not any(
            r == "O2PG-RV-032" for _, r, _ in flags
        ):
            flags.append((0, "O2PG-RV-032", None))
        return Edit(start, end, new, None, flags)

    def _call(self, start: int, end: int, stmt: str) -> Edit | None:
        match = re.match(rf"^(?P<name>{IDENT}(?:{W}*\.{W}*{IDENT}){{0,2}})(?P<rest>.*)$", stmt, _IS)
        if not match:
            return None
        name = re.sub(r"\s+", "", match.group("name"))
        parts = name.upper().split(".")
        if parts[0] in _KEYWORD_STATEMENTS or parts[-1] in _COLLECTION_METHODS:
            return None
        if _ORACLE_PACKAGE.match(name + "."):
            return None
        rest = match.group("rest")
        stripped = rest.strip()
        if not stripped:
            return Edit(start, end, f"CALL {name}()", "O2PG-CV-036")
        if not stripped.startswith("("):
            return None
        open_index = rest.index("(")
        close = matching_paren(rest, open_index)
        if close is None or rest[close + 1 :].strip():
            return None
        return Edit(start, end, f"CALL {name}{rest.rstrip()}", "O2PG-CV-036")

    # ------------------------------------------------------- other passes

    def _cursor_attributes(self) -> None:
        def make(match: re.Match[str]) -> Edit | None:
            owner = match.group("owner").upper()
            attr = match.group("attr").upper()
            if owner != "SQL" and owner.lower() not in self.cursors and attr in {"ISOPEN"}:
                return None
            if attr == "FOUND":
                return Edit(match.start(), match.end(), "FOUND", "O2PG-CV-035")
            if attr == "NOTFOUND":
                return Edit(match.start(), match.end(), "NOT FOUND", "O2PG-CV-035")
            if attr == "ISOPEN" and owner == "SQL":
                return Edit(match.start(), match.end(), "false", "O2PG-CV-035")
            return None

        self._sub(
            re.compile(
                rf"(?<![\w$#.])(?P<owner>{IDENT}){W}*%{W}*(?P<attr>FOUND|NOTFOUND|ISOPEN)\b", _I
            ),
            make,
        )

    def _for_loops(self) -> None:
        edits: list[Edit] = []
        pattern = re.compile(rf"\bFOR{W}+(?P<var>{IDENT}){W}+IN{W}+(?P<rev>REVERSE{W}+)?", _I)
        for match in pattern.finditer(self.text):
            after = self.text[match.end() :]
            if match.group("rev"):
                edit = self._reverse_bounds(match.end())
                if edit:
                    edits.append(edit)
                continue
            if re.match(rf"\({W}*(?:SELECT|WITH)\b", after, _I):
                var = match.group("var").lower()
                if var not in self.declared:
                    self.declared.add(var)
                    self.extra_decls.append(f"{match.group('var')} record;")
                    self.applied["O2PG-CV-033"] += 1
        self._apply(edits)

    def _reverse_bounds(self, start: int) -> Edit | None:
        loop = re.compile(r"\bLOOP\b", _I).search(self.text, start)
        if not loop:
            return None
        span = self.text[start : loop.start()]
        if any(self.pool.token(ch) and "bounds swapped" in self.pool.token(ch).text for ch in span):  # type: ignore[union-attr]
            return None
        dots = top_level(re.compile(r"\.\."), span)
        if len(dots) != 1:
            return None
        low, high = span[: dots[0].start()].strip(), span[dots[0].end() :].strip()
        note = self.pool.comment("o2pg: bounds swapped for PostgreSQL")
        return Edit(start, loop.start(), f"{high} .. {low} {note} ", "O2PG-CV-034")

    def _exception_names(self) -> None:
        def make(match: re.Match[str]) -> Edit | None:
            word = match.group(1)
            target = _EXCEPTION_NAMES[word.upper()]
            if word.lower() == target:
                return None
            rule = "O2PG-CV-026" if word.upper() != target.upper() else None
            return Edit(match.start(), match.end(), target, rule)

        names = "|".join(_EXCEPTION_NAMES)
        self._sub(re.compile(rf"(?<![\w$#.])({names})\b", _I), make)
        if self.exceptions:
            names = "|".join(re.escape(n) for n in sorted(self.exceptions))
            self._flag(
                [
                    Flag(m.start(), "O2PG-RV-012")
                    for m in re.finditer(rf"(?<![\w$#.])(?:{names})\b", self.text, _I)
                ]
            )

    def _sqlcode(self) -> None:
        self._sub(
            re.compile(r"(?<![\w$#.])SQLCODE\b", _I),
            lambda m: Edit(
                m.start(), m.end(), "SQLSTATE", "O2PG-CV-027", [(0, "O2PG-RV-036", None)]
            ),
        )

    def _national_literals(self) -> None:
        def make(match: re.Match[str]) -> Edit | None:
            literal = match.group("lit")
            if self.pool.kind(literal) != KIND_STRING:
                return None
            return Edit(match.start(), match.end(), literal, "O2PG-CV-050")

        self._sub(re.compile(rf"(?<![\w$#.])[nN](?P<lit>{LIT})"), make)

    def _minus(self) -> None:
        self._sub(
            re.compile(r"(?<![\w$#.])MINUS\b", _I),
            lambda m: Edit(m.start(), m.end(), "EXCEPT", "O2PG-CV-046"),
        )

    def _end_labels(self) -> None:
        labels = {m.group(1).upper() for m in re.finditer(rf"<<{W}*({IDENT}){W}*>>", self.text)}

        def make(match: re.Match[str]) -> Edit | None:
            name = match.group("name").upper()
            if name in {"IF", "LOOP", "CASE"} or name in labels:
                return None
            return Edit(match.start(), match.end(), "END;", "O2PG-CV-006")

        self._sub(re.compile(rf"\bEND{W}+(?P<name>{IDENT}){W}*;", _I), make)

    def _detect(self) -> None:
        flags: list[Flag] = []
        for pattern, rule in _DETECT:
            flags += [Flag(m.start(), rule) for m in pattern.finditer(self.text)]
        for match in _ORACLE_PACKAGE.finditer(self.text):
            pkg = match.group("pkg").upper()
            advice = _PACKAGE_ADVICE.get(
                pkg, f"{pkg}: Oracle-supplied package with no direct PostgreSQL equivalent"
            )
            flags.append(Flag(match.start("pkg"), "O2PG-RV-014", advice))
        for match in _ORACLE_FUNCTIONS.finditer(self.text):
            name = (match.group("name") or match.group("bare")).upper()
            advice = _FUNCTION_ADVICE.get(
                name,
                f"{name} has no PostgreSQL built-in (orafce "
                "provides some Oracle functions on Azure)",
            )
            flags.append(Flag(match.start(), "O2PG-RV-015", advice))
        flags += self._package_state_flags()
        flags += self._empty_string_flags()
        flags += self._concatenation_flags()
        flags += self._integer_division_flags()
        self._flag(flags)

    def _package_state_flags(self) -> list[Flag]:
        flags = []
        for name in sorted(self.scope.package_vars):
            if name in self.declared:
                continue
            pattern = re.compile(rf"(?<![\w$#.]){re.escape(name)}\b(?!{W}*\()", _I)
            reason = (
                f"package variable {name}: PostgreSQL has no package state; keep it in a table, "
                "a temporary table, or a custom setting via set_config()/current_setting()"
            )
            flags += [Flag(m.start(), "O2PG-RV-011", reason) for m in pattern.finditer(self.text)]
        for qualified in sorted(self.scope.qualified_package_vars):
            pkg, var = qualified.split(".", 1)
            pattern = re.compile(rf"(?<![\w$#.]){re.escape(pkg)}{W}*\.{W}*{re.escape(var)}\b", _I)
            reason = (
                f"package variable {qualified}: PostgreSQL has no package state; keep it in a "
                "table or a custom setting via set_config()/current_setting()"
            )
            flags += [Flag(m.start(), "O2PG-RV-011", reason) for m in pattern.finditer(self.text)]
        return flags

    def _empty_string_flags(self) -> list[Flag]:
        return [
            Flag(i, "O2PG-RV-034")
            for i, ch in enumerate(self.text)
            if ord(ch) >= 0x100000 and self.pool.kind(ch) == KIND_EMPTY
        ]

    def _concatenation_flags(self) -> list[Flag]:
        flags = []
        for match in re.finditer(r"\|\|", self.text):
            before = re.search(
                rf"(\S){W}*$", self.text[max(0, match.start() - 200) : match.start()]
            )
            after = re.match(rf"{W}*(\S)", self.text[match.end() :])
            prev_char = before.group(1) if before else ""
            next_char = after.group(1) if after else ""
            if self._literal_char(prev_char) and self._literal_char(next_char):
                continue
            flags.append(Flag(match.start(), "O2PG-RV-035"))
        return flags

    def _literal_char(self, char: str) -> bool:
        return bool(char) and self.pool.kind(char) == KIND_STRING

    def _integer_division_flags(self) -> list[Flag]:
        flags = []
        for match in re.finditer(r"/", self.text):
            left = _left_operand(self.text, match.start())
            right = _right_operand(self.text, match.end())
            if left is None or right is None:
                continue
            if self._integerish(self.text[left : match.start()].strip()) and self._integerish(
                self.text[right[0] : right[1]].strip()
            ):
                flags.append(Flag(match.start(), "O2PG-RV-040"))
        return flags

    def _integerish(self, operand: str) -> bool:
        return bool(_INTEGER.match(operand)) or self.variables.get(operand.lower()) == "integer"


def _shift(offset: int, shifts: list[tuple[int, int, int, int]]) -> int:
    """Map an offset in the old text to the new text after edits.

    ``shifts`` holds ``(old_start, old_end, delta_before, delta_after)`` per edit.
    """
    delta = 0
    for old_start, old_end, before, after in shifts:
        if offset < old_start:
            break
        if offset < old_end:
            return old_start + before
        delta = after
    return offset + delta


_OPERAND_CHARS = re.compile(r"[\w$#.]")


def _left_operand(text: str, op_index: int) -> int | None:
    """Start index of the operand that ends just before ``text[op_index]``."""
    index = op_index - 1
    while index >= 0 and (text[index].isspace() or 0xF0000 <= ord(text[index]) <= 0xFFFFD):
        index -= 1
    if index < 0:
        return None
    if text[index] == ")":
        depth = 0
        while index >= 0:
            if text[index] == ")":
                depth += 1
            elif text[index] == "(":
                depth -= 1
                if depth == 0:
                    break
            index -= 1
        if index < 0:
            return None
        start = index
        while start > 0 and _OPERAND_CHARS.match(text[start - 1]):
            start -= 1
        return start
    if not _OPERAND_CHARS.match(text[index]):
        return None
    start = index
    while start > 0 and _OPERAND_CHARS.match(text[start - 1]):
        start -= 1
    word = text[start : index + 1].upper()
    if word in {"RETURN", "THEN", "ELSE", "WHEN", "AND", "OR", "NOT", "IN", "SELECT", "IS"}:
        return None
    return start


_RIGHT = re.compile(rf"{W}*(?P<atom>\d+(?:\.\d+)?|{IDENT}(?:\.{IDENT})*|\(|{LIT})")


def _right_operand(text: str, index: int) -> tuple[int, int] | None:
    """``(start, end)`` of the operand starting at or after ``index``."""
    match = _RIGHT.match(text, index)
    if not match:
        return None
    start = match.start("atom")
    if match.group("atom") == "(":
        close = matching_paren(text, start)
        return None if close is None else (start, close + 1)
    end = match.end("atom")
    if end < len(text) and text[end] == "(":
        close = matching_paren(text, end)
        if close is None:
            return None
        end = close + 1
    return start, end


def rewrite_block(
    text: str, routine_kind: str = "FUNCTION", **scope: object
) -> tuple[str, Counter[str]]:
    """Rewrite plain-text PL/SQL (a block or a fragment) and return ``(sql, rewrites)``.

    A convenience wrapper used by tests and for experimenting: it masks ``text``,
    runs :class:`BlockRewriter` and restores the result.
    """
    pool = TokenPool()
    rewriter = BlockRewriter(pool.mask(text), Scope(pool=pool, routine_kind=routine_kind, **scope))  # type: ignore[arg-type]
    masked = rewriter.run()
    counter = Counter(rewriter.applied)
    return pool.restore(masked, counter), counter


__all__ = ["BlockRewriter", "Scope", "rewrite_block"]
