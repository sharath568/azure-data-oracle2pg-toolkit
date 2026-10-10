-- SALES.ORDERS_BI (TRIGGER) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 0 (0 blocking). See conversion-report.md.

SET search_path = sales, public;

CREATE OR REPLACE FUNCTION sales.orders_bi()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = sales, public
AS $$
BEGIN
  BEGIN
    NEW.order_id := nextval('orders_seq');
  END;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER orders_bi
BEFORE INSERT ON sales.orders
FOR EACH ROW
WHEN (NEW.order_id IS NULL)
EXECUTE FUNCTION sales.orders_bi();
