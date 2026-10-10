-- A package with a constant and routines that call each other.

CREATE OR REPLACE PACKAGE app.billing AS
  c_tax_rate CONSTANT NUMBER := 0.2;
  FUNCTION gross (p_net IN NUMBER) RETURN NUMBER;
  PROCEDURE add_order (p_customer_id IN NUMBER, p_net IN NUMBER, p_order_id OUT NUMBER);
END billing;
/

CREATE OR REPLACE PACKAGE BODY app.billing AS

  FUNCTION gross (p_net IN NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN ROUND(p_net * (1 + c_tax_rate), 2);
  END gross;

  -- inserts an open order and logs it
  PROCEDURE add_order (p_customer_id IN NUMBER, p_net IN NUMBER, p_order_id OUT NUMBER) IS
  BEGIN
    INSERT INTO orders (order_id, customer_id, amount, status, created_at)
    VALUES (order_seq.NEXTVAL, p_customer_id, gross(p_net), 'OPEN', SYSDATE)
    RETURNING order_id INTO p_order_id;
    log_message('order added by billing');
  END add_order;

END billing;
/
