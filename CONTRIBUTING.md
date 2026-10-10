# Contributing

Thanks for taking a look. The most useful contributions right now are:

1. **Collector reports.** If `collector/collect.sql` fails or produces odd output
   on your Oracle version, open an issue with the version, the error and (if you
   can) a redacted snippet of the JSON.
2. **New rules** for Oracle features that bite during migrations and are not
   covered yet, and **conversion rules** for `o2pg convert`.
3. **False positives and false negatives** in existing rules, ideally with a
   minimal piece of SQL or PL/SQL that shows the problem.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

## Adding a rule

1. Pick the module in `src/o2pg/rules/` that matches the category and the next
   free id for that prefix (`O2PG-DT-018`, `O2PG-PL-019`, ...). Ids are never
   reused.
2. Use `pattern_rule(...)` for a regex over code, or the `@rule(...)` decorator
   for anything that inspects inventory sections directly. The registry checks
   the id format, the category prefix and the extension allow-list at import time.
3. Write the remediation as something a person can act on, naming the
   PostgreSQL or Azure feature to use. Only mention extensions from
   `AZURE_ALLOWED_EXTENSIONS` in `rules/base.py`; if you want to add one there,
   link the Azure documentation that lists it in the pull request.
4. Set `ai_risk` only when the problem is implicit Oracle behaviour that a model
   would plausibly convert wrongly. Phrase it as what to tell the model.
5. Add a positive and a negative case to `CASES` in `tests/test_rules.py`. Make
   the negative case a near miss (the keyword in a comment or string, or the
   same shape with the important property flipped). `test_every_rule_has_cases`
   fails until you do.
6. Regenerate the generated files and commit them:

   ```bash
   python scripts/gen_rules_doc.py
   o2pg assess examples/sample_inventory.json -o examples/sample_report.md
   ```

   Tests fail if either file is stale.

## Adding a conversion rule

`o2pg convert` rules live in `src/o2pg/convert/registry.py`: `rewrite(...)` for
an `O2PG-CV-*` mechanical rewrite and `review(...)` for an `O2PG-RV-*` review
point. The code that applies them is mostly in `src/o2pg/convert/body.py`.

1. Only add a rewrite when the PostgreSQL form is the right translation in every
   case the pattern can match. If it is only usually right, make it a review
   point instead, or put the caveat in `note`.
2. Work on masked text (comments and literals are hidden behind placeholder
   characters) so the rewrite can never touch them.
3. Add a positive case to `REWRITES` (or `REVIEWS`) in `tests/test_convert.py`
   and the construct to `TOKENS` so the comment and string-literal negatives run.
   The idempotence and "no code dropped" tests run automatically.
4. If it changes behaviour, add a fixture to `tests/fixtures/plsql/` and a test
   in `tests/test_convert_pg.py` that calls the converted routine.
5. Regenerate and commit `docs/conversion-rules.md` (`python scripts/gen_rules_doc.py`)
   and `examples/converted/`
   (`o2pg convert examples/sample_inventory.json -o examples/converted`).

### Running the PostgreSQL integration tests

```bash
pip install -e '.[dev,pg]'
docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
O2PG_TEST_DSN=postgresql://postgres:postgres@localhost:5432/postgres pytest -m integration
```

The tests create and drop their own database, so the role needs `CREATEDB`.
Without `O2PG_TEST_DSN` they are skipped.

## Changing the collector

Keep it plain SQL*Plus: `SET` commands, `PROMPT` and `SELECT` against `ALL_`
views only, one JSON object per line. Every key the analyser reads must appear
as `'key' VALUE ...`; `tests/test_docs.py` checks this against the dataclasses
in `src/o2pg/inventory.py`.

## Style

- Python 3.10+, type hints on public functions, docstrings on modules, classes
  and public functions.
- `ruff` is the formatter and linter; CI runs both.
- Plain language in rule text and docs. Short sentences beat clever ones.
