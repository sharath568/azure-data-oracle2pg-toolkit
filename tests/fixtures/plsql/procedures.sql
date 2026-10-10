-- Procedures, cursors and dynamic SQL.

CREATE OR REPLACE PROCEDURE app.split_amount (
  p_amount IN NUMBER,
  p_parts  IN PLS_INTEGER,
  p_each   OUT NUMBER,
  p_count  IN OUT NUMBER
) IS
BEGIN
  p_each := ROUND(p_amount / p_parts, 2);
  p_count := NVL(p_count, 0) + 1;
END split_amount;
/

CREATE OR REPLACE PROCEDURE app.log_message (p_msg IN VARCHAR2) AS
BEGIN
  INSERT INTO audit_log (id, msg, logged_at) VALUES (order_seq.NEXTVAL, p_msg, SYSTIMESTAMP);
  DBMS_OUTPUT.PUT_LINE(p_msg);
END log_message;
/

CREATE OR REPLACE PROCEDURE app.close_old_orders (p_days IN NUMBER, p_closed OUT NUMBER) IS
BEGIN
  UPDATE orders
     SET status = 'CLOSED'
   WHERE created_at < SYSDATE - p_days
     AND status = 'OPEN';
  p_closed := SQL%ROWCOUNT;
  COMMIT;
END close_old_orders;
/

CREATE OR REPLACE PROCEDURE app.purge_audit IS
BEGIN
  EXECUTE IMMEDIATE 'DELETE FROM audit_log';
END purge_audit;
/

CREATE OR REPLACE FUNCTION app.order_total_for (p_customer_id IN NUMBER) RETURN NUMBER IS
  l_total  NUMBER := 0;
  l_amount orders.amount%TYPE;
  CURSOR c_orders (p_cust NUMBER) IS
    SELECT amount FROM orders WHERE customer_id = p_cust AND status = 'CLOSED';
BEGIN
  OPEN c_orders(p_customer_id);
  LOOP
    FETCH c_orders INTO l_amount;
    EXIT WHEN c_orders%NOTFOUND;
    l_total := l_total + l_amount;
  END LOOP;
  CLOSE c_orders;
  FOR r IN (SELECT amount FROM orders WHERE customer_id = p_customer_id AND status = 'OPEN') LOOP
    l_total := l_total + r.amount;
  END LOOP;
  RETURN l_total;
END order_total_for;
/
