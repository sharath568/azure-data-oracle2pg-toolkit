-- HR.UPDATE_JOB_HISTORY (TRIGGER) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 0 (0 blocking). See conversion-report.md.

SET search_path = hr, public;

CREATE OR REPLACE FUNCTION hr.update_job_history()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, public
AS $$
BEGIN
  BEGIN
    CALL add_job_history(OLD.employee_id, OLD.hire_date, LOCALTIMESTAMP(0),
                    OLD.job_id, OLD.department_id);
  END;
  RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER update_job_history
AFTER UPDATE OF job_id, department_id ON hr.employees
FOR EACH ROW
EXECUTE FUNCTION hr.update_job_history();
