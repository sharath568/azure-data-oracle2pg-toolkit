-- Oracle side of the partitioning fixture: one table per pattern o2pg converts
-- (or refuses to). Run as user PART; statements end with "/" lines.

CREATE TABLE events (
  event_id NUMBER(10)   NOT NULL,
  event_ts DATE         NOT NULL,
  kind     VARCHAR2(20),
  CONSTRAINT events_pk PRIMARY KEY (event_id)
)
PARTITION BY RANGE (event_ts) INTERVAL (NUMTOYMINTERVAL(1, 'MONTH'))
(PARTITION p_before_2026 VALUES LESS THAN (DATE '2026-01-01'))
/
CREATE INDEX events_kind_ix ON events (kind) LOCAL
/
CREATE TABLE event_details (
  detail_id NUMBER(10)    NOT NULL,
  event_id  NUMBER(10)    NOT NULL,
  note      VARCHAR2(100),
  CONSTRAINT event_details_pk PRIMARY KEY (detail_id),
  CONSTRAINT event_details_fk FOREIGN KEY (event_id) REFERENCES events (event_id)
)
PARTITION BY REFERENCE (event_details_fk)
/
CREATE TABLE readings (
  reading_id   NUMBER(10) NOT NULL,
  reading_date DATE,
  reading      NUMBER(10,2)
)
PARTITION BY RANGE (reading_date)
(PARTITION p_2025 VALUES LESS THAN (DATE '2026-01-01'),
 PARTITION p_2026 VALUES LESS THAN (DATE '2027-01-01'),
 PARTITION p_max  VALUES LESS THAN (MAXVALUE))
/
CREATE TABLE customers_by_region (
  customer_id NUMBER(10) NOT NULL,
  region      VARCHAR2(2),
  name        VARCHAR2(50)
)
PARTITION BY LIST (region)
(PARTITION p_amer  VALUES ('US', 'CA'),
 PARTITION p_emea  VALUES ('UK', 'DE'),
 PARTITION p_none  VALUES (NULL),
 PARTITION p_other VALUES (DEFAULT))
/
CREATE TABLE sessions (
  session_id NUMBER(10) NOT NULL,
  user_id    NUMBER(10) NOT NULL
)
PARTITION BY HASH (user_id)
(PARTITION p_h1, PARTITION p_h2, PARTITION p_h3, PARTITION p_h4)
/
CREATE TABLE sales (
  sale_id   NUMBER(10)   NOT NULL,
  sale_date DATE         NOT NULL,
  channel   VARCHAR2(10) NOT NULL,
  amount    NUMBER(10,2),
  CONSTRAINT sales_pk PRIMARY KEY (sale_id, sale_date)
)
PARTITION BY RANGE (sale_date) SUBPARTITION BY LIST (channel)
SUBPARTITION TEMPLATE
(SUBPARTITION web   VALUES ('WEB'),
 SUBPARTITION store VALUES ('STORE'),
 SUBPARTITION other VALUES (DEFAULT))
(PARTITION p_2025q1 VALUES LESS THAN (DATE '2025-04-01'),
 PARTITION p_2025q2 VALUES LESS THAN (DATE '2025-07-01'),
 PARTITION p_max    VALUES LESS THAN (MAXVALUE))
/
CREATE TABLE ledger (
  entry_id     NUMBER(10)   NOT NULL,
  fiscal_year  NUMBER(4)    NOT NULL,
  fiscal_month NUMBER(2)    NOT NULL,
  amount       NUMBER(12,2)
)
PARTITION BY RANGE (fiscal_year, fiscal_month)
(PARTITION p_fy25_h1 VALUES LESS THAN (2025, 7),
 PARTITION p_fy25_h2 VALUES LESS THAN (2026, 1),
 PARTITION p_max     VALUES LESS THAN (MAXVALUE, MAXVALUE))
/
