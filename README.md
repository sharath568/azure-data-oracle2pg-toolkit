# azure-data-oracle2pg-toolkit

[![CI](https://github.com/sharath568/azure-data-oracle2pg-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/sharath568/azure-data-oracle2pg-toolkit/actions/workflows/ci.yml)

`o2pg` looks at an Oracle schema and tells you what will hurt when you move it to
**Azure Database for PostgreSQL Flexible Server**, before you write a line of
conversion code or paste anything into an AI assistant.

It works offline. You run one read-only SQL*Plus script against Oracle, take the
JSON file it writes, and analyse that file anywhere. No database connection,
no agents, no credentials stored next to the tool.

This repo goes with the article
[Before You Point AI at Your Oracle Schema](docs/article-draft.md).

## What you get

- **97 rules** across data types, PL/SQL, SQL dialect, schema objects,
  partitioning, security, performance and Azure-specific limits. Each one has a
  severity, an effort estimate and a concrete fix that names the PostgreSQL or
  Azure feature to use.
- **A complexity score and effort band** so you can compare schemas and rank work.
- **An AI readiness section** listing the Oracle behaviours that are implicit in
  your schema (`''` is NULL, `DATE` has a time, bare `NUMBER`, trigger side
  effects, package state, ...) and what to tell a model before you ask it to
  convert anything.
- **The list of Azure extensions to allow-list** (`orafce`, `pg_cron`,
  `pg_partman`, `postgis`, ...) based on what the schema actually uses.
- **Suggested PostgreSQL DDL** with conservative type mappings and `TODO`
  comments wherever a person needs to decide.
- Reports in **Markdown, JSON or a single self-contained HTML file**.
- **PL/SQL to PL/pgSQL conversion helpers (v0.2):** `o2pg convert` makes the
  mechanical rewrites (headers, types, `NVL`, `DECODE`, `SYSDATE`, sequences,
  `:NEW`, `SELECT INTO STRICT`, ...) and leaves everything else in place behind
  an inline `O2PG-REVIEW` marker. See [Converting PL/SQL](#converting-plsql-v02).

## Five-minute quickstart

You need Python 3.10 or later.

```bash
git clone https://github.com/sharath568/azure-data-oracle2pg-toolkit.git
cd azure-data-oracle2pg-toolkit
pip install .

# 1. Try it on the bundled HR/SALES sample, no Oracle needed
o2pg sample -o sample_inventory.json
o2pg assess sample_inventory.json                      # Markdown to stdout
o2pg assess sample_inventory.json -f html -o report    # writes report.html
o2pg map-types sample_inventory.json -o schema.sql     # suggested DDL
o2pg convert sample_inventory.json -o converted         # PL/SQL to PL/pgSQL helpers
o2pg rules                                              # list all rules
```

When you're ready to look at your own schema:

```bash
# 2. On a machine with SQL*Plus or SQLcl and access to the database
sqlplus -s app_reader/secret@//dbhost:1521/ORCLPDB1 @collector/collect.sql HR,SALES
#    -> writes o2pg_inventory.json

# 3. Anywhere with Python
o2pg assess o2pg_inventory.json -f html -o assessment
```

The collector needs Oracle 19c or later and a user that can see the schemas
through the `ALL_` views (the schema owner, or a user with
`SELECT_CATALOG_ROLE`). It only runs `SELECT` against dictionary views. It does
not read table data.

Useful flags:

| Flag | What it does |
|---|---|
| `-f md\|json\|html` | Report format (default `md`) |
| `-o PATH` | Write to a file; the extension is added if missing |
| `--min-severity high` | Hide lower severity rules |
| `--fail-on blocker` | Exit with status 2 if any rule at that severity or above fires (handy in CI) |

## Sample output

From `examples/sample_report.md`, generated from `examples/sample_inventory.json`:

```text
| Metric               | Value                                      |
|----------------------|--------------------------------------------|
| Migration complexity | 86/100 (Very high)                         |
| Effort band          | L: ~2 to 6 person-months (133.3 points)    |
| Blockers             | 5                                          |
| AI readiness         | 26/100                                     |
| Rules triggered      | 89 of 97                                   |

## AI readiness

26/100. Do not paste this schema into an LLM as-is. Too much behaviour is
implicit; convert with a written spec and review every object.

1. DATE columns carry a time of day (O2PG-DT-003, 6 object(s)): Oracle DATE
   includes hours, minutes and seconds. Tell the model to map DATE to
   timestamp(0), not date.
2. Package-level state (global variables) (O2PG-PL-002, 2 object(s)): ...
4. Empty string literals ('' is NULL in Oracle) (O2PG-SQ-008, 1 object(s)): ...
```

And a slice of `o2pg map-types`:

```sql
CREATE TABLE sales.orders (
    order_id bigint NOT NULL,
    order_date timestamp(0) NOT NULL,
    ...
    order_tz_ts timestamptz(6),  -- PostgreSQL normalises to UTC and does not keep the original offset
    CONSTRAINT orders_pk PRIMARY KEY (order_id)
) PARTITION BY RANGE (order_date);
-- TODO ORDERS_PK must include the partition key (ORDER_DATE)
```

The sample schema is deliberately messy, so it fires almost every rule. A real
schema usually looks calmer.

## Converting PL/SQL (v0.2)

`o2pg convert` turns stored PL/SQL into PL/pgSQL, one file per object, and
writes a report of what it changed and what it left for you.

```bash
o2pg convert o2pg_inventory.json -o converted                 # every object
o2pg convert o2pg_inventory.json -o converted --object HR.EMP_MGMT
o2pg convert my_package.sql                                   # a SQL*Plus script, to stdout
```

It works on standalone procedures and functions, packages and simple row or
statement triggers:

- A **package** becomes a schema named after the package, with one routine per
  procedure or function. Constants are copied into the routines that use them.
  Package variables, collection types and user-defined exceptions are kept as
  commented-out source with a review marker, because PostgreSQL has no
  equivalent.
- A **trigger** becomes a `RETURNS trigger` function that wraps the Oracle block,
  plus `CREATE OR REPLACE TRIGGER ... EXECUTE FUNCTION`.
- Compound triggers, DDL and database event triggers, object types and wrapped
  code are not converted. Their source is kept in the output as a comment under
  a review marker.

Every change is a **mechanical rewrite** with a rule id (53 of them, `O2PG-CV-*`).
Anything the converter recognises but won't change gets an inline marker on the
line above it (44 kinds, `O2PG-RV-*`):

```sql
/* O2PG-REVIEW O2PG-RV-005: ROWNUM has no PostgreSQL equivalent; use LIMIT ... */
```

Blocking markers mean the Oracle code is still there and will not work as
written. That is on purpose: a routine that fails to compile is better than one
that runs with different behaviour. The full list, with what each rewrite does
and what to check afterwards, is in [docs/conversion-rules.md](docs/conversion-rules.md).

### Before and after

From the bundled sample (`examples/converted/`, generated by the tool). The
Oracle function:

```sql
FUNCTION get_order_total (p_order_id NUMBER) RETURN NUMBER IS
  l_total NUMBER;
BEGIN
  SELECT SUM(unit_price * quantity * (1 - NVL(discount, 0)))
    INTO l_total
    FROM order_items
   WHERE order_id = p_order_id;
  RETURN NVL(l_total, 0);
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RETURN 0;
END get_order_total;
```

becomes:

```sql
CREATE OR REPLACE FUNCTION sales.get_order_total (p_order_id numeric)
RETURNS numeric
LANGUAGE plpgsql
SET search_path = sales, public
AS $$
DECLARE
  l_total numeric;
BEGIN
  SELECT SUM(unit_price * quantity * (1 - COALESCE(discount, 0)))
    INTO STRICT l_total
    FROM order_items
   WHERE order_id = p_order_id;
  RETURN COALESCE(l_total, 0);
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RETURN 0;
END;
$$;
```

`STRICT` matters. Oracle raises `NO_DATA_FOUND` or `TOO_MANY_ROWS` from a
`SELECT ... INTO`; PostgreSQL silently stores NULL or the first row unless you
add `STRICT`. The converter adds it everywhere a `SELECT ... INTO` has a `FROM`.

A routine that needs a person looks like this (from `emp_mgmt.hire`):

```sql
BEGIN
  IF p_salary > c_max_salary THEN
    /* O2PG-REVIEW O2PG-RV-035: || returns NULL in PostgreSQL when any operand is NULL; Oracle treats NULL as ''. Use concat() or COALESCE where an operand can be NULL */
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'Salary above band: ' || p_salary, HINT = 'Oracle error code -20001';
  END IF;
  INSERT INTO employees (employee_id, last_name, email, job_id, salary, department_id, hire_date)
  VALUES (nextval('employees_seq'), p_last_name, p_email, p_job_id, p_salary,
          p_department_id, date_trunc('day', LOCALTIMESTAMP(0)))
  RETURNING employee_id INTO l_id;
  /* O2PG-REVIEW O2PG-RV-011: package variable g_hires_this_session: PostgreSQL has no package state; keep it in a table, a temporary table, or a custom setting via set_config()/current_setting() */
  g_hires_this_session := g_hires_this_session + 1;
```

On the sample the report says: 14 objects, 21 routines and triggers; 5 converted
with no review points, 3 with only non-blocking ones, 9 blocked and 4 not
converted; 156 rewrites, 49 review points (38 blocking); **mechanical coverage
48.0%** (49 of 102 statements need no review). The sample is deliberately
nasty. The test fixtures in `tests/fixtures/plsql/` come out at 91.4%, but they
were written to exercise the rewrites, so don't read that as a prediction for
your code either.

### How it is tested

Every rewrite has a positive test and a test that the same text inside a comment
or string literal is left alone. The rewrites are checked to be idempotent, and
a token-level test checks that no word of the Oracle source (code, comments or
literals) disappears unless a documented rewrite replaced it.

The integration tests (`pytest -m integration`, needs `O2PG_TEST_DSN`) load the
`map-types` DDL into PostgreSQL 16, create every converted routine and trigger
that has no blocking marker with `check_function_bodies = on`, and then call
them: `NVL`/`DECODE` results, `nextval`, `SELECT INTO STRICT` raising
`no_data_found` and `too_many_rows`, `RAISE_APPLICATION_ERROR` codes, OUT
parameters, triggers setting `NEW` columns, a package calling its own routines,
and the sample's MERGE. CI runs them against a `postgres:16` service container.

### Limits

- **It is not a parser.** Like the assessment, it works on text with comments
  and literals masked out. It does not know column types, so date arithmetic on
  a column (`order_date + 1`) is not rewritten; it only rewrites expressions
  built from `SYSDATE`, `TRUNC`/`ADD_MONTHS` results and variables declared as
  dates. Constructs it does not recognise pass through unchanged, with no marker.
- **Dynamic SQL is not converted.** `EXECUTE IMMEDIATE` becomes `EXECUTE`, but
  the SQL string is flagged, not rewritten.
- **"Converted" means it compiles, not that it is right.** `CREATE FUNCTION`
  checks PL/pgSQL syntax and declarations, but not the SQL inside. Run the
  routines against real data and compare results with Oracle before relying on
  them. The behaviour tests here cover the rewrites, not your business logic.
- Coverage is a count of statements without a marker. It is a way to rank work,
  not a measure of correctness.

## Rule categories

| Category | Prefix | Rules | Examples |
|---|---|---|---|
| datatypes | DT | 17 | `DATE` with time, bare `NUMBER`, `BYTE` semantics, LOBs, `BFILE`, `XMLTYPE`, `SDO_GEOMETRY`, object type columns |
| plsql | PL | 18 | packages, package state, autonomous transactions, `BULK COLLECT`/`FORALL`, `REF CURSOR`, `UTL_FILE`, `UTL_HTTP`, wrapped code |
| sql-dialect | SQ | 18 | `CONNECT BY`, `ROWNUM`, `DECODE`, `NVL`, `(+)` joins, `MERGE`, `SYSDATE`, `''` literals, `\|\|` with NULLs |
| objects | OB | 17 | synonyms, database links, row and compound triggers, sequences, GTTs, MV refresh, long and reserved identifiers |
| partitioning | PT | 6 | interval, reference and composite partitioning, keys without the partition column, global indexes |
| security | SE | 6 | VPD to RLS, TDE columns, `PUBLIC` grants, definer rights |
| performance | PF | 9 | bitmap, reverse key, domain and function-based indexes, IOTs, unindexed FKs, very large tables |
| azure-specific | AZ | 6 | `DBMS_SCHEDULER` to `pg_cron`, OS jobs, Advanced Queuing, non-Unicode character sets, `orafce`, Java |

The full reference with remediation text is in [docs/rules.md](docs/rules.md),
generated from the code by `scripts/gen_rules_doc.py`.

### About the extensions it recommends

`o2pg` only recommends extensions it is confident are on the Flexible Server
allow-list: `orafce`, `pg_cron`, `pg_partman`, `postgis`, `vector` (pgvector),
`postgres_fdw`, `pg_stat_statements`, `pgcrypto`, `uuid-ossp` and `hstore`.
You still have to add them to the `azure.extensions` server parameter, and
`pg_cron` also needs `shared_preload_libraries` and a restart. Check the
[supported extensions list](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-extensions)
for your PostgreSQL version before you plan around one.

## How the scores work

These are heuristics for ranking work and comparing schemas. They are not a quote.

- **Complexity (0 to 100):** each triggered rule adds a severity weight
  (blocker 25, high 8, medium 3, low 1). Repeated hits count logarithmically, so
  one noisy rule can't dominate. The total goes through `100 * (1 - e^(-x/300))`.
  Any blocker lifts the level to at least "High".
- **Effort points:** each rule's per-object effort, plus 0.1 per table and one
  point per 150 lines of PL/SQL. Bands: S up to 20 points, M up to 80, L up to
  240, XL beyond.
- **AI readiness (0 to 100):** starts at 100 and decays with every triggered
  rule that hides behaviour a model is likely to miss (blocker 30, high 20,
  medium 10, low 5 penalty points, through `100 * e^(-p/150)`).

## Limitations

Please read these before you trust a number in the report.

- **The collector hasn't been run against a live Oracle database in CI yet.** It
  was written against the 19c data dictionary documentation and the test suite
  checks that it emits every field the analyser reads, but that is not the same
  as running it. If it fails on your database, please open an issue with the
  error.
- **No parser.** PL/SQL and view text are scanned with regular expressions after
  stripping comments and masking string literals. That removes most false
  positives, but it can't see types, resolve names, or look inside dynamic SQL.
- **Views are read from `TEXT_VC`**, which is cut at 4000 characters. Long views
  are only partly scanned.
- **Package state detection is a heuristic** that expects one declaration per
  line.
- **Effort numbers are relative.** Use them to compare objects and schemas, not
  to write a project plan.
- **The DDL from `map-types` is a starting point.** It skips views, code,
  indexes other than primary and unique keys, and anything it marks `TODO`.
- **`convert` output needs review and testing.** See the limits under
  [Converting PL/SQL](#converting-plsql-v02).
- It does not look at data. Data profiling (real precision of `NUMBER` columns,
  whether `DATE` columns ever have a time) is on the roadmap.

## Roadmap

- ~~PL/SQL to PL/pgSQL conversion helpers~~: shipped in v0.2.0 (`o2pg convert`).
- **View conversion** with the same rewrite engine, and type-aware date
  arithmetic using column types from the inventory.
- **Data validation:** row counts, checksums and sampled column comparisons
  between Oracle and PostgreSQL after a load, plus profiling queries that answer
  the "does this `DATE` ever have a time?" questions up front.
- **Performance comparison harness:** replay captured statements against both
  databases and compare plans and timings.

## Trying it against a real Azure server

[infra/](infra/) has a small Bicep template for a Burstable B1ms Flexible
Server, which is eligible for the Azure free account offer, so you can load the
suggested DDL and see what breaks. Read the cost note there first.

## Contributing

Issues and pull requests are welcome, especially new rules and reports of the
collector failing on a real database. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
