-- Oracle side of the hard corpus. Run as user LAB (Oracle 19c or later).
-- Statements are separated by lines holding only "/".

CREATE TABLE dept (
  dept_id   NUMBER(6)     PRIMARY KEY,
  dept_name VARCHAR2(30)  NOT NULL
)
/
CREATE TABLE emp (
  emp_id  NUMBER(6)     PRIMARY KEY,
  name    VARCHAR2(50)  NOT NULL,
  mgr_id  NUMBER(6)     REFERENCES emp (emp_id),
  dept_id NUMBER(6)     REFERENCES dept (dept_id),
  salary  NUMBER(10,2)  NOT NULL,
  hired   DATE          NOT NULL,
  email   VARCHAR2(100)
)
/
CREATE TABLE event_log (
  msg       VARCHAR2(400),
  logged_at TIMESTAMP
)
/
CREATE TYPE lab_emp_row AS OBJECT (emp_id NUMBER(6), name VARCHAR2(50))
/
CREATE TYPE lab_emp_tab AS TABLE OF lab_emp_row
/
