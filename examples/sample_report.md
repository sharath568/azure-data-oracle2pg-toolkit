# o2pg assessment: ORCLPDB1

Generated 2026-10-10T23:37:46Z by o2pg 0.2.0. Source: Oracle 19.0.0.0.0; schemas HR, SALES; character set WE8MSWIN1252; collected 2026-10-01T14:05:11Z.

## Summary

| Metric | Value |
|---|---|
| Migration complexity | 86/100 (Very high) |
| Effort band | L: ~2 to 6 person-months (133.3 points) |
| Blockers | 5 |
| AI readiness | 26/100 |
| Rules triggered | 89 of 97 |

Triggered rules by severity:

| Severity | Rules |
|---|---|
| Blocker | 5 |
| High | 19 |
| Medium | 40 |
| Low | 21 |
| Info | 4 |

Objects in the inventory:

| Object | Count |
|---|---|
| Tables | 14 |
| Columns | 86 |
| Constraints | 26 |
| Indexes | 21 |
| Sequences | 3 |
| Views | 4 |
| Synonyms | 4 |
| Triggers | 5 |
| Database links | 1 |
| Materialized views | 2 |
| Partitioned tables | 3 |
| LOB columns | 2 |
| User-defined types | 3 |
| Scheduler jobs | 3 |
| Grant summary rows | 7 |
| VPD policies | 1 |
| AQ queues | 1 |
| TDE encrypted columns | 1 |
| Invalid objects | 1 |
| PL/SQL and other source objects | 18 |
| Lines of PL/SQL | 259 |

## AI readiness

**26/100.** Do not paste this schema into an LLM as-is. Too much behaviour is implicit; convert with a written spec and review every object.

These Oracle behaviours are implicit in the schema and code. A model converting it will not know about them unless you say so. Put this list in your prompt or conversion spec:

1. **DATE columns carry a time of day** (O2PG-DT-003, 6 object(s)): Oracle DATE includes hours, minutes and seconds. Tell the model to map DATE to timestamp(0), not date.
2. **Package-level state (global variables)** (O2PG-PL-002, 2 object(s)): These packages keep state between calls. A model converting one function at a time cannot see that; tell it which variables are session state and how you want them replaced (for example current_setting('app.x')).
3. **Autonomous transactions** (O2PG-PL-003, 1 object(s)): Autonomous transactions commit even when the caller rolls back. Models tend to drop the pragma and silently change that behaviour; tell it how you want it handled.
4. **Empty string literals ('' is NULL in Oracle)** (O2PG-SQ-008, 1 object(s)): Oracle treats '' as NULL everywhere. A model will translate '' literally. Tell it to treat every '' as NULL and to flag comparisons with '' for review.
5. **NUMBER without declared precision** (O2PG-DT-001, 3 object(s)): Bare NUMBER is not an integer and not a float. Tell the model to map it to numeric unless you give it a profiled precision and scale.
6. **NOT NULL character columns will start accepting empty strings** (O2PG-DT-006, 13 object(s)): In Oracle '' IS NULL. Tell the model that NOT NULL character columns must also reject empty strings, and to add CHECK (col <> '') where that matters.
7. **Row-level DML triggers** (O2PG-OB-004, 3 object(s)): Triggers change data the DDL does not mention. Give the model the trigger bodies along with the table, or it will produce tables that silently lose those side effects.
8. **Quoted, mixed-case or special-character identifiers** (O2PG-OB-016, 1 object(s)): Upper-case Oracle names copied with quotes ("EMPLOYEES") force quoting in every PostgreSQL query. Tell the model to emit unquoted lower-case names and list the identifiers that really are case-sensitive.
9. **Dynamic SQL (EXECUTE IMMEDIATE, DBMS_SQL)** (O2PG-PL-007, 1 object(s)): SQL inside strings is not visible as SQL. Tell the model to convert the dialect inside dynamic SQL strings as well, and review those strings yourself.
10. **Definer-rights program units** (O2PG-SE-004, 7 object(s)): Oracle code runs as its owner by default; PostgreSQL code runs as the caller. Tell the model which units need SECURITY DEFINER, or permissions will break.
11. **ROWNUM** (O2PG-SQ-002, 2 object(s)): ROWNUM filters before ORDER BY. A model will usually 'fix' this into a correct top-n with LIMIT, which changes results. Tell it whether to keep the old behaviour.
12. **SYSDATE and SYSTIMESTAMP** (O2PG-SQ-007, 8 object(s)): SYSDATE is server local time and changes within a transaction. Tell the model which PostgreSQL function you want (LOCALTIMESTAMP, now() or clock_timestamp()).
13. **TO_DATE / TO_CHAR / TO_NUMBER format models** (O2PG-SQ-012, 1 object(s)): Conversions without an explicit format depend on NLS session settings the model cannot see. Give it the NLS_DATE_FORMAT and NLS_NUMERIC_CHARACTERS in use.
14. **Date arithmetic with numbers** (O2PG-SQ-017, 2 object(s)): Numbers added to dates are days (fractions are hours and minutes). Tell the model to convert them to intervals after DATE becomes timestamp(0).
15. **String concatenation with possible NULLs** (O2PG-SQ-018, 6 object(s)): Oracle || ignores NULLs; PostgreSQL || returns NULL. Tell the model to use concat() or COALESCE for any nullable operand.
16. **Character columns with BYTE length semantics** (O2PG-DT-005, 28 object(s)): VARCHAR2 lengths here are in bytes. Tell the model whether you want character lengths kept as-is or recalculated.
17. **RAISE_APPLICATION_ERROR and Oracle error codes** (O2PG-PL-013, 1 object(s)): Error numbers are part of the contract with the application. Give the model a mapping from ORA-20xxx codes to SQLSTATE values instead of letting it invent one.

## Azure extensions to allow-list

Add these to the `azure.extensions` server parameter before `CREATE EXTENSION`. `pg_cron` also needs `shared_preload_libraries` and a restart.

| Extension | Suggested by |
|---|---|
| `hstore` | O2PG-PL-018 |
| `orafce` | O2PG-AZ-005, O2PG-PL-012, O2PG-SQ-003, O2PG-SQ-004, O2PG-SQ-009, O2PG-SQ-011 |
| `pg_cron` | O2PG-AZ-001, O2PG-PT-002 |
| `pg_partman` | O2PG-PT-001, O2PG-PT-002 |
| `pg_stat_statements` | O2PG-SQ-013 |
| `pgcrypto` | O2PG-PL-012, O2PG-SE-002 |
| `postgis` | O2PG-DT-012 |
| `postgres_fdw` | O2PG-OB-003, O2PG-PL-003 |

## Findings

| Severity | Rule | Title | Objects | Effort |
|---|---|---|---|---|
| Blocker | O2PG-AZ-002 | Scheduler jobs that run OS executables or scripts | 1 | 2.0 |
| Blocker | O2PG-DT-010 | BFILE columns | 1 | 3.0 |
| Blocker | O2PG-PL-009 | UTL_FILE file access | 1 | 4.0 |
| Blocker | O2PG-PL-010 | Network calls from the database (UTL_HTTP, UTL_SMTP, UTL_TCP, UTL_MAIL) | 1 | 4.0 |
| Blocker | O2PG-PL-015 | Wrapped (obfuscated) PL/SQL | 1 | 5.0 |
| High | O2PG-DT-003 | DATE columns carry a time of day | 6 | 1.5 |
| High | O2PG-OB-012 | User-defined object and collection types | 3 | 4.5 |
| High | O2PG-AZ-003 | Advanced Queuing | 2 | 6.0 |
| High | O2PG-DT-016 | Columns of user-defined object or collection types | 2 | 3.0 |
| High | O2PG-OB-003 | Database links | 2 | 6.0 |
| High | O2PG-PL-002 | Package-level state (global variables) | 2 | 6.0 |
| High | O2PG-DT-008 | LONG and LONG RAW columns | 1 | 1.0 |
| High | O2PG-DT-012 | SDO_GEOMETRY (Oracle Spatial) columns | 1 | 2.0 |
| High | O2PG-OB-005 | Compound triggers | 1 | 2.0 |
| High | O2PG-OB-006 | Schema and database event triggers | 1 | 2.0 |
| High | O2PG-OB-009 | Materialized views refreshed ON COMMIT | 1 | 3.0 |
| High | O2PG-OB-015 | Identifiers longer than 63 bytes | 1 | 0.5 |
| High | O2PG-PF-005 | Domain indexes (Oracle Text and others) | 1 | 2.0 |
| High | O2PG-PL-003 | Autonomous transactions | 1 | 3.0 |
| High | O2PG-PT-003 | Reference and system partitioning | 1 | 3.0 |
| High | O2PG-PT-005 | Primary or unique keys that do not include the partition key | 1 | 1.5 |
| High | O2PG-SE-001 | Virtual Private Database (VPD) policies | 1 | 3.0 |
| High | O2PG-SQ-001 | Hierarchical queries (CONNECT BY) | 1 | 2.0 |
| High | O2PG-SQ-008 | Empty string literals ('' is NULL in Oracle) | 1 | 1.0 |
| Medium | O2PG-DT-006 | NOT NULL character columns will start accepting empty strings | 13 | 1.3 |
| Medium | O2PG-SQ-007 | SYSDATE and SYSTIMESTAMP | 8 | 2.0 |
| Medium | O2PG-SE-004 | Definer-rights program units | 7 | 1.75 |
| Medium | O2PG-SQ-018 | String concatenation with possible NULLs | 6 | 1.5 |
| Medium | O2PG-DT-001 | NUMBER without declared precision | 3 | 0.3 |
| Medium | O2PG-OB-004 | Row-level DML triggers | 3 | 3.0 |
| Medium | O2PG-PF-008 | Very large tables | 3 | 3.0 |
| Medium | O2PG-PL-001 | Packages | 3 | 6.0 |
| Medium | O2PG-AZ-001 | DBMS_SCHEDULER jobs that run SQL or PL/SQL | 2 | 1.5 |
| Medium | O2PG-DT-004 | Time zone aware timestamps | 2 | 0.5 |
| Medium | O2PG-DT-009 | LOB columns | 2 | 1.0 |
| Medium | O2PG-OB-002 | Public synonyms | 2 | 1.0 |
| Medium | O2PG-PL-012 | Other Oracle-supplied packages (DBMS_*, UTL_*) | 2 | 1.5 |
| Medium | O2PG-PL-014 | Transaction control inside stored code | 2 | 2.0 |
| Medium | O2PG-PL-016 | Pipelined table functions | 2 | 2.0 |
| Medium | O2PG-PL-018 | Associative arrays (INDEX BY tables) | 2 | 2.0 |
| Medium | O2PG-PT-006 | Global indexes on partitioned tables | 2 | 1.0 |
| Medium | O2PG-SQ-002 | ROWNUM | 2 | 1.5 |
| Medium | O2PG-SQ-015 | USER, USERENV and SYS_CONTEXT | 2 | 1.0 |
| Medium | O2PG-SQ-017 | Date arithmetic with numbers | 2 | 0.5 |
| Medium | O2PG-AZ-004 | Database character set is not Unicode | 1 | 2.0 |
| Medium | O2PG-DT-011 | XMLTYPE columns | 1 | 1.0 |
| Medium | O2PG-DT-013 | ROWID and UROWID columns | 1 | 0.5 |
| Medium | O2PG-OB-011 | Global temporary tables | 1 | 1.0 |
| Medium | O2PG-OB-013 | Invalid objects | 1 | 0.25 |
| Medium | O2PG-OB-014 | Tables without a primary key | 1 | 0.5 |
| Medium | O2PG-OB-016 | Quoted, mixed-case or special-character identifiers | 1 | 0.25 |
| Medium | O2PG-OB-017 | Names that are reserved words in PostgreSQL | 1 | 0.25 |
| Medium | O2PG-PF-001 | Bitmap indexes | 1 | 0.5 |
| Medium | O2PG-PF-004 | Index-organized tables | 1 | 0.75 |
| Medium | O2PG-PL-005 | REF CURSOR and SYS_REFCURSOR | 1 | 1.0 |
| Medium | O2PG-PL-007 | Dynamic SQL (EXECUTE IMMEDIATE, DBMS_SQL) | 1 | 1.5 |
| Medium | O2PG-PL-008 | DBMS_LOB usage | 1 | 1.0 |
| Medium | O2PG-PT-002 | Interval partitioning | 1 | 1.5 |
| Medium | O2PG-PT-004 | Composite (sub)partitioning | 1 | 1.5 |
| Medium | O2PG-SE-002 | TDE column encryption | 1 | 0.5 |
| Medium | O2PG-SE-003 | Object privileges granted to PUBLIC | 1 | 0.25 |
| Medium | O2PG-SQ-005 | Oracle outer join operator (+) | 1 | 0.75 |
| Medium | O2PG-SQ-006 | MERGE statements | 1 | 1.0 |
| Medium | O2PG-SQ-012 | TO_DATE / TO_CHAR / TO_NUMBER format models | 1 | 0.25 |
| Low | O2PG-DT-005 | Character columns with BYTE length semantics | 28 | 1.4 |
| Low | O2PG-DT-015 | Column defaults that use Oracle functions | 6 | 0.6 |
| Low | O2PG-SQ-004 | NVL and NVL2 | 5 | 0.5 |
| Low | O2PG-DT-007 | CHAR columns and blank padding | 3 | 0.3 |
| Low | O2PG-PF-006 | Foreign keys without a supporting index | 3 | 0.3 |
| Low | O2PG-OB-007 | Sequences with MAXVALUE beyond bigint | 2 | 0.2 |
| Low | O2PG-SQ-009 | SELECT ... FROM DUAL | 2 | 0.2 |
| Low | O2PG-SQ-011 | Oracle date functions | 2 | 0.5 |
| Low | O2PG-SQ-014 | Sequence NEXTVAL / CURRVAL | 2 | 0.2 |
| Low | O2PG-DT-002 | Binary and approximate numeric types | 1 | 0.1 |
| Low | O2PG-DT-014 | Virtual columns | 1 | 0.25 |
| Low | O2PG-OB-001 | Private synonyms | 1 | 0.25 |
| Low | O2PG-PF-002 | Function-based indexes | 1 | 0.25 |
| Low | O2PG-PF-003 | Reverse key indexes | 1 | 0.25 |
| Low | O2PG-PF-007 | Compressed tables | 1 | 0.25 |
| Low | O2PG-PL-004 | BULK COLLECT and FORALL | 1 | 0.5 |
| Low | O2PG-PL-013 | RAISE_APPLICATION_ERROR and Oracle error codes | 1 | 0.5 |
| Low | O2PG-PT-001 | Range, list and hash partitioned tables | 1 | 1.0 |
| Low | O2PG-SQ-003 | DECODE | 1 | 0.25 |
| Low | O2PG-SQ-010 | MINUS set operator | 1 | 0.1 |
| Low | O2PG-SQ-013 | Optimizer hints | 1 | 0.1 |
| Info | O2PG-PL-006 | %TYPE and %ROWTYPE anchored declarations | 4 | 0.0 |
| Info | O2PG-SE-006 | Object privileges to map to roles | 4 | 0.4 |
| Info | O2PG-OB-008 | Sequences must be re-synced after the data load | 3 | 0.15 |
| Info | O2PG-AZ-005 | orafce could shorten the conversion | 1 | 0.0 |

### O2PG-AZ-002: Scheduler jobs that run OS executables or scripts

**Severity:** Blocker · **Category:** azure-specific · **Objects:** 1 · **Effort points:** 2.0

EXECUTABLE and script jobs run programs on the database host. A managed service gives you no host access.

**What to do:** Move them to Azure Automation, Azure Functions with a timer trigger, Container Apps jobs or whatever scheduler the rest of your platform uses.

| Object | Type | Detail |
|---|---|---|
| SALES.ARCHIVE_EXPORT_FILES | JOB | EXECUTABLE: /u01/app/scripts/archive_exports.sh |

### O2PG-DT-010: BFILE columns

**Severity:** Blocker · **Category:** datatypes · **Objects:** 1 · **Effort points:** 3.0

BFILE points at a file on the database server's file system. Azure Database for PostgreSQL Flexible Server gives you no file system access, so there is nothing to point at.

**What to do:** Move the files to Azure Blob Storage and store the blob URL or path in a text column. File reads move to the application.

| Object | Type | Detail |
|---|---|---|
| SALES.PRODUCT_DOCS.DOC_FILE | COLUMN | BFILE -> suggest text |

### O2PG-PL-009: UTL_FILE file access

**Severity:** Blocker · **Category:** plsql · **Objects:** 1 · **Effort points:** 4.0

Reading and writing server files is not possible on Azure Database for PostgreSQL Flexible Server; there is no file system you can reach from SQL.

**What to do:** Move file import/export to the application, Azure Data Factory or an Azure Function writing to Blob Storage. For bulk loads use COPY FROM STDIN from a client.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 4 matches, first at line 16 |

### O2PG-PL-010: Network calls from the database (UTL_HTTP, UTL_SMTP, UTL_TCP, UTL_MAIL)

**Severity:** Blocker · **Category:** plsql · **Objects:** 1 · **Effort points:** 4.0

The database calling web services or sending mail has no supported equivalent on Flexible Server.

**What to do:** Write an outbox table inside the transaction and have an application worker, Azure Function or Logic App deliver the message. This is usually more reliable anyway.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 32 |

### O2PG-PL-015: Wrapped (obfuscated) PL/SQL

**Severity:** Blocker · **Category:** plsql · **Objects:** 1 · **Effort points:** 5.0

The source was created with the wrap utility, so nobody can read or convert it.

**What to do:** Get the original source from version control or the vendor. Without it the unit has to be rewritten from its observed behaviour.

| Object | Type | Detail |
|---|---|---|
| SALES.PRICING | PACKAGE BODY | source is wrapped |

### O2PG-DT-003: DATE columns carry a time of day

**Severity:** High · **Category:** datatypes · **Objects:** 6 · **Effort points:** 1.5

Oracle DATE stores year to second. PostgreSQL date has no time at all, so a name-for-name mapping silently drops the time portion.

**What to do:** Map to timestamp(0). Only use date after checking that every row has a midnight time (e.g. COUNT(*) WHERE col <> TRUNC(col) = 0). Review TRUNC(col) and col + n arithmetic in code at the same time.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES.HIRE_DATE | COLUMN | DATE -> suggest timestamp(0) |
| HR.JOB_HISTORY.START_DATE | COLUMN | DATE -> suggest timestamp(0) |
| HR.JOB_HISTORY.END_DATE | COLUMN | DATE -> suggest timestamp(0) |
| SALES.CUSTOMERS.CREATED_AT | COLUMN | DATE -> suggest timestamp(0) |
| SALES.ORDERS.ORDER_DATE | COLUMN | DATE -> suggest timestamp(0) |
| SALES.SALES_HISTORY.SALE_DATE | COLUMN | DATE -> suggest timestamp(0) |

### O2PG-OB-012: User-defined object and collection types

**Severity:** High · **Category:** objects · **Objects:** 3 · **Effort points:** 4.5

Object types, VARRAYs and nested table types. PostgreSQL composite types have no methods, inheritance or constructors with defaults.

**What to do:** Object types used only in PL/SQL -> composite types. Member methods -> plain functions taking the composite as the first argument. Collections -> arrays. Types used as column types: see O2PG-DT-016.

| Object | Type | Detail |
|---|---|---|
| SALES.ADDRESS_T | TYPE | OBJECT, 1 method(s) |
| SALES.PHONE_LIST_T | TYPE | VARYING ARRAY OF VARCHAR2 |
| SALES.ORDER_EVENT_T | TYPE | OBJECT |

### O2PG-AZ-003: Advanced Queuing

**Severity:** High · **Category:** azure-specific · **Objects:** 2 · **Effort points:** 6.0

Oracle AQ queues and the code that enqueues or dequeues. PostgreSQL has no built-in message queue.

**What to do:** For in-database work queues, a table plus SELECT ... FOR UPDATE SKIP LOCKED and LISTEN/NOTIFY works well. For integration between systems, use Azure Service Bus and an outbox table written in the same transaction.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_EVENTS_Q | QUEUE | queue table ORDER_EVENTS_QT |
| SALES.PUBLISH_ORDER_EVENT | PROCEDURE | calls DBMS_AQ / DBMS_AQADM |

### O2PG-DT-016: Columns of user-defined object or collection types

**Severity:** High · **Category:** datatypes · **Objects:** 2 · **Effort points:** 3.0

Columns typed as Oracle object types, VARRAYs or nested tables. PostgreSQL has composite types and arrays, but no methods, no nested table storage and different constructor syntax.

**What to do:** Usually the cleanest answer is to normalise: nested tables and VARRAYs become child tables, object types become plain columns. Arrays or jsonb are reasonable when the data is only ever read as a whole.

| Object | Type | Detail |
|---|---|---|
| SALES.CUSTOMERS.ADDRESS | COLUMN | type SALES.ADDRESS_T |
| SALES.CUSTOMERS.PHONES | COLUMN | type SALES.PHONE_LIST_T |

### O2PG-OB-003: Database links

**Severity:** High · **Category:** objects · **Objects:** 2 · **Effort points:** 6.0

Database links (and synonyms that reach through them) let SQL query another database. On Azure Flexible Server postgres_fdw can reach other PostgreSQL servers, but there is no supported way to query a remaining Oracle database from the PostgreSQL side.

**What to do:** Links to databases that also move to PostgreSQL -> postgres_fdw with a foreign server and user mapping (check outbound networking from the server's VNet or firewall). Links to systems that stay on Oracle -> move the integration into the application or a data pipeline, and plan the cutover order around it. Credentials stored in links become user mappings; rotate them.

| Object | Type | Detail |
|---|---|---|
| SALES.LEGACY_BILLING | DATABASE LINK | host billing-db.corp.example.com:1521/BILLPDB, stored user BILLING_RO |
| SALES.LEGACY_INVOICES | SYNONYM | -> BILLING.INVOICES@LEGACY_BILLING |

### O2PG-PL-002: Package-level state (global variables)

**Severity:** High · **Category:** plsql · **Objects:** 2 · **Effort points:** 6.0

Variables declared at package level keep their value for the whole session. Code often relies on that as a cache or as a way to pass context between calls. PostgreSQL functions have no persistent state.

**What to do:** Replace with session settings (set_config / current_setting with a custom prefix), temporary tables, or pass the value explicitly. Read-only lookups usually become a table or an IMMUTABLE function.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE | 2 variable(s): g_last_change, g_hires_this_session |
| HR.EMP_MGMT | PACKAGE BODY | 1 variable(s): g_cache_loaded |

### O2PG-DT-008: LONG and LONG RAW columns

**Severity:** High · **Category:** datatypes · **Objects:** 1 · **Effort points:** 1.0

LONG types have been deprecated for decades and carry many restrictions. Migration tools often need special handling to extract them, and code that reads them piecewise must change.

**What to do:** LONG -> text, LONG RAW -> bytea. Check that your data movement tool supports LONG extraction and test with the largest values.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_AUDIT_LOG.OLD_VALUES | COLUMN | LONG -> suggest text |

### O2PG-DT-012: SDO_GEOMETRY (Oracle Spatial) columns

**Severity:** High · **Category:** datatypes · **Objects:** 1 · **Effort points:** 2.0

Oracle Spatial geometries need PostGIS. SRIDs, spatial indexes and SDO_* functions all need translation.

**What to do:** Allow-list postgis in azure.extensions, CREATE EXTENSION postgis, map to geometry(<type>, <srid>), replace spatial indexes with GiST, and rewrite SDO_RELATE / SDO_WITHIN_DISTANCE as ST_Intersects / ST_DWithin.

| Object | Type | Detail |
|---|---|---|
| HR.LOCATIONS.GEO_LOCATION | COLUMN | SDO_GEOMETRY -> suggest geometry |

### O2PG-OB-005: Compound triggers

**Severity:** High · **Category:** objects · **Objects:** 1 · **Effort points:** 2.0

Compound triggers combine statement and row timing points with shared state, usually to work around mutating-table errors.

**What to do:** Split into separate row and statement triggers. In PostgreSQL 10+ AFTER statement triggers with transition tables (REFERENCING NEW TABLE AS ...) often replace the whole pattern, since PostgreSQL has no mutating-table restriction.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDERS_AUDIT_CT | TRIGGER | on ORDERS |

### O2PG-OB-006: Schema and database event triggers

**Severity:** High · **Category:** objects · **Objects:** 1 · **Effort points:** 2.0

DDL, logon and startup triggers defined on SCHEMA or DATABASE. PostgreSQL event triggers cover DDL only; there is no logon trigger.

**What to do:** DDL auditing -> event triggers or Azure server logs. Logon logic -> role settings (ALTER ROLE ... SET), connection pool init SQL or the application.

| Object | Type | Detail |
|---|---|---|
| HR.HR_DDL_AUDIT | TRIGGER | AFTER EVENT CREATE OR ALTER OR DROP |

### O2PG-OB-009: Materialized views refreshed ON COMMIT

**Severity:** High · **Category:** objects · **Objects:** 1 · **Effort points:** 3.0

PostgreSQL materialized views are only refreshed by an explicit REFRESH, which is always a full rebuild. Nothing keeps them in sync on commit.

**What to do:** Replace with a plain table maintained by triggers on the base tables, or accept staleness and refresh on a schedule. If the view exists only for query rewrite, a normal view plus indexes may be enough.

| Object | Type | Detail |
|---|---|---|
| SALES.MV_DAILY_SALES | MATERIALIZED VIEW | FAST ON COMMIT |

### O2PG-OB-015: Identifiers longer than 63 bytes

**Severity:** High · **Category:** objects · **Objects:** 1 · **Effort points:** 0.5

Oracle 12.2+ allows 128-byte names. PostgreSQL truncates identifiers to 63 bytes, so two long names with the same prefix collide, and generated DDL can fail.

**What to do:** Rename before conversion and keep a rename map so code and the application can be updated consistently.

| Object | Type | Detail |
|---|---|---|
| SALES.IX_ORDER_ITEMS_PRODUCT_ID_UNIT_PRICE_QUANTITY_DISCOUNT_FOR_MONTHLY_REPORTING | INDEX | 76 bytes |

### O2PG-PF-005: Domain indexes (Oracle Text and others)

**Severity:** High · **Category:** performance · **Objects:** 1 · **Effort points:** 2.0

Domain indexes such as Oracle Text CONTEXT/CTXCAT have no equivalent, and the CONTAINS/CATSEARCH queries that use them must be rewritten.

**What to do:** Use PostgreSQL full text search: a tsvector generated column with a GIN index and @@ to_tsquery(...). Check language configuration and ranking against the Oracle results. Spatial domain indexes: see the SDO_GEOMETRY rule.

| Object | Type | Detail |
|---|---|---|
| SALES.PROD_DESC_CTX | INDEX | CONTEXT on PRODUCTS |

### O2PG-PL-003: Autonomous transactions

**Severity:** High · **Category:** plsql · **Objects:** 1 · **Effort points:** 3.0

PRAGMA AUTONOMOUS_TRANSACTION commits independently of the caller. It is most often used for error logging that must survive a rollback. PostgreSQL has no equivalent.

**What to do:** Options: write the log through a separate connection (postgres_fdw to the same server is a known workaround), move logging to the application, or use RAISE LOG and Azure Monitor / server logs. Re-check the business rule before emulating it.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 6 |

### O2PG-PT-003: Reference and system partitioning

**Severity:** High · **Category:** partitioning · **Objects:** 1 · **Effort points:** 3.0

REFERENCE partitioning inherits the parent's scheme through a foreign key; SYSTEM partitioning lets the application pick the partition. Neither exists in PostgreSQL.

**What to do:** For reference partitioning, copy the parent's partition key into the child table, partition the child on it and include it in the foreign key. System partitioning usually becomes HASH or LIST partitioning on an explicit column.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_ITEMS | TABLE | REFERENCE (via ORDER_ITEMS_ORDER_FK), 84 partitions |

### O2PG-PT-005: Primary or unique keys that do not include the partition key

**Severity:** High · **Category:** partitioning · **Objects:** 1 · **Effort points:** 1.5

PostgreSQL requires every primary key and unique constraint on a partitioned table to include all partition key columns. Oracle does not (it uses global indexes), so these constraints cannot be created as-is.

**What to do:** Either add the partition key to the constraint (which weakens uniqueness to 'unique per partition key value'), enforce global uniqueness another way, or change the partition key. Decide this before moving data.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDERS.ORDERS_PK | CONSTRAINT | missing ORDER_DATE |

### O2PG-SE-001: Virtual Private Database (VPD) policies

**Severity:** High · **Category:** security · **Objects:** 1 · **Effort points:** 3.0

VPD policies add predicates to queries through a policy function. PostgreSQL row level security is the equivalent, but policies are SQL expressions on the table, not functions returning predicate strings.

**What to do:** ALTER TABLE ... ENABLE ROW LEVEL SECURITY and CREATE POLICY ... USING (...) per policy. Move the context values the policy function read from SYS_CONTEXT into current_setting('app.x'). Remember table owners bypass RLS unless FORCE ROW LEVEL SECURITY is set.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDERS | TABLE | policy ORDERS_REGION_POLICY -> SALES.SEC_CTX.ORDERS_PREDICATE |

### O2PG-SQ-001: Hierarchical queries (CONNECT BY)

**Severity:** High · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 2.0

CONNECT BY, START WITH, PRIOR, LEVEL, SYS_CONNECT_BY_PATH and ORDER SIBLINGS BY have no direct PostgreSQL syntax.

**What to do:** Rewrite as WITH RECURSIVE. Carry a depth column for LEVEL, build a path array for SYS_CONNECT_BY_PATH and ORDER SIBLINGS BY, and add a CYCLE clause (PostgreSQL 14+) where NOCYCLE was used.

| Object | Type | Detail |
|---|---|---|
| HR.ORG_CHART | VIEW | 1 match, first at line 6 |

### O2PG-SQ-008: Empty string literals ('' is NULL in Oracle)

**Severity:** High · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 1.0

In Oracle '' is NULL, so col = '' is never true, NVL(col, '') does nothing and assigning '' stores NULL. In PostgreSQL all of these behave differently.

**What to do:** Review each use. Comparisons with '' usually become IS NULL, or COALESCE(col, '') = '' if both should count as empty. Decide on one convention for the data load too (convert '' to NULL, or the reverse).

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES_BIU | TRIGGER | 1 match, first at line 9 |

### O2PG-DT-006: NOT NULL character columns will start accepting empty strings

**Severity:** Medium · **Category:** datatypes · **Objects:** 13 · **Effort points:** 1.3

Oracle treats '' as NULL, so NOT NULL on a VARCHAR2 also rejects empty strings. In PostgreSQL '' is a real value and passes NOT NULL. The constraint gets weaker after migration without anyone touching it.

**What to do:** Where the business rule is 'must have a value', add CHECK (col <> '') or CHECK (btrim(col) <> '') next to NOT NULL.

| Object | Type | Detail |
|---|---|---|
| HR.DEPARTMENTS.DEPARTMENT_NAME | COLUMN | VARCHAR2 -> suggest varchar(30) |
| HR.EMPLOYEES.LAST_NAME | COLUMN | VARCHAR2 -> suggest varchar(25) |
| HR.EMPLOYEES.EMAIL | COLUMN | VARCHAR2 -> suggest varchar(25) |
| HR.EMPLOYEES.JOB_ID | COLUMN | VARCHAR2 -> suggest varchar(10) |
| HR.JOBS.JOB_ID | COLUMN | VARCHAR2 -> suggest varchar(10) |
| HR.JOBS.JOB_TITLE | COLUMN | VARCHAR2 -> suggest varchar(35) |
| HR.JOB_HISTORY.JOB_ID | COLUMN | VARCHAR2 -> suggest varchar(10) |
| HR.LOCATIONS.CITY | COLUMN | VARCHAR2 -> suggest varchar(30) |
| HR.COUNTRY_CODES.COUNTRY_ID | COLUMN | CHAR -> suggest char(2) |
| SALES.CUSTOMERS.CUST_FIRST_NAME | COLUMN | VARCHAR2 -> suggest varchar(50) |
| SALES.CUSTOMERS.CUST_LAST_NAME | COLUMN | VARCHAR2 -> suggest varchar(50) |
| SALES.PRODUCTS.PRODUCT_NAME | COLUMN | VARCHAR2 -> suggest varchar(50) |
| SALES.SALES_HISTORY.REGION_CODE | COLUMN | VARCHAR2 -> suggest varchar(10) |

### O2PG-SQ-007: SYSDATE and SYSTIMESTAMP

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 8 · **Effort points:** 2.0

SYSDATE returns the database server's OS clock, ignores the session time zone and advances during a transaction. now() / CURRENT_TIMESTAMP is frozen at transaction start and is time zone aware.

**What to do:** LOCALTIMESTAMP(0) is the closest match for SYSDATE inside one statement; use clock_timestamp() where the value must move during a long transaction. Azure servers run in UTC unless you change the TimeZone parameter.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES_BIU | TRIGGER | 3 matches, first at line 6 |
| HR.UPDATE_JOB_HISTORY | TRIGGER | 1 match, first at line 5 |
| HR.HR_DDL_AUDIT | TRIGGER | 1 match, first at line 5 |
| HR.EMP_MGMT | PACKAGE BODY | 5 matches, first at line 9 |
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 40 |
| SALES.PUBLISH_ORDER_EVENT | PROCEDURE | 1 match, first at line 9 |
| SALES.TOP_CUSTOMERS | VIEW | 1 match, first at line 4 |
| SALES.OPEN_ORDERS_V | VIEW | 1 match, first at line 4 |

### O2PG-SE-004: Definer-rights program units

**Severity:** Medium · **Category:** security · **Objects:** 7 · **Effort points:** 1.75

Oracle program units run with the owner's privileges unless declared AUTHID CURRENT_USER. PostgreSQL functions default to SECURITY INVOKER, the opposite.

**What to do:** Decide per unit. Where definer rights are really needed, use SECURITY DEFINER and always pin search_path (SET search_path = schema, pg_temp) to avoid hijacking. Elsewhere, grant the caller the privileges it needs.

| Object | Type | Detail |
|---|---|---|
| HR.ADD_JOB_HISTORY | PROCEDURE | AUTHID DEFINER (default) |
| HR.EMP_MGMT | PACKAGE | AUTHID DEFINER (default) |
| SALES.PRICING | PACKAGE | AUTHID DEFINER (default) |
| SALES.PUBLISH_ORDER_EVENT | PROCEDURE | AUTHID DEFINER (default) |
| SALES.GET_ORDER_TOTAL | FUNCTION | AUTHID DEFINER (default) |
| SALES.PRODUCT_SNIPPET | FUNCTION | AUTHID DEFINER (default) |
| SALES.REFRESH_ROLLUPS | PROCEDURE | AUTHID DEFINER (default) |

### O2PG-SQ-018: String concatenation with possible NULLs

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 6 · **Effort points:** 1.5

In Oracle 'a' || NULL is 'a'. In PostgreSQL it is NULL. Any concatenation that can see a NULL column changes result.

**What to do:** Use concat() or concat_ws(), which ignore NULLs, or wrap nullable operands in COALESCE(x, '').

| Object | Type | Detail |
|---|---|---|
| HR.HR_DDL_AUDIT | TRIGGER | 2 matches, first at line 5 |
| HR.EMP_MGMT | PACKAGE BODY | 5 matches, first at line 18 |
| SALES.ORDER_PROC | PACKAGE BODY | 8 matches, first at line 18 |
| SALES.PRODUCT_SNIPPET | FUNCTION | 1 match, first at line 6 |
| SALES.ADDRESS_T | TYPE BODY | 4 matches, first at line 4 |
| HR.ORG_CHART | VIEW | 1 match, first at line 2 |

### O2PG-DT-001: NUMBER without declared precision

**Severity:** Medium · **Category:** datatypes · **Objects:** 3 · **Effort points:** 0.3

Bare NUMBER (and INTEGER, which Oracle stores as NUMBER(*,0)) accepts up to 38 significant digits. There is no single right PostgreSQL type without knowing the data.

**What to do:** Map to numeric to stay safe. Profile MAX/MIN and scale per column, then narrow to integer, bigint or numeric(p,s) where the data allows. Keys joined to other tables should end up with the same type on both sides.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES.BONUS_FACTOR | COLUMN | NUMBER -> suggest numeric |
| HR.COUNTRY_CODES.REGION_ID | COLUMN | NUMBER -> suggest numeric |
| SALES.SALES_HISTORY.SALE_ID | COLUMN | NUMBER -> suggest numeric |

### O2PG-OB-004: Row-level DML triggers

**Severity:** Medium · **Category:** objects · **Objects:** 3 · **Effort points:** 3.0

Row triggers carry business logic that does not show up in table DDL: audit columns, derived values, cascades into other tables. PostgreSQL triggers call a separate trigger function and use NEW/OLD instead of :NEW/:OLD.

**What to do:** Create a trigger function (RETURNS trigger) per trigger and a CREATE TRIGGER that calls it. Replace :NEW/:OLD with NEW/OLD, INSERTING/UPDATING/DELETING with TG_OP, and remember BEFORE row triggers must RETURN NEW. Sequence-filling triggers can usually be replaced by identity columns.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES_BIU | TRIGGER | BEFORE EACH ROW INSERT OR UPDATE on EMPLOYEES |
| HR.UPDATE_JOB_HISTORY | TRIGGER | AFTER EACH ROW UPDATE on EMPLOYEES |
| SALES.ORDERS_BI | TRIGGER | BEFORE EACH ROW INSERT on ORDERS |

### O2PG-PF-008: Very large tables

**Severity:** Medium · **Category:** performance · **Objects:** 3 · **Effort points:** 3.0

Tables with more than 50,000,000 rows according to optimizer statistics. Load time, index builds and validation for these drive the cutover window.

**What to do:** Plan these separately: parallel load by key range or partition, create indexes after the load, and consider partitioning in PostgreSQL even if Oracle did not.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDERS | TABLE | 182,400,311 rows |
| SALES.ORDER_ITEMS | TABLE | 640,118,902 rows |
| SALES.SALES_HISTORY | TABLE | 1,250,000,000 rows |

### O2PG-PL-001: Packages

**Severity:** Medium · **Category:** plsql · **Objects:** 3 · **Effort points:** 6.0

PostgreSQL has no packages. Specs, bodies, private subprograms and package-level types all need a new home.

**What to do:** The usual pattern is one schema per package with one function or procedure per subprogram. Private subprograms become functions you do not grant EXECUTE on. Package types become composite types in that schema. The orafce extension does not emulate packages.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE | 73 lines incl. body |
| SALES.ORDER_PROC | PACKAGE | 60 lines incl. body |
| SALES.PRICING | PACKAGE | 11 lines incl. body |

### O2PG-AZ-001: DBMS_SCHEDULER jobs that run SQL or PL/SQL

**Severity:** Medium · **Category:** azure-specific · **Objects:** 2 · **Effort points:** 1.5

Scheduled database work. pg_cron is supported on Azure Database for PostgreSQL Flexible Server and covers the common cases, with cron syntax instead of calendar expressions.

**What to do:** Add pg_cron to shared_preload_libraries and azure.extensions (restart required), CREATE EXTENSION pg_cron in the database named by cron.database_name, then cron.schedule(...) or cron.schedule_in_database(...). Translate repeat_interval calendar strings to cron expressions; job chains and windows need app logic.

| Object | Type | Detail |
|---|---|---|
| SALES.NIGHTLY_ROLLUP | JOB | PLSQL_BLOCK: FREQ=DAILY;BYHOUR=2;BYMINUTE=0 |
| SALES.DAILY_EXPORT | JOB | STORED_PROCEDURE: FREQ=DAILY;BYHOUR=4 |

### O2PG-DT-004: Time zone aware timestamps

**Severity:** Medium · **Category:** datatypes · **Objects:** 2 · **Effort points:** 0.5

TIMESTAMP WITH TIME ZONE keeps the original offset or region in Oracle. TIMESTAMP WITH LOCAL TIME ZONE converts to the session zone. PostgreSQL timestamptz stores UTC and renders in the session TimeZone, so the original zone is lost.

**What to do:** Map both to timestamptz. If the original zone matters, add a separate text column holding the IANA zone name. Set the server TimeZone parameter on Azure explicitly rather than relying on the default (UTC).

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES.LAST_UPDATED | COLUMN | TIMESTAMP(6) WITH LOCAL TIME ZONE -> suggest timestamptz(6) |
| SALES.ORDERS.ORDER_TZ_TS | COLUMN | TIMESTAMP(6) WITH TIME ZONE -> suggest timestamptz(6) |

### O2PG-DT-009: LOB columns

**Severity:** Medium · **Category:** datatypes · **Objects:** 2 · **Effort points:** 1.0

CLOB and BLOB map to text and bytea, which are limited to 1 GB per value and are read in one piece. DBMS_LOB code and locator-based APIs do not carry over.

**What to do:** Map CLOB/NCLOB -> text and BLOB -> bytea. Check the largest value per column. For very large binaries consider Azure Blob Storage with a URL column instead of storing bytes in the database. Data loads of LOB-heavy tables are usually the slowest part.

| Object | Type | Detail |
|---|---|---|
| SALES.PRODUCTS.DESCRIPTION | COLUMN | CLOB -> suggest text |
| SALES.PRODUCTS.IMAGE | COLUMN | BLOB -> suggest bytea |

### O2PG-OB-002: Public synonyms

**Severity:** Medium · **Category:** objects · **Objects:** 2 · **Effort points:** 1.0

Public synonyms make an object visible to every user without a schema prefix. There is no global namespace like that in PostgreSQL.

**What to do:** Add the owning schema to search_path for the roles that use the objects (ALTER ROLE ... SET search_path, or ALTER DATABASE ... SET search_path).

| Object | Type | Detail |
|---|---|---|
| PUBLIC.EMPLOYEES | SYNONYM | -> HR.EMPLOYEES |
| PUBLIC.EMP_DETAILS | SYNONYM | -> HR.EMP_DETAILS_VIEW |

### O2PG-PL-012: Other Oracle-supplied packages (DBMS_*, UTL_*)

**Severity:** Medium · **Category:** plsql · **Objects:** 2 · **Effort points:** 1.5

Calls to Oracle-supplied packages that do not have a dedicated rule. Each needs a replacement built-in, extension or rewrite.

**What to do:** See the per-object detail for a suggested replacement. Common ones: DBMS_OUTPUT -> RAISE NOTICE, DBMS_CRYPTO -> pgcrypto, DBMS_LOCK.SLEEP -> pg_sleep, DBMS_ALERT and DBMS_PIPE -> LISTEN/NOTIFY. orafce provides DBMS_OUTPUT and UTL_FILE-style functions, but its UTL_FILE needs server directories that Azure does not expose.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | DBMS_OUTPUT (RAISE NOTICE) |
| SALES.REFRESH_ROLLUPS | PROCEDURE | DBMS_MVIEW (REFRESH MATERIALIZED VIEW), DBMS_STATS (ANALYZE / autovacuum) |

### O2PG-PL-014: Transaction control inside stored code

**Severity:** Medium · **Category:** plsql · **Objects:** 2 · **Effort points:** 2.0

PostgreSQL functions cannot COMMIT or ROLLBACK. Procedures can (PostgreSQL 11+), but only when called with CALL outside an explicit transaction block.

**What to do:** Convert units that commit into procedures, or move the commit to the caller. Savepoint-style ROLLBACK TO becomes a BEGIN ... EXCEPTION block.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 10 |
| SALES.REFRESH_ROLLUPS | PROCEDURE | 1 match, first at line 5 |

### O2PG-PL-016: Pipelined table functions

**Severity:** Medium · **Category:** plsql · **Objects:** 2 · **Effort points:** 2.0

PIPELINED functions stream rows to a SELECT ... FROM TABLE(f()) caller.

**What to do:** Rewrite as RETURNS SETOF / RETURNS TABLE with RETURN NEXT or RETURN QUERY, and drop the TABLE() wrapper at call sites.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE | 1 match, first at line 8 |
| SALES.ORDER_PROC | PACKAGE BODY | 2 matches, first at line 37 |

### O2PG-PL-018: Associative arrays (INDEX BY tables)

**Severity:** Medium · **Category:** plsql · **Objects:** 2 · **Effort points:** 2.0

Associative arrays are sparse maps keyed by integer or string. PostgreSQL arrays are dense and 1-based.

**What to do:** Integer-keyed dense arrays -> PostgreSQL arrays. String-keyed maps -> jsonb or hstore. Often a temporary table or a join removes the need entirely.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 3 |
| SALES.ORDERS_AUDIT_CT | TRIGGER | 1 match, first at line 4 |

### O2PG-PT-006: Global indexes on partitioned tables

**Severity:** Medium · **Category:** partitioning · **Objects:** 2 · **Effort points:** 1.0

A non-partitioned index on a partitioned table is a global index. PostgreSQL only has local (per-partition) indexes.

**What to do:** Recreate as a regular index on the parent, which PostgreSQL builds on every partition. Queries that relied on a global index without the partition key in the WHERE clause will scan every partition's index; test them.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDERS_PK | INDEX | on ORDERS (ORDER_ID) |
| SALES.IX_ORDER_ITEMS_PRODUCT_ID_UNIT_PRICE_QUANTITY_DISCOUNT_FOR_MONTHLY_REPORTING | INDEX | on ORDER_ITEMS (PRODUCT_ID, UNIT_PRICE, QUANTITY, DISCOUNT) |

### O2PG-SQ-002: ROWNUM

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 2 · **Effort points:** 1.5

ROWNUM is assigned before ORDER BY is applied. 'WHERE ROWNUM <= 10 ORDER BY x' returns 10 arbitrary rows, sorted, not the top 10.

**What to do:** Use LIMIT / FETCH FIRST n ROWS ONLY for top-n, and row_number() OVER (...) where ROWNUM is used as a value. Decide whether the original ordering bug is a feature.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 40 |
| SALES.TOP_CUSTOMERS | VIEW | 1 match, first at line 6 |

### O2PG-SQ-015: USER, USERENV and SYS_CONTEXT

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 2 · **Effort points:** 1.0

Session context functions. SYS_CONTEXT is also how Oracle VPD and application contexts pass values around.

**What to do:** USER -> current_user (or session_user). SYS_CONTEXT('USERENV', ...) -> inet_client_addr(), current_database(), current_setting(...). Custom application contexts -> set_config('app.x', ...) / current_setting('app.x', true).

| Object | Type | Detail |
|---|---|---|
| HR.HR_DDL_AUDIT | TRIGGER | 1 match, first at line 5 |
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 9 |

### O2PG-SQ-017: Date arithmetic with numbers

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 2 · **Effort points:** 0.5

In Oracle DATE + 1 adds a day and DATE + 1/24 adds an hour. Once DATE becomes timestamp, timestamp + integer is an error in PostgreSQL.

**What to do:** Use intervals: col + interval '1 day', col + make_interval(hours => n).

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 21 |
| SALES.OPEN_ORDERS_V | VIEW | 1 match, first at line 4 |

### O2PG-AZ-004: Database character set is not Unicode

**Severity:** Medium · **Category:** azure-specific · **Objects:** 1 · **Effort points:** 2.0

Azure Flexible Server databases are normally created as UTF8. Data from a single-byte or legacy character set is converted on load, and invalid bytes that Oracle stored without complaint will be rejected.

**What to do:** Scan for unconvertible data before the migration (Oracle DMU or a CSSCAN-style check), fix it at the source, and make sure the load tool declares the source encoding correctly. Re-check column lengths for characters that grow in UTF-8.

| Object | Type | Detail |
|---|---|---|
| ORCLPDB1 | DATABASE | NLS_CHARACTERSET WE8MSWIN1252 |

### O2PG-DT-011: XMLTYPE columns

**Severity:** Medium · **Category:** datatypes · **Objects:** 1 · **Effort points:** 1.0

PostgreSQL has an xml type with xpath() and XMLTABLE, but no XQuery, no XML indexes and no XML schema validation. Storage options (binary XML, object-relational) do not carry over.

**What to do:** Map to xml if you query it with XPath, or to jsonb if you control the format and mostly query it. Rewrite EXTRACT/EXTRACTVALUE/XMLQUERY calls.

| Object | Type | Detail |
|---|---|---|
| SALES.PRODUCTS.SPECS | COLUMN | XMLTYPE -> suggest xml |

### O2PG-DT-013: ROWID and UROWID columns

**Severity:** Medium · **Category:** datatypes · **Objects:** 1 · **Effort points:** 0.5

Stored ROWIDs are physical addresses into Oracle. PostgreSQL's ctid changes on every update and VACUUM FULL, so it cannot replace them.

**What to do:** Find what the stored ROWID points at and replace it with that row's primary key.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_AUDIT_LOG.SOURCE_ROWID | COLUMN | ROWID -> suggest text |

### O2PG-OB-011: Global temporary tables

**Severity:** Medium · **Category:** objects · **Objects:** 1 · **Effort points:** 1.0

Oracle GTTs are permanent definitions with per-session data. PostgreSQL temporary tables are created per session, so code that expects the table to already exist breaks.

**What to do:** Create the temp table at the start of the session or procedure (CREATE TEMP TABLE IF NOT EXISTS ... ON COMMIT DELETE ROWS), or use an unlogged table keyed by session/transaction id. Watch catalog bloat if temp tables are created very often.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_STAGE | TABLE | ON COMMIT DELETE ROWS |

### O2PG-OB-013: Invalid objects

**Severity:** Medium · **Category:** objects · **Objects:** 1 · **Effort points:** 0.25

Objects that do not compile in Oracle today. Converting them costs time and they may be dead code.

**What to do:** Recompile in Oracle (UTL_RECOMP or ALTER ... COMPILE). Anything that still fails and is not referenced should be dropped from scope, not migrated.

| Object | Type | Detail |
|---|---|---|
| SALES.OLD_REPORT_PKG | PACKAGE BODY | STATUS = INVALID |

### O2PG-OB-014: Tables without a primary key

**Severity:** Medium · **Category:** objects · **Objects:** 1 · **Effort points:** 0.5

Logical replication, most change data capture tools and row-by-row validation all need a key. Without one, online migration and data comparison get much harder.

**What to do:** Add a primary key where a natural or surrogate key exists. Otherwise set REPLICA IDENTITY FULL on the PostgreSQL side for replication, and expect slower apply.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_AUDIT_LOG | TABLE | no primary key |

### O2PG-OB-016: Quoted, mixed-case or special-character identifiers

**Severity:** Medium · **Category:** objects · **Objects:** 1 · **Effort points:** 0.25

Oracle folds unquoted names to upper case; PostgreSQL folds them to lower case. Names created with quotes in mixed case or with characters such as # or spaces will need quotes forever in PostgreSQL.

**What to do:** Convert to lower-case snake_case names during migration and keep a rename map. Do not carry the quotes over unless the application cannot change.

| Object | Type | Detail |
|---|---|---|
| SALES.CUSTOMERS.Notes | COLUMN | needs quoting: "Notes" |

### O2PG-OB-017: Names that are reserved words in PostgreSQL

**Severity:** Medium · **Category:** objects · **Objects:** 1 · **Effort points:** 0.25

Some names that are legal unquoted in Oracle (LIMIT, OFFSET, ANALYZE, WINDOW, RETURNING, ...) are reserved in PostgreSQL and fail in DDL and queries unless quoted.

**What to do:** Rename the table or column (for example limit -> credit_limit), or quote it everywhere.

| Object | Type | Detail |
|---|---|---|
| SALES.CUSTOMERS.LIMIT | COLUMN | limit is reserved |

### O2PG-PF-001: Bitmap indexes

**Severity:** Medium · **Category:** performance · **Objects:** 1 · **Effort points:** 0.5

PostgreSQL has no stored bitmap indexes. It builds bitmaps at query time from ordinary B-tree indexes and combines them, which covers most of the same queries.

**What to do:** Create B-tree indexes on the same columns, or a partial index for the selective values. For very large append-only tables BRIN is worth testing. Verify the star queries that relied on bitmap AND/OR.

| Object | Type | Detail |
|---|---|---|
| SALES.ORD_STATUS_BIX | INDEX | BITMAP on ORDERS (ORDER_STATUS) |

### O2PG-PF-004: Index-organized tables

**Severity:** Medium · **Category:** performance · **Objects:** 1 · **Effort points:** 0.75

IOTs store rows in primary key order inside the index. PostgreSQL tables are heaps; CLUSTER reorders once but is not maintained.

**What to do:** Create a heap table with the primary key. For small lookup tables that is all. For range-scanned IOTs, add INCLUDE columns to the primary key index to get index-only scans, and keep autovacuum healthy so the visibility map stays current.

| Object | Type | Detail |
|---|---|---|
| HR.COUNTRY_CODES | TABLE | ORGANIZATION INDEX |

### O2PG-PL-005: REF CURSOR and SYS_REFCURSOR

**Severity:** Medium · **Category:** plsql · **Objects:** 1 · **Effort points:** 1.0

PostgreSQL has refcursor, but the client side differs: cursors are only valid inside a transaction and drivers fetch them differently.

**What to do:** Map to refcursor, or better, to RETURNS TABLE / RETURNS SETOF functions. Check the application data access layer, which is usually where the real work is.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE | 1 match, first at line 6 |

### O2PG-PL-007: Dynamic SQL (EXECUTE IMMEDIATE, DBMS_SQL)

**Severity:** Medium · **Category:** plsql · **Objects:** 1 · **Effort points:** 1.5

SQL built in strings is invisible to static analysis, including this tool. Statements inside the strings are Oracle dialect too.

**What to do:** EXECUTE IMMEDIATE -> EXECUTE ... USING; DBMS_SQL -> EXECUTE with format() and %I/%L. Review every string by hand for Oracle syntax and SQL injection.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 48 |

### O2PG-PL-008: DBMS_LOB usage

**Severity:** Medium · **Category:** plsql · **Objects:** 1 · **Effort points:** 1.0

PostgreSQL text and bytea are values, not locators. DBMS_LOB has no equivalent.

**What to do:** Replace DBMS_LOB.SUBSTR/INSTR/GETLENGTH/APPEND with substr, strpos, length and ||. Code that streams large LOBs in chunks may be better moved to the application.

| Object | Type | Detail |
|---|---|---|
| SALES.PRODUCT_SNIPPET | FUNCTION | 3 matches, first at line 5 |

### O2PG-PT-002: Interval partitioning

**Severity:** Medium · **Category:** partitioning · **Objects:** 1 · **Effort points:** 1.5

Oracle creates interval partitions automatically on insert. PostgreSQL never creates partitions on its own; rows with no matching partition fail or land in DEFAULT.

**What to do:** Use pg_partman with a premake count and run its maintenance from pg_cron (both are available on Azure Flexible Server). Keep a DEFAULT partition and alert if it ever receives rows.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDERS | TABLE | RANGE (ORDER_DATE), 84 partitions, INTERVAL NUMTOYMINTERVAL(1,'MONTH') |

### O2PG-PT-004: Composite (sub)partitioning

**Severity:** Medium · **Category:** partitioning · **Objects:** 1 · **Effort points:** 1.5

Sub-partitions become partitions of partitions in PostgreSQL. Every level is a real table, so the object count grows quickly.

**What to do:** Create each partition with its own PARTITION BY clause. Check that the total number of leaf partitions stays reasonable (planning time grows with it), and consider dropping the second level if it existed only for storage management.

| Object | Type | Detail |
|---|---|---|
| SALES.SALES_HISTORY | TABLE | RANGE (SALE_DATE), sub LIST, 40 partitions |

### O2PG-SE-002: TDE column encryption

**Severity:** Medium · **Category:** security · **Objects:** 1 · **Effort points:** 0.5

Transparent Data Encryption at column level. Azure Database for PostgreSQL encrypts all storage at rest by default (optionally with customer-managed keys in Key Vault), but has no transparent per-column encryption.

**What to do:** If at-rest encryption was the requirement, Azure storage encryption covers it. If the column must stay unreadable to DBAs, encrypt in the application or use pgcrypto with keys held outside the database.

| Object | Type | Detail |
|---|---|---|
| SALES.CUSTOMERS.CREDIT_CARD_NO | COLUMN | AES 192 bits key |

### O2PG-SE-003: Object privileges granted to PUBLIC

**Severity:** Medium · **Category:** security · **Objects:** 1 · **Effort points:** 0.25

Grants to PUBLIC are visible to every role. Carrying them over keeps an open door you may not have intended.

**What to do:** Replace with grants to an explicit role. Also REVOKE CREATE ON SCHEMA public FROM PUBLIC on older PostgreSQL versions (15+ does this by default).

| Object | Type | Detail |
|---|---|---|
| HR -> PUBLIC | GRANT | SELECT on 2 object(s) |

### O2PG-SQ-005: Oracle outer join operator (+)

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 0.75

The (+) operator is not supported. Conditions in WHERE must move into ON clauses.

**What to do:** Rewrite as ANSI LEFT/RIGHT JOIN. Be careful with filters on the outer table: in Oracle they carried (+) too, and they must go into the ON clause, not WHERE.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_DETAILS_VIEW | VIEW | 3 matches, first at line 5 |

### O2PG-SQ-006: MERGE statements

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 1.0

PostgreSQL 15+ supports MERGE, but not Oracle's DELETE WHERE inside WHEN MATCHED, and concurrency behaviour differs from INSERT ... ON CONFLICT.

**What to do:** On Azure Flexible Server pick PostgreSQL 15 or later and use MERGE, or use INSERT ... ON CONFLICT DO UPDATE for single-row upserts that must be safe under concurrency.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 5 |

### O2PG-SQ-012: TO_DATE / TO_CHAR / TO_NUMBER format models

**Severity:** Medium · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 0.25

PostgreSQL has these functions, but format models differ in the details (RR, FM, D, J, NLS parameters), PostgreSQL to_date is more lenient, and calls without a format rely on Oracle's NLS_DATE_FORMAT.

**What to do:** Check every format model, and add an explicit one wherever the Oracle code relied on NLS defaults. Test with boundary dates and invalid input.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 18 |

### O2PG-DT-005: Character columns with BYTE length semantics

**Severity:** Low · **Category:** datatypes · **Objects:** 28 · **Effort points:** 1.4

VARCHAR2(n BYTE) limits bytes, not characters. PostgreSQL varchar(n) limits characters, so the limit effectively grows for multibyte data.

**What to do:** Usually harmless for the database, but application-side validation that mirrors the byte limit may now disagree with the column. Decide on character limits explicitly.

| Object | Type | Detail |
|---|---|---|
| HR.DEPARTMENTS.DEPARTMENT_NAME | COLUMN | VARCHAR2 -> suggest varchar(30) |
| HR.EMPLOYEES.FIRST_NAME | COLUMN | VARCHAR2 -> suggest varchar(20) |
| HR.EMPLOYEES.LAST_NAME | COLUMN | VARCHAR2 -> suggest varchar(25) |
| HR.EMPLOYEES.EMAIL | COLUMN | VARCHAR2 -> suggest varchar(25) |
| HR.EMPLOYEES.PHONE_NUMBER | COLUMN | VARCHAR2 -> suggest varchar(20) |
| HR.EMPLOYEES.JOB_ID | COLUMN | VARCHAR2 -> suggest varchar(10) |
| HR.EMPLOYEES.FULL_NAME | COLUMN | VARCHAR2 -> suggest varchar(46) |
| HR.JOBS.JOB_ID | COLUMN | VARCHAR2 -> suggest varchar(10) |
| HR.JOBS.JOB_TITLE | COLUMN | VARCHAR2 -> suggest varchar(35) |
| HR.JOB_HISTORY.JOB_ID | COLUMN | VARCHAR2 -> suggest varchar(10) |
| HR.LOCATIONS.STREET_ADDRESS | COLUMN | VARCHAR2 -> suggest varchar(40) |
| HR.LOCATIONS.POSTAL_CODE | COLUMN | VARCHAR2 -> suggest varchar(12) |
| HR.LOCATIONS.CITY | COLUMN | VARCHAR2 -> suggest varchar(30) |
| HR.LOCATIONS.STATE_PROVINCE | COLUMN | VARCHAR2 -> suggest varchar(25) |
| HR.LOCATIONS.COUNTRY_ID | COLUMN | CHAR -> suggest char(2) |

...and 13 more.

### O2PG-DT-015: Column defaults that use Oracle functions

**Severity:** Low · **Category:** datatypes · **Objects:** 6 · **Effort points:** 0.6

Defaults such as SYSDATE, SYS_GUID(), USER or seq.NEXTVAL do not exist in PostgreSQL.

**What to do:** SYSDATE -> LOCALTIMESTAMP(0) or clock_timestamp() (see the SYSDATE rule for the difference), SYS_GUID() -> gen_random_uuid(), USER -> current_user, seq.NEXTVAL -> nextval('seq') or an identity column.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES.EMPLOYEE_ID | COLUMN | default "HR"."EMPLOYEES_SEQ"."NEXTVAL" |
| HR.EMPLOYEES.HIRE_DATE | COLUMN | default SYSDATE |
| HR.EMPLOYEES.ROW_GUID | COLUMN | default SYS_GUID() |
| HR.EMP_AUDIT_LOG.AUDIT_TS | COLUMN | default SYSTIMESTAMP |
| HR.EMP_AUDIT_LOG.CHANGED_BY | COLUMN | default USER |
| SALES.CUSTOMERS.CREATED_AT | COLUMN | default SYSDATE |

### O2PG-SQ-004: NVL and NVL2

**Severity:** Low · **Category:** sql-dialect · **Objects:** 5 · **Effort points:** 0.5

NVL and NVL2 are Oracle functions. NVL evaluates both arguments; COALESCE short-circuits.

**What to do:** NVL(a,b) -> COALESCE(a,b); NVL2(a,b,c) -> CASE WHEN a IS NOT NULL THEN b ELSE c END. orafce provides nvl and nvl2.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES_BIU | TRIGGER | 1 match, first at line 6 |
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 42 |
| SALES.ORDERS_AUDIT_CT | TRIGGER | 1 match, first at line 9 |
| SALES.GET_ORDER_TOTAL | FUNCTION | 2 matches, first at line 4 |
| SALES.TOP_CUSTOMERS | VIEW | 1 match, first at line 1 |

### O2PG-DT-007: CHAR columns and blank padding

**Severity:** Low · **Category:** datatypes · **Objects:** 3 · **Effort points:** 0.3

CHAR(n) pads with blanks in both databases, but comparison and concatenation rules differ. In PostgreSQL trailing blanks of character(n) are insignificant and are dropped when cast to text, which changes LENGTH() and || results.

**What to do:** Prefer varchar(n) or text unless the fixed width is genuinely required. If you keep char(n), test any code that compares CHAR with VARCHAR2 values or relies on padding.

| Object | Type | Detail |
|---|---|---|
| HR.LOCATIONS.COUNTRY_ID | COLUMN | CHAR -> suggest char(2) |
| HR.COUNTRY_CODES.COUNTRY_ID | COLUMN | CHAR -> suggest char(2) |
| SALES.CUSTOMERS.STATUS | COLUMN | CHAR -> suggest char(10) |

### O2PG-PF-006: Foreign keys without a supporting index

**Severity:** Low · **Category:** performance · **Objects:** 3 · **Effort points:** 0.3

Neither database indexes foreign key columns automatically. Unindexed foreign keys make parent deletes and updates scan the child table, which hurts more under PostgreSQL's MVCC.

**What to do:** Create an index whose leading columns are the foreign key columns.

| Object | Type | Detail |
|---|---|---|
| HR.DEPARTMENTS.DEPT_LOC_FK | CONSTRAINT | (LOCATION_ID) |
| HR.EMPLOYEES.EMP_MANAGER_FK | CONSTRAINT | (MANAGER_ID) |
| HR.JOB_HISTORY.JHIST_JOB_FK | CONSTRAINT | (JOB_ID) |

### O2PG-OB-007: Sequences with MAXVALUE beyond bigint

**Severity:** Low · **Category:** objects · **Objects:** 2 · **Effort points:** 0.2

Oracle's default sequence MAXVALUE is 10^28 - 1. PostgreSQL sequences are bigint, so CREATE SEQUENCE fails if the value is copied as-is.

**What to do:** Drop MAXVALUE (defaults to bigint max) or cap it at 9223372036854775807.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES_SEQ | SEQUENCE | MAXVALUE 9999999999999999999999999999 |
| SALES.ORDERS_SEQ | SEQUENCE | MAXVALUE 9999999999999999999999999999 |

### O2PG-SQ-009: SELECT ... FROM DUAL

**Severity:** Low · **Category:** sql-dialect · **Objects:** 2 · **Effort points:** 0.2

PostgreSQL does not need or have DUAL.

**What to do:** Drop FROM DUAL; in PL/pgSQL assign directly (v := expr). orafce creates a dual table if you need a bridge.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 6 |
| SALES.ORDERS_BI | TRIGGER | 1 match, first at line 6 |

### O2PG-SQ-011: Oracle date functions

**Severity:** Low · **Category:** sql-dialect · **Objects:** 2 · **Effort points:** 0.5

ADD_MONTHS, MONTHS_BETWEEN, LAST_DAY, NEXT_DAY and TRUNC(date) are Oracle built-ins.

**What to do:** Use interval arithmetic and date_trunc(), or enable orafce, which provides add_months, months_between, last_day and next_day with Oracle semantics.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 2 matches, first at line 22 |
| SALES.TOP_CUSTOMERS | VIEW | 2 matches, first at line 4 |

### O2PG-SQ-014: Sequence NEXTVAL / CURRVAL

**Severity:** Low · **Category:** sql-dialect · **Objects:** 2 · **Effort points:** 0.2

seq.NEXTVAL is Oracle syntax. CURRVAL semantics are per session in both databases.

**What to do:** seq.NEXTVAL -> nextval('seq'), seq.CURRVAL -> currval('seq'). Consider identity columns.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 21 |
| SALES.ORDERS_BI | TRIGGER | 1 match, first at line 6 |

### O2PG-DT-002: Binary and approximate numeric types

**Severity:** Low · **Category:** datatypes · **Objects:** 1 · **Effort points:** 0.1

FLOAT in Oracle is a decimal type with binary precision; BINARY_FLOAT and BINARY_DOUBLE are IEEE types with their own NaN and infinity literals.

**What to do:** BINARY_FLOAT -> real, BINARY_DOUBLE -> double precision. For FLOAT use numeric when values must round-trip exactly, otherwise double precision. Replace BINARY_FLOAT_NAN style literals with 'NaN'::float8.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_ITEMS.DISCOUNT | COLUMN | BINARY_DOUBLE -> suggest double precision |

### O2PG-DT-014: Virtual columns

**Severity:** Low · **Category:** datatypes · **Objects:** 1 · **Effort points:** 0.25

PostgreSQL generated columns are computed on write and stored. The expression must use immutable functions only, which rules out SYSDATE and most NLS-dependent calls.

**What to do:** Use GENERATED ALWAYS AS (expr) STORED and translate the expression. If it is not immutable, use a view or a trigger instead.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES.FULL_NAME | COLUMN | expression: "FIRST_NAME"\|\|' '\|\|"LAST_NAME" |

### O2PG-OB-001: Private synonyms

**Severity:** Low · **Category:** objects · **Objects:** 1 · **Effort points:** 0.25

PostgreSQL has no synonyms. Code that relies on them resolves names differently.

**What to do:** Most private synonyms just point at another schema: set search_path for the role (ALTER ROLE app SET search_path = app, hr) or qualify names. For renames, use a view, or a wrapper function for synonyms that point at code.

| Object | Type | Detail |
|---|---|---|
| SALES.EMP | SYNONYM | -> HR.EMPLOYEES |

### O2PG-PF-002: Function-based indexes

**Severity:** Low · **Category:** performance · **Objects:** 1 · **Effort points:** 0.25

PostgreSQL expression indexes are the equivalent, but every function in the expression must be IMMUTABLE and the query must use the same expression.

**What to do:** CREATE INDEX ... ((upper(col))). Translate the expression and mark custom functions IMMUTABLE only if they truly are.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_UPPER_LNAME_IX | INDEX | FUNCTION-BASED NORMAL on EMPLOYEES (SYS_NC00020$) |

### O2PG-PF-003: Reverse key indexes

**Severity:** Low · **Category:** performance · **Objects:** 1 · **Effort points:** 0.25

Reverse key indexes spread sequential keys across leaf blocks to reduce RAC contention. PostgreSQL has no reverse key option and no RAC block contention.

**What to do:** Use a plain B-tree index. Range scans on the column start working again, which may change plans for the better.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_AUDIT_EMP_IX | INDEX | NORMAL/REV on EMP_AUDIT_LOG (EMPLOYEE_ID) |

### O2PG-PF-007: Compressed tables

**Severity:** Low · **Category:** performance · **Objects:** 1 · **Effort points:** 0.25

Basic, OLTP/advanced and hybrid columnar compression have no PostgreSQL equivalent; only large values are compressed (TOAST). Expect the data to grow on disk.

**What to do:** Size Azure storage from uncompressed volumes, not Oracle segment sizes. Consider lz4 TOAST compression (default_toast_compression) for wide text columns.

| Object | Type | Detail |
|---|---|---|
| SALES.SALES_HISTORY | TABLE | ADVANCED |

### O2PG-PL-004: BULK COLLECT and FORALL

**Severity:** Low · **Category:** plsql · **Objects:** 1 · **Effort points:** 0.5

Bulk binding exists to cut context switches between PL/SQL and SQL. PL/pgSQL has no BULK COLLECT or FORALL syntax.

**What to do:** Usually the best rewrite is a single set-based INSERT ... SELECT or UPDATE ... FROM. Otherwise use arrays (array_agg, unnest) or a plain FOR loop over a query.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 2 matches, first at line 39 |

### O2PG-PL-013: RAISE_APPLICATION_ERROR and Oracle error codes

**Severity:** Low · **Category:** plsql · **Objects:** 1 · **Effort points:** 0.5

Applications often match on ORA-20xxx numbers or SQLCODE values. PostgreSQL uses five-character SQLSTATE codes.

**What to do:** RAISE EXCEPTION '...' USING ERRCODE = 'P0001' (or a custom 5-character code), and update application error handling that parses ORA- numbers.

| Object | Type | Detail |
|---|---|---|
| HR.EMP_MGMT | PACKAGE BODY | 1 match, first at line 18 |

### O2PG-PT-001: Range, list and hash partitioned tables

**Severity:** Low · **Category:** partitioning · **Objects:** 1 · **Effort points:** 1.0

PostgreSQL declarative partitioning supports RANGE, LIST and HASH. The syntax is different and each partition is created as its own table.

**What to do:** CREATE TABLE ... PARTITION BY RANGE (key) and one CREATE TABLE ... PARTITION OF per partition. Add a DEFAULT partition where Oracle used MAXVALUE or DEFAULT. pg_partman automates creating and dropping time-based partitions.

| Object | Type | Detail |
|---|---|---|
| SALES.SALES_HISTORY | TABLE | RANGE (SALE_DATE), sub LIST, 40 partitions |

### O2PG-SQ-003: DECODE

**Severity:** Low · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 0.25

DECODE is Oracle-only. It also treats NULL = NULL as a match, unlike CASE.

**What to do:** Rewrite as CASE. Where DECODE compared NULLs, use IS NOT DISTINCT FROM in the CASE. orafce ships a decode() function for common type combinations if you need a bridge.

| Object | Type | Detail |
|---|---|---|
| SALES.OPEN_ORDERS_V | VIEW | 1 match, first at line 2 |

### O2PG-SQ-010: MINUS set operator

**Severity:** Low · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 0.1

MINUS is spelled EXCEPT in PostgreSQL.

**What to do:** Replace MINUS with EXCEPT (EXCEPT ALL if duplicates matter).

| Object | Type | Detail |
|---|---|---|
| SALES.OPEN_ORDERS_V | VIEW | 1 match, first at line 5 |

### O2PG-SQ-013: Optimizer hints

**Severity:** Low · **Category:** sql-dialect · **Objects:** 1 · **Effort points:** 0.1

Hints become ordinary comments in PostgreSQL. Anything that only performs well because of a hint may need a different index or a rewrite.

**What to do:** Note which statements were hinted and why, and include them in performance tests. Use pg_stat_statements on Azure to find the slow ones after cutover.

| Object | Type | Detail |
|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 1 match, first at line 19 |

### O2PG-PL-006: %TYPE and %ROWTYPE anchored declarations

**Severity:** Info · **Category:** plsql · **Objects:** 4 · **Effort points:** 0.0

PL/pgSQL supports %TYPE and %ROWTYPE, so these mostly convert as-is.

**What to do:** No change needed in most cases. Make sure the anchored table or column exists with the same name in PostgreSQL (watch for case folding).

| Object | Type | Detail |
|---|---|---|
| HR.ADD_JOB_HISTORY | PROCEDURE | 5 matches, first at line 2 |
| HR.EMP_MGMT | PACKAGE | 1 match, first at line 6 |
| HR.EMP_MGMT | PACKAGE BODY | 2 matches, first at line 15 |
| SALES.ORDERS_AUDIT_CT | TRIGGER | 1 match, first at line 4 |

### O2PG-SE-006: Object privileges to map to roles

**Severity:** Info · **Category:** security · **Objects:** 4 · **Effort points:** 0.4

Summary of who holds object privileges on the analysed schemas. Each grantee becomes a role (or a member of one) in PostgreSQL.

**What to do:** Create group roles (NOLOGIN) for each grantee pattern, grant privileges to the group, and use ALTER DEFAULT PRIVILEGES so new objects inherit them.

| Object | Type | Detail |
|---|---|---|
| APP_USER | ROLE | EXECUTE x3, INSERT x6, UPDATE x5 |
| PUBLIC | ROLE | SELECT x2 |
| REPORTING_RO | ROLE | SELECT x9 |
| SALES | ROLE | EXECUTE x1, SELECT x3 |

### O2PG-OB-008: Sequences must be re-synced after the data load

**Severity:** Info · **Category:** objects · **Objects:** 3 · **Effort points:** 0.15

Loading data does not move sequences. After cutover each sequence must start above the highest value already used, or inserts start failing with duplicates.

**What to do:** After the final load run setval('seq', (SELECT max(id) FROM t)) per sequence, or use the Oracle LAST_NUMBER captured at cutover plus a safety margin.

| Object | Type | Detail |
|---|---|---|
| HR.EMPLOYEES_SEQ | SEQUENCE | last_number 48301 |
| HR.DEPARTMENTS_SEQ | SEQUENCE | last_number 280 |
| SALES.ORDERS_SEQ | SEQUENCE | last_number 182401001 |

### O2PG-AZ-005: orafce could shorten the conversion

**Severity:** Info · **Category:** azure-specific · **Objects:** 1 · **Effort points:** 0.0

The code uses Oracle built-ins that the orafce extension implements. orafce is on the Flexible Server allow-list.

**What to do:** Allow-list orafce in azure.extensions and CREATE EXTENSION orafce. Use it as a bridge to get code running, then decide whether to keep the dependency; plain PostgreSQL equivalents are easier for the next team to maintain.

| Object | Type | Detail |
|---|---|---|
| ORCLPDB1 | DATABASE | 8 code object(s) use ADD_MONTHS, DBMS_OUTPUT, DECODE, DUAL, NVL |

## Most complex objects

| Object | Type | Score | Rules |
|---|---|---|---|
| SALES.ORDER_PROC | PACKAGE BODY | 89.9 | O2PG-PL-007, O2PG-PL-009, O2PG-PL-010, O2PG-PL-016, O2PG-SQ-002, O2PG-SQ-006, O2PG-SQ-007, O2PG-SQ-009, O2PG-SQ-012, O2PG-SQ-013, O2PG-SQ-017, O2PG-SQ-018 |
| SALES.CUSTOMERS | TABLE | 51.9 | O2PG-DT-003, O2PG-DT-005, O2PG-DT-006, O2PG-DT-007, O2PG-DT-015, O2PG-DT-016, O2PG-OB-016, O2PG-OB-017, O2PG-SE-002 |
| HR.EMP_MGMT | PACKAGE BODY | 50.8 | O2PG-PL-002, O2PG-PL-003, O2PG-PL-004, O2PG-PL-006, O2PG-PL-012, O2PG-PL-013, O2PG-PL-014, O2PG-PL-018, O2PG-SQ-004, O2PG-SQ-007, O2PG-SQ-011, O2PG-SQ-014, O2PG-SQ-015, O2PG-SQ-018 |
| SALES.ORDERS | TABLE | 41.5 | O2PG-DT-003, O2PG-DT-004, O2PG-DT-005, O2PG-PF-008, O2PG-PT-002, O2PG-PT-005, O2PG-SE-001 |
| HR.EMPLOYEES | TABLE | 35.9 | O2PG-DT-001, O2PG-DT-003, O2PG-DT-004, O2PG-DT-005, O2PG-DT-006, O2PG-DT-014, O2PG-DT-015, O2PG-PF-006 |
| SALES.PRICING | PACKAGE BODY | 30.1 | O2PG-PL-015 |
| SALES.PRODUCT_DOCS | TABLE | 28.0 | O2PG-DT-010 |
| SALES.SALES_HISTORY | TABLE | 27.2 | O2PG-DT-001, O2PG-DT-003, O2PG-DT-005, O2PG-DT-006, O2PG-PF-007, O2PG-PF-008, O2PG-PT-001, O2PG-PT-004 |
| SALES.ARCHIVE_EXPORT_FILES | JOB | 27.0 | O2PG-AZ-002 |
| HR.EMP_MGMT | PACKAGE | 23.4 | O2PG-PL-001, O2PG-PL-002, O2PG-PL-005, O2PG-PL-006, O2PG-SE-004 |
| HR.JOB_HISTORY | TABLE | 21.8 | O2PG-DT-003, O2PG-DT-005, O2PG-DT-006, O2PG-PF-006 |
| HR.EMP_AUDIT_LOG | TABLE | 20.3 | O2PG-DT-005, O2PG-DT-008, O2PG-DT-013, O2PG-DT-015, O2PG-OB-014 |
| HR.HR_DDL_AUDIT | TRIGGER | 20.1 | O2PG-OB-006, O2PG-SQ-007, O2PG-SQ-015, O2PG-SQ-018 |
| HR.LOCATIONS | TABLE | 19.5 | O2PG-DT-005, O2PG-DT-006, O2PG-DT-007, O2PG-DT-012 |
| SALES.PUBLISH_ORDER_EVENT | PROCEDURE | 17.6 | O2PG-AZ-003, O2PG-SE-004, O2PG-SQ-007 |

## About the numbers

Scores are heuristics for ranking work and comparing schemas, not a quote. Complexity weights each triggered rule by severity (blocker 25, high 8, medium 3, low 1) and counts repeated hits logarithmically. Effort points add each rule's per-object effort, a baseline per table and one point per 150 lines of PL/SQL. AI readiness starts at 100 and decays with every triggered rule that hides behaviour an LLM is likely to miss (blocker 30, high 20, medium 10, low 5 penalty points).
