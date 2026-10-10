"""Masking and small structural helpers for the converter.

The converter never rewrites inside comments or string literals. Before any rule
runs, every comment, string literal and quoted identifier is swapped for a single
placeholder character from a Unicode private use plane, using the same lexer as
the assessment rules (:data:`o2pg.plsql.LEXICAL_TOKEN`). Placeholders are not
word characters, so ``\\bNVL\\b`` can never match inside a literal, and they are
restored verbatim (or normalised for PostgreSQL) at the end.

* Comments (and review markers the converter adds) live in plane 15, so the
  pattern :data:`W` treats them like whitespace.
* String literals and quoted identifiers live in plane 16 (:data:`LIT`).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from o2pg.ddl import ident
from o2pg.plsql import LEXICAL_TOKEN

_COMMENT_BASE = 0xF0000
_COMMENT_LIMIT = 0xFFFFD
_LITERAL_BASE = 0x100000
_LITERAL_LIMIT = 0x10FFFD

W = r"[\s\U000F0000-\U000FFFFD]"
"""Regex class: whitespace or a comment placeholder."""

LIT = r"[\U00100000-\U0010FFFD]"
"""Regex class: a string literal or quoted identifier placeholder."""

IDENT = r"[A-Za-z_][\w$#]*"
"""An unquoted Oracle identifier."""

MARKER_PREFIX = "/* O2PG-REVIEW "
_MARKER = re.compile(r"/\* O2PG-REVIEW (?P<rule>O2PG-RV-\d{3}): (?P<reason>.*?) \*/")
_QQUOTE = re.compile(r"^[nN]?[qQ]'(.)(.*)(.)'$", re.DOTALL)
_SIMPLE_QIDENT = re.compile(r'^"([A-Za-z_][\w$#]*)"$')


class ConversionError(ValueError):
    """Raised when source text cannot be processed at all."""


@dataclass(frozen=True)
class Token:
    """A hidden piece of source text. ``kind`` is one of the ``KIND_*`` names."""

    kind: str
    text: str


KIND_COMMENT = "comment"
KIND_MARKER = "marker"
KIND_ANCHOR = "anchor"
KIND_STRING = "string"
KIND_EMPTY = "empty"
KIND_QIDENT = "qident"


class TokenPool:
    """Owns the placeholder table for one conversion (an object and its parts)."""

    def __init__(self) -> None:
        self._comments: list[Token] = []
        self._literals: list[Token] = []

    # -- creating placeholders -------------------------------------------------

    def _add_comment(self, token: Token) -> str:
        code = _COMMENT_BASE + len(self._comments)
        if code > _COMMENT_LIMIT:
            raise ConversionError("too many comments in one object")
        self._comments.append(token)
        return chr(code)

    def _add_literal(self, token: Token) -> str:
        code = _LITERAL_BASE + len(self._literals)
        if code > _LITERAL_LIMIT:
            raise ConversionError("too many string literals in one object")
        self._literals.append(token)
        return chr(code)

    def mask(self, text: str) -> str:
        """Replace comments, literals and quoted identifiers with placeholders."""
        return LEXICAL_TOKEN.sub(self._mask_one, text)

    def _mask_one(self, match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith(MARKER_PREFIX) and _MARKER.match(token):
            return self._add_comment(Token(KIND_MARKER, token))
        if token.startswith(("--", "/*")):
            return self._add_comment(Token(KIND_COMMENT, token))
        if token.startswith('"'):
            return self._add_literal(Token(KIND_QIDENT, token))
        if token == "''":
            return self._add_literal(Token(KIND_EMPTY, token))
        return self._add_literal(Token(KIND_STRING, token))

    def literal(self, value: str) -> str:
        """A placeholder for a new PostgreSQL string literal holding ``value``."""
        return self._add_literal(Token(KIND_STRING, "'" + value.replace("'", "''") + "'"))

    def comment(self, text: str) -> str:
        """A placeholder for a new block comment (``text`` without the delimiters)."""
        return self._add_comment(Token(KIND_COMMENT, "/* " + text.replace("*/", "* /") + " */"))

    def marker(self, rule_id: str, reason: str) -> str:
        """A placeholder for an ``O2PG-REVIEW`` marker comment."""
        clean = " ".join(reason.replace("*/", "* /").split())
        return self._add_comment(Token(KIND_MARKER, f"{MARKER_PREFIX}{rule_id}: {clean} */"))

    def anchor(self) -> str:
        """An invisible placeholder used to remember a position across edits."""
        return self._add_comment(Token(KIND_ANCHOR, ""))

    # -- inspecting placeholders -----------------------------------------------

    def token(self, char: str) -> Token | None:
        """The token behind a placeholder character, or None for ordinary text."""
        code = ord(char) if len(char) == 1 else -1
        if _COMMENT_BASE <= code <= _COMMENT_LIMIT:
            index = code - _COMMENT_BASE
            return self._comments[index] if index < len(self._comments) else None
        if _LITERAL_BASE <= code <= _LITERAL_LIMIT:
            index = code - _LITERAL_BASE
            return self._literals[index] if index < len(self._literals) else None
        return None

    def kind(self, char: str) -> str | None:
        """Kind of the token behind ``char`` (None for ordinary text)."""
        token = self.token(char)
        return token.kind if token else None

    def is_string(self, text: str) -> bool:
        """True when ``text`` (stripped) is exactly one non-empty string literal."""
        text = text.strip()
        return len(text) == 1 and self.kind(text) == KIND_STRING

    def string_value(self, text: str) -> str | None:
        """The value of a single ordinary string literal placeholder, if it is one."""
        text = text.strip()
        if not self.is_string(text):
            return None
        token = self.token(text)
        assert token is not None
        raw = _normalise_literal(token.text)
        return raw[1:-1].replace("''", "'")

    def marker_rules(self, text: str) -> list[str]:
        """Rule ids of the review markers present in ``text``."""
        rules = []
        for char in text:
            token = self.token(char)
            if token and token.kind == KIND_MARKER:
                found = _MARKER.match(token.text)
                if found:
                    rules.append(found.group("rule"))
        return rules

    def has_marker(self, text: str) -> bool:
        """True when ``text`` contains at least one review marker."""
        return any(self.kind(char) == KIND_MARKER for char in text if ord(char) >= _COMMENT_BASE)

    def restore(self, text: str, counter: Counter[str] | None = None) -> str:
        """Put the hidden tokens back, normalising them for PostgreSQL.

        ``counter`` receives one count per normalisation, keyed by conversion
        rule id (quoted identifiers, ``q'[...]'`` literals, nested comments).
        """
        out: list[str] = []
        for char in text:
            if ord(char) < _COMMENT_BASE:
                out.append(char)
                continue
            token = self.token(char)
            if token is None:
                out.append(char)
                continue
            out.append(self._restored(token, counter))
        return "".join(out)

    def _restored(self, token: Token, counter: Counter[str] | None) -> str:
        text = token.text
        if token.kind == KIND_ANCHOR:
            return ""
        if token.kind == KIND_STRING:
            normal = _normalise_literal(text)
            if normal != text and counter is not None:
                counter["O2PG-CV-050"] += 1
            return normal
        if token.kind == KIND_QIDENT:
            simple = _SIMPLE_QIDENT.match(text)
            if simple:
                converted = ident(simple.group(1))
                if converted != text and counter is not None:
                    counter["O2PG-CV-049"] += 1
                return converted
            return text
        if token.kind == KIND_COMMENT and text.startswith("/*") and not text.startswith("/*+"):
            inner = text[2:-2] if text.endswith("*/") and len(text) >= 4 else text[2:]
            if "/*" in inner:
                if counter is not None:
                    counter["O2PG-CV-051"] += 1
                closing = "*/" if text.endswith("*/") and len(text) >= 4 else ""
                return "/*" + inner.replace("/*", "/ *") + closing
        return text


def _normalise_literal(text: str) -> str:
    """Turn ``q'[...]'`` and ``N'...'`` literals into standard PostgreSQL literals."""
    quoted = _QQUOTE.match(text)
    if quoted:
        body = quoted.group(2)
        return "'" + body.replace("'", "''") + "'"
    if text[:1] in "nN" and text[1:2] == "'":
        return text[1:]
    return text


def find_markers(sql: str) -> list[tuple[int, str, str]]:
    """``(line, rule_id, reason)`` for every review marker in restored SQL text."""
    found = []
    for match in _MARKER.finditer(sql):
        line = sql.count("\n", 0, match.start()) + 1
        found.append((line, match.group("rule"), match.group("reason")))
    return found


def matching_paren(text: str, open_index: int) -> int | None:
    """Index of the ``)`` that closes the ``(`` at ``open_index``, or None."""
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split ``text`` on ``separator`` characters that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def depth_map(text: str) -> list[int]:
    """Parenthesis depth at every index of ``text``."""
    depths = []
    depth = 0
    for char in text:
        if char == ")":
            depth -= 1
        depths.append(depth)
        if char == "(":
            depth += 1
    return depths


def top_level(pattern: re.Pattern[str], text: str, start: int = 0) -> list[re.Match[str]]:
    """Matches of ``pattern`` in ``text`` that start at parenthesis depth 0."""
    depths = depth_map(text)
    return [m for m in pattern.finditer(text, start) if depths[m.start()] == 0]


_RESET = re.compile(r"\b(BEGIN|THEN|ELSE|LOOP|DECLARE|EXCEPTION|CASE|END)\b", re.IGNORECASE)
_FIRST_WORD = re.compile(rf"{W}*(?P<word>{IDENT})", re.IGNORECASE)


@dataclass(frozen=True)
class Statement:
    """One ``;``-terminated statement.

    ``chunk_start`` is just after the previous ``;``; ``start`` is where the
    statement itself begins (after any ``BEGIN``, ``THEN``, ``LOOP`` ... that
    opened it); ``end`` is the index of the terminating ``;``.
    """

    chunk_start: int
    start: int
    end: int


def statements(text: str) -> list[Statement]:
    """Split masked PL/SQL into statements. A heuristic, but literal-safe."""
    found: list[Statement] = []
    chunk_start = 0
    for index, char in enumerate(text):
        if char != ";":
            continue
        found.append(Statement(chunk_start, _statement_start(text, chunk_start, index), index))
        chunk_start = index + 1
    return found


def _statement_start(text: str, chunk_start: int, end: int) -> int:
    """Start of the statement in ``text[chunk_start:end]`` after block keywords."""
    start = chunk_start
    case_depth = 0
    depths = None
    for match in _RESET.finditer(text, chunk_start, end):
        word = match.group(1).upper()
        if depths is None:
            depths = depth_map(text[chunk_start:end])
        if depths[match.start() - chunk_start] != 0:
            continue
        if word == "CASE":
            first = _FIRST_WORD.match(text, start)
            is_statement = first is not None and first.start("word") == match.start()
            if not is_statement:
                case_depth += 1
            continue
        if word == "END":
            if case_depth:
                case_depth -= 1
            continue
        if case_depth:
            continue
        if word == "LOOP" and _previous_word(text, match.start()) == "END":
            continue  # END LOOP [label]; closes a loop, it does not open a statement
        if word in {"EXCEPTION", "DECLARE"}:
            leading = _FIRST_WORD.match(text, start)
            if leading is None or leading.start("word") != match.start():
                continue  # e.g. "e_bad EXCEPTION;" is a declaration, not a handler section
        if word in {"THEN", "ELSE"} and _FIRST_WORD.match(text, start) is not None:
            leading = _FIRST_WORD.match(text, start)
            if leading is not None and leading.group("word").upper() == "MERGE":
                continue  # WHEN [NOT] MATCHED THEN belongs to the MERGE statement
        start = match.end()
    while start < end and (text[start].isspace() or _is_comment_char(text[start])):
        start += 1
    return start


def _is_comment_char(char: str) -> bool:
    return _COMMENT_BASE <= ord(char) <= _COMMENT_LIMIT


def first_word(text: str) -> str:
    """Upper-cased first identifier of ``text`` (empty string if none)."""
    match = _FIRST_WORD.match(text)
    return match.group("word").upper() if match else ""


_BLOCK_WORD = re.compile(r"\b(BEGIN|CASE|END)\b", re.IGNORECASE)
_AFTER_END = re.compile(rf"{W}*(?P<word>{IDENT})?", re.IGNORECASE)


def block_end(text: str, begin: int) -> int | None:
    """Index just past the ``;`` of the ``END`` that closes the ``BEGIN`` at ``begin``.

    ``END IF`` and ``END LOOP`` are skipped; ``CASE`` expressions and statements
    are tracked so their ``END`` is not mistaken for the block's.
    """
    stack: list[str] = []
    for match in _BLOCK_WORD.finditer(text, begin):
        word = match.group(1).upper()
        if word in {"BEGIN", "CASE"}:
            if word == "CASE" and stack and _previous_word(text, match.start()) == "END":
                continue  # the CASE of END CASE
            stack.append(word)
            continue
        after = _AFTER_END.match(text, match.end())
        following = (after.group("word") or "").upper() if after else ""
        if following in {"IF", "LOOP"}:
            continue
        if following == "CASE":
            if stack and stack[-1] == "CASE":
                stack.pop()
            continue
        if not stack:
            return None
        stack.pop()
        if not stack:
            semicolon = text.find(";", match.end())
            return None if semicolon < 0 else semicolon + 1
    return None


def _previous_word(text: str, index: int) -> str:
    match = re.search(rf"({IDENT}){W}*$", text[max(0, index - 80) : index])
    return match.group(1).upper() if match else ""


def line_start(text: str, offset: int) -> int:
    """Index of the first character of the line containing ``offset``."""
    return text.rfind("\n", 0, offset) + 1


def indent_at(text: str, start: int) -> str:
    """Leading spaces and tabs of the line starting at ``start``."""
    end = start
    while end < len(text) and text[end] in " \t":
        end += 1
    return text[start:end]
