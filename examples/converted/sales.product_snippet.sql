-- SALES.PRODUCT_SNIPPET (FUNCTION) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 1 (0 blocking). See conversion-report.md.

SET search_path = sales, public;

CREATE OR REPLACE FUNCTION sales.product_snippet (p_product_id numeric)
RETURNS varchar
LANGUAGE plpgsql
SET search_path = sales, public
AS $$
DECLARE
  l_desc text;
BEGIN
  SELECT description INTO STRICT l_desc FROM products WHERE product_id = p_product_id;
  IF length(l_desc) > 200 THEN
    /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
    RETURN substr(l_desc, 1, 197) || '...';
  END IF;
  RETURN substr(l_desc, 1, 200);
END;
$$;
