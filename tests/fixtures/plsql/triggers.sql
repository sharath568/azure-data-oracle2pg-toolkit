-- Row triggers.

CREATE OR REPLACE TRIGGER app.customers_biu
BEFORE INSERT OR UPDATE ON app.customers
FOR EACH ROW
BEGIN
  IF INSERTING THEN
    IF :NEW.customer_id IS NULL THEN
      :NEW.customer_id := customer_seq.NEXTVAL;
    END IF;
    :NEW.created_at := SYSDATE;
  END IF;
  IF UPDATING THEN
    :NEW.updated_at := SYSDATE;
  END IF;
  :NEW.code := UPPER(:NEW.code);
END customers_biu;
/

CREATE OR REPLACE TRIGGER app.orders_bi
BEFORE INSERT ON app.orders
FOR EACH ROW
WHEN (new.order_id IS NULL)
BEGIN
  SELECT order_seq.NEXTVAL INTO :new.order_id FROM dual;
END;
/

CREATE OR REPLACE TRIGGER app.orders_audit
AFTER INSERT OR DELETE ON app.orders
REFERENCING NEW AS n OLD AS o
FOR EACH ROW
BEGIN
  IF DELETING THEN
    INSERT INTO audit_log (id, order_id, msg, logged_at)
    VALUES (order_seq.NEXTVAL, :o.order_id, 'order deleted', SYSTIMESTAMP);
  ELSE
    INSERT INTO audit_log (id, order_id, msg, logged_at)
    VALUES (order_seq.NEXTVAL, :n.order_id, 'order added', SYSTIMESTAMP);
  END IF;
END orders_audit;
/
