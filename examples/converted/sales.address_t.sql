-- SALES.ADDRESS_T (TYPE + TYPE BODY) converted to PL/pgSQL by o2pg 0.2.0
-- Mechanical rewrites only. Read every O2PG-REVIEW marker and test the logic
-- against real data before relying on it.
-- Review points: 1 (1 blocking). See conversion-report.md.

/* O2PG-REVIEW O2PG-RV-001: object type: create a composite type (CREATE TYPE ... AS (...)) and turn member methods into functions that take the type as a parameter */
-- TYPE address_t AS OBJECT (
--   street    VARCHAR2(60),
--   city      VARCHAR2(40),
--   postcode  VARCHAR2(12),
--   MEMBER FUNCTION one_line RETURN VARCHAR2
-- );
-- TYPE BODY address_t AS
--   MEMBER FUNCTION one_line RETURN VARCHAR2 IS
--   BEGIN
--     RETURN street || ', ' || city || ' ' || postcode;
--   END;
-- END;
