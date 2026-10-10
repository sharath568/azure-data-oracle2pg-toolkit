-- SALES.PUBLISH_ORDER_EVENT (PROCEDURE) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 5 (5 blocking). See conversion-report.md.

SET search_path = sales, public;

CREATE OR REPLACE PROCEDURE sales.publish_order_event (p_order_id numeric)
LANGUAGE plpgsql
SET search_path = sales, public
AS $$
DECLARE
  /* O2PG-REVIEW O2PG-RV-010: no automatic mapping for type DBMS_AQ.ENQUEUE_OPTIONS_T */
  /* O2PG-REVIEW O2PG-RV-014: Advanced Queuing: use a queue table (SELECT ... FOR UPDATE SKIP LOCKED) or an Azure messaging service */
  l_enq_opts   DBMS_AQ.ENQUEUE_OPTIONS_T;
  /* O2PG-REVIEW O2PG-RV-010: no automatic mapping for type DBMS_AQ.MESSAGE_PROPERTIES_T */
  /* O2PG-REVIEW O2PG-RV-014: Advanced Queuing: use a queue table (SELECT ... FOR UPDATE SKIP LOCKED) or an Azure messaging service */
  l_msg_props  DBMS_AQ.MESSAGE_PROPERTIES_T;
  l_msg_id     bytea;
BEGIN
  /* O2PG-REVIEW O2PG-RV-014: Advanced Queuing: use a queue table (SELECT ... FOR UPDATE SKIP LOCKED) or an Azure messaging service */
  DBMS_AQ.ENQUEUE(queue_name         => 'SALES.ORDER_EVENTS_Q',
                  enqueue_options    => l_enq_opts,
                  message_properties => l_msg_props,
                  payload            => order_event_t(p_order_id, clock_timestamp()),
                  msgid              => l_msg_id);
END;
$$;
