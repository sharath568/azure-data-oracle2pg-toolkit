"""PL/SQL rules (O2PG-PL-xxx): stored program units from ALL_SOURCE."""

from __future__ import annotations

import re
from collections.abc import Iterator

from o2pg.inventory import Inventory
from o2pg.plsql import package_state_variables
from o2pg.rules.base import Category, Hit, Severity, pattern_rule, rule

_PL = Category.PLSQL


@rule(
    "O2PG-PL-001",
    "Packages",
    _PL,
    Severity.MEDIUM,
    2.0,
    description=(
        "PostgreSQL has no packages. Specs, bodies, private subprograms and package-level "
        "types all need a new home."
    ),
    remediation=(
        "The usual pattern is one schema per package with one function or procedure per "
        "subprogram. Private subprograms become functions you do not grant EXECUTE on. "
        "Package types become composite types in that schema. The orafce extension does "
        "not emulate packages."
    ),
)
def _packages(inventory: Inventory) -> Iterator[Hit]:
    bodies = {(s.owner, s.name): s for s in inventory.source if s.type == "PACKAGE BODY"}
    for unit in inventory.source:
        if unit.type == "PACKAGE":
            lines = unit.text.count("\n")
            body = bodies.get((unit.owner, unit.name))
            if body:
                lines += body.text.count("\n")
            yield Hit(f"{unit.owner}.{unit.name}", "PACKAGE", f"{lines} lines incl. body")


@rule(
    "O2PG-PL-002",
    "Package-level state (global variables)",
    _PL,
    Severity.HIGH,
    3.0,
    description=(
        "Variables declared at package level keep their value for the whole session. Code "
        "often relies on that as a cache or as a way to pass context between calls. "
        "PostgreSQL functions have no persistent state."
    ),
    remediation=(
        "Replace with session settings (set_config / current_setting with a custom prefix), "
        "temporary tables, or pass the value explicitly. Read-only lookups usually become a "
        "table or an IMMUTABLE function."
    ),
    ai_risk=(
        "These packages keep state between calls. A model converting one function at a time "
        "cannot see that; tell it which variables are session state and how you want them "
        "replaced (for example current_setting('app.x'))."
    ),
)
def _package_state(inventory: Inventory) -> Iterator[Hit]:
    for unit in inventory.code_units:
        if unit.kind not in {"PACKAGE", "PACKAGE BODY"}:
            continue
        names = package_state_variables(unit.cleaned, is_body=unit.kind == "PACKAGE BODY")
        if names:
            shown = ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")
            yield Hit(unit.qualified, unit.kind, f"{len(names)} variable(s): {shown}")


pattern_rule(
    "O2PG-PL-003",
    "Autonomous transactions",
    _PL,
    Severity.HIGH,
    3.0,
    r"\bPRAGMA\s+AUTONOMOUS_TRANSACTION\b",
    plsql_only=True,
    description=(
        "PRAGMA AUTONOMOUS_TRANSACTION commits independently of the caller. It is most often "
        "used for error logging that must survive a rollback. PostgreSQL has no equivalent."
    ),
    remediation=(
        "Options: write the log through a separate connection (postgres_fdw to the same "
        "server is a known workaround), move logging to the application, or use RAISE LOG "
        "and Azure Monitor / server logs. Re-check the business rule before emulating it."
    ),
    ai_risk=(
        "Autonomous transactions commit even when the caller rolls back. Models tend to drop "
        "the pragma and silently change that behaviour; tell it how you want it handled."
    ),
    extensions=("postgres_fdw",),
)

pattern_rule(
    "O2PG-PL-004",
    "BULK COLLECT and FORALL",
    _PL,
    Severity.LOW,
    0.5,
    r"\bBULK\s+COLLECT\b|\bFORALL\b",
    plsql_only=True,
    description=(
        "Bulk binding exists to cut context switches between PL/SQL and SQL. PL/pgSQL has no "
        "BULK COLLECT or FORALL syntax."
    ),
    remediation=(
        "Usually the best rewrite is a single set-based INSERT ... SELECT or UPDATE ... FROM. "
        "Otherwise use arrays (array_agg, unnest) or a plain FOR loop over a query."
    ),
)

pattern_rule(
    "O2PG-PL-005",
    "REF CURSOR and SYS_REFCURSOR",
    _PL,
    Severity.MEDIUM,
    1.0,
    r"\bREF\s+CURSOR\b|\bSYS_REFCURSOR\b",
    plsql_only=True,
    description=(
        "PostgreSQL has refcursor, but the client side differs: cursors are only valid inside "
        "a transaction and drivers fetch them differently."
    ),
    remediation=(
        "Map to refcursor, or better, to RETURNS TABLE / RETURNS SETOF functions. Check the "
        "application data access layer, which is usually where the real work is."
    ),
)

pattern_rule(
    "O2PG-PL-006",
    "%TYPE and %ROWTYPE anchored declarations",
    _PL,
    Severity.INFO,
    0.0,
    r"%\s*(?:ROW)?TYPE\b",
    plsql_only=True,
    description="PL/pgSQL supports %TYPE and %ROWTYPE, so these mostly convert as-is.",
    remediation=(
        "No change needed in most cases. Make sure the anchored table or column exists with "
        "the same name in PostgreSQL (watch for case folding)."
    ),
)

pattern_rule(
    "O2PG-PL-007",
    "Dynamic SQL (EXECUTE IMMEDIATE, DBMS_SQL)",
    _PL,
    Severity.MEDIUM,
    1.5,
    r"\bEXECUTE\s+IMMEDIATE\b|\bDBMS_SQL\s*\.",
    plsql_only=True,
    description=(
        "SQL built in strings is invisible to static analysis, including this tool. "
        "Statements inside the strings are Oracle dialect too."
    ),
    remediation=(
        "EXECUTE IMMEDIATE -> EXECUTE ... USING; DBMS_SQL -> EXECUTE with format() and %I/%L. "
        "Review every string by hand for Oracle syntax and SQL injection."
    ),
    ai_risk=(
        "SQL inside strings is not visible as SQL. Tell the model to convert the dialect "
        "inside dynamic SQL strings as well, and review those strings yourself."
    ),
)

pattern_rule(
    "O2PG-PL-008",
    "DBMS_LOB usage",
    _PL,
    Severity.MEDIUM,
    1.0,
    r"\bDBMS_LOB\s*\.",
    plsql_only=True,
    description="PostgreSQL text and bytea are values, not locators. DBMS_LOB has no equivalent.",
    remediation=(
        "Replace DBMS_LOB.SUBSTR/INSTR/GETLENGTH/APPEND with substr, strpos, length and ||. "
        "Code that streams large LOBs in chunks may be better moved to the application."
    ),
)

pattern_rule(
    "O2PG-PL-009",
    "UTL_FILE file access",
    _PL,
    Severity.BLOCKER,
    4.0,
    r"\bUTL_FILE\s*\.",
    plsql_only=True,
    description=(
        "Reading and writing server files is not possible on Azure Database for PostgreSQL "
        "Flexible Server; there is no file system you can reach from SQL."
    ),
    remediation=(
        "Move file import/export to the application, Azure Data Factory or an Azure Function "
        "writing to Blob Storage. For bulk loads use COPY FROM STDIN from a client."
    ),
)

pattern_rule(
    "O2PG-PL-010",
    "Network calls from the database (UTL_HTTP, UTL_SMTP, UTL_TCP, UTL_MAIL)",
    _PL,
    Severity.BLOCKER,
    4.0,
    r"\bUTL_(?:HTTP|SMTP|TCP|MAIL|INADDR)\s*\.",
    plsql_only=True,
    description=(
        "The database calling web services or sending mail has no supported equivalent on "
        "Flexible Server."
    ),
    remediation=(
        "Write an outbox table inside the transaction and have an application worker, Azure "
        "Function or Logic App deliver the message. This is usually more reliable anyway."
    ),
)

pattern_rule(
    "O2PG-PL-011",
    "Job scheduling from code (DBMS_JOB, DBMS_SCHEDULER)",
    _PL,
    Severity.MEDIUM,
    1.0,
    r"\bDBMS_(?:JOB|SCHEDULER)\s*\.",
    plsql_only=True,
    description="Code that creates or runs scheduler jobs at runtime.",
    remediation=(
        "Use pg_cron (cron.schedule / cron.unschedule). On Azure, add pg_cron to "
        "shared_preload_libraries and azure.extensions; jobs are created from the database "
        "named in cron.database_name (postgres by default) and can target others with "
        "cron.schedule_in_database."
    ),
    extensions=("pg_cron",),
)

_SPECIFIC_PACKAGES = frozenset(
    {
        "DBMS_SQL",
        "DBMS_LOB",
        "DBMS_JOB",
        "DBMS_SCHEDULER",
        "DBMS_AQ",
        "DBMS_AQADM",
        "DBMS_FGA",
        "DBMS_REDACT",
        "DBMS_RLS",
        "UTL_FILE",
        "UTL_HTTP",
        "UTL_SMTP",
        "UTL_TCP",
        "UTL_MAIL",
        "UTL_INADDR",
    }
)
_PACKAGE_HINTS = {
    "DBMS_OUTPUT": "RAISE NOTICE",
    "DBMS_CRYPTO": "pgcrypto",
    "DBMS_OBFUSCATION_TOOLKIT": "pgcrypto",
    "DBMS_RANDOM": "random() / gen_random_uuid()",
    "DBMS_LOCK": "pg_sleep() / advisory locks",
    "DBMS_ALERT": "LISTEN/NOTIFY",
    "DBMS_PIPE": "LISTEN/NOTIFY",
    "DBMS_UTILITY": "GET STACKED DIAGNOSTICS / built-ins",
    "DBMS_APPLICATION_INFO": "SET application_name",
    "DBMS_STATS": "ANALYZE / autovacuum",
    "DBMS_MVIEW": "REFRESH MATERIALIZED VIEW",
    "DBMS_SESSION": "set_config()",
    "UTL_RAW": "bytea functions / encode()",
    "UTL_ENCODE": "encode() / decode()",
    "UTL_I18N": "convert_to() / convert_from()",
}
_SUPPLIED_PACKAGE = re.compile(r"\b((?:DBMS|UTL)_\w+)\s*\.", re.IGNORECASE)


@rule(
    "O2PG-PL-012",
    "Other Oracle-supplied packages (DBMS_*, UTL_*)",
    _PL,
    Severity.MEDIUM,
    0.75,
    description=(
        "Calls to Oracle-supplied packages that do not have a dedicated rule. Each needs a "
        "replacement built-in, extension or rewrite."
    ),
    remediation=(
        "See the per-object detail for a suggested replacement. Common ones: DBMS_OUTPUT -> "
        "RAISE NOTICE, DBMS_CRYPTO -> pgcrypto, DBMS_LOCK.SLEEP -> pg_sleep, DBMS_ALERT and "
        "DBMS_PIPE -> LISTEN/NOTIFY. orafce provides DBMS_OUTPUT and UTL_FILE-style "
        "functions, but its UTL_FILE needs server directories that Azure does not expose."
    ),
    extensions=("pgcrypto", "orafce"),
)
def _supplied_packages(inventory: Inventory) -> Iterator[Hit]:
    for unit in inventory.code_units:
        if not unit.plsql:
            continue
        found = sorted(
            {m.group(1).upper() for m in _SUPPLIED_PACKAGE.finditer(unit.cleaned)}
            - _SPECIFIC_PACKAGES
        )
        if found:
            parts = [f"{p} ({_PACKAGE_HINTS[p]})" if p in _PACKAGE_HINTS else p for p in found]
            yield Hit(unit.qualified, unit.kind, ", ".join(parts))


pattern_rule(
    "O2PG-PL-013",
    "RAISE_APPLICATION_ERROR and Oracle error codes",
    _PL,
    Severity.LOW,
    0.5,
    r"\bRAISE_APPLICATION_ERROR\s*\(|\bSQLCODE\b",
    plsql_only=True,
    description=(
        "Applications often match on ORA-20xxx numbers or SQLCODE values. PostgreSQL uses "
        "five-character SQLSTATE codes."
    ),
    remediation=(
        "RAISE EXCEPTION '...' USING ERRCODE = 'P0001' (or a custom 5-character code), and "
        "update application error handling that parses ORA- numbers."
    ),
    ai_risk=(
        "Error numbers are part of the contract with the application. Give the model a "
        "mapping from ORA-20xxx codes to SQLSTATE values instead of letting it invent one."
    ),
)

pattern_rule(
    "O2PG-PL-014",
    "Transaction control inside stored code",
    _PL,
    Severity.MEDIUM,
    1.0,
    r"\bCOMMIT\b|\bROLLBACK\b(?!\s+TO\b)",
    plsql_only=True,
    description=(
        "PostgreSQL functions cannot COMMIT or ROLLBACK. Procedures can (PostgreSQL 11+), "
        "but only when called with CALL outside an explicit transaction block."
    ),
    remediation=(
        "Convert units that commit into procedures, or move the commit to the caller. "
        "Savepoint-style ROLLBACK TO becomes a BEGIN ... EXCEPTION block."
    ),
)


@rule(
    "O2PG-PL-015",
    "Wrapped (obfuscated) PL/SQL",
    _PL,
    Severity.BLOCKER,
    5.0,
    description="The source was created with the wrap utility, so nobody can read or convert it.",
    remediation=(
        "Get the original source from version control or the vendor. Without it the unit "
        "has to be rewritten from its observed behaviour."
    ),
)
def _wrapped(inventory: Inventory) -> Iterator[Hit]:
    for unit in inventory.source:
        first_line = unit.text.lstrip().split("\n", 1)[0]
        if re.search(r"\bwrapped\b", first_line, re.IGNORECASE):
            yield Hit(f"{unit.owner}.{unit.name}", unit.type, "source is wrapped")


pattern_rule(
    "O2PG-PL-016",
    "Pipelined table functions",
    _PL,
    Severity.MEDIUM,
    1.0,
    r"\bPIPELINED\b|\bPIPE\s+ROW\b",
    plsql_only=True,
    description="PIPELINED functions stream rows to a SELECT ... FROM TABLE(f()) caller.",
    remediation=(
        "Rewrite as RETURNS SETOF / RETURNS TABLE with RETURN NEXT or RETURN QUERY, and "
        "drop the TABLE() wrapper at call sites."
    ),
)

pattern_rule(
    "O2PG-PL-017",
    "GOTO statements",
    _PL,
    Severity.LOW,
    1.0,
    r"\bGOTO\b",
    plsql_only=True,
    description="PL/pgSQL has no GOTO statement, so jumps to labels must be restructured.",
    remediation="Restructure with loops, EXIT/CONTINUE with labels, or early RETURN.",
)

pattern_rule(
    "O2PG-PL-018",
    "Associative arrays (INDEX BY tables)",
    _PL,
    Severity.MEDIUM,
    1.0,
    r"\bTABLE\s+OF\b[^;]*\bINDEX\s+BY\b",
    plsql_only=True,
    description=(
        "Associative arrays are sparse maps keyed by integer or string. PostgreSQL arrays are "
        "dense and 1-based."
    ),
    remediation=(
        "Integer-keyed dense arrays -> PostgreSQL arrays. String-keyed maps -> jsonb or "
        "hstore. Often a temporary table or a join removes the need entirely."
    ),
    extensions=("hstore",),
)
