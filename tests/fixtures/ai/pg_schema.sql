-- PostgreSQL side of the hard corpus: the tables as o2pg map-types would create
-- them. The Oracle object types (lab_emp_row, lab_emp_tab) have no counterpart
-- here on purpose: replacing them is part of converting lab.hired_since.

CREATE SCHEMA IF NOT EXISTS lab;

CREATE TABLE lab.dept (
  dept_id   numeric(6)    PRIMARY KEY,
  dept_name varchar(30)   NOT NULL
);

CREATE TABLE lab.emp (
  emp_id  numeric(6)     PRIMARY KEY,
  name    varchar(50)    NOT NULL,
  mgr_id  numeric(6)     REFERENCES lab.emp (emp_id),
  dept_id numeric(6)     REFERENCES lab.dept (dept_id),
  salary  numeric(10,2)  NOT NULL,
  hired   timestamp(0)   NOT NULL,
  email   varchar(100)
);

CREATE TABLE lab.event_log (
  msg       varchar(400),
  logged_at timestamp(6)
);
