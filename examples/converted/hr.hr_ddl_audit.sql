-- HR.HR_DDL_AUDIT (TRIGGER) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 1 (1 blocking). See conversion-report.md.

/* O2PG-REVIEW O2PG-RV-001: schema or database event trigger: use an event trigger (CREATE EVENT TRIGGER), which needs elevated privileges; check what your Azure server allows */
-- TRIGGER hr_ddl_audit
-- AFTER CREATE OR ALTER OR DROP ON SCHEMA
-- BEGIN
--   INSERT INTO emp_audit_log (audit_ts, action, changed_by, old_values)
--   VALUES (SYSTIMESTAMP, ora_sysevent, USER, ora_dict_obj_type || ' ' || ora_dict_obj_name);
-- END;
