---
title: Before You Point AI at Your Oracle Schema
published: false
description: An Oracle schema is more than its DDL. Here is what a model can't see when you ask it to convert to PostgreSQL, and a small offline tool that finds it first.
tags: azure, postgres, oracle, database
---

Every week someone shows me the same demo. They copy a few hundred lines of
Oracle DDL and a package body into a chat window, ask for PostgreSQL, and get
back something that looks great. It compiles. The tables are there. The
functions are there. It took two minutes.

I spent a lot of years as an Oracle DBA, and my honest reaction to that demo is
"nice, now let's find out what it changed."

Because an Oracle schema isn't just its DDL. A lot of how an Oracle database
behaves is never written down in the schema at all. It lives in defaults,
in conventions the database enforces quietly, and in code that runs when you
aren't looking. A model converting text can only convert what's in the text.

This post is about those invisible parts, and a small open source tool I built
to find them before anyone starts converting:
[azure-data-oracle2pg-toolkit](https://github.com/sharath568/azure-data-oracle2pg-toolkit).

## Five things the model can't see

None of these are exotic. You will find most of them in any Oracle schema that
has been in production for a while.

**1. An empty string is NULL.** In Oracle, `''` and `NULL` are the same thing.
So this trigger line, which looks like it clears empty phone numbers:

```sql
IF :NEW.phone_number = '' THEN
  :NEW.phone_number := NULL;
END IF;
```

never actually fires in Oracle, because `x = NULL` is never true. Translate it
literally to PostgreSQL and it suddenly starts working, which changes behaviour.
The same rule means a `NOT NULL` `VARCHAR2` column also rejects empty strings
in Oracle. In PostgreSQL `''` sails straight through `NOT NULL`. Your constraint
just got weaker and nobody touched it.

**2. `DATE` has a time.** Oracle `DATE` stores hours, minutes and seconds.
PostgreSQL `date` doesn't. If the conversion maps `DATE` to `date` because the
names match, you lose the time portion on every row, and every
`hire_date + 1` in your code turns into an error once the column becomes a
`timestamp`.

**3. Bare `NUMBER` means "up to 38 digits, any scale".** It isn't an integer
and it isn't a float. Models love to turn `NUMBER` into `integer` or
`double precision` because that's what the column "looks like". Sometimes
that's right. You only know after you look at the data.

**4. Triggers do work the DDL doesn't mention.** A `BEFORE INSERT` trigger
that fills in the key from a sequence, stamps `last_updated`, uppercases the
email and writes to an audit table is part of the table's behaviour. If the
model only sees `CREATE TABLE`, you get a table that silently stops doing
those things.

**5. Packages keep state.** A variable declared at package level lives for the
whole session. Code uses that as a cache, a counter, or a way to pass context
from a trigger to a procedure. PostgreSQL functions have nothing like it, and a
model converting one function at a time has no way to know the variable is
shared.

There are more. `SYSDATE` is server clock time and moves during a transaction,
while `now()` is frozen at transaction start. `'a' || NULL` is `'a'` in Oracle
and `NULL` in PostgreSQL. Oracle code runs with the owner's privileges by
default, PostgreSQL code runs as the caller. `ROWNUM <= 10 ORDER BY x` gives
you ten arbitrary rows, sorted, and a model will "fix" that into a real top 10
without asking.

The common thread: the source doesn't say any of this. You have to know it.

## So I wrote it down as code

`o2pg` is an offline triage tool. It doesn't connect to anything and it doesn't
convert anything. It reads an inventory of your schema and tells you what will
hurt when you move to Azure Database for PostgreSQL Flexible Server, including
which of those problems are the kind a model will get wrong.

There are two pieces.

A read-only SQL*Plus script that a DBA runs against Oracle. It queries the
`ALL_` dictionary views (tables, columns, constraints, indexes, partitioning,
triggers, PL/SQL source, links, jobs, grants and so on) and spools one JSON
file. It never touches table data. That matters, because the people who can
run things against production and the people doing the migration analysis are
often not the same people.

```bash
sqlplus -s app_reader/secret@//dbhost:1521/ORCLPDB1 @collector/collect.sql HR,SALES
```

And a Python CLI that analyses the file anywhere:

```bash
pip install .
o2pg assess o2pg_inventory.json -f html -o assessment
```

If you just want to see it, the repo ships a sample HR/SALES schema that I made
deliberately messy:

```bash
o2pg sample -o sample_inventory.json
o2pg assess sample_inventory.json
```

## What the output looks like

Here is the top of the report for the sample schema, unedited:

```text
| Metric               | Value                                   |
|----------------------|-----------------------------------------|
| Migration complexity | 86/100 (Very high)                      |
| Effort band          | L: ~2 to 6 person-months (133.3 points) |
| Blockers             | 5                                       |
| AI readiness         | 26/100                                  |
| Rules triggered      | 89 of 97                                |
```

The five blockers are the things Flexible Server simply can't do: a `BFILE`
column, `UTL_FILE` writing CSVs to a server directory, `UTL_MAIL` sending mail
from inside a procedure, a wrapped package body nobody has the source for, and
a scheduler job that runs a shell script. None of those are conversion
problems. They are architecture decisions, and it's much better to find them
in week one.

The part I care about most is the AI readiness section. It lists every rule
that fired because of implicit behaviour, with a sentence you can paste
straight into a prompt or a conversion spec:

```text
26/100. Do not paste this schema into an LLM as-is. Too much behaviour is
implicit; convert with a written spec and review every object.

1. DATE columns carry a time of day (O2PG-DT-003, 6 object(s)): Oracle DATE
   includes hours, minutes and seconds. Tell the model to map DATE to
   timestamp(0), not date.
2. Package-level state (global variables) (O2PG-PL-002, 2 object(s)): These
   packages keep state between calls. A model converting one function at a
   time cannot see that; tell it which variables are session state and how
   you want them replaced (for example current_setting('app.x')).
3. Autonomous transactions (O2PG-PL-003, 1 object(s)): Autonomous transactions
   commit even when the caller rolls back. Models tend to drop the pragma and
   silently change that behaviour; tell it how you want it handled.
4. Empty string literals ('' is NULL in Oracle) (O2PG-SQ-008, 1 object(s)):
   Oracle treats '' as NULL everywhere. A model will translate '' literally.
   Tell it to treat every '' as NULL and to flag comparisons with '' for review.
```

That list is the point of the whole exercise. I'm not against using AI for
migrations. I use it. But the model needs the same briefing a new team member
would get, and this generates the briefing from your actual schema instead of
from memory.

There's also `o2pg map-types`, which writes suggested PostgreSQL DDL with
conservative type choices and leaves `TODO` comments where a person has to
decide:

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

That last line is a good example of something that is easy to miss. Oracle is
happy to have a primary key on `order_id` alone for a table partitioned by
`order_date`, because it uses a global index. PostgreSQL requires every primary
key on a partitioned table to include the partition key. You either weaken the
uniqueness guarantee or rethink the partitioning, and you want to have that
conversation before you move 180 million rows.

## The Azure part

Moving to a managed service changes the rules in a few specific ways, and the
tool has a category just for that.

There's no file system you can reach from SQL, so `UTL_FILE`, `BFILE` and
external tables need a new home, usually Azure Blob Storage plus something
outside the database. There are no outbound calls from the database, so
`UTL_HTTP` and `UTL_MAIL` become an outbox table and a worker. OS-level
scheduler jobs move to whatever runs your other scheduled work.

On the other hand, a lot of what you need is there once you allow-list it.
`pg_cron` covers most `DBMS_SCHEDULER` jobs. `pg_partman` handles the
partition creation that Oracle interval partitioning did for you. `orafce`
gives you `NVL`, `DECODE`, `ADD_MONTHS` and friends as a bridge while you
convert. `postgis` takes over from Oracle Spatial. The report lists which of
these your schema actually needs, and the repo includes a small Bicep template
for a Burstable B1ms server so you can load the suggested DDL and watch what
breaks.

## What it doesn't do

I'd rather say this up front.

It's not a parser. Code is scanned with regular expressions after stripping
comments and masking string literals. That's good enough to find patterns
reliably, but it can't follow types or look inside dynamic SQL. The scores are
heuristics for comparing objects and schemas, not a project estimate. And the
collector script was written against the 19c dictionary documentation and is
checked by the test suite for shape, but it hasn't been run against a live
database in CI yet. If it breaks on yours, I really want the issue.

## What's next

In November I'm adding PL/SQL to PL/pgSQL helpers for the mechanical
rewrites, with everything else left as clearly marked review points. After that
comes data validation between the two databases, and a harness to compare
query performance before cutover.

If you have an Oracle schema headed for PostgreSQL, run the collector, read
the AI readiness section, and then decide how much you want to hand to a model.
The code is MIT licensed at
[github.com/sharath568/azure-data-oracle2pg-toolkit](https://github.com/sharath568/azure-data-oracle2pg-toolkit).
Rules for things that have bitten you are the best contribution I could ask
for.
