-- Constructs the converter must not touch: every routine here should come out
-- blocked, with the Oracle code still in place under a review marker.

CREATE OR REPLACE PROCEDURE app.archive_orders IS
  TYPE id_tab_t IS TABLE OF orders.order_id%TYPE;
  l_ids id_tab_t;
BEGIN
  SELECT order_id BULK COLLECT INTO l_ids FROM orders WHERE status = 'CLOSED';
  FORALL i IN 1 .. l_ids.COUNT
    DELETE FROM orders WHERE order_id = l_ids(i);
END archive_orders;
/

CREATE OR REPLACE FUNCTION app.top_customer RETURN NUMBER IS
  l_id NUMBER;
BEGIN
  SELECT customer_id INTO l_id FROM customers WHERE ROWNUM = 1;
  RETURN l_id;
END top_customer;
/

CREATE OR REPLACE PROCEDURE app.audit_now (p_msg IN VARCHAR2) IS
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  INSERT INTO audit_log (id, msg, logged_at) VALUES (order_seq.NEXTVAL, p_msg, SYSTIMESTAMP);
  COMMIT;
END audit_now;
/

CREATE OR REPLACE FUNCTION app.org_chart RETURN NUMBER IS
  l_n NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_n FROM customers START WITH customer_id = 1 CONNECT BY PRIOR customer_id = customer_id + 1;
  RETURN l_n;
END org_chart;
/
