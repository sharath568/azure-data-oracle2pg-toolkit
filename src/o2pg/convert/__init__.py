"""PL/SQL to PL/pgSQL conversion helpers (``o2pg convert``).

Conservative and rule based: the converter makes the mechanical rewrites it is
sure about and leaves everything else in place with an inline
``/* O2PG-REVIEW <rule>: <reason> */`` marker. It is not a parser and it does
not prove the result equivalent; test the converted code.
"""

from __future__ import annotations

from o2pg.convert.engine import (
    STATUS_BLOCKED,
    STATUS_CONVERTED,
    STATUS_NOT_CONVERTED,
    STATUS_REVIEW,
    ConversionResult,
    ConvertedObject,
    ConvertedUnit,
    ReviewPoint,
    convert_inventory,
    convert_sql,
    split_script,
)
from o2pg.convert.registry import ConversionRule, conversion_rules, get_conversion_rule
from o2pg.convert.report import conversion_rules_markdown, report_json, report_markdown

__all__ = [
    "STATUS_BLOCKED",
    "STATUS_CONVERTED",
    "STATUS_NOT_CONVERTED",
    "STATUS_REVIEW",
    "ConversionResult",
    "ConversionRule",
    "ConvertedObject",
    "ConvertedUnit",
    "ReviewPoint",
    "conversion_rules",
    "conversion_rules_markdown",
    "convert_inventory",
    "convert_sql",
    "get_conversion_rule",
    "report_json",
    "report_markdown",
    "split_script",
]
