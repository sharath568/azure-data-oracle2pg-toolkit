-- Rows for the hard corpus. Plain SQL that runs unchanged on Oracle and on
-- PostgreSQL (with search_path = lab). One statement per line; the harness
-- loads it inside each case's transaction, which is then rolled back.
INSERT INTO dept (dept_id, dept_name) VALUES (10, 'Executive')
INSERT INTO dept (dept_id, dept_name) VALUES (20, 'Engineering')
INSERT INTO dept (dept_id, dept_name) VALUES (30, 'Sales')
INSERT INTO dept (dept_id, dept_name) VALUES (40, 'Research')
INSERT INTO emp (emp_id, name, mgr_id, dept_id, salary, hired, email) VALUES (1, 'Ana', NULL, 10, 20000, TIMESTAMP '2019-03-01 00:00:00', 'ana@example.com')
INSERT INTO emp (emp_id, name, mgr_id, dept_id, salary, hired, email) VALUES (2, 'Raj', 1, 20, 9000, TIMESTAMP '2020-06-15 00:00:00', 'raj@example.com')
INSERT INTO emp (emp_id, name, mgr_id, dept_id, salary, hired, email) VALUES (3, 'Li', 2, 20, 7000, TIMESTAMP '2022-01-10 00:00:00', 'li@example.com')
INSERT INTO emp (emp_id, name, mgr_id, dept_id, salary, hired, email) VALUES (4, 'Bob', 2, 20, 4800.50, TIMESTAMP '2024-09-01 00:00:00', NULL)
INSERT INTO emp (emp_id, name, mgr_id, dept_id, salary, hired, email) VALUES (5, 'Eva', 1, 30, 5200, TIMESTAMP '2023-02-20 00:00:00', 'eva@example.com')
INSERT INTO emp (emp_id, name, mgr_id, dept_id, salary, hired, email) VALUES (6, 'Tom', 5, 30, 3900, TIMESTAMP '2025-11-03 00:00:00', NULL)
