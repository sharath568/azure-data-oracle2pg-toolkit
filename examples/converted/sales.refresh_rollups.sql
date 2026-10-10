-- SALES.REFRESH_ROLLUPS (PROCEDURE) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 2 (2 blocking). See conversion-report.md.

SET search_path = sales, public;

CREATE OR REPLACE PROCEDURE sales.refresh_rollups ()
LANGUAGE plpgsql
AS $$
BEGIN
  /* O2PG-REVIEW O2PG-RV-014: use REFRESH MATERIALIZED VIEW [CONCURRENTLY] (EXECUTE format(...) if the name is a parameter) */
  DBMS_MVIEW.REFRESH('SALES.MV_CUSTOMER_SUMMARY', 'C');
  /* O2PG-REVIEW O2PG-RV-014: use ANALYZE; autovacuum usually keeps statistics current */
  DBMS_STATS.GATHER_TABLE_STATS('SALES', 'SALES_HISTORY');
  COMMIT;
END;
$$;
