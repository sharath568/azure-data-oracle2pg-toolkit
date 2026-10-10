-- SALES.GET_ORDER_TOTAL (FUNCTION) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 0 (0 blocking). See conversion-report.md.

SET search_path = sales, public;

CREATE OR REPLACE FUNCTION sales.get_order_total (p_order_id numeric)
RETURNS numeric
LANGUAGE plpgsql
SET search_path = sales, public
AS $$
DECLARE
  l_total numeric;
BEGIN
  SELECT SUM(unit_price * quantity * (1 - COALESCE(discount, 0)))
    INTO STRICT l_total
    FROM order_items
   WHERE order_id = p_order_id;
  RETURN COALESCE(l_total, 0);
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RETURN 0;
END;
$$;
