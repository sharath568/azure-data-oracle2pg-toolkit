# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
