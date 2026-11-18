# Partitioning lab: Oracle habits on PostgreSQL

`lab.py` runs six small experiments on a real PostgreSQL server and prints what
happened. Each one is a place where an Oracle DBA's expectations and
PostgreSQL's behaviour differ, and each one is behind a rule in
`o2pg partitions`.

```bash
pip install 'o2pg[pg]'
python labs/partitioning/lab.py postgresql://postgres:postgres@localhost:5432/postgres
```

It creates a throwaway database, loads 12 monthly partitions of 50,000 rows,
and drops the database at the end. It takes a few seconds. The test suite runs
the same experiments with fewer rows and asserts the facts (not the timings).

## Results

From one run on a small Linux VM. The timings are only there to show the
shape; run it on your own server for real numbers.

Measured on PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1).

| Experiment | Result |
|---|---|
| NULL key, last partition `TO (MAXVALUE)` | rejected: no partition of relation "nk_max" found for row |
| NULL key, last partition `DEFAULT` | accepted into nk_def_pmax |
| `PRIMARY KEY (order_id)` on a table partitioned by date | refused: unique constraint on partitioned table must include all partitioning columns |
| `PRIMARY KEY (order_id, order_date)` | 2 rows with order_id 42 accepted |
| New partition while DEFAULT holds a row for its range | updated partition constraint for default partition "dc_default" would be violated by some row |
| New partition, DEFAULT has 300k unrelated rows / is empty | 20.0 ms / 1.4 ms |
| Remove one month (50,000 rows): DELETE | 20.8 ms (and dead rows to vacuum) |
| Remove one month: DETACH CONCURRENTLY + DROP | 5.1 ms |
| UPDATE moves the key to the next month | row moved from rm_m01 to rm_m02 |

Partitions scanned (of 12) when asking for March:

| Filter | Partitions scanned |
|---|---|
| `created >= '2025-03-01' AND created < '2025-04-01'` | 1 |
| `date_trunc('month', created) = '2025-03-01'` | 12 |
| `created::date BETWEEN '2025-03-01' AND '2025-03-31'` | 12 |
| `to_char(created, 'YYYY-MM') = '2025-03'` | 12 |
| `prepared statement, generic plan (11 pruned at run time)` | 1 |


## What each result means for a migration

- **NULL keys.** Oracle stores a row whose range key is NULL in the `MAXVALUE`
  partition. PostgreSQL refuses it unless there is a DEFAULT partition, so
  `o2pg map-types` turns `MAXVALUE` into `DEFAULT` when a key column is
  nullable. If the column is `NOT NULL`, it keeps `TO (MAXVALUE)`.
- **Global unique indexes.** A primary key on `order_id` alone, on a table
  partitioned by `order_date`, is refused. Adding the date makes it legal, but
  then the same `order_id` can exist twice in different months. Decide which
  you need: values from one sequence, a separate lookup table, or uniqueness per
  date. `map-types` leaves the key out with a TODO so the rest of the DDL loads.
- **Pruning.** Only a condition on the column itself prunes. Wrapping the key
  in `date_trunc`, a cast, or `to_char` scans every partition. Prepared
  statements with a generic plan still prune at run time.
- **DEFAULT partitions cost something.** A new partition cannot be created
  while the DEFAULT partition holds rows for its range, and even when it holds
  none, PostgreSQL scans it to check.
- **Archiving.** `DETACH PARTITION ... CONCURRENTLY` plus `DROP TABLE` removes a
  month without leaving dead rows to vacuum. `DELETE` does the same work row by
  row.
- **Row movement.** An `UPDATE` that changes the key moves the row. Oracle
  raises ORA-14402 unless `ROW MOVEMENT` is enabled.
