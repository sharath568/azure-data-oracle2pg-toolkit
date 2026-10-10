"""Registry of conversion rules: mechanical rewrites and review points.

Every change the converter makes is recorded against a rewrite rule
(``O2PG-CV-nnn``). Everything it recognises but will not change is left in place
with an inline ``/* O2PG-REVIEW O2PG-RV-nnn: reason */`` marker. Review rules
are *blocking* when the code as written will not work in PostgreSQL (or will
quietly behave differently enough that it must not run unreviewed), and
non-blocking when it will run but a person should confirm the semantics.

``docs/conversion-rules.md`` is generated from this registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONVERSION_RULE_ID = re.compile(r"^O2PG-(?P<kind>CV|RV)-\d{3}$")


@dataclass(frozen=True)
class ConversionRule:
    """One rewrite (``CV``) or review point (``RV``).

    ``note`` is shown in the report whenever a rewrite is applied, for the
    cases where the PostgreSQL form is the right mechanical translation but the
    behaviour is not byte-for-byte identical. ``see`` lists related assessment
    rules from ``docs/rules.md``.
    """

    id: str
    title: str
    description: str
    note: str = ""
    blocking: bool = False
    see: tuple[str, ...] = ()

    @property
    def is_rewrite(self) -> bool:
        """True for ``O2PG-CV`` rewrites, False for ``O2PG-RV`` review points."""
        return self.id.startswith("O2PG-CV-")


_REGISTRY: dict[str, ConversionRule] = {}


def _register(rule: ConversionRule) -> ConversionRule:
    match = CONVERSION_RULE_ID.match(rule.id)
    if not match:
        raise ValueError(f"bad conversion rule id {rule.id!r}")
    if rule.id in _REGISTRY:
        raise ValueError(f"duplicate conversion rule id {rule.id}")
    if rule.is_rewrite and rule.blocking:
        raise ValueError(f"{rule.id}: only review rules can be blocking")
    if "*/" in rule.description or "*/" in rule.note:
        raise ValueError(f"{rule.id}: text must not contain a comment terminator")
    _REGISTRY[rule.id] = rule
    return rule


def rewrite(
    rule_id: str, title: str, description: str, *, note: str = "", see: tuple[str, ...] = ()
) -> ConversionRule:
    """Register a mechanical rewrite."""
    return _register(ConversionRule(rule_id, title, description, note=note, see=see))


def review(
    rule_id: str, title: str, description: str, *, blocking: bool, see: tuple[str, ...] = ()
) -> ConversionRule:
    """Register a review point. ``description`` doubles as the default marker text."""
    return _register(ConversionRule(rule_id, title, description, blocking=blocking, see=see))


def conversion_rules() -> list[ConversionRule]:
    """All conversion rules sorted by id (rewrites first, then review points)."""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def get_conversion_rule(rule_id: str) -> ConversionRule:
    """Look up a conversion rule by id. Raises KeyError if unknown."""
    return _REGISTRY[rule_id.upper()]


# --------------------------------------------------------------------------------------
# Mechanical rewrites
# --------------------------------------------------------------------------------------

rewrite(
    "O2PG-CV-001",
    "Routine header",
    "`[CREATE OR REPLACE] PROCEDURE|FUNCTION name (...) IS|AS` becomes `CREATE OR REPLACE "
    "PROCEDURE|FUNCTION schema.name (...) LANGUAGE plpgsql AS $$ ... $$;`. The declarations "
    "between IS/AS and BEGIN move under `DECLARE`.",
)
rewrite(
    "O2PG-CV-002",
    "Function RETURN clause",
    "`RETURN type` in a function header becomes `RETURNS type`.",
)
rewrite(
    "O2PG-CV-003",
    "Parameter modes",
    "`p IN t`, `p OUT t` and `p IN OUT [NOCOPY] t` become `p t`, `OUT p t` and `INOUT p t`. "
    "PostgreSQL writes the mode before the name.",
)
rewrite(
    "O2PG-CV-004",
    "Data types",
    "Parameter, return, variable and `CAST` types are mapped with the same table as "
    "`o2pg map-types` (`VARCHAR2(n)` to `varchar(n)`, `NUMBER(p,s)` to `numeric(p,s)`, `DATE` "
    "to `timestamp(0)`, `CLOB` to `text`, ...). PL/SQL-only types map to `integer` "
    "(`PLS_INTEGER`, `BINARY_INTEGER`, `NATURAL`, `POSITIVE`), `boolean` and `refcursor` "
    "(`SYS_REFCURSOR`). `%TYPE` and `%ROWTYPE` are kept: PL/pgSQL supports both.",
    note=(
        "Oracle INTEGER and SMALLINT are NUMBER(38) and are kept as PostgreSQL integer and "
        "smallint, which raise an error on overflow; widen them to numeric if values can exceed "
        "the 32-bit or 16-bit range."
    ),
    see=("O2PG-DT-001", "O2PG-DT-003"),
)
rewrite(
    "O2PG-CV-005",
    "Parameter defaults",
    "`p t := expr` and `p t DEFAULT expr` in a parameter list become `p t DEFAULT expr`.",
)
rewrite(
    "O2PG-CV-006",
    "END labels",
    "`END name;` becomes `END;` when no `<<name>>` label exists. PL/pgSQL only accepts a name "
    "after END when it matches a block label.",
)
rewrite(
    "O2PG-CV-007",
    "search_path pinned on the routine",
    "Converted routines get `SET search_path = <owner schema>, public` (package routines also "
    "get the package schema first), because Oracle resolves unqualified names in the owner's "
    "schema. The file also sets search_path before the CREATE so `%TYPE` references resolve.",
    note=(
        "A procedure that commits or rolls back cannot carry a SET clause in PostgreSQL, so "
        "those procedures are left without it and resolve names through the caller's search_path."
    ),
)
rewrite(
    "O2PG-CV-008",
    "Invoker and definer rights",
    "`AUTHID CURRENT_USER` becomes `SECURITY INVOKER`. Oracle's default (definer rights) is not "
    "copied: the routine runs with the caller's privileges, which is PostgreSQL's default.",
    note=(
        "Oracle runs this code with the owner's privileges by default. Grant callers what they "
        "need, or add SECURITY DEFINER deliberately (keep the SET search_path if you do)."
    ),
    see=("O2PG-SE-004",),
)
rewrite(
    "O2PG-CV-009",
    "Oracle-only routine options",
    "`DETERMINISTIC`, `RESULT_CACHE`, `PARALLEL_ENABLE` and `ACCESSIBLE BY` are kept as a comment "
    "on the routine. None of them changes results.",
    note=(
        "PostgreSQL routines default to VOLATILE. Mark them IMMUTABLE or STABLE yourself once you "
        "know they do not read changing data."
    ),
)
rewrite(
    "O2PG-CV-010",
    "Explicit cursor declarations",
    "`CURSOR c (p NUMBER) IS SELECT ...` becomes `c CURSOR (p numeric) FOR SELECT ...`.",
)
rewrite(
    "O2PG-CV-011",
    "REF CURSOR types",
    "`TYPE t IS REF CURSOR [RETURN ...]` is kept as a comment and every use of `t` becomes "
    "`refcursor`.",
    note="The RETURN row type of a strong REF CURSOR is not enforced by refcursor.",
    see=("O2PG-PL-005",),
)
rewrite(
    "O2PG-CV-012",
    "Package split into a schema",
    "A package becomes a schema named after the package, with one PostgreSQL routine per "
    "procedure or function in the package body.",
    see=("O2PG-PL-001",),
)
rewrite(
    "O2PG-CV-013",
    "Package constants inlined",
    "Package-level `CONSTANT` declarations are copied into the DECLARE section of each routine "
    "that uses them.",
)
rewrite(
    "O2PG-CV-014",
    "Forward declarations",
    "Forward declarations in a package body are kept as comments; PL/pgSQL does not need them.",
)
rewrite(
    "O2PG-CV-015",
    "NVL to COALESCE",
    "`NVL(a, b)` becomes `COALESCE(a, b)`.",
    note=(
        "NVL converts b to the type of a; COALESCE needs a common type, so NVL(number, 'text') "
        "style calls fail and need an explicit cast."
    ),
    see=("O2PG-SQ-004",),
)
rewrite(
    "O2PG-CV-016",
    "NVL2 to CASE",
    "`NVL2(a, b, c)` becomes `CASE WHEN a IS NOT NULL THEN b ELSE c END`.",
    see=("O2PG-SQ-004",),
)
rewrite(
    "O2PG-CV-017",
    "DECODE to CASE",
    "`DECODE(x, s1, r1, ..., default)` becomes `CASE x WHEN s1 THEN r1 ... ELSE default END` "
    "when every search value is a non-NULL literal. When a search value is `NULL`, `''` or an "
    "expression, and x is a simple name, it becomes a searched CASE using `IS NULL` or "
    "`IS NOT DISTINCT FROM`, because DECODE treats two NULLs as equal.",
    note=(
        "DECODE converts every result to the type of the first one; CASE needs compatible types."
    ),
    see=("O2PG-SQ-003",),
)
rewrite(
    "O2PG-CV-018",
    "SYSDATE and CURRENT_DATE",
    "`SYSDATE` and Oracle's `CURRENT_DATE` become `LOCALTIMESTAMP(0)`, the same choice "
    "`o2pg map-types` makes for column defaults.",
    note=(
        "LOCALTIMESTAMP is fixed at the start of the transaction; SYSDATE reads the clock on "
        "every call and uses the database server's time zone. Use clock_timestamp()::timestamp(0) "
        "where code measures elapsed time inside one transaction."
    ),
    see=("O2PG-SQ-007",),
)
rewrite(
    "O2PG-CV-019",
    "SYSTIMESTAMP",
    "`SYSTIMESTAMP` becomes `clock_timestamp()`, which also reads the clock on every call and "
    "returns a time zone aware value.",
    see=("O2PG-SQ-007",),
)
rewrite(
    "O2PG-CV-020",
    "Sequence NEXTVAL and CURRVAL",
    "`seq.NEXTVAL` becomes `nextval('seq')` and `seq.CURRVAL` becomes `currval('seq')`, with the "
    "same identifier folding as `o2pg map-types`.",
    see=("O2PG-SQ-014",),
)
rewrite(
    "O2PG-CV-021",
    "FROM DUAL",
    "`FROM DUAL` is removed. PostgreSQL allows SELECT without FROM.",
    see=("O2PG-SQ-009",),
)
rewrite(
    "O2PG-CV-022",
    "SELECT expr INTO var without FROM",
    "`SELECT expr INTO target;` with a single expression and no FROM (usually after FROM DUAL is "
    "removed) becomes `target := expr;`.",
)
rewrite(
    "O2PG-CV-023",
    "SELECT INTO STRICT",
    "`SELECT ... INTO target FROM ...` and `EXECUTE ... INTO target` get `STRICT`, so a query "
    "that returns no rows raises `no_data_found` and one that returns several raises "
    "`too_many_rows`, as in Oracle. Without STRICT, PostgreSQL silently stores NULL or the first "
    "row.",
    note=(
        "This restores Oracle's behaviour; check that exception handlers expect it. Aggregates "
        "without GROUP BY always return one row, so STRICT changes nothing there."
    ),
)
rewrite(
    "O2PG-CV-024",
    "DBMS_OUTPUT.PUT_LINE",
    "`DBMS_OUTPUT.PUT_LINE(x);` becomes `RAISE NOTICE '%', x;`.",
    note="Notices go to the client and the server log (depending on log_min_messages).",
)
rewrite(
    "O2PG-CV-025",
    "RAISE_APPLICATION_ERROR",
    "`RAISE_APPLICATION_ERROR(-20001, msg)` becomes `RAISE EXCEPTION USING ERRCODE = 'P0001', "
    "MESSAGE = msg, HINT = 'Oracle error code -20001'`.",
    note=(
        "Callers that test SQLCODE for the -20xxx number must test SQLSTATE 'P0001' and the "
        "message or hint instead."
    ),
    see=("O2PG-PL-013",),
)
rewrite(
    "O2PG-CV-026",
    "Predefined exception names",
    "`DUP_VAL_ON_INDEX` becomes `unique_violation`, `ZERO_DIVIDE` becomes `division_by_zero`, "
    "`INVALID_NUMBER` becomes `invalid_text_representation`, `VALUE_ERROR` becomes "
    "`data_exception`, `CURSOR_ALREADY_OPEN` becomes `duplicate_cursor`, `INVALID_CURSOR` becomes "
    "`invalid_cursor_state` and `TIMEOUT_ON_RESOURCE` becomes `lock_not_available`. "
    "`NO_DATA_FOUND`, `TOO_MANY_ROWS` and `OTHERS` exist in PL/pgSQL under the same names.",
    note="VALUE_ERROR maps to the whole data_exception class, which is slightly wider.",
)
rewrite(
    "O2PG-CV-027",
    "SQLCODE to SQLSTATE",
    "`SQLCODE` becomes `SQLSTATE`. Also flagged for review because the value changes from a "
    "negative number to a five-character code.",
)
rewrite(
    "O2PG-CV-028",
    "EXECUTE IMMEDIATE",
    "`EXECUTE IMMEDIATE str` becomes `EXECUTE str`. Also flagged for review because the SQL text "
    "itself is not converted.",
    see=("O2PG-PL-007",),
)
rewrite(
    "O2PG-CV-029",
    "COMMIT and ROLLBACK in procedures",
    "Transaction control in a procedure is kept: PostgreSQL 11+ procedures can COMMIT and "
    "ROLLBACK.",
    note=(
        "Such a procedure must be called with CALL outside an explicit transaction block, not from "
        "a function or trigger, and cannot commit inside a BEGIN ... EXCEPTION block. It is also "
        "created without SET search_path (see O2PG-CV-007)."
    ),
    see=("O2PG-PL-014",),
)
rewrite(
    "O2PG-CV-030",
    ":NEW and :OLD",
    "`:NEW.col` and `:OLD.col` (and REFERENCING aliases) become `NEW.col` and `OLD.col`.",
    see=("O2PG-OB-004",),
)
rewrite(
    "O2PG-CV-031",
    "INSERTING, UPDATING, DELETING",
    "`INSERTING`, `UPDATING` and `DELETING` become `(TG_OP = 'INSERT')`, `(TG_OP = 'UPDATE')` "
    "and `(TG_OP = 'DELETE')`.",
)
rewrite(
    "O2PG-CV-032",
    "Row and statement triggers",
    "A simple BEFORE/AFTER/INSTEAD OF trigger becomes a `RETURNS trigger` function that wraps "
    "the Oracle block and returns `NEW` (BEFORE/INSTEAD OF row triggers; `OLD` for DELETE) or "
    "`NULL`, followed by `CREATE OR REPLACE TRIGGER ... EXECUTE FUNCTION`. The WHEN clause, "
    "`UPDATE OF` column lists and FOR EACH ROW carry over.",
    note="CREATE OR REPLACE TRIGGER needs PostgreSQL 14 or later.",
    see=("O2PG-OB-004",),
)
rewrite(
    "O2PG-CV-033",
    "Cursor FOR loop records",
    "`FOR r IN (SELECT ...) LOOP` is kept; PL/pgSQL needs the loop variable declared, so "
    "`r record;` is added to the DECLARE section.",
)
rewrite(
    "O2PG-CV-034",
    "FOR ... IN REVERSE bounds",
    "`FOR i IN REVERSE 1 .. n` becomes `FOR i IN REVERSE n .. 1`; PL/pgSQL expects the upper "
    "bound first. A short comment marks the swap so a second run leaves it alone.",
)
rewrite(
    "O2PG-CV-035",
    "Cursor attributes",
    "`SQL%FOUND` and `c%FOUND` become `FOUND`, `%NOTFOUND` becomes `NOT FOUND`, `SQL%ISOPEN` "
    "becomes `false`, and `v := SQL%ROWCOUNT;` becomes `GET DIAGNOSTICS v = ROW_COUNT;`.",
    note=(
        "FOUND reflects the most recent statement, so c%NOTFOUND is only equivalent directly "
        "after FETCH c, which is the usual pattern."
    ),
)
rewrite(
    "O2PG-CV-036",
    "Procedure call statements",
    "A statement that is only a call, `proc(args);` or `pkg.proc;`, becomes `CALL proc(args);`. "
    "In Oracle such a statement can only be a procedure call.",
)
rewrite(
    "O2PG-CV-037",
    "TRUNC on dates",
    "`TRUNC(d)` and `TRUNC(d, 'MM')` become `date_trunc('day', d)` and "
    "`date_trunc('month', d)` when d is SYSDATE, SYSTIMESTAMP or a variable declared as a date "
    "or timestamp.",
    see=("O2PG-SQ-011",),
)
rewrite(
    "O2PG-CV-038",
    "ADD_MONTHS",
    "`ADD_MONTHS(d, n)` becomes `(d + interval '1 month' * (n))`.",
    note=(
        "ADD_MONTHS moves the last day of a month to the last day of the target month "
        "(28 Feb + 1 month = 31 Mar); interval arithmetic gives 28 Mar."
    ),
    see=("O2PG-SQ-011",),
)
rewrite(
    "O2PG-CV-039",
    "Date plus or minus a number",
    "`d + n` and `d - n`, where d is SYSDATE, SYSTIMESTAMP, a converted TRUNC/ADD_MONTHS or a "
    "date variable and n is a number, become `d + interval '1 day' * n`.",
    see=("O2PG-SQ-017",),
)
rewrite(
    "O2PG-CV-040",
    "TO_DATE",
    "`TO_DATE(s, fmt)` becomes `to_timestamp(s, fmt)::timestamp(0)`, because PostgreSQL's "
    "to_date drops the time of day.",
    note="Most format elements match; check RR, FF and NLS-dependent elements.",
    see=("O2PG-SQ-012",),
)
rewrite(
    "O2PG-CV-041",
    "One-argument TO_CHAR and TO_NUMBER",
    "`TO_CHAR(x)` becomes `CAST(x AS text)` and `TO_NUMBER(x)` becomes `CAST(x AS numeric)`; "
    "PostgreSQL's versions need a format argument.",
    note="TO_CHAR of a DATE used NLS_DATE_FORMAT in Oracle; the cast gives ISO format.",
    see=("O2PG-SQ-012",),
)
rewrite(
    "O2PG-CV-042",
    "DBMS_LOB.SUBSTR and GETLENGTH",
    "`DBMS_LOB.SUBSTR(lob, amount, offset)` becomes `substr(lob, offset, amount)` (note the "
    "argument order) and `DBMS_LOB.GETLENGTH(lob)` becomes `length(lob)`.",
    see=("O2PG-PL-008",),
)
rewrite(
    "O2PG-CV-043",
    "DBMS_LOCK.SLEEP",
    "`DBMS_LOCK.SLEEP(n);` and `DBMS_SESSION.SLEEP(n);` become `PERFORM pg_sleep(n);`.",
)
rewrite(
    "O2PG-CV-044",
    "SYS_GUID",
    "`SYS_GUID()` becomes `gen_random_uuid()` (core PostgreSQL 13+).",
    note="SYS_GUID returns RAW(16); gen_random_uuid returns uuid.",
)
rewrite(
    "O2PG-CV-045",
    "Two-argument INSTR",
    "`INSTR(s, sub)` becomes `strpos(s, sub)`.",
)
rewrite(
    "O2PG-CV-046",
    "MINUS",
    "The `MINUS` set operator becomes `EXCEPT`.",
    see=("O2PG-SQ-010",),
)
rewrite(
    "O2PG-CV-047",
    "MERGE target aliases in UPDATE SET",
    "In `MERGE INTO t a ... UPDATE SET a.col = ...`, the alias is removed from the SET targets; "
    "PostgreSQL does not allow qualified target columns.",
    see=("O2PG-SQ-006",),
)
rewrite(
    "O2PG-CV-048",
    "INSERT ... VALUES record",
    "`INSERT INTO t VALUES rec;` becomes `INSERT INTO t VALUES (rec.*);`.",
)
rewrite(
    "O2PG-CV-049",
    "Quoted identifiers",
    'Quoted identifiers such as `"EMPLOYEES"` are folded the same way as `o2pg map-types` '
    "folds table and column names (lower case, quoted only when needed).",
    see=("O2PG-OB-016",),
)
rewrite(
    "O2PG-CV-050",
    "Alternative quoting and N'' literals",
    "`q'[it's]'` becomes `'it''s'` and `N'text'` becomes `'text'`.",
)
rewrite(
    "O2PG-CV-051",
    "Nested comment openers",
    "A `/*` inside a block comment becomes `/ *`. PostgreSQL nests block comments; Oracle "
    "does not.",
)
rewrite(
    "O2PG-CV-052",
    "SUBSTR from position 0",
    "`SUBSTR(s, 0, n)` becomes `substr(s, 1, n)`. Oracle treats position 0 as 1; PostgreSQL "
    "would return one character less.",
)
rewrite(
    "O2PG-CV-053",
    "Two-argument REPLACE",
    "`REPLACE(s, x)` becomes `replace(s, x, '')`; Oracle's two-argument form removes x.",
)

# --------------------------------------------------------------------------------------
# Review points
# --------------------------------------------------------------------------------------

review(
    "O2PG-RV-001",
    "Object type not converted",
    "this object is not converted (see the reason); the original source is kept below as a comment",
    blocking=True,
    see=("O2PG-OB-005", "O2PG-OB-006", "O2PG-OB-012", "O2PG-PL-015"),
)
review(
    "O2PG-RV-002",
    "Autonomous transaction",
    "PRAGMA AUTONOMOUS_TRANSACTION has no PostgreSQL equivalent; pg_background is not available "
    "on Azure Flexible Server and a postgres_fdw/dblink loopback is a workaround with its own "
    "costs. Prefer a redesign (log after the transaction, RAISE LOG, or the application)",
    blocking=True,
    see=("O2PG-PL-003",),
)
review(
    "O2PG-RV-003",
    "BULK COLLECT",
    "BULK COLLECT has no PL/pgSQL equivalent; use SELECT array_agg(col) INTO arr, a cursor loop, "
    "or better a single set-based statement",
    blocking=True,
    see=("O2PG-PL-004",),
)
review(
    "O2PG-RV-004",
    "FORALL",
    "FORALL has no PL/pgSQL equivalent; replace the loop with one set-based statement, e.g. "
    "UPDATE ... FROM unnest(arr) or INSERT ... SELECT",
    blocking=True,
    see=("O2PG-PL-004",),
)
review(
    "O2PG-RV-005",
    "ROWNUM",
    "ROWNUM has no PostgreSQL equivalent; use LIMIT (or FETCH FIRST n ROWS ONLY) for top-n and "
    "row_number() OVER (...) for numbering",
    blocking=True,
    see=("O2PG-SQ-002",),
)
review(
    "O2PG-RV-006",
    "CONNECT BY",
    "hierarchical query: rewrite CONNECT BY / START WITH / PRIOR / LEVEL as WITH RECURSIVE",
    blocking=True,
    see=("O2PG-SQ-001",),
)
review(
    "O2PG-RV-007",
    "Oracle outer join operator",
    "the (+) outer join operator does not exist in PostgreSQL; rewrite as LEFT/RIGHT JOIN",
    blocking=True,
    see=("O2PG-SQ-005",),
)
review(
    "O2PG-RV-008",
    "Pipelined functions",
    "PIPELINED / PIPE ROW: rewrite as RETURNS SETOF (or RETURNS TABLE) with RETURN NEXT",
    blocking=True,
    see=("O2PG-PL-016",),
)
review(
    "O2PG-RV-009",
    "Collection and record types",
    "PL/SQL collection, record or subtype declaration: use a PostgreSQL array, a composite type "
    "(CREATE TYPE) or a temporary table",
    blocking=True,
    see=("O2PG-PL-018", "O2PG-OB-012"),
)
review(
    "O2PG-RV-010",
    "Data type with no mapping",
    "no automatic mapping for this data type",
    blocking=True,
    see=("O2PG-DT-017",),
)
review(
    "O2PG-RV-011",
    "Package state",
    "package-level variable: PostgreSQL has no package state; keep it in a table or temporary "
    "table, or in a custom setting via set_config()/current_setting()",
    blocking=True,
    see=("O2PG-PL-002",),
)
review(
    "O2PG-RV-012",
    "User-defined exceptions",
    "user-defined exception: PL/pgSQL cannot declare exceptions; RAISE a custom SQLSTATE "
    "(e.g. RAISE EXCEPTION USING ERRCODE = 'U0001') and catch WHEN SQLSTATE 'U0001'",
    blocking=True,
)
review(
    "O2PG-RV-013",
    "Other PRAGMA",
    "PRAGMA has no PL/pgSQL equivalent (EXCEPTION_INIT: catch the matching SQLSTATE instead)",
    blocking=True,
)
review(
    "O2PG-RV-014",
    "Oracle-supplied package",
    "Oracle-supplied package call with no direct PostgreSQL equivalent",
    blocking=True,
    see=("O2PG-PL-008", "O2PG-PL-009", "O2PG-PL-010", "O2PG-PL-011", "O2PG-PL-012", "O2PG-AZ-003"),
)
review(
    "O2PG-RV-015",
    "Oracle-only function",
    "Oracle-only function with no PostgreSQL built-in (orafce provides some of them on Azure)",
    blocking=True,
    see=("O2PG-AZ-005", "O2PG-SQ-011", "O2PG-SQ-015"),
)
review(
    "O2PG-RV-016",
    "Transaction control in a function or trigger",
    "COMMIT/ROLLBACK is not allowed in a PostgreSQL function or trigger; move transaction "
    "control to the caller or make this a procedure",
    blocking=True,
    see=("O2PG-PL-014",),
)
review(
    "O2PG-RV-017",
    "Savepoints and SET TRANSACTION",
    "PL/pgSQL has no SAVEPOINT or SET TRANSACTION statement; use a nested BEGIN ... EXCEPTION "
    "block for partial rollback",
    blocking=True,
    see=("O2PG-PL-014",),
)
review(
    "O2PG-RV-018",
    "GOTO",
    "PL/pgSQL has no GOTO; restructure with loops, EXIT/CONTINUE and labels",
    blocking=True,
    see=("O2PG-PL-017",),
)
review(
    "O2PG-RV-019",
    "Nested subprograms",
    "nested procedure or function: PL/pgSQL cannot declare subprograms inside a block; move it "
    "to its own routine",
    blocking=True,
)
review(
    "O2PG-RV-020",
    "Package initialisation section",
    "package initialisation block: there is no package load event in PostgreSQL; run this "
    "explicitly where the state is first needed",
    blocking=True,
    see=("O2PG-PL-002",),
)
review(
    "O2PG-RV-021",
    "Cursor attribute without a direct equivalent",
    "cursor attribute with no direct equivalent: use GET DIAGNOSTICS n = ROW_COUNT after the "
    "statement, or track the count yourself",
    blocking=True,
)
review(
    "O2PG-RV-022",
    "DECODE that needs a hand-written CASE",
    "DECODE with a non-literal search value on a complex expression: write the CASE by hand so "
    "the expression is evaluated once and NULLs still match",
    blocking=True,
    see=("O2PG-SQ-003",),
)
review(
    "O2PG-RV-023",
    "UPDATING('column')",
    "UPDATING('col') has no TG_OP equivalent; use an UPDATE OF col trigger or compare "
    "NEW.col IS DISTINCT FROM OLD.col (which is not quite the same)",
    blocking=True,
)
review(
    "O2PG-RV-024",
    "Function with OUT parameters",
    "a PostgreSQL function cannot have OUT parameters and a separate RETURN value; return a "
    "composite or make it a procedure",
    blocking=True,
)
review(
    "O2PG-RV-025",
    "Parameter default order",
    "PostgreSQL requires every parameter after one with a DEFAULT to have a default too; "
    "reorder the parameters and update named-notation callers",
    blocking=True,
)
review(
    "O2PG-RV-026",
    "ROWID",
    "ROWID has no stable PostgreSQL equivalent; use the primary key",
    blocking=True,
    see=("O2PG-DT-013",),
)
review(
    "O2PG-RV-027",
    "Flashback query",
    "AS OF TIMESTAMP/SCN flashback queries have no PostgreSQL equivalent",
    blocking=True,
    see=("O2PG-SQ-016",),
)
review(
    "O2PG-RV-028",
    "DUAL left in place",
    "this reference to DUAL could not be removed mechanically",
    blocking=True,
    see=("O2PG-SQ-009",),
)
review(
    "O2PG-RV-029",
    "Unsupported routine or trigger form",
    "this routine or trigger form is not converted",
    blocking=True,
)
review(
    "O2PG-RV-030",
    "Negative SUBSTR position",
    "a negative SUBSTR position counts from the end in Oracle but not in PostgreSQL; use "
    "right() or compute the position",
    blocking=True,
)
review(
    "O2PG-RV-031",
    "MERGE needs a check",
    "MERGE runs on PostgreSQL 15+ (Azure Flexible Server offers 15, 16 and 17); check the ON "
    "condition, and that concurrent inserts are handled (INSERT ... ON CONFLICT may fit better)",
    blocking=False,
    see=("O2PG-SQ-006",),
)
review(
    "O2PG-RV-032",
    "MERGE with conditional UPDATE or DELETE",
    "Oracle's UPDATE ... WHERE / DELETE WHERE inside MERGE: move the condition to WHEN MATCHED "
    "AND cond THEN UPDATE / DELETE",
    blocking=True,
    see=("O2PG-SQ-006",),
)
review(
    "O2PG-RV-033",
    "Dynamic SQL",
    "dynamic SQL: the SQL text is not converted; check it is valid PostgreSQL and change :name "
    "binds to $1, $2 with USING",
    blocking=False,
    see=("O2PG-PL-007",),
)
review(
    "O2PG-RV-034",
    "Empty string literal",
    "'' is NULL in Oracle but an empty string in PostgreSQL; comparisons with '' and assignments "
    "of '' behave differently",
    blocking=False,
    see=("O2PG-SQ-008",),
)
review(
    "O2PG-RV-035",
    "Concatenation with possible NULLs",
    "|| returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use "
    "concat() or COALESCE where an operand can be NULL",
    blocking=False,
    see=("O2PG-SQ-018",),
)
review(
    "O2PG-RV-036",
    "SQLCODE values",
    "SQLSTATE is a five-character code such as 'P0002', not a negative number; check "
    "comparisons and anything that stores it",
    blocking=False,
    see=("O2PG-PL-013",),
)
review(
    "O2PG-RV-037",
    "SQLERRM with an argument",
    "SQLERRM(code) does not exist in PL/pgSQL; SQLERRM only reports the current error",
    blocking=False,
)
review(
    "O2PG-RV-038",
    "TRUNC of unknown type",
    "TRUNC on a value of unknown type: PostgreSQL's trunc() is numeric only; use "
    "date_trunc('day', x) if x is a date or timestamp",
    blocking=False,
    see=("O2PG-SQ-011",),
)
review(
    "O2PG-RV-039",
    "Date arithmetic to check",
    "date arithmetic: timestamp - timestamp is an interval in PostgreSQL (a number of days in "
    "Oracle), and timestamp +/- number needs interval '1 day' * n",
    blocking=False,
    see=("O2PG-SQ-017",),
)
review(
    "O2PG-RV-040",
    "Integer division",
    "both operands look like integers: PostgreSQL truncates integer division (7/2 = 3), "
    "Oracle does not (3.5); cast one side to numeric",
    blocking=False,
)
review(
    "O2PG-RV-041",
    "LEAST and GREATEST with NULLs",
    "LEAST/GREATEST ignore NULL arguments in PostgreSQL; Oracle returns NULL if any argument is "
    "NULL",
    blocking=False,
)
review(
    "O2PG-RV-042",
    "Regular expression functions",
    "REGEXP_REPLACE replaces every match in Oracle but only the first in PostgreSQL unless "
    "the 'g' flag is given; check flags and the PostgreSQL 15+ regexp_* signatures",
    blocking=False,
)
review(
    "O2PG-RV-043",
    "Single-argument TO_DATE",
    "TO_DATE without a format relied on NLS_DATE_FORMAT; add an explicit format",
    blocking=True,
    see=("O2PG-SQ-012",),
)
review(
    "O2PG-RV-044",
    "Three- and four-argument INSTR",
    "INSTR with a start position or occurrence has no PostgreSQL built-in (orafce has instr)",
    blocking=True,
)
