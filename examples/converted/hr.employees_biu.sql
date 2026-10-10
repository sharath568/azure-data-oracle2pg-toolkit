-- HR.EMPLOYEES_BIU (TRIGGER) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 2 (1 blocking). See conversion-report.md.

SET search_path = hr, public;

CREATE OR REPLACE FUNCTION hr.employees_biu()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, public
AS $$
BEGIN
  BEGIN
    IF (TG_OP = 'INSERT') THEN
      NEW.hire_date := COALESCE(NEW.hire_date, LOCALTIMESTAMP(0));
    END IF;
    -- normalise blanks: Oracle treats '' as NULL, so this also clears empty strings
    /* O2PG-REVIEW O2PG-RV-034: '' is NULL in Oracle but an empty string in PostgreSQL; comparisons with '' and assignments of '' behave differently */
    IF NEW.phone_number = '' THEN
      NEW.phone_number := NULL;
    END IF;
    NEW.email := UPPER(NEW.email);
    NEW.last_updated := clock_timestamp();
    /* O2PG-REVIEW O2PG-RV-011: package variable emp_mgmt.g_last_change: PostgreSQL has no package state; keep it in a table or a custom setting via set_config()/current_setting() */
    emp_mgmt.g_last_change := LOCALTIMESTAMP(0);
  END;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER employees_biu
BEFORE INSERT OR UPDATE ON hr.employees
FOR EACH ROW
EXECUTE FUNCTION hr.employees_biu();
