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
- It does not look at data. Data profiling (real precision of `NUMBER` columns,
  whether `DATE` columns ever have a time) is on the roadmap.

## Roadmap

- **November 2026: PL/SQL to PL/pgSQL conversion helpers.** Mechanical rewrites
  for the safe cases (`NVL`, `DECODE`, `:NEW`, `SYSDATE`, sequences), with
  everything else left as marked review points.
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
