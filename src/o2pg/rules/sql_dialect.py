"""SQL dialect rules (O2PG-SQ-xxx): Oracle SQL found in views and PL/SQL source."""

from __future__ import annotations

import re

from o2pg.plsql import HINT_MARKER
from o2pg.rules.base import Category, Severity, pattern_rule

_SQ = Category.SQL_DIALECT

pattern_rule(
    "O2PG-SQ-001",
    "Hierarchical queries (CONNECT BY)",
    _SQ,
    Severity.HIGH,
    2.0,
    r"\bCONNECT\s+BY\b",
    description=(
        "CONNECT BY, START WITH, PRIOR, LEVEL, SYS_CONNECT_BY_PATH and ORDER SIBLINGS BY "
        "have no direct PostgreSQL syntax."
    ),
    remediation=(
        "Rewrite as WITH RECURSIVE. Carry a depth column for LEVEL, build a path array for "
        "SYS_CONNECT_BY_PATH and ORDER SIBLINGS BY, and add a CYCLE clause (PostgreSQL 14+) "
        "where NOCYCLE was used."
    ),
)

pattern_rule(
    "O2PG-SQ-002",
    "ROWNUM",
    _SQ,
    Severity.MEDIUM,
    0.75,
    r"\bROWNUM\b",
    description=(
        "ROWNUM is assigned before ORDER BY is applied. 'WHERE ROWNUM <= 10 ORDER BY x' "
        "returns 10 arbitrary rows, sorted, not the top 10."
    ),
    remediation=(
        "Use LIMIT / FETCH FIRST n ROWS ONLY for top-n, and row_number() OVER (...) where "
        "ROWNUM is used as a value. Decide whether the original ordering bug is a feature."
    ),
    ai_risk=(
        "ROWNUM filters before ORDER BY. A model will usually 'fix' this into a correct "
        "top-n with LIMIT, which changes results. Tell it whether to keep the old behaviour."
    ),
)

pattern_rule(
    "O2PG-SQ-003",
    "DECODE",
    _SQ,
    Severity.LOW,
    0.25,
    r"\bDECODE\s*\(",
    description="DECODE is Oracle-only. It also treats NULL = NULL as a match, unlike CASE.",
    remediation=(
        "Rewrite as CASE. Where DECODE compared NULLs, use IS NOT DISTINCT FROM in the CASE. "
        "orafce ships a decode() function for common type combinations if you need a bridge."
    ),
    extensions=("orafce",),
)

pattern_rule(
    "O2PG-SQ-004",
    "NVL and NVL2",
    _SQ,
    Severity.LOW,
    0.1,
    r"\bNVL2?\s*\(",
    description=(
        "NVL and NVL2 are Oracle functions. NVL evaluates both arguments; COALESCE short-circuits."
    ),
    remediation=(
        "NVL(a,b) -> COALESCE(a,b); NVL2(a,b,c) -> CASE WHEN a IS NOT NULL THEN b ELSE c END. "
        "orafce provides nvl and nvl2."
    ),
    extensions=("orafce",),
)

pattern_rule(
    "O2PG-SQ-005",
    "Oracle outer join operator (+)",
    _SQ,
    Severity.MEDIUM,
    0.75,
    r"\(\s*\+\s*\)",
    description="The (+) operator is not supported. Conditions in WHERE must move into ON clauses.",
    remediation=(
        "Rewrite as ANSI LEFT/RIGHT JOIN. Be careful with filters on the outer table: in "
        "Oracle they carried (+) too, and they must go into the ON clause, not WHERE."
    ),
)

pattern_rule(
    "O2PG-SQ-006",
    "MERGE statements",
    _SQ,
    Severity.MEDIUM,
    1.0,
    r"\bMERGE\s+INTO\b",
    description=(
        "PostgreSQL 15+ supports MERGE, but not Oracle's DELETE WHERE inside WHEN MATCHED, "
        "and concurrency behaviour differs from INSERT ... ON CONFLICT."
    ),
    remediation=(
        "On Azure Flexible Server pick PostgreSQL 15 or later and use MERGE, or use INSERT "
        "... ON CONFLICT DO UPDATE for single-row upserts that must be safe under concurrency."
    ),
)

pattern_rule(
    "O2PG-SQ-007",
    "SYSDATE and SYSTIMESTAMP",
    _SQ,
    Severity.MEDIUM,
    0.25,
    r"\b(?:SYSDATE|SYSTIMESTAMP)\b",
    description=(
        "SYSDATE returns the database server's OS clock, ignores the session time zone and "
        "advances during a transaction. now() / CURRENT_TIMESTAMP is frozen at transaction "
        "start and is time zone aware."
    ),
    remediation=(
        "LOCALTIMESTAMP(0) is the closest match for SYSDATE inside one statement; use "
        "clock_timestamp() where the value must move during a long transaction. Azure "
        "servers run in UTC unless you change the TimeZone parameter."
    ),
    ai_risk=(
        "SYSDATE is server local time and changes within a transaction. Tell the model which "
        "PostgreSQL function you want (LOCALTIMESTAMP, now() or clock_timestamp())."
    ),
)

pattern_rule(
    "O2PG-SQ-008",
    "Empty string literals ('' is NULL in Oracle)",
    _SQ,
    Severity.HIGH,
    1.0,
    r"''",
    description=(
        "In Oracle '' is NULL, so col = '' is never true, NVL(col, '') does nothing and "
        "assigning '' stores NULL. In PostgreSQL all of these behave differently."
    ),
    remediation=(
        "Review each use. Comparisons with '' usually become IS NULL, or "
        "COALESCE(col, '') = '' if both should count as empty. Decide on one convention "
        "for the data load too (convert '' to NULL, or the reverse)."
    ),
    ai_risk=(
        "Oracle treats '' as NULL everywhere. A model will translate '' literally. Tell it "
        "to treat every '' as NULL and to flag comparisons with '' for review."
    ),
)

pattern_rule(
    "O2PG-SQ-009",
    "SELECT ... FROM DUAL",
    _SQ,
    Severity.LOW,
    0.1,
    r"\bFROM\s+(?:SYS\s*\.\s*)?DUAL\b",
    description="PostgreSQL does not need or have DUAL.",
    remediation=(
        "Drop FROM DUAL; in PL/pgSQL assign directly (v := expr). orafce creates a dual table if "
        "you need a bridge."
    ),
    extensions=("orafce",),
)

pattern_rule(
    "O2PG-SQ-010",
    "MINUS set operator",
    _SQ,
    Severity.LOW,
    0.1,
    r"\bMINUS\b",
    description="MINUS is spelled EXCEPT in PostgreSQL.",
    remediation="Replace MINUS with EXCEPT (EXCEPT ALL if duplicates matter).",
)

pattern_rule(
    "O2PG-SQ-011",
    "Oracle date functions",
    _SQ,
    Severity.LOW,
    0.25,
    r"\b(?:ADD_MONTHS|MONTHS_BETWEEN|LAST_DAY|NEXT_DAY)\s*\("
    r"|\bTRUNC\s*\(\s*(?:SYSDATE|SYSTIMESTAMP)\b",
    description=(
        "ADD_MONTHS, MONTHS_BETWEEN, LAST_DAY, NEXT_DAY and TRUNC(date) are Oracle built-ins."
    ),
    remediation=(
        "Use interval arithmetic and date_trunc(), or enable orafce, which provides "
        "add_months, months_between, last_day and next_day with Oracle semantics."
    ),
    extensions=("orafce",),
)

pattern_rule(
    "O2PG-SQ-012",
    "TO_DATE / TO_CHAR / TO_NUMBER format models",
    _SQ,
    Severity.MEDIUM,
    0.25,
    r"\b(?:TO_DATE|TO_CHAR|TO_NUMBER|TO_TIMESTAMP)\s*\(",
    description=(
        "PostgreSQL has these functions, but format models differ in the details (RR, FM, "
        "D, J, NLS parameters), PostgreSQL to_date is more lenient, and calls without a "
        "format rely on Oracle's NLS_DATE_FORMAT."
    ),
    remediation=(
        "Check every format model, and add an explicit one wherever the Oracle code relied "
        "on NLS defaults. Test with boundary dates and invalid input."
    ),
    ai_risk=(
        "Conversions without an explicit format depend on NLS session settings the model "
        "cannot see. Give it the NLS_DATE_FORMAT and NLS_NUMERIC_CHARACTERS in use."
    ),
)

pattern_rule(
    "O2PG-SQ-013",
    "Optimizer hints",
    _SQ,
    Severity.LOW,
    0.1,
    re.escape(HINT_MARKER),
    description=(
        "Hints become ordinary comments in PostgreSQL. Anything that only performs well "
        "because of a hint may need a different index or a rewrite."
    ),
    remediation=(
        "Note which statements were hinted and why, and include them in performance tests. "
        "Use pg_stat_statements on Azure to find the slow ones after cutover."
    ),
    extensions=("pg_stat_statements",),
)

pattern_rule(
    "O2PG-SQ-014",
    "Sequence NEXTVAL / CURRVAL",
    _SQ,
    Severity.LOW,
    0.1,
    r"\b[\w$#]+\s*\.\s*(?:NEXTVAL|CURRVAL)\b",
    description=(
        "seq.NEXTVAL is Oracle syntax. CURRVAL semantics are per session in both databases."
    ),
    remediation=(
        "seq.NEXTVAL -> nextval('seq'), seq.CURRVAL -> currval('seq'). Consider identity columns."
    ),
)

pattern_rule(
    "O2PG-SQ-015",
    "USER, USERENV and SYS_CONTEXT",
    _SQ,
    Severity.MEDIUM,
    0.5,
    r"\bSYS_CONTEXT\s*\(|\bUSERENV\b|(?<![\w$#.\"])USER(?![\w$#])",
    description=(
        "Session context functions. SYS_CONTEXT is also how Oracle VPD and application "
        "contexts pass values around."
    ),
    remediation=(
        "USER -> current_user (or session_user). SYS_CONTEXT('USERENV', ...) -> "
        "inet_client_addr(), current_database(), current_setting(...). Custom application "
        "contexts -> set_config('app.x', ...) / current_setting('app.x', true)."
    ),
)

pattern_rule(
    "O2PG-SQ-016",
    "Flashback queries",
    _SQ,
    Severity.HIGH,
    2.0,
    r"\bAS\s+OF\s+(?:TIMESTAMP|SCN)\b|\bVERSIONS\s+BETWEEN\b",
    description=(
        "AS OF TIMESTAMP / SCN and VERSIONS BETWEEN read old row versions. PostgreSQL cannot."
    ),
    remediation=(
        "If the code needs history, add history tables maintained by triggers or the "
        "application. For recovery, Azure Flexible Server point-in-time restore replaces "
        "the operational use of flashback."
    ),
)

pattern_rule(
    "O2PG-SQ-017",
    "Date arithmetic with numbers",
    _SQ,
    Severity.MEDIUM,
    0.25,
    r"\b(?:SYSDATE|SYSTIMESTAMP|\w*DATE|\w+_DT)\s*[-+]\s*\d|\bTRUNC\s*\([^()]*\)\s*[-+]\s*\d",
    description=(
        "In Oracle DATE + 1 adds a day and DATE + 1/24 adds an hour. Once DATE becomes "
        "timestamp, timestamp + integer is an error in PostgreSQL."
    ),
    remediation="Use intervals: col + interval '1 day', col + make_interval(hours => n).",
    ai_risk=(
        "Numbers added to dates are days (fractions are hours and minutes). Tell the model "
        "to convert them to intervals after DATE becomes timestamp(0)."
    ),
)

pattern_rule(
    "O2PG-SQ-018",
    "String concatenation with possible NULLs",
    _SQ,
    Severity.MEDIUM,
    0.25,
    r"\|\|",
    description=(
        "In Oracle 'a' || NULL is 'a'. In PostgreSQL it is NULL. Any concatenation that can "
        "see a NULL column changes result."
    ),
    remediation=(
        "Use concat() or concat_ws(), which ignore NULLs, or wrap nullable operands in "
        "COALESCE(x, '')."
    ),
    ai_risk=(
        "Oracle || ignores NULLs; PostgreSQL || returns NULL. Tell the model to use concat() "
        "or COALESCE for any nullable operand."
    ),
)
