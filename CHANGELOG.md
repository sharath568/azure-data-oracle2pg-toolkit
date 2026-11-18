# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-11-18

### Added

- Collector: `partitions` and `subpartitions` (with `HIGH_VALUE`, read from the
  LONG column through a `WITH FUNCTION` clause), `part_indexes` (LOCAL or
  GLOBAL) and `subpartition_keys`. Inventories without them still load.
- `o2pg map-types` creates every partition and subpartition: range (including
  multi-column and `MAXVALUE`), list (including `NULL` and `DEFAULT`), hash
  (`MODULUS`/`REMAINDER`) and range/list/hash composites. Interval partitions
  are named like pg_partman 5 (`orders_p20250101`), and interval tables get a
  `<table>_add_partitions` procedure that creates partitions ahead of time.
- `o2pg partitions`: a report of every partitioning decision.
- `labs/partitioning`: six measured experiments on PostgreSQL (NULL keys,
  global uniqueness, pruning, DEFAULT partitions, archiving, row movement).
- `tests/fixtures/partitions`: Oracle DDL, rows, a matching inventory and the
  partition each row lands in; the CI job `partitions` runs the real collector
  on Oracle Database Free and compares rows per partition with PostgreSQL 16.
- The sample inventory now has partitions, so `o2pg sample` users can try
  `o2pg partitions` (`examples/partitions-report.md`).

### Changed

- A `MAXVALUE` range partition becomes the DEFAULT partition when a key column
  is nullable (Oracle stores NULL keys there; PostgreSQL range partitions do not
  accept NULL).
- A primary or unique key that does not include every partitioning column is
  written as a comment with a TODO instead of failing the whole script, and
  foreign keys that reference it become TODOs too.
- Multi-column LIST tables are created unpartitioned (PostgreSQL does not
  support them) instead of with an invalid `PARTITION BY` clause.
- Collector version string is now 0.4.0.

## [0.3.0] - 2026-11-16

### Added

- `o2pg ai-convert`: sends the units `o2pg convert` left with review points to
  Azure OpenAI (REST, structured JSON output, no SDK) and gates every answer:
  a static gate (only the routine, same name, same signature unless a review
  point requires a change, no Oracle syntax or functions left, no undeclared
  review markers), a compile gate in a throwaway PostgreSQL database
  (`--dsn`), and a behaviour gate against equivalence cases (`--cases`).
  Static and compile failures go back to the model (`--attempts`, default 2);
  behaviour failures only with `--behaviour-feedback`.
- Units that need a design decision (autonomous transactions, package state,
  Oracle-supplied packages, transaction control in functions, flashback,
  unconverted objects) are not sent and are listed under "Needs a person".
- Candidate files with a header stating status, changes per review rule,
  assumptions and signature changes; `verified.sql`; `ai-report.md` and
  `ai-report.json` with token counts and a cost estimate.
- `--provider replay` and `--record DIR`: save answers and replay a run exactly
  without a model.
- `o2pg equiv record` (python-oracledb, `pip install 'o2pg[oracle]'`) and
  `o2pg equiv check`: record results on Oracle and compare converted code on
  PostgreSQL. Functions are called from a PL/SQL block so NO_DATA_FOUND is not
  swallowed; numbers are compared exactly; `''` never equals NULL.
- `tests/fixtures/ai/`: a 14-routine hard corpus with 34 Oracle cases, plus
  hand-written reference and flawed answers that test each gate.
- CI job `oracle` that replays every case on Oracle Database Free
  (`gvenzl/oracle-free`), and an ai-convert smoke test.
- `scripts/ai_eval.sh` and `docs/ai-convert.md`.

### Changed

- Collector version string is now 0.3.0 (no functional change).

## [0.2.0] - 2026-10-10

### Added

- `o2pg convert`: PL/SQL to PL/pgSQL conversion helpers for standalone
  procedures and functions, packages (split into a schema plus one routine per
  procedure or function) and simple row and statement triggers (a trigger
  function plus `CREATE OR REPLACE TRIGGER`). Input is an inventory JSON file or
  a SQL*Plus script of `CREATE OR REPLACE` units; output is one `.sql` file per
  object plus `conversion-report.md` and `conversion-report.json`. Options:
  `-o/--output`, `--object SCHEMA.NAME`, `--format md|json|both`, `--schema`.
- 53 mechanical rewrites (`O2PG-CV-*`), including routine headers and parameter
  modes, data types via the `map-types` table, `NVL`/`NVL2`/`DECODE`,
  `SYSDATE`/`SYSTIMESTAMP`, sequences, `FROM DUAL`, `SELECT ... INTO STRICT`,
  `DBMS_OUTPUT.PUT_LINE`, `RAISE_APPLICATION_ERROR`, exception names,
  `:NEW`/`:OLD`, `INSERTING`/`UPDATING`/`DELETING`, cursor declarations and
  FOR loops, date arithmetic on known date expressions, and `CALL` for
  procedure call statements.
- 44 review points (`O2PG-RV-*`) left inline as
  `/* O2PG-REVIEW <rule>: <reason> */`, blocking or non-blocking, including
  package state, autonomous transactions, `BULK COLLECT`/`FORALL`, `ROWNUM`,
  `CONNECT BY`, `(+)` joins, `MERGE`, dynamic SQL, `''` literals and `||` with
  possible NULLs. Unconverted objects (compound and DDL triggers, object types,
  wrapped code) are kept as comments.
- Conversion report with applied rewrites, review points with line numbers,
  per-unit status and a mechanical coverage percentage.
- `docs/conversion-rules.md`, generated from the conversion registry.
- `examples/converted/`: the bundled sample, converted.
- PostgreSQL 16 integration tests (`pytest -m integration`, `O2PG_TEST_DSN`)
  that create every non-blocked converted routine with
  `check_function_bodies = on` and call converted routines to check behaviour,
  plus a CI job with a `postgres:16` service container. Optional `pg` extra
  installs psycopg.

### Changed

- `scripts/gen_rules_doc.py` writes and checks both rule references.
- Collector version string is now 0.2.0 (no functional change).

## [0.1.0] - 2026-10-07

### Added

- `collector/collect.sql`: read-only SQL*Plus collector (Oracle 19c+) that
  writes one JSON inventory covering tables, columns, constraints, indexes,
  sequences, views, synonyms, triggers, PL/SQL source, database links,
  materialized views, partitioning, LOBs, object types, scheduler jobs, grants,
  VPD policies, AQ queues, TDE columns and invalid objects.
- `o2pg assess` with Markdown, JSON and self-contained HTML reports,
  `--min-severity` and `--fail-on`.
- 97 rules in eight categories, each with severity, effort, remediation and
  Azure Flexible Server notes.
- Complexity score, effort band, per-object ranking and an AI readiness section.
- `o2pg map-types` for suggested PostgreSQL DDL.
- `o2pg rules` and `o2pg sample`.
- Bicep template for a Burstable B1ms Flexible Server to test against.
