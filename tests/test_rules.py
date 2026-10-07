"""Every rule gets at least one positive and one negative case.

Negative cases deliberately include near-misses: the keyword inside a comment
or string literal, a similar but harmless construct, or the same data shape
with the one property that matters flipped.
"""

from __future__ import annotations

import pytest

from helpers import (
    code,
    column,
    constraint,
    index,
    inventory,
    partitioned,
    section,
    table,
)
from o2pg.inventory import Inventory
from o2pg.rules import all_rules, get_rule

E28 = 10**28 - 1

CASES: dict[str, tuple[Inventory, Inventory]] = {
    # ---- datatypes -------------------------------------------------------
    "O2PG-DT-001": (
        inventory(column("NUMBER")),
        inventory(column("NUMBER", data_precision=10, data_scale=2)),
    ),
    "O2PG-DT-002": (
        inventory(column("BINARY_DOUBLE")),
        inventory(column("NUMBER", data_precision=5, data_scale=0)),
    ),
    "O2PG-DT-003": (inventory(column("DATE")), inventory(column("TIMESTAMP(6)"))),
    "O2PG-DT-004": (
        inventory(column("TIMESTAMP(6) WITH TIME ZONE")),
        inventory(column("TIMESTAMP(6)")),
    ),
    "O2PG-DT-005": (
        inventory(column("VARCHAR2", char_used="B")),
        inventory(column("VARCHAR2", char_used="C")),
    ),
    "O2PG-DT-006": (
        inventory(column("VARCHAR2", nullable="N")),
        inventory(column("VARCHAR2", nullable="Y")),
    ),
    "O2PG-DT-007": (
        inventory(column("CHAR", char_length=10)),
        inventory(column("CHAR", char_length=1)),
    ),
    "O2PG-DT-008": (inventory(column("LONG RAW")), inventory(column("RAW"))),
    "O2PG-DT-009": (inventory(column("CLOB")), inventory(column("VARCHAR2"))),
    "O2PG-DT-010": (inventory(column("BFILE")), inventory(column("BLOB"))),
    "O2PG-DT-011": (
        inventory(column("XMLTYPE", data_type_owner="SYS")),
        inventory(column("CLOB")),
    ),
    "O2PG-DT-012": (
        inventory(column("SDO_GEOMETRY", data_type_owner="MDSYS")),
        inventory(column("NUMBER")),
    ),
    "O2PG-DT-013": (inventory(column("UROWID")), inventory(column("VARCHAR2"))),
    "O2PG-DT-014": (
        inventory(column("NUMBER", virtual="YES", data_default="A + B")),
        inventory(column("NUMBER", virtual="NO", data_default="0")),
    ),
    "O2PG-DT-015": (
        inventory(column("DATE", data_default="SYSDATE")),
        inventory(column("VARCHAR2", data_default="'SYSDATE_LABEL'")),
    ),
    "O2PG-DT-016": (
        inventory(column("ADDRESS_T", data_type_owner="APP")),
        inventory(column("XMLTYPE", data_type_owner="SYS")),
    ),
    "O2PG-DT-017": (
        inventory(column("ANYDATA", data_type_owner="SYS")),
        inventory(column("DATE")),
    ),
    # ---- plsql -----------------------------------------------------------
    "O2PG-PL-001": (
        inventory(code("PACKAGE p AS\nEND p;\n", kind="PACKAGE")),
        inventory(code("PROCEDURE p IS BEGIN NULL; END;\n", kind="PROCEDURE")),
    ),
    "O2PG-PL-002": (
        inventory(code("PACKAGE p AS\n  g_count NUMBER := 0;\nEND p;\n", kind="PACKAGE")),
        inventory(
            code(
                "PACKAGE p AS\n  c_max CONSTANT NUMBER := 10;\n  PROCEDURE run;\nEND p;\n",
                kind="PACKAGE",
            )
        ),
    ),
    "O2PG-PL-003": (
        inventory(code("PROCEDURE log IS\n  PRAGMA AUTONOMOUS_TRANSACTION;\nBEGIN NULL; END;")),
        inventory(code("PROCEDURE log IS\n  -- PRAGMA AUTONOMOUS_TRANSACTION;\nBEGIN NULL; END;")),
    ),
    "O2PG-PL-004": (
        inventory(code("SELECT id BULK COLLECT INTO l_ids FROM t;")),
        inventory(code("l_msg := 'use BULK COLLECT here';")),
    ),
    "O2PG-PL-005": (
        inventory(code("FUNCTION f RETURN SYS_REFCURSOR IS")),
        inventory(code("FUNCTION f RETURN NUMBER IS -- not a SYS_REFCURSOR")),
    ),
    "O2PG-PL-006": (
        inventory(code("l_row employees%ROWTYPE;")),
        inventory(code("l_row NUMBER;")),
    ),
    "O2PG-PL-007": (
        inventory(code("EXECUTE IMMEDIATE 'TRUNCATE TABLE t';")),
        inventory(code("/* EXECUTE IMMEDIATE was removed */ NULL;")),
    ),
    "O2PG-PL-008": (
        inventory(code("x := DBMS_LOB.SUBSTR(c, 10, 1);")),
        inventory(code("x := 'DBMS_LOB.SUBSTR';")),
    ),
    "O2PG-PL-009": (
        inventory(code("f := UTL_FILE.FOPEN('DIR', 'a.txt', 'w');")),
        inventory(code("-- UTL_FILE.FOPEN('DIR', 'a.txt', 'w');\nNULL;")),
    ),
    "O2PG-PL-010": (
        inventory(code("r := UTL_HTTP.REQUEST('http://x');")),
        inventory(code("r := UTL_RAW.CAST_TO_RAW('x');")),
    ),
    "O2PG-PL-011": (
        inventory(code("DBMS_SCHEDULER.CREATE_JOB(job_name => 'J');")),
        inventory(code("-- DBMS_SCHEDULER.CREATE_JOB\nNULL;")),
    ),
    "O2PG-PL-012": (
        inventory(code("DBMS_OUTPUT.PUT_LINE('hi');")),
        inventory(code("x := DBMS_LOB.GETLENGTH(c);")),
    ),
    "O2PG-PL-013": (
        inventory(code("RAISE_APPLICATION_ERROR(-20001, 'bad');")),
        inventory(code("EXCEPTION WHEN OTHERS THEN RAISE;")),
    ),
    "O2PG-PL-014": (
        inventory(code("UPDATE t SET a = 1;\nCOMMIT;")),
        inventory(code("SAVEPOINT a;\nROLLBACK TO SAVEPOINT a;")),
    ),
    "O2PG-PL-015": (
        inventory(code("PACKAGE BODY p wrapped\na000000\n1\nabcd\n")),
        inventory(code("PACKAGE BODY p AS\n  -- unwrapped\nEND p;\n")),
    ),
    "O2PG-PL-016": (
        inventory(code("FUNCTION f RETURN t_tab PIPELINED IS")),
        inventory(code("FUNCTION f RETURN t_tab IS")),
    ),
    "O2PG-PL-017": (
        inventory(code("IF x THEN GOTO done; END IF;\n<<done>>\nNULL;")),
        inventory(code("<<outer>>\nLOOP EXIT outer; END LOOP;")),
    ),
    "O2PG-PL-018": (
        inventory(code("TYPE t IS TABLE OF NUMBER INDEX BY PLS_INTEGER;")),
        inventory(code("TYPE t IS TABLE OF NUMBER;")),
    ),
    # ---- sql dialect -----------------------------------------------------
    "O2PG-SQ-001": (
        inventory(code("SELECT * FROM e START WITH m IS NULL CONNECT BY PRIOR id = m", "VIEW")),
        inventory(code("SELECT * FROM e WHERE m IS NULL", "VIEW")),
    ),
    "O2PG-SQ-002": (
        inventory(code("SELECT * FROM t WHERE ROWNUM <= 10", "VIEW")),
        inventory(code("SELECT row_number() OVER (ORDER BY id) rn FROM t", "VIEW")),
    ),
    "O2PG-SQ-003": (
        inventory(code("SELECT DECODE(a, 1, 'x', 'y') FROM t", "VIEW")),
        inventory(code("SELECT 'DECODE(a)' FROM t", "VIEW")),
    ),
    "O2PG-SQ-004": (
        inventory(code("SELECT NVL(a, 0) FROM t", "VIEW")),
        inventory(code("SELECT COALESCE(a, 0) FROM t", "VIEW")),
    ),
    "O2PG-SQ-005": (
        inventory(code("SELECT * FROM a, b WHERE a.id = b.id (+)", "VIEW")),
        inventory(code("SELECT * FROM a LEFT JOIN b ON a.id = b.id", "VIEW")),
    ),
    "O2PG-SQ-006": (
        inventory(
            code("MERGE INTO t USING s ON (t.id = s.id) WHEN MATCHED THEN UPDATE SET a = 1;")
        ),
        inventory(code("INSERT INTO t SELECT * FROM s;")),
    ),
    "O2PG-SQ-007": (
        inventory(code("SELECT SYSDATE FROM t", "VIEW")),
        inventory(code("SELECT CURRENT_TIMESTAMP, sysdate_col FROM t", "VIEW")),
    ),
    "O2PG-SQ-008": (
        inventory(code("IF x = '' THEN y := 1; END IF;")),
        inventory(code("IF x = 'it''s' THEN y := ''''; END IF;")),
    ),
    "O2PG-SQ-009": (
        inventory(code("SELECT 1 INTO x FROM DUAL;")),
        inventory(code("SELECT 1 INTO x FROM dual_log;")),
    ),
    "O2PG-SQ-010": (
        inventory(code("SELECT a FROM t MINUS SELECT a FROM s", "VIEW")),
        inventory(code("SELECT a FROM t EXCEPT SELECT a FROM s", "VIEW")),
    ),
    "O2PG-SQ-011": (
        inventory(code("SELECT ADD_MONTHS(d, 1) FROM t", "VIEW")),
        inventory(code("SELECT date_trunc('month', d) FROM t", "VIEW")),
    ),
    "O2PG-SQ-012": (
        inventory(code("SELECT TO_DATE(s, 'YYYY-MM-DD') FROM t", "VIEW")),
        inventory(code("SELECT CAST(s AS DATE) FROM t", "VIEW")),
    ),
    "O2PG-SQ-013": (
        inventory(code("SELECT /*+ FULL(t) */ * FROM t", "VIEW")),
        inventory(code("SELECT /* not a hint */ * FROM t", "VIEW")),
    ),
    "O2PG-SQ-014": (
        inventory(code("x := my_seq.NEXTVAL;")),
        inventory(code("x := 'my_seq.NEXTVAL';")),
    ),
    "O2PG-SQ-015": (
        inventory(code("x := SYS_CONTEXT('USERENV', 'IP_ADDRESS');")),
        inventory(code("SELECT user_id, t.user FROM t WHERE username = 'USER';")),
    ),
    "O2PG-SQ-016": (
        inventory(code("SELECT * FROM t AS OF TIMESTAMP SYSTIMESTAMP - 1", "VIEW")),
        inventory(code("SELECT ts AS of_date FROM t", "VIEW")),
    ),
    "O2PG-SQ-017": (
        inventory(code("SELECT * FROM t WHERE d > SYSDATE - 7", "VIEW")),
        inventory(code("SELECT * FROM t WHERE d > SYSDATE - p_days", "VIEW")),
    ),
    "O2PG-SQ-018": (
        inventory(code("SELECT a || b FROM t", "VIEW")),
        inventory(code("SELECT concat(a, b), '||' FROM t", "VIEW")),
    ),
    # ---- objects ---------------------------------------------------------
    "O2PG-OB-001": (
        inventory(section("synonyms", owner="APP", name="S", table_owner="HR", table_name="T")),
        inventory(section("synonyms", owner="PUBLIC", name="S", table_owner="HR", table_name="T")),
    ),
    "O2PG-OB-002": (
        inventory(section("synonyms", owner="PUBLIC", name="S", table_owner="HR", table_name="T")),
        inventory(section("synonyms", owner="APP", name="S", table_owner="HR", table_name="T")),
    ),
    "O2PG-OB-003": (
        inventory(section("db_links", owner="APP", name="REMOTE", host="db:1521/X")),
        inventory(section("synonyms", owner="APP", name="S", table_owner="HR", table_name="T")),
    ),
    "O2PG-OB-004": (
        inventory(
            section(
                "triggers",
                owner="APP",
                name="TR",
                table_name="T1",
                trigger_type="BEFORE EACH ROW",
                triggering_event="INSERT",
                base_object_type="TABLE",
            )
        ),
        inventory(
            section(
                "triggers",
                owner="APP",
                name="TR",
                table_name="T1",
                trigger_type="BEFORE STATEMENT",
                triggering_event="INSERT",
                base_object_type="TABLE",
            )
        ),
    ),
    "O2PG-OB-005": (
        inventory(section("triggers", owner="APP", name="TR", trigger_type="COMPOUND")),
        inventory(section("triggers", owner="APP", name="TR", trigger_type="AFTER EACH ROW")),
    ),
    "O2PG-OB-006": (
        inventory(
            section(
                "triggers",
                owner="APP",
                name="TR",
                trigger_type="AFTER EVENT",
                base_object_type="SCHEMA",
            )
        ),
        inventory(
            section(
                "triggers",
                owner="APP",
                name="TR",
                trigger_type="AFTER EVENT",
                base_object_type="TABLE",
            )
        ),
    ),
    "O2PG-OB-007": (
        inventory(section("sequences", owner="APP", name="S", max_value=E28)),
        inventory(section("sequences", owner="APP", name="S", max_value=1000)),
    ),
    "O2PG-OB-008": (
        inventory(section("sequences", owner="APP", name="S", last_number=42)),
        inventory(table()),
    ),
    "O2PG-OB-009": (
        inventory(
            section("mviews", owner="APP", name="MV", refresh_method="FAST", refresh_mode="COMMIT")
        ),
        inventory(
            section(
                "mviews", owner="APP", name="MV", refresh_method="COMPLETE", refresh_mode="DEMAND"
            )
        ),
    ),
    "O2PG-OB-010": (
        inventory(
            section("mviews", owner="APP", name="MV", refresh_method="FAST", refresh_mode="DEMAND")
        ),
        inventory(
            section(
                "mviews", owner="APP", name="MV", refresh_method="COMPLETE", refresh_mode="DEMAND"
            )
        ),
    ),
    "O2PG-OB-011": (
        inventory(table(temporary="Y", duration="SYS$SESSION")),
        inventory(table(temporary="N")),
    ),
    "O2PG-OB-012": (
        inventory(section("types", owner="APP", name="ADDR_T", typecode="OBJECT", methods=2)),
        inventory(table()),
    ),
    "O2PG-OB-013": (
        inventory(section("invalid_objects", owner="APP", name="OLD", type="PACKAGE BODY")),
        inventory(table()),
    ),
    "O2PG-OB-014": (
        inventory(table()),
        inventory(table(), constraint("T1_PK", "P", "ID")),
    ),
    "O2PG-OB-015": (
        inventory(table(name="T" * 64)),
        inventory(table(name="T" * 63)),
    ),
    "O2PG-OB-016": (
        inventory(column("VARCHAR2", name="Notes")),
        inventory(column("VARCHAR2", name="NOTES$2")),
    ),
    "O2PG-OB-017": (
        inventory(column("NUMBER", name="LIMIT")),
        inventory(column("NUMBER", name="CREDIT_LIMIT")),
    ),
    # ---- partitioning ----------------------------------------------------
    "O2PG-PT-001": (
        inventory(partitioned(ptype="LIST")),
        inventory(partitioned(ptype="RANGE", interval="NUMTOYMINTERVAL(1,'MONTH')")),
    ),
    "O2PG-PT-002": (
        inventory(partitioned(ptype="RANGE", interval="NUMTODSINTERVAL(1,'DAY')")),
        inventory(partitioned(ptype="RANGE")),
    ),
    "O2PG-PT-003": (
        inventory(partitioned(ptype="REFERENCE", keys="", ref_constraint_name="FK1")),
        inventory(partitioned(ptype="HASH")),
    ),
    "O2PG-PT-004": (
        inventory(partitioned(subpartitioning_type="LIST")),
        inventory(partitioned(subpartitioning_type="NONE")),
    ),
    "O2PG-PT-005": (
        inventory(partitioned(keys="D"), constraint("T1_PK", "P", "ID")),
        inventory(partitioned(keys="D"), constraint("T1_PK", "P", "ID,D")),
    ),
    "O2PG-PT-006": (
        inventory(partitioned(), index("T1_IX", "A", partitioned="NO")),
        inventory(partitioned(), index("T1_IX", "A", partitioned="YES")),
    ),
    # ---- security --------------------------------------------------------
    "O2PG-SE-001": (
        inventory(
            section(
                "policies", object_owner="APP", object_name="T1", policy_name="P", function="APP.F"
            )
        ),
        inventory(table()),
    ),
    "O2PG-SE-002": (
        inventory(
            section(
                "encrypted_columns",
                owner="APP",
                table_name="T1",
                column_name="C",
                encryption_alg="AES 256 bits key",
            )
        ),
        inventory(table()),
    ),
    "O2PG-SE-003": (
        inventory(
            section("grants", owner="APP", grantee="PUBLIC", privilege="SELECT", object_count=3)
        ),
        inventory(
            section("grants", owner="APP", grantee="APP_RO", privilege="SELECT", object_count=3)
        ),
    ),
    "O2PG-SE-004": (
        inventory(code("PROCEDURE p IS\nBEGIN NULL; END;", kind="PROCEDURE")),
        inventory(code("PROCEDURE p AUTHID CURRENT_USER IS\nBEGIN NULL; END;", kind="PROCEDURE")),
    ),
    "O2PG-SE-005": (
        inventory(code("DBMS_RLS.ADD_POLICY(object_name => 'T');")),
        inventory(code("-- DBMS_RLS.ADD_POLICY\nNULL;")),
    ),
    "O2PG-SE-006": (
        inventory(
            section("grants", owner="APP", grantee="APP_RO", privilege="SELECT", object_count=3)
        ),
        inventory(table()),
    ),
    # ---- performance -----------------------------------------------------
    "O2PG-PF-001": (
        inventory(index("IX", "A", index_type="BITMAP")),
        inventory(index("IX", "A", index_type="NORMAL")),
    ),
    "O2PG-PF-002": (
        inventory(index("IX", "SYS_NC00005$", index_type="FUNCTION-BASED NORMAL")),
        inventory(index("IX", "A", index_type="NORMAL")),
    ),
    "O2PG-PF-003": (
        inventory(index("IX", "A", index_type="NORMAL/REV")),
        inventory(index("IX", "A", index_type="NORMAL")),
    ),
    "O2PG-PF-004": (inventory(table(iot_type="IOT")), inventory(table())),
    "O2PG-PF-005": (
        inventory(index("IX", "DOC", index_type="DOMAIN", ityp_name="CONTEXT")),
        inventory(index("IX", "DOC", index_type="NORMAL")),
    ),
    "O2PG-PF-006": (
        inventory(constraint("FK1", "R", "PARENT_ID,KIND")),
        inventory(constraint("FK1", "R", "PARENT_ID,KIND"), index("IX", "KIND,PARENT_ID,X")),
    ),
    "O2PG-PF-007": (
        inventory(table(compression="ENABLED", compress_for="ADVANCED")),
        inventory(table(compression="DISABLED")),
    ),
    "O2PG-PF-008": (inventory(table(num_rows=60_000_000)), inventory(table(num_rows=1000))),
    "O2PG-PF-009": (inventory(table(cluster_name="EMP_DEPT_CL")), inventory(table())),
    # ---- azure-specific ----------------------------------------------------
    "O2PG-AZ-001": (
        inventory(
            section(
                "scheduler_jobs",
                owner="APP",
                name="J",
                job_type="PLSQL_BLOCK",
                job_action="BEGIN NULL; END;",
                repeat_interval="FREQ=DAILY",
            )
        ),
        inventory(
            section(
                "scheduler_jobs",
                owner="APP",
                name="J",
                job_type="EXECUTABLE",
                job_action="/bin/true",
            )
        ),
    ),
    "O2PG-AZ-002": (
        inventory(
            section(
                "scheduler_jobs",
                owner="APP",
                name="J",
                job_type="EXECUTABLE",
                job_action="/bin/true",
            )
        ),
        inventory(
            section(
                "scheduler_jobs",
                owner="APP",
                name="J",
                job_type="PLSQL_BLOCK",
                job_action="BEGIN NULL; END;",
            )
        ),
    ),
    "O2PG-AZ-003": (
        inventory(section("queues", owner="APP", name="Q", queue_table="QT")),
        inventory(code("-- DBMS_AQ.ENQUEUE\nNULL;")),
    ),
    "O2PG-AZ-004": (
        inventory(metadata={"db_name": "DB", "character_set": "WE8ISO8859P1"}),
        inventory(metadata={"db_name": "DB", "character_set": "AL32UTF8"}),
    ),
    "O2PG-AZ-005": (
        inventory(code("SELECT NVL(a, 0) FROM t", "VIEW")),
        inventory(code("SELECT COALESCE(a, 0) FROM t", "VIEW")),
    ),
    "O2PG-AZ-006": (
        inventory(code("public class Hello { }", kind="JAVA SOURCE")),
        inventory(code("FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END;", kind="FUNCTION")),
    ),
}


def test_every_rule_has_cases() -> None:
    assert set(CASES) == {r.id for r in all_rules()}


@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_rule_fires_on_positive_case(rule_id: str) -> None:
    positive, _ = CASES[rule_id]
    finding = get_rule(rule_id).evaluate(positive)
    assert finding is not None
    assert finding.hits
    for hit in finding.hits:
        assert hit.object_name
        assert hit.object_type


@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_rule_is_silent_on_negative_case(rule_id: str) -> None:
    _, negative = CASES[rule_id]
    assert get_rule(rule_id).evaluate(negative) is None


@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_rule_is_silent_on_empty_inventory(rule_id: str) -> None:
    assert get_rule(rule_id).evaluate(Inventory()) is None


# ---- targeted behaviour beyond the basic positive/negative pairs -------------


@pytest.mark.parametrize(
    "rule_id",
    ["O2PG-PL-003", "O2PG-PL-004", "O2PG-PL-009", "O2PG-PL-013", "O2PG-SE-005"],
)
def test_plsql_only_rules_ignore_views(rule_id: str) -> None:
    text = (
        "PRAGMA AUTONOMOUS_TRANSACTION BULK COLLECT UTL_FILE.FOPEN "
        "RAISE_APPLICATION_ERROR( DBMS_RLS.ADD_POLICY"
    )
    assert get_rule(rule_id).evaluate(inventory(code(text, "VIEW"))) is None


def test_pattern_hit_reports_count_and_first_line() -> None:
    finding = get_rule("O2PG-SQ-004").evaluate(
        inventory(code("BEGIN\n  x := 1;\n  y := NVL(a, 0);\n  z := NVL2(a, 1, 0);\nEND;"))
    )
    assert finding is not None
    assert finding.hits[0].detail == "2 matches, first at line 3"


def test_number_integer_declaration_is_flagged() -> None:
    inv = inventory(column("NUMBER", data_precision=None, data_scale=0))
    finding = get_rule("O2PG-DT-001").evaluate(inv)
    assert finding is not None
    assert "numeric(38)" in finding.hits[0].detail


def test_identity_default_is_not_reported_as_oracle_default() -> None:
    inv = inventory(column("NUMBER", identity="YES", data_default='"APP"."ISEQ$$_1".nextval'))
    assert get_rule("O2PG-DT-015").evaluate(inv) is None


def test_sequence_default_is_reported() -> None:
    inv = inventory(column("NUMBER", data_default='"APP"."S1"."NEXTVAL"'))
    assert get_rule("O2PG-DT-015").evaluate(inv) is not None


def test_user_defined_type_is_not_also_unmapped() -> None:
    inv = inventory(
        column("ADDRESS_T", data_type_owner="APP"),
        section("types", owner="APP", name="ADDRESS_T", typecode="OBJECT"),
    )
    assert get_rule("O2PG-DT-016").evaluate(inv) is not None
    assert get_rule("O2PG-DT-017").evaluate(inv) is None


def test_type_name_match_without_owner_counts_as_user_defined() -> None:
    inv = inventory(column("PHONE_LIST_T"), section("types", owner="APP", name="PHONE_LIST_T"))
    assert get_rule("O2PG-DT-016").evaluate(inv) is not None


def test_package_state_ignores_parameter_lines() -> None:
    spec = (
        "PACKAGE p AS\n"
        "  FUNCTION hire (p_name VARCHAR2,\n"
        "                 p_salary NUMBER) RETURN NUMBER;\n"
        "  e_bad EXCEPTION;\n"
        "  TYPE r_t IS RECORD (a NUMBER, b DATE);\n"
        "END p;\n"
    )
    assert get_rule("O2PG-PL-002").evaluate(inventory(code(spec, kind="PACKAGE"))) is None


def test_package_body_state_only_counts_declarations_before_first_subprogram() -> None:
    body = (
        "PACKAGE BODY p AS\n"
        "  g_cache BOOLEAN := FALSE;\n"
        "  PROCEDURE run IS\n"
        "    l_local NUMBER;\n"
        "  BEGIN NULL; END;\n"
        "END p;\n"
    )
    finding = get_rule("O2PG-PL-002").evaluate(inventory(code(body)))
    assert finding is not None
    assert finding.hits[0].detail == "1 variable(s): g_cache"


def test_packages_rule_counts_body_lines() -> None:
    inv = inventory(
        code("PACKAGE p AS\nEND p;\n", kind="PACKAGE", name="P"),
        code("PACKAGE BODY p AS\nBEGIN\nNULL;\nEND p;\n", name="P"),
    )
    finding = get_rule("O2PG-PL-001").evaluate(inv)
    assert finding is not None
    assert finding.hits[0].detail == "6 lines incl. body"


def test_supplied_package_rule_lists_hints() -> None:
    finding = get_rule("O2PG-PL-012").evaluate(
        inventory(code("DBMS_CRYPTO.HASH(x, 3); DBMS_OUTPUT.PUT_LINE(y); UTL_FILE.FCLOSE(f);"))
    )
    assert finding is not None
    detail = finding.hits[0].detail
    assert "DBMS_CRYPTO (pgcrypto)" in detail
    assert "DBMS_OUTPUT (RAISE NOTICE)" in detail
    assert "UTL_FILE" not in detail


def test_db_link_synonym_is_reported_with_link() -> None:
    inv = inventory(
        section("synonyms", owner="APP", name="S", table_owner="R", table_name="T", db_link="L")
    )
    finding = get_rule("O2PG-OB-003").evaluate(inv)
    assert finding is not None
    assert finding.hits[0].detail == "-> R.T@L"
    assert get_rule("O2PG-OB-001").evaluate(inv) is None


def test_db_link_detail_mentions_stored_user() -> None:
    inv = inventory(section("db_links", owner="APP", name="L", username="SCOTT", host="h"))
    finding = get_rule("O2PG-OB-003").evaluate(inv)
    assert finding is not None
    assert "stored user SCOTT" in finding.hits[0].detail


def test_gtt_transaction_duration() -> None:
    finding = get_rule("O2PG-OB-011").evaluate(
        inventory(table(temporary="Y", duration="SYS$TRANSACTION"))
    )
    assert finding is not None
    assert finding.hits[0].detail == "ON COMMIT DELETE ROWS"


def test_temporary_tables_do_not_need_primary_keys() -> None:
    assert get_rule("O2PG-OB-014").evaluate(inventory(table(temporary="Y"))) is None


def test_long_identifier_uses_bytes_not_characters() -> None:
    name = "É" * 32  # 32 characters, 64 bytes in UTF-8
    assert get_rule("O2PG-OB-015").evaluate(inventory(table(name=name))) is not None


def test_reserved_word_check_skips_indexes() -> None:
    assert get_rule("O2PG-OB-017").evaluate(inventory(index("LIMIT", "A"))) is None


def test_reference_partition_detail_names_the_constraint() -> None:
    finding = get_rule("O2PG-PT-003").evaluate(
        inventory(partitioned(ptype="REFERENCE", keys="", ref_constraint_name="ITEMS_FK"))
    )
    assert finding is not None
    assert "via ITEMS_FK" in finding.hits[0].detail


def test_partition_key_check_skips_tables_without_keys() -> None:
    inv = inventory(partitioned(ptype="REFERENCE", keys=""), constraint("PK", "P", "ID"))
    assert get_rule("O2PG-PT-005").evaluate(inv) is None


def test_fk_covered_by_leading_columns_only() -> None:
    inv = inventory(constraint("FK1", "R", "A"), index("IX", "B,A"))
    assert get_rule("O2PG-PF-006").evaluate(inv) is not None


def test_aq_code_without_queue_rows() -> None:
    finding = get_rule("O2PG-AZ-003").evaluate(inventory(code("DBMS_AQ.ENQUEUE(q, o, p, m, id);")))
    assert finding is not None
    assert finding.hits[0].detail == "calls DBMS_AQ / DBMS_AQADM"


def test_external_c_call_spec() -> None:
    text = 'FUNCTION f RETURN PLS_INTEGER AS LANGUAGE C NAME "f" LIBRARY libf;'
    finding = get_rule("O2PG-AZ-006").evaluate(inventory(code(text, kind="FUNCTION")))
    assert finding is not None
    assert finding.hits[0].detail == "external call spec"


def test_orafce_candidate_lists_functions() -> None:
    finding = get_rule("O2PG-AZ-005").evaluate(
        inventory(code("SELECT DECODE(a,1,2), NVL(b,0) FROM DUAL", "VIEW"))
    )
    assert finding is not None
    assert finding.hits[0].detail == "1 code object(s) use DECODE, DUAL, NVL"


def test_charset_rule_skips_missing_metadata() -> None:
    assert get_rule("O2PG-AZ-004").evaluate(inventory(table())) is None


def test_scheduler_plsql_block_is_scanned_as_code() -> None:
    inv = inventory(
        section(
            "scheduler_jobs",
            owner="APP",
            name="J",
            job_type="PLSQL_BLOCK",
            job_action="BEGIN UTL_FILE.FREMOVE('D', 'x'); END;",
        )
    )
    finding = get_rule("O2PG-PL-009").evaluate(inv)
    assert finding is not None
    assert finding.hits[0].object_type == "SCHEDULER JOB"
