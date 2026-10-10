-- Fixture routines for o2pg convert. Oracle PL/SQL, SQL*Plus style.
-- Each one exercises mechanical rewrites; the integration tests create the
-- converted versions in PostgreSQL and call them.

CREATE OR REPLACE FUNCTION app.display_name (p_name IN VARCHAR2) RETURN VARCHAR2 IS
BEGIN
  RETURN NVL(p_name, 'anonymous');
END display_name;
/

CREATE OR REPLACE FUNCTION app.grade_label (p_grade IN VARCHAR2) RETURN VARCHAR2 IS
BEGIN
  -- DECODE matches NULL with NULL, which CASE x WHEN NULL would not
  RETURN DECODE(p_grade, 'A', 'excellent', 'B', 'good', NULL, 'missing', 'other');
END grade_label;
/

CREATE OR REPLACE FUNCTION app.has_value (p_value IN NUMBER) RETURN VARCHAR2 IS
BEGIN
  RETURN NVL2(p_value, 'set', 'unset');
END has_value;
/

CREATE OR REPLACE FUNCTION app.next_customer_id RETURN NUMBER IS
  l_id NUMBER;
BEGIN
  SELECT customer_seq.NEXTVAL INTO l_id FROM dual;
  RETURN l_id;
END next_customer_id;
/

CREATE OR REPLACE FUNCTION app.current_customer_id RETURN NUMBER IS
BEGIN
  RETURN customer_seq.CURRVAL;
END current_customer_id;
/

CREATE OR REPLACE FUNCTION app.customer_name (p_id IN NUMBER) RETURN VARCHAR2 IS
  l_name customers.name%TYPE;
BEGIN
  SELECT name INTO l_name FROM customers WHERE customer_id = p_id;
  RETURN l_name;
END customer_name;
/

CREATE OR REPLACE FUNCTION app.customer_name_or_default (p_id IN NUMBER) RETURN VARCHAR2 IS
  l_name customers.name%TYPE;
BEGIN
  SELECT name INTO l_name FROM customers WHERE customer_id = p_id;
  RETURN l_name;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RETURN 'unknown';
END customer_name_or_default;
/

CREATE OR REPLACE FUNCTION app.customer_by_email (p_email IN VARCHAR2) RETURN NUMBER IS
  l_id customers.customer_id%TYPE;
BEGIN
  SELECT customer_id INTO l_id FROM customers WHERE email = p_email;
  RETURN l_id;
END customer_by_email;
/

CREATE OR REPLACE FUNCTION app.check_amount (p_amount IN NUMBER) RETURN NUMBER IS
BEGIN
  IF p_amount < 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Amount must not be negative');
  END IF;
  RETURN p_amount;
END check_amount;
/

CREATE OR REPLACE FUNCTION app.safe_ratio (p_a IN NUMBER, p_b IN NUMBER) RETURN NUMBER IS
BEGIN
  RETURN p_a / p_b;
EXCEPTION
  WHEN ZERO_DIVIDE THEN
    RETURN NULL;
END safe_ratio;
/

CREATE OR REPLACE FUNCTION app.days_ago (p_days IN NUMBER) RETURN DATE IS
BEGIN
  RETURN TRUNC(SYSDATE) - p_days;
END days_ago;
/

CREATE OR REPLACE FUNCTION app.month_start (p_day IN DATE) RETURN DATE IS
BEGIN
  RETURN TRUNC(p_day, 'MM');
END month_start;
/

CREATE OR REPLACE FUNCTION app.add_quarter (p_day IN DATE) RETURN DATE IS
BEGIN
  RETURN ADD_MONTHS(p_day, 3);
END add_quarter;
/

CREATE OR REPLACE FUNCTION app.parse_moment (p_text IN VARCHAR2) RETURN DATE IS
BEGIN
  RETURN TO_DATE(p_text, 'YYYY-MM-DD HH24:MI:SS');
END parse_moment;
/

CREATE OR REPLACE FUNCTION app.first_chars (p_text IN VARCHAR2, p_n IN PLS_INTEGER) RETURN VARCHAR2 IS
BEGIN
  RETURN SUBSTR(p_text, 0, p_n);
END first_chars;
/

CREATE OR REPLACE FUNCTION app.position_of (p_text IN VARCHAR2, p_part IN VARCHAR2) RETURN NUMBER IS
BEGIN
  RETURN INSTR(p_text, p_part);
END position_of;
/

CREATE OR REPLACE FUNCTION app.countdown (p_from IN PLS_INTEGER) RETURN VARCHAR2 IS
  l_out VARCHAR2(200);
BEGIN
  FOR i IN REVERSE 1 .. p_from LOOP
    l_out := l_out || TO_CHAR(i);
  END LOOP;
  RETURN l_out;
END countdown;
/
