-- The "hard" corpus for o2pg ai-convert and o2pg equiv.
--
-- Each routine leans on one Oracle behaviour that the mechanical converter
-- either cannot rewrite (blocking review point) or rewrites into something that
-- runs but answers differently (non-blocking review point). cases.json holds
-- the Oracle results these routines must reproduce in PostgreSQL.
--
-- Load order on Oracle: oracle_schema.sql, this file, data.sql (as user LAB).

-- ROWNUM over an ordered inline view, and NO_DATA_FOUND for an empty department.
CREATE OR REPLACE FUNCTION lab.highest_paid (p_dept IN NUMBER) RETURN NUMBER IS
  l_emp NUMBER;
BEGIN
  SELECT emp_id INTO l_emp
    FROM (SELECT emp_id FROM emp WHERE dept_id = p_dept ORDER BY salary DESC, emp_id)
   WHERE ROWNUM = 1;
  RETURN l_emp;
END highest_paid;
/

-- CONNECT BY walking up the management chain, LISTAGG ordered by LEVEL.
CREATE OR REPLACE FUNCTION lab.chain_of_command (p_emp IN NUMBER) RETURN VARCHAR2 IS
  l_chain VARCHAR2(4000);
BEGIN
  SELECT LISTAGG(name, ' > ') WITHIN GROUP (ORDER BY LEVEL DESC)
    INTO l_chain
    FROM emp
   START WITH emp_id = p_emp
 CONNECT BY PRIOR mgr_id = emp_id;
  RETURN l_chain;
END chain_of_command;
/

-- CONNECT BY walking down, with a WHERE on LEVEL applied after the walk.
CREATE OR REPLACE FUNCTION lab.headcount_under (p_mgr IN NUMBER) RETURN NUMBER IS
  l_n NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_n
    FROM emp
   WHERE LEVEL > 1
   START WITH emp_id = p_mgr
 CONNECT BY PRIOR emp_id = mgr_id;
  RETURN l_n;
END headcount_under;
/

-- (+) with a filter on the outer-joined table: the filter belongs in the ON clause.
CREATE OR REPLACE FUNCTION lab.depts_without_earner (p_min IN NUMBER) RETURN NUMBER IS
  l_n NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_n
    FROM dept d, emp e
   WHERE e.dept_id (+) = d.dept_id
     AND e.salary (+) > p_min
     AND e.emp_id IS NULL;
  RETURN l_n;
END depts_without_earner;
/

-- BULK COLLECT + FORALL, then SQL%ROWCOUNT (the total across all FORALL iterations).
CREATE OR REPLACE PROCEDURE lab.raise_dept (p_dept IN NUMBER, p_pct IN NUMBER, p_updated OUT NUMBER) IS
  TYPE id_tab_t IS TABLE OF emp.emp_id%TYPE;
  l_ids id_tab_t;
BEGIN
  SELECT emp_id BULK COLLECT INTO l_ids FROM emp WHERE dept_id = p_dept;
  FORALL i IN 1 .. l_ids.COUNT
    UPDATE emp SET salary = ROUND(salary * (1 + p_pct / 100), 2) WHERE emp_id = l_ids(i);
  p_updated := SQL%ROWCOUNT;
END raise_dept;
/

-- || with a NULL operand: Oracle treats NULL as ''.
CREATE OR REPLACE FUNCTION lab.contact_line (p_emp IN NUMBER) RETURN VARCHAR2 IS
  l_line VARCHAR2(200);
BEGIN
  SELECT name || ' <' || email || '>' INTO l_line FROM emp WHERE emp_id = p_emp;
  RETURN l_line;
END contact_line;
/

-- '' is NULL in Oracle, so the first branch can never be taken there.
CREATE OR REPLACE FUNCTION lab.code_kind (p_code IN VARCHAR2) RETURN VARCHAR2 IS
BEGIN
  IF p_code = '' THEN
    RETURN 'empty';
  ELSIF p_code IS NULL THEN
    RETURN 'missing';
  ELSE
    RETURN 'value';
  END IF;
END code_kind;
/

-- DATE - DATE is a number of days (with a fraction) in Oracle.
CREATE OR REPLACE FUNCTION lab.days_between (p_from IN DATE, p_to IN DATE) RETURN NUMBER IS
BEGIN
  RETURN p_to - p_from;
END days_between;
/

-- INTEGER / INTEGER is not truncated in Oracle.
CREATE OR REPLACE FUNCTION lab.avg_split (p_total IN INTEGER, p_parts IN INTEGER) RETURN NUMBER IS
  l_share NUMBER;
BEGIN
  l_share := p_total / p_parts;
  RETURN l_share;
END avg_split;
/

-- Negative SUBSTR position counts from the end; past the start it returns NULL.
CREATE OR REPLACE FUNCTION lab.last_chars (p_text IN VARCHAR2, p_n IN NUMBER) RETURN VARCHAR2 IS
BEGIN
  RETURN SUBSTR(p_text, -p_n);
END last_chars;
/

-- Four-argument INSTR (nth occurrence), 0 when there is no such occurrence.
CREATE OR REPLACE FUNCTION lab.nth_pos (p_text IN VARCHAR2, p_find IN VARCHAR2, p_nth IN NUMBER) RETURN NUMBER IS
BEGIN
  RETURN INSTR(p_text, p_find, 1, p_nth);
END nth_pos;
/

-- REGEXP_REPLACE replaces every match, and an empty result is NULL.
CREATE OR REPLACE FUNCTION lab.digits_only (p_text IN VARCHAR2) RETURN VARCHAR2 IS
BEGIN
  RETURN REGEXP_REPLACE(p_text, '[^0-9]', '');
END digits_only;
/

-- PIPELINED table function over SQL object types (a contract change in PostgreSQL).
CREATE OR REPLACE FUNCTION lab.hired_since (p_day IN DATE) RETURN lab_emp_tab PIPELINED IS
BEGIN
  FOR r IN (SELECT emp_id, name FROM emp WHERE hired >= p_day ORDER BY emp_id) LOOP
    PIPE ROW (lab_emp_row(r.emp_id, r.name));
  END LOOP;
  RETURN;
END hired_since;
/

-- An autonomous transaction: a design decision, not a code rewrite. ai-convert skips it.
CREATE OR REPLACE PROCEDURE lab.log_event (p_msg IN VARCHAR2) IS
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  INSERT INTO event_log (msg, logged_at) VALUES (p_msg, SYSTIMESTAMP);
  COMMIT;
END log_event;
/
