REM ============================================================================
REM  o2pg collector 0.2.0
REM
REM  Read-only inventory of one or more Oracle schemas, written as a single JSON
REM  file that `o2pg assess` can analyse offline.
REM
REM  Usage (SQL*Plus or SQLcl):
REM      sqlplus -s user/password@//host:1521/service @collect.sql HR,SALES
REM
REM  Output: o2pg_inventory.json in the current directory.
REM
REM  Requirements:
REM    * Oracle Database 19c or later. The script uses JSON_OBJECT ... RETURNING
REM      CLOB and the *_VC columns (DATA_DEFAULT_VC, SEARCH_CONDITION_VC,
REM      TEXT_VC). Older releases are not supported.
REM    * A user that can see the target schemas through the ALL_ views: the
REM      schema owner itself, or any user with SELECT ANY DICTIONARY or
REM      SELECT_CATALOG_ROLE plus SELECT on the objects.
REM
REM  The script only runs SELECT statements against data dictionary views. It
REM  does not read table data and does not create or change anything.
REM
REM  Status: written against the 19c data dictionary documentation and checked
REM  for shape by the o2pg test suite, but not yet run in CI against a live
REM  database. Please open an issue if it fails on your system.
REM ============================================================================

SET ECHO OFF
SET TERMOUT OFF
SET FEEDBACK OFF
SET HEADING OFF
SET PAGESIZE 0
SET NEWPAGE NONE
SET LINESIZE 32767
SET LONG 2000000000
SET LONGCHUNKSIZE 32767
SET TRIMSPOOL ON
SET TRIMOUT ON
SET TAB OFF
SET VERIFY OFF
SET WRAP ON
SET SERVEROUTPUT OFF
SET NULL ''
WHENEVER SQLERROR EXIT SQL.SQLCODE

DEFINE o2pg_schemas = "&1"
DEFINE o2pg_owners = "SELECT REGEXP_SUBSTR(UPPER('&o2pg_schemas'), '[^, ]+', 1, LEVEL) FROM dual CONNECT BY REGEXP_SUBSTR(UPPER('&o2pg_schemas'), '[^, ]+', 1, LEVEL) IS NOT NULL"
DEFINE o2pg_tables = "SELECT owner, table_name FROM all_tables WHERE owner IN (&o2pg_owners) AND table_name NOT LIKE 'BIN$%' AND table_name NOT LIKE 'AQ$%' AND nested = 'NO' AND secondary = 'N' AND (iot_type IS NULL OR iot_type = 'IOT') AND (owner, table_name) NOT IN (SELECT owner, mview_name FROM all_mviews) AND (owner, table_name) NOT IN (SELECT owner, queue_table FROM all_queue_tables)"

SPOOL o2pg_inventory.json

PROMPT {
PROMPT "metadata":
SELECT JSON_OBJECT(
         'collector_version'    VALUE '0.2.0',
         'collected_at'         VALUE TO_CHAR(SYS_EXTRACT_UTC(SYSTIMESTAMP), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
         'db_name'              VALUE SYS_CONTEXT('USERENV', 'DB_NAME'),
         'db_version'           VALUE v.version,
         'character_set'        VALUE n.character_set,
         'nchar_character_set'  VALUE n.nchar_character_set,
         'nls_length_semantics' VALUE n.nls_length_semantics,
         'schemas'              VALUE UPPER('&o2pg_schemas')
         RETURNING CLOB)
  FROM (SELECT MAX(version) AS version
          FROM product_component_version
         WHERE product LIKE 'Oracle Database%') v,
       (SELECT MAX(CASE parameter WHEN 'NLS_CHARACTERSET' THEN value END) AS character_set,
               MAX(CASE parameter WHEN 'NLS_NCHAR_CHARACTERSET' THEN value END) AS nchar_character_set,
               MAX(CASE parameter WHEN 'NLS_LENGTH_SEMANTICS' THEN value END) AS nls_length_semantics
          FROM nls_database_parameters) n;

PROMPT ,"tables": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'        VALUE x.owner,
         'name'         VALUE x.table_name,
         'temporary'    VALUE x.temporary,
         'duration'     VALUE x.duration,
         'iot_type'     VALUE x.iot_type,
         'partitioned'  VALUE x.partitioned,
         'num_rows'     VALUE x.num_rows,
         'compression'  VALUE x.compression,
         'compress_for' VALUE x.compress_for,
         'logging'      VALUE x.logging,
         'cluster_name' VALUE x.cluster_name,
         'degree'       VALUE TRIM(x.degree)
         RETURNING CLOB)
  FROM (SELECT t.*
          FROM all_tables t
         WHERE (t.owner, t.table_name) IN (&o2pg_tables)
         ORDER BY t.owner, t.table_name) x;
PROMPT ]

PROMPT ,"columns": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'           VALUE x.owner,
         'table_name'      VALUE x.table_name,
         'name'            VALUE x.column_name,
         'data_type'       VALUE x.data_type,
         'data_type_owner' VALUE x.data_type_owner,
         'data_length'     VALUE x.data_length,
         'data_precision'  VALUE x.data_precision,
         'data_scale'      VALUE x.data_scale,
         'char_length'     VALUE x.char_length,
         'char_used'       VALUE x.char_used,
         'nullable'        VALUE x.nullable,
         'data_default'    VALUE x.data_default_vc,
         'virtual'         VALUE x.virtual_column,
         'identity'        VALUE x.identity_column
         RETURNING CLOB)
  FROM (SELECT c.*
          FROM all_tab_cols c
         WHERE (c.owner, c.table_name) IN (&o2pg_tables)
           AND c.hidden_column = 'NO'
         ORDER BY c.owner, c.table_name, c.column_id) x;
PROMPT ]

PROMPT ,"constraints": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'             VALUE x.owner,
         'table_name'        VALUE x.table_name,
         'name'              VALUE x.constraint_name,
         'type'              VALUE x.constraint_type,
         'columns'           VALUE x.col_list,
         'search_condition'  VALUE x.search_condition_vc,
         'r_owner'           VALUE x.r_owner,
         'r_constraint_name' VALUE x.r_constraint_name,
         'delete_rule'       VALUE x.delete_rule,
         'status'            VALUE x.status,
         'validated'         VALUE x.validated,
         'deferrable'        VALUE x.deferrable
         RETURNING CLOB)
  FROM (SELECT c.owner, c.table_name, c.constraint_name, c.constraint_type,
               c.search_condition_vc, c.r_owner, c.r_constraint_name, c.delete_rule,
               c.status, c.validated, c.deferrable,
               (SELECT LISTAGG(cc.column_name, ',') WITHIN GROUP (ORDER BY cc.position, cc.column_name)
                  FROM all_cons_columns cc
                 WHERE cc.owner = c.owner
                   AND cc.constraint_name = c.constraint_name
                   AND cc.table_name = c.table_name) AS col_list
          FROM all_constraints c
         WHERE (c.owner, c.table_name) IN (&o2pg_tables)
           AND c.constraint_type IN ('P', 'U', 'R', 'C')
         ORDER BY c.owner, c.table_name, c.constraint_name) x;
PROMPT ]

PROMPT ,"indexes": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'       VALUE x.owner,
         'name'        VALUE x.index_name,
         'table_owner' VALUE x.table_owner,
         'table_name'  VALUE x.table_name,
         'index_type'  VALUE x.index_type,
         'uniqueness'  VALUE x.uniqueness,
         'columns'     VALUE x.col_list,
         'partitioned' VALUE x.partitioned,
         'ityp_name'   VALUE x.ityp_name
         RETURNING CLOB)
  FROM (SELECT i.owner, i.index_name, i.table_owner, i.table_name, i.index_type,
               i.uniqueness, i.partitioned, i.ityp_name,
               (SELECT LISTAGG(ic.column_name, ',') WITHIN GROUP (ORDER BY ic.column_position)
                  FROM all_ind_columns ic
                 WHERE ic.index_owner = i.owner
                   AND ic.index_name = i.index_name) AS col_list
          FROM all_indexes i
         WHERE (i.table_owner, i.table_name) IN (&o2pg_tables)
           AND i.index_type <> 'LOB'
         ORDER BY i.owner, i.index_name) x;
PROMPT ]

PROMPT ,"sequences": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'        VALUE x.sequence_owner,
         'name'         VALUE x.sequence_name,
         'min_value'    VALUE x.min_value,
         'max_value'    VALUE x.max_value,
         'increment_by' VALUE x.increment_by,
         'cycle'        VALUE x.cycle_flag,
         'order'        VALUE x.order_flag,
         'cache_size'   VALUE x.cache_size,
         'last_number'  VALUE x.last_number
         RETURNING CLOB)
  FROM (SELECT s.*
          FROM all_sequences s
         WHERE s.sequence_owner IN (&o2pg_owners)
           AND s.sequence_name NOT LIKE 'ISEQ$$%'
         ORDER BY s.sequence_owner, s.sequence_name) x;
PROMPT ]

PROMPT ,"views": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'       VALUE x.owner,
         'name'        VALUE x.view_name,
         'text'        VALUE x.text_vc,
         'text_length' VALUE x.text_length
         RETURNING CLOB)
  FROM (SELECT v.owner, v.view_name, v.text_vc, v.text_length
          FROM all_views v
         WHERE v.owner IN (&o2pg_owners)
         ORDER BY v.owner, v.view_name) x;
PROMPT ]

PROMPT ,"synonyms": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'       VALUE x.owner,
         'name'        VALUE x.synonym_name,
         'table_owner' VALUE x.table_owner,
         'table_name'  VALUE x.table_name,
         'db_link'     VALUE x.db_link
         RETURNING CLOB)
  FROM (SELECT s.*
          FROM all_synonyms s
         WHERE s.owner IN (&o2pg_owners)
            OR (s.owner = 'PUBLIC' AND s.table_owner IN (&o2pg_owners))
         ORDER BY s.owner, s.synonym_name) x;
PROMPT ]

PROMPT ,"triggers": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'            VALUE x.owner,
         'name'             VALUE x.trigger_name,
         'table_owner'      VALUE x.table_owner,
         'table_name'       VALUE x.table_name,
         'trigger_type'     VALUE x.trigger_type,
         'triggering_event' VALUE x.triggering_event,
         'base_object_type' VALUE x.base_object_type,
         'status'           VALUE x.status
         RETURNING CLOB)
  FROM (SELECT t.owner, t.trigger_name, t.table_owner, t.table_name, t.trigger_type,
               t.triggering_event, t.base_object_type, t.status
          FROM all_triggers t
         WHERE t.owner IN (&o2pg_owners)
           AND t.trigger_name NOT LIKE 'BIN$%'
         ORDER BY t.owner, t.trigger_name) x;
PROMPT ]

PROMPT ,"source": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner' VALUE x.owner,
         'name'  VALUE x.name,
         'type'  VALUE x.type,
         'line'  VALUE x.line,
         'text'  VALUE x.text
         RETURNING CLOB)
  FROM (SELECT s.owner, s.name, s.type, s.line, s.text
          FROM all_source s
         WHERE s.owner IN (&o2pg_owners)
           AND s.type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER',
                          'TYPE', 'TYPE BODY', 'JAVA SOURCE')
           AND s.name NOT LIKE 'BIN$%'
         ORDER BY s.owner, s.type, s.name, s.line) x;
PROMPT ]

PROMPT ,"db_links": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'    VALUE x.owner,
         'name'     VALUE x.db_link,
         'username' VALUE x.username,
         'host'     VALUE x.host
         RETURNING CLOB)
  FROM (SELECT l.owner, l.db_link, l.username, l.host
          FROM all_db_links l
         WHERE l.owner IN (&o2pg_owners) OR l.owner = 'PUBLIC'
         ORDER BY l.owner, l.db_link) x;
PROMPT ]

PROMPT ,"mviews": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'            VALUE x.owner,
         'name'             VALUE x.mview_name,
         'refresh_method'   VALUE x.refresh_method,
         'refresh_mode'     VALUE x.refresh_mode,
         'fast_refreshable' VALUE x.fast_refreshable,
         'build_mode'       VALUE x.build_mode
         RETURNING CLOB)
  FROM (SELECT m.owner, m.mview_name, m.refresh_method, m.refresh_mode,
               m.fast_refreshable, m.build_mode
          FROM all_mviews m
         WHERE m.owner IN (&o2pg_owners)
         ORDER BY m.owner, m.mview_name) x;
PROMPT ]

PROMPT ,"part_tables": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'                VALUE x.owner,
         'table_name'           VALUE x.table_name,
         'partitioning_type'    VALUE x.partitioning_type,
         'subpartitioning_type' VALUE x.subpartitioning_type,
         'partition_count'      VALUE x.partition_count,
         'interval'             VALUE x.interval_expr,
         'ref_constraint_name'  VALUE x.ref_ptn_constraint_name,
         'partition_keys'       VALUE x.key_list
         RETURNING CLOB)
  FROM (SELECT p.owner, p.table_name, p.partitioning_type, p.subpartitioning_type,
               p.partition_count, p."INTERVAL" AS interval_expr, p.ref_ptn_constraint_name,
               (SELECT LISTAGG(k.column_name, ',') WITHIN GROUP (ORDER BY k.column_position)
                  FROM all_part_key_columns k
                 WHERE k.owner = p.owner
                   AND k.name = p.table_name
                   AND k.object_type = 'TABLE') AS key_list
          FROM all_part_tables p
         WHERE (p.owner, p.table_name) IN (&o2pg_tables)
         ORDER BY p.owner, p.table_name) x;
PROMPT ]

PROMPT ,"lobs": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'         VALUE x.owner,
         'table_name'    VALUE x.table_name,
         'column_name'   VALUE x.column_name,
         'securefile'    VALUE x.securefile,
         'compression'   VALUE x.compression,
         'deduplication' VALUE x.deduplication,
         'in_row'        VALUE x.in_row
         RETURNING CLOB)
  FROM (SELECT l.owner, l.table_name, l.column_name, l.securefile, l.compression,
               l.deduplication, l.in_row
          FROM all_lobs l
         WHERE (l.owner, l.table_name) IN (&o2pg_tables)
         ORDER BY l.owner, l.table_name, l.column_name) x;
PROMPT ]

PROMPT ,"types": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'     VALUE x.owner,
         'name'      VALUE x.type_name,
         'typecode'  VALUE x.typecode,
         'coll_type' VALUE x.coll_type,
         'elem_type' VALUE x.elem_type_name,
         'methods'   VALUE x.methods
         RETURNING CLOB)
  FROM (SELECT t.owner, t.type_name, t.typecode, c.coll_type, c.elem_type_name, t.methods
          FROM all_types t
          LEFT JOIN all_coll_types c
            ON c.owner = t.owner AND c.type_name = t.type_name
         WHERE t.owner IN (&o2pg_owners)
         ORDER BY t.owner, t.type_name) x;
PROMPT ]

PROMPT ,"scheduler_jobs": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'           VALUE x.owner,
         'name'            VALUE x.job_name,
         'job_type'        VALUE x.job_type,
         'job_action'      VALUE x.job_action,
         'program_name'    VALUE x.program_name,
         'repeat_interval' VALUE x.repeat_interval,
         'enabled'         VALUE x.enabled
         RETURNING CLOB)
  FROM (SELECT j.owner, j.job_name, j.job_type, j.job_action, j.program_name,
               j.repeat_interval, j.enabled
          FROM all_scheduler_jobs j
         WHERE j.owner IN (&o2pg_owners)
         ORDER BY j.owner, j.job_name) x;
PROMPT ]

PROMPT ,"grants": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'        VALUE x.table_schema,
         'grantee'      VALUE x.grantee,
         'privilege'    VALUE x.privilege,
         'object_count' VALUE x.object_count
         RETURNING CLOB)
  FROM (SELECT p.table_schema, p.grantee, p.privilege, COUNT(*) AS object_count
          FROM all_tab_privs p
         WHERE p.table_schema IN (&o2pg_owners)
         GROUP BY p.table_schema, p.grantee, p.privilege
         ORDER BY p.table_schema, p.grantee, p.privilege) x;
PROMPT ]

PROMPT ,"policies": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'object_owner' VALUE x.object_owner,
         'object_name'  VALUE x.object_name,
         'policy_name'  VALUE x.policy_name,
         'function'     VALUE x.policy_function,
         'enabled'      VALUE x.enable
         RETURNING CLOB)
  FROM (SELECT p.object_owner, p.object_name, p.policy_name, p.enable,
               p.pf_owner || '.' || NVL2(p."PACKAGE", p."PACKAGE" || '.', NULL) || p."FUNCTION"
                 AS policy_function
          FROM all_policies p
         WHERE p.object_owner IN (&o2pg_owners)
         ORDER BY p.object_owner, p.object_name, p.policy_name) x;
PROMPT ]

PROMPT ,"queues": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'       VALUE x.owner,
         'name'        VALUE x.name,
         'queue_table' VALUE x.queue_table,
         'queue_type'  VALUE x.queue_type
         RETURNING CLOB)
  FROM (SELECT q.owner, q.name, q.queue_table, q.queue_type
          FROM all_queues q
         WHERE q.owner IN (&o2pg_owners)
           AND q.queue_type = 'NORMAL_QUEUE'
         ORDER BY q.owner, q.name) x;
PROMPT ]

PROMPT ,"encrypted_columns": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner'          VALUE x.owner,
         'table_name'     VALUE x.table_name,
         'column_name'    VALUE x.column_name,
         'encryption_alg' VALUE x.encryption_alg
         RETURNING CLOB)
  FROM (SELECT e.owner, e.table_name, e.column_name, e.encryption_alg
          FROM all_encrypted_columns e
         WHERE e.owner IN (&o2pg_owners)
         ORDER BY e.owner, e.table_name, e.column_name) x;
PROMPT ]

PROMPT ,"invalid_objects": [
SELECT CASE WHEN ROWNUM > 1 THEN ',' END || JSON_OBJECT(
         'owner' VALUE x.owner,
         'name'  VALUE x.object_name,
         'type'  VALUE x.object_type
         RETURNING CLOB)
  FROM (SELECT o.owner, o.object_name, o.object_type
          FROM all_objects o
         WHERE o.owner IN (&o2pg_owners)
           AND o.status = 'INVALID'
         ORDER BY o.owner, o.object_type, o.object_name) x;
PROMPT ]
PROMPT }

SPOOL OFF
SET TERMOUT ON
PROMPT o2pg: wrote o2pg_inventory.json
EXIT
