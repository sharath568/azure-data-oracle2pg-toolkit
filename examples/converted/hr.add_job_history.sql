-- HR.ADD_JOB_HISTORY (PROCEDURE) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 0 (0 blocking). See conversion-report.md.

SET search_path = hr, public;

CREATE OR REPLACE PROCEDURE hr.add_job_history ( p_emp_id job_history.employee_id%TYPE
  , p_start_date job_history.start_date%TYPE
  , p_end_date job_history.end_date%TYPE
  , p_job_id job_history.job_id%TYPE
  , p_department_id job_history.department_id%TYPE
  )
LANGUAGE plpgsql
SET search_path = hr, public
AS $$
BEGIN
  INSERT INTO job_history (employee_id, start_date, end_date, job_id, department_id)
  VALUES (p_emp_id, p_start_date, p_end_date, p_job_id, p_department_id);
END;
$$;
