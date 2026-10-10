-- SALES.ORDER_PROC (PACKAGE + PACKAGE BODY) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 17 (12 blocking). See conversion-report.md.

CREATE SCHEMA IF NOT EXISTS order_proc;
SET search_path = order_proc, sales, public;

-- Package-level declarations (Oracle source kept as comments)
/* O2PG-REVIEW O2PG-RV-009: PL/SQL collection, record or subtype declaration: use a PostgreSQL array, a composite type (CREATE TYPE) or a temporary table */
-- TYPE order_line_t IS RECORD (order_id NUMBER, total NUMBER);
/* O2PG-REVIEW O2PG-RV-009: PL/SQL collection, record or subtype declaration: use a PostgreSQL array, a composite type (CREATE TYPE) or a temporary table */
-- TYPE order_lines_t IS TABLE OF order_line_t;
-- PROCEDURE upsert_customer (p_id NUMBER, p_email VARCHAR2, p_name VARCHAR2);  -- o2pg: spec, implemented below
-- PROCEDURE export_daily (p_day DATE);  -- o2pg: spec, implemented below
-- PROCEDURE notify_big_order (p_order_id NUMBER);  -- o2pg: spec, implemented below
-- FUNCTION recent_orders (p_days NUMBER) RETURN order_lines_t PIPELINED;  -- o2pg: spec, implemented below
-- PROCEDURE purge_stage;  -- o2pg: spec, implemented below

CREATE OR REPLACE PROCEDURE order_proc.upsert_customer (p_id numeric, p_email varchar, p_name varchar)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = order_proc, sales, public
AS $$
BEGIN
  /* O2PG-REVIEW O2PG-RV-031: MERGE runs on PostgreSQL 15+ (Azure Flexible Server offers 15, 16 and 17); check the ON condition, and that concurrent inserts are handled (INSERT ... ON CONFLICT may fit better) */
  MERGE INTO customers c
  USING (SELECT p_id AS customer_id, p_email AS email, p_name AS last_name) s
     ON (c.customer_id = s.customer_id)
   WHEN MATCHED THEN
     UPDATE SET email = s.email, cust_last_name = s.last_name
   WHEN NOT MATCHED THEN
     INSERT (customer_id, email, cust_first_name, cust_last_name)
     VALUES (s.customer_id, s.email, ' ', s.last_name);
END;
$$;

CREATE OR REPLACE PROCEDURE order_proc.export_daily (p_day timestamp(0))
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = order_proc, sales, public
AS $$
DECLARE
  r record;
  /* O2PG-REVIEW O2PG-RV-010: no automatic mapping for type UTL_FILE.FILE_TYPE */
  /* O2PG-REVIEW O2PG-RV-014: UTL_FILE: there is no server file system on Azure Flexible Server; write files from the application or Azure Storage instead */
  l_file UTL_FILE.FILE_TYPE;
BEGIN
  /* O2PG-REVIEW O2PG-RV-014: UTL_FILE: there is no server file system on Azure Flexible Server; write files from the application or Azure Storage instead */
  /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
  l_file := UTL_FILE.FOPEN('EXPORT_DIR', 'orders_' || TO_CHAR(p_day, 'YYYYMMDD') || '.csv', 'w');
  FOR r IN (SELECT /*+ INDEX(o ord_customer_ix) */ o.order_id, o.customer_id, o.order_total
              FROM orders o
             WHERE o.order_date >= date_trunc('day', p_day) AND o.order_date < date_trunc('day', p_day) + interval '1 day') LOOP
    /* O2PG-REVIEW O2PG-RV-014: UTL_FILE: there is no server file system on Azure Flexible Server; write files from the application or Azure Storage instead */
    /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
    UTL_FILE.PUT_LINE(l_file, r.order_id || ',' || r.customer_id || ',' || r.order_total);
  END LOOP;
  /* O2PG-REVIEW O2PG-RV-014: UTL_FILE: there is no server file system on Azure Flexible Server; write files from the application or Azure Storage instead */
  UTL_FILE.FCLOSE(l_file);
END;
$$;

CREATE OR REPLACE PROCEDURE order_proc.notify_big_order (p_order_id numeric)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = order_proc, sales, public
AS $$
DECLARE
  l_total numeric;
BEGIN
  SELECT order_total INTO STRICT l_total FROM orders WHERE order_id = p_order_id;
  IF l_total > 100000 THEN
    /* O2PG-REVIEW O2PG-RV-014: sending mail from the database: move it to the application or a queue consumer */
    UTL_MAIL.SEND(sender => 'orders@example.com', recipients => 'sales-leads@example.com',
                  /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
                  subject => 'Big order ' || p_order_id, message => 'Total: ' || l_total);
  END IF;
END;
$$;

/* O2PG-REVIEW O2PG-RV-010: no automatic mapping for return type order_lines_t */
/* O2PG-REVIEW O2PG-RV-008: PIPELINED / PIPE ROW: rewrite as RETURNS SETOF (or RETURNS TABLE) with RETURN NEXT */
CREATE OR REPLACE FUNCTION order_proc.recent_orders (p_days numeric)
RETURNS order_lines_t
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = order_proc, sales, public
AS $$
DECLARE
  r record;
BEGIN
  FOR r IN (SELECT order_id, order_total FROM orders
             /* O2PG-REVIEW O2PG-RV-005: ROWNUM has no PostgreSQL equivalent; use LIMIT (or FETCH FIRST n ROWS ONLY) for top-n and row_number() OVER (...) for numbering */
             WHERE order_date > LOCALTIMESTAMP(0) - interval '1 day' * p_days AND ROWNUM <= 1000) LOOP
    /* O2PG-REVIEW O2PG-RV-008: PIPELINED / PIPE ROW: rewrite as RETURNS SETOF (or RETURNS TABLE) with RETURN NEXT */
    PIPE ROW (order_line_t(r.order_id, r.order_total));
  END LOOP;
  RETURN;
END;
$$;

CREATE OR REPLACE PROCEDURE order_proc.purge_stage ()
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = order_proc, sales, public
AS $$
BEGIN
  /* O2PG-REVIEW O2PG-RV-033: dynamic SQL: the SQL text is not converted; check it is valid PostgreSQL and change :name binds to $1, $2 with USING */
  EXECUTE 'TRUNCATE TABLE order_stage';
END;
$$;
