-- HR.EMP_MGMT (PACKAGE + PACKAGE BODY) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 18 (14 blocking). See conversion-report.md.

CREATE SCHEMA IF NOT EXISTS emp_mgmt;
SET search_path = emp_mgmt, hr, public;

-- Package-level declarations (Oracle source kept as comments)
-- session-level state used by triggers and reports
/* O2PG-REVIEW O2PG-RV-011: package-level variable: PostgreSQL has no package state; keep it in a table or temporary table, or in a custom setting via set_config()/current_setting() */
-- g_last_change         DATE;
/* O2PG-REVIEW O2PG-RV-011: package-level variable: PostgreSQL has no package state; keep it in a table or temporary table, or in a custom setting via set_config()/current_setting() */
-- g_hires_this_session  PLS_INTEGER := 0;
-- c_max_salary          CONSTANT NUMBER := 250000;  -- o2pg: inlined where used
-- TYPE emp_cur_t IS REF CURSOR RETURN employees%ROWTYPE;  -- o2pg: now refcursor
/* O2PG-REVIEW O2PG-RV-012: user-defined exception: PL/pgSQL cannot declare exceptions; RAISE a custom SQLSTATE (e.g. RAISE EXCEPTION USING ERRCODE = 'U0001') and catch WHEN SQLSTATE 'U0001' */
-- e_bad_salary          EXCEPTION;
-- FUNCTION hire (p_last_name VARCHAR2, p_email VARCHAR2, p_job_id VARCHAR2,
--                  p_salary NUMBER, p_department_id NUMBER) RETURN NUMBER;  -- o2pg: spec, implemented below
-- PROCEDURE raise_salary (p_department_id NUMBER, p_pct NUMBER);  -- o2pg: spec, implemented below
-- FUNCTION employees_in (p_department_id NUMBER) RETURN emp_cur_t;  -- o2pg: spec, implemented below
-- PROCEDURE log_error (p_msg VARCHAR2);  -- o2pg: spec, implemented below
/* O2PG-REVIEW O2PG-RV-011: package-level variable: PostgreSQL has no package state; keep it in a table or temporary table, or in a custom setting via set_config()/current_setting() */
-- g_cache_loaded  BOOLEAN := FALSE;
/* O2PG-REVIEW O2PG-RV-009: PL/SQL collection, record or subtype declaration: use a PostgreSQL array, a composite type (CREATE TYPE) or a temporary table */
-- TYPE salary_tab_t IS TABLE OF NUMBER INDEX BY PLS_INTEGER;

CREATE OR REPLACE PROCEDURE emp_mgmt.log_error (p_msg varchar)
LANGUAGE plpgsql
AS $$
DECLARE
  /* O2PG-REVIEW O2PG-RV-002: PRAGMA AUTONOMOUS_TRANSACTION has no PostgreSQL equivalent; pg_background is not available on Azure Flexible Server and a postgres_fdw/dblink loopback is a workaround with its own costs. Prefer a redesign (log after the transaction, RAISE LOG, or the application) */
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  INSERT INTO emp_audit_log (audit_ts, action, changed_by, old_values)
  VALUES (clock_timestamp(), 'ERROR', USER, SUBSTR(p_msg, 1, 4000));
  COMMIT;
END;
$$;

CREATE OR REPLACE FUNCTION emp_mgmt.hire (p_last_name varchar, p_email varchar, p_job_id varchar,
               p_salary numeric, p_department_id numeric)
RETURNS numeric
LANGUAGE plpgsql
SET search_path = emp_mgmt, hr, public
AS $$
DECLARE
  c_max_salary          CONSTANT numeric := 250000;
  l_id employees.employee_id%TYPE;
BEGIN
  IF p_salary > c_max_salary THEN
    /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'Salary above band: ' || p_salary, HINT = 'Oracle error code -20001';
  END IF;
  INSERT INTO employees (employee_id, last_name, email, job_id, salary, department_id, hire_date)
  VALUES (nextval('employees_seq'), p_last_name, p_email, p_job_id, p_salary,
          p_department_id, date_trunc('day', LOCALTIMESTAMP(0)))
  RETURNING employee_id INTO l_id;
  /* O2PG-REVIEW O2PG-RV-011: package variable g_hires_this_session: PostgreSQL has no package state; keep it in a table, a temporary table, or a custom setting via set_config()/current_setting() */
  g_hires_this_session := g_hires_this_session + 1;
  /* O2PG-REVIEW O2PG-RV-011: package variable g_last_change: PostgreSQL has no package state; keep it in a table, a temporary table, or a custom setting via set_config()/current_setting() */
  g_last_change := LOCALTIMESTAMP(0);
  /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
  RAISE NOTICE '%', 'Hired ' || p_last_name || ' as ' || l_id;
  RETURN l_id;
EXCEPTION
  WHEN unique_violation THEN
    /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
    CALL log_error('Duplicate email ' || p_email);
    RAISE;
END;
$$;

CREATE OR REPLACE PROCEDURE emp_mgmt.raise_salary (p_department_id numeric, p_pct numeric)
LANGUAGE plpgsql
SET search_path = emp_mgmt, hr, public
AS $$
DECLARE
  c_max_salary          CONSTANT numeric := 250000;
  /* O2PG-REVIEW O2PG-RV-009: PL/SQL collection, record or subtype declaration: use a PostgreSQL array, a composite type (CREATE TYPE) or a temporary table */
  TYPE id_tab_t IS TABLE OF employees.employee_id%TYPE;
  /* O2PG-REVIEW O2PG-RV-010: no automatic mapping for type id_tab_t */
  l_ids   id_tab_t;
  /* O2PG-REVIEW O2PG-RV-010: no automatic mapping for type salary_tab_t */
  l_sals  salary_tab_t;
BEGIN
  /* O2PG-REVIEW O2PG-RV-003: BULK COLLECT has no PL/pgSQL equivalent; use SELECT array_agg(col) INTO arr, a cursor loop, or better a single set-based statement */
  SELECT employee_id, salary BULK COLLECT INTO l_ids, l_sals
    FROM employees
   WHERE department_id = p_department_id
     AND COALESCE(commission_pct, 0) = 0;
  /* O2PG-REVIEW O2PG-RV-004: FORALL has no PL/pgSQL equivalent; replace the loop with one set-based statement, e.g. UPDATE ... FROM unnest(arr) or INSERT ... SELECT */
  FORALL i IN 1 .. l_ids.COUNT
    UPDATE employees
       /* O2PG-REVIEW O2PG-RV-041: LEAST/GREATEST ignore NULL arguments in PostgreSQL; Oracle returns NULL if any argument is NULL */
       SET salary = LEAST(l_sals(i) * (1 + p_pct / 100), c_max_salary)
     WHERE employee_id = l_ids(i);
  /* O2PG-REVIEW O2PG-RV-011: package variable g_last_change: PostgreSQL has no package state; keep it in a table, a temporary table, or a custom setting via set_config()/current_setting() */
  g_last_change := LOCALTIMESTAMP(0);
END;
$$;

CREATE OR REPLACE FUNCTION emp_mgmt.employees_in (p_department_id numeric)
RETURNS refcursor
LANGUAGE plpgsql
SET search_path = emp_mgmt, hr, public
AS $$
DECLARE
  l_cur refcursor;
BEGIN
  OPEN l_cur FOR
    SELECT * FROM employees
     WHERE department_id = p_department_id
       AND hire_date > (LOCALTIMESTAMP(0) + interval '1 month' * (-24));
  RETURN l_cur;
END;
$$;
