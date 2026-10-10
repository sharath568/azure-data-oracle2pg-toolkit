"""Lightweight lexical helpers for scanning Oracle SQL and PL/SQL source.

This is not a parser. The goal is to make regex-based rules trustworthy by
removing the two biggest sources of false positives: comments and the contents
of string literals. Line numbers are preserved so findings can point at a line.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(
    r"""
      --[^\n]*                                  # single-line comment
    | /\*.*?(?:\*/|\Z)                          # block comment or optimizer hint
    | (?<![\w$\#])[nN]?[qQ]'(?:                 # alternative quoting, q'[...]'
          \[.*?\]' | \(.*?\)' | \{.*?\}' | <.*?>' | (?P<qd>\S).*?(?P=qd)'
      )
    | '(?:[^']|'')*(?:'|\Z)                     # ordinary string literal
    | "[^"]*(?:"|\Z)                            # quoted identifier
    """,
    re.DOTALL | re.VERBOSE,
)

LEXICAL_TOKEN = _TOKEN
"""Regex matching one comment, string literal or quoted identifier.

Shared with :mod:`o2pg.convert`, which hides these tokens before rewriting code.
"""

HINT_MARKER = "/*+*/"
"""Placeholder left behind for an optimizer hint comment (``/*+ ... */``)."""

MASKED_LITERAL = "'~'"
"""Placeholder left behind for a non-empty string literal."""


def _replace(match: re.Match[str]) -> str:
    token = match.group(0)
    newlines = "\n" * token.count("\n")
    if token.startswith("--"):
        return ""
    if token.startswith("/*"):
        return (HINT_MARKER if token.startswith("/*+") else "") + newlines
    if token.startswith('"'):
        return token
    if token == "''":
        return "''"
    return MASKED_LITERAL + newlines


def clean_source(text: str) -> str:
    """Return ``text`` with comments removed and string literals masked.

    * ``--`` and ``/* */`` comments are dropped (newlines inside them are kept).
    * Optimizer hints become :data:`HINT_MARKER` so hint rules can still find them.
    * Non-empty string literals (including ``q'[...]'`` forms) become
      :data:`MASKED_LITERAL`; the empty literal ``''`` is kept as-is because it
      carries meaning in Oracle (it is NULL).
    * Quoted identifiers are kept verbatim.
    """
    return _TOKEN.sub(_replace, text)


def line_of(text: str, offset: int) -> int:
    """Return the 1-based line number of ``offset`` in ``text``."""
    return text.count("\n", 0, offset) + 1


_PACKAGE_HEADER = re.compile(
    r"\bpackage\s+(?:body\s+)?[\w$#\".]+\s+(?:authid\s+\w+\s+)?(?:is|as)\b", re.IGNORECASE
)
_FIRST_SUBPROGRAM = re.compile(r"\b(?:procedure|function)\b", re.IGNORECASE)
_DECLARATION = re.compile(
    r"^[ \t]*(?P<name>[a-z_][\w$#]*)[ \t]+(?P<constant>constant[ \t]+)?"
    r"(?P<type>[a-z_][\w$#.]*(?:%(?:row)?type)?)(?P<tail>[^;\n]*);[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_NOT_A_VARIABLE = frozenset(
    """
    type subtype cursor pragma procedure function end create package is as begin
    select from where and or order group having union join on into values set return
    if else elsif loop while for null raise open close fetch exit when then case
    insert update delete merge commit rollback execute pipe goto with
    """.split()
)
_NOT_A_TYPE = frozenset({"exception", "in", "out", "nocopy"})


def package_state_variables(cleaned: str, is_body: bool) -> list[str]:
    """Return names of mutable package-level variables in cleaned package source.

    For a package specification the whole spec is searched. For a package body
    only the declarative section before the first procedure or function is
    searched, which is where package-level state lives in practice. Constants,
    exceptions, types and cursors are ignored. This is a heuristic: it works on
    conventionally formatted code (one declaration per line).
    """
    header = _PACKAGE_HEADER.search(cleaned)
    start = header.end() if header else 0
    end = len(cleaned)
    if is_body:
        first = _FIRST_SUBPROGRAM.search(cleaned, start)
        if first:
            end = first.start()
    names: list[str] = []
    for match in _DECLARATION.finditer(cleaned, start, end):
        if match.group("constant"):
            continue
        if match.group("name").lower() in _NOT_A_VARIABLE:
            continue
        if match.group("type").lower() in _NOT_A_TYPE:
            continue
        tail = match.group("tail")
        if tail.count(")") > tail.count("("):
            continue  # last line of a parameter list, not a declaration
        names.append(match.group("name"))
    return names
