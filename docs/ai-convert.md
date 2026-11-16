# AI-assisted conversion with gates (`o2pg ai-convert`, v0.3)

`o2pg convert` makes the rewrites it can prove and marks the rest. `o2pg
ai-convert` sends only the marked units to a model, and accepts nothing until
the answer has passed three gates. The mechanical output is never edited.

## Why gates

On the hard corpus in `tests/fixtures/ai/`, the mechanical v0.2 output matches
Oracle on **6 of 34** cases. The blocked units fail loudly, which is fine. The
dangerous ones are the units with only non-blocking review points: they
compile, they run, and they give a different answer:

| Routine | Input | Oracle | Mechanical PL/pgSQL |
|---|---|---|---|
| `contact_line` | employee with no email | `Bob <>` | `NULL` |
| `code_kind` | `''` | `missing` | `empty` |
| `avg_split` | 7, 2 | `3.5` | `3` |
| `digits_only` | `n/a` | `NULL` | `/a` |

A model can fix all of these. It can also produce code that looks right and
isn't, so every answer goes through the same checks.

## The gates

| Gate | Checks | Catches |
|---|---|---|
| static | Only the routine (plus `CREATE TYPE` when a contract change needs it); the same name; the same signature unless a review point requires a change; no Oracle syntax or functions left outside comments and strings; no review marker left unless listed as unresolved | `DROP FUNCTION` added "to be safe", `p_dept numeric` quietly becoming `integer`, `instr()` from orafce, leftover `ROWNUM` |
| compile | `CREATE` in a throwaway database with `check_function_bodies = on`, rolled back | Syntax errors. PL/pgSQL does not check column or function names here: an unknown column only fails when the code runs |
| behaviour | Every equivalence case for the routine, run against the candidate in a rolled-back transaction, compared with what Oracle returned | Wrong answers that compile: `(+)` filters moved to `WHERE`, `date_part('day', ...)` for a day count, `GET DIAGNOSTICS` after a loop instead of after one statement, `''` instead of `NULL` |

A static or compile failure goes back to the model with the error, up to
`--attempts` (default 2). A behaviour failure does not, unless you pass
`--behaviour-feedback`: showing the model the failing test invites code that
fits the test rather than Oracle.

## What is never sent

Units with a review point that needs a design decision are reported under
"Needs a person": autonomous transactions, package state, Oracle-supplied
packages (`UTL_FILE`, `DBMS_AQ`, `UTL_MAIL`, ...), transaction control in a
function or trigger, package initialisation, flashback, and object kinds the
converter does not handle. A plausible-looking answer to any of these is worse
than an honest blocker.

## Statuses

| Status | Meaning |
|---|---|
| verified | Passed static, compile and behaviour. Still review it like any change |
| compiles | Passed static and compile; no equivalence case covers it |
| static only | No `--dsn` given; nothing was compiled or run |
| unresolved | The model left review points it could not resolve, and said so |
| rejected | Failed a gate; the report shows which and why |
| error | No usable answer from the provider |
| skipped | Not sent (design decision, or no review points) |

Candidates are written as one `.sql` file per unit with a header that states
the status, every change per review rule, assumptions, and any signature
change. `verified.sql` collects the verified units.

## Equivalence cases (`o2pg equiv`)

A case file lists calls and what Oracle returned. See
`tests/fixtures/ai/cases.json`:

```json
{"id": "depts_without_earner.8000", "call": "lab.depts_without_earner",
 "kind": "function", "returns": "number", "args": [8000], "expect": {"value": 2}}
```

Kinds are `function` (returns a value), `procedure` (OUT parameters, plus an
optional `then` query to check table state), and `rows` (a table function).
`expect` holds `value`, `out`, `rows`, or `error` (`NO_DATA_FOUND`,
`TOO_MANY_ROWS`, `ZERO_DIVIDE`, `APPLICATION_ERROR` with `message`, ...).

```bash
# Record (or check) the expectations on Oracle; needs pip install 'o2pg[oracle]'
o2pg equiv record cases.json --oracle-dsn lab/secret@localhost:1521/FREEPDB1          # compare
o2pg equiv record cases.json --oracle-dsn lab/secret@localhost:1521/FREEPDB1 --write  # overwrite

# Check converted code on PostgreSQL
o2pg equiv check cases.json --dsn postgresql://postgres@localhost/postgres --sql verified.sql
```

Two Oracle details the harness handles for you: functions are called from a
PL/SQL block, because a function that raises `NO_DATA_FOUND` inside a SQL
query returns NULL instead of an error; and binding `''` sends NULL, as an
application would. Numbers are compared by decimal value, and `''` is never
equal to NULL.

The CI job `oracle` starts Oracle Database Free, runs the setup scripts, and
replays every case, so the expectations in the repo are Oracle's, not ours.

## Running it

```bash
pip install 'o2pg[pg]'
export AZURE_OPENAI_ENDPOINT=https://<name>.openai.azure.com
export AZURE_OPENAI_API_KEY=...                 # or AZURE_OPENAI_AD_TOKEN
docker run -d -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16

o2pg ai-convert o2pg_inventory.json -o ai \
  --dsn postgresql://postgres:postgres@localhost:5432/postgres \
  --cases my_cases.json --record ai/answers
```

The default deployment is `gpt-5-mini` (override with `--deployment` or
`AZURE_OPENAI_CHAT_DEPLOYMENT`). Answers saved with `--record` replay exactly
with `--provider replay --replay ai/answers`, without a model or a key.

`scripts/ai_eval.sh` runs the hard corpus and the bundled sample through one or
more deployments and keeps everything under `eval/`.

## The test fixtures are not model output

`tests/fixtures/ai/responses/reference` holds hand-written correct conversions,
and `responses/flawed` holds hand-written answers with the mistakes the gates
are meant to catch. They test the pipeline. They say nothing about how well any
model does; run `scripts/ai_eval.sh` for that.
