# Test server for o2pg output

`main.bicep` creates the smallest useful Azure Database for PostgreSQL Flexible
Server so you can load the DDL from `o2pg map-types` and see what breaks:

| Setting | Value |
|---|---|
| Compute | Burstable, `Standard_B1ms` (1 vCore, 2 GiB) |
| Storage | 32 GB, auto-grow off |
| High availability | Off |
| Backups | 7 days, locally redundant |
| Network | Public access, firewall open to one IP only |
| Extensions allow-listed | orafce, pg_cron, pg_partman, postgis, pgcrypto, pg_stat_statements, postgres_fdw |

## Cost

The B1ms size with 32 GB of storage is the configuration covered by the Azure
free account offer for PostgreSQL Flexible Server (a monthly allowance of
compute hours, storage and backup storage for the first 12 months). Check the
current terms on the Azure free account page before relying on it. Outside the
offer, or once it ends, the server is billed per hour while it exists, and
storage is billed even while the server is stopped.

Stopping the server (`az postgres flexible-server stop`) pauses compute billing,
but Azure starts a stopped server again automatically after 7 days. Delete the
resource group when you're done.

## Deploy

```bash
az group create --name rg-o2pg-test --location eastus

az deployment group create \
  --resource-group rg-o2pg-test \
  --template-file infra/main.bicep \
  --parameters clientIpAddress=$(curl -s https://api.ipify.org) \
               administratorLoginPassword='<a strong password>'
```

The deployment prints the server name and a ready-made `psql` command.

## Use it

```bash
o2pg map-types examples/sample_inventory.json -o schema.sql
psql "host=<server>.postgres.database.azure.com port=5432 dbname=postgres user=o2pgadmin sslmode=require" \
  -c 'CREATE EXTENSION IF NOT EXISTS postgis' -f schema.sql
```

Expect errors: the sample is built to hit `TODO`s, and fixing them is the point.

`pg_cron` also needs to be preloaded, which requires a restart:

```bash
az postgres flexible-server parameter set -g rg-o2pg-test -s <server> \
  --name shared_preload_libraries --value pg_cron,pg_stat_statements
az postgres flexible-server restart -g rg-o2pg-test -n <server>
```

## Clean up

```bash
az group delete --name rg-o2pg-test --yes --no-wait
```

This deletes the server, its backups and the firewall rule.
