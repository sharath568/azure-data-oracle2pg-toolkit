"""Azure-specific rules (O2PG-AZ-xxx): what a managed Flexible Server changes."""

from __future__ import annotations

import re
from collections.abc import Iterator

from o2pg.inventory import Inventory
from o2pg.rules.base import Category, Hit, Severity, rule

_AZ = Category.AZURE

_EXTERNAL_JOB_TYPES = frozenset({"EXECUTABLE", "EXTERNAL_SCRIPT", "SQL_SCRIPT", "BACKUP_SCRIPT"})


@rule(
    "O2PG-AZ-001",
    "DBMS_SCHEDULER jobs that run SQL or PL/SQL",
    _AZ,
    Severity.MEDIUM,
    0.75,
    description=(
        "Scheduled database work. pg_cron is supported on Azure Database for PostgreSQL "
        "Flexible Server and covers the common cases, with cron syntax instead of calendar "
        "expressions."
    ),
    remediation=(
        "Add pg_cron to shared_preload_libraries and azure.extensions (restart required), "
        "CREATE EXTENSION pg_cron in the database named by cron.database_name, then "
        "cron.schedule(...) or cron.schedule_in_database(...). Translate repeat_interval "
        "calendar strings to cron expressions; job chains and windows need app logic."
    ),
    extensions=("pg_cron",),
)
def _scheduler_jobs(inventory: Inventory) -> Iterator[Hit]:
    for job in inventory.scheduler_jobs:
        if job.job_type.upper() not in _EXTERNAL_JOB_TYPES:
            schedule = job.repeat_interval or "no repeat interval"
            yield Hit(f"{job.owner}.{job.name}", "JOB", f"{job.job_type}: {schedule}")


@rule(
    "O2PG-AZ-002",
    "Scheduler jobs that run OS executables or scripts",
    _AZ,
    Severity.BLOCKER,
    2.0,
    description=(
        "EXECUTABLE and script jobs run programs on the database host. A managed service "
        "gives you no host access."
    ),
    remediation=(
        "Move them to Azure Automation, Azure Functions with a timer trigger, Container Apps "
        "jobs or whatever scheduler the rest of your platform uses."
    ),
)
def _external_jobs(inventory: Inventory) -> Iterator[Hit]:
    for job in inventory.scheduler_jobs:
        if job.job_type.upper() in _EXTERNAL_JOB_TYPES:
            yield Hit(f"{job.owner}.{job.name}", "JOB", f"{job.job_type}: {job.job_action}")


_AQ_CODE = re.compile(r"\bDBMS_AQ(?:ADM)?\s*\.", re.IGNORECASE)


@rule(
    "O2PG-AZ-003",
    "Advanced Queuing",
    _AZ,
    Severity.HIGH,
    3.0,
    description=(
        "Oracle AQ queues and the code that enqueues or dequeues. PostgreSQL has no "
        "built-in message queue."
    ),
    remediation=(
        "For in-database work queues, a table plus SELECT ... FOR UPDATE SKIP LOCKED and "
        "LISTEN/NOTIFY works well. For integration between systems, use Azure Service Bus "
        "and an outbox table written in the same transaction."
    ),
)
def _advanced_queuing(inventory: Inventory) -> Iterator[Hit]:
    for queue in inventory.queues:
        yield Hit(f"{queue.owner}.{queue.name}", "QUEUE", f"queue table {queue.queue_table}")
    for unit in inventory.code_units:
        if unit.plsql and _AQ_CODE.search(unit.cleaned):
            yield Hit(unit.qualified, unit.kind, "calls DBMS_AQ / DBMS_AQADM")


_UNICODE_CHARSETS = frozenset({"AL32UTF8", "UTF8"})


@rule(
    "O2PG-AZ-004",
    "Database character set is not Unicode",
    _AZ,
    Severity.MEDIUM,
    2.0,
    description=(
        "Azure Flexible Server databases are normally created as UTF8. Data from a "
        "single-byte or legacy character set is converted on load, and invalid bytes that "
        "Oracle stored without complaint will be rejected."
    ),
    remediation=(
        "Scan for unconvertible data before the migration (Oracle DMU or a CSSCAN-style "
        "check), fix it at the source, and make sure the load tool declares the source "
        "encoding correctly. Re-check column lengths for characters that grow in UTF-8."
    ),
)
def _charset(inventory: Inventory) -> Iterator[Hit]:
    charset = inventory.metadata.character_set.upper()
    if charset and charset not in _UNICODE_CHARSETS:
        yield Hit(
            inventory.metadata.db_name or "(database)", "DATABASE", f"NLS_CHARACTERSET {charset}"
        )


_ORAFCE_FUNCTIONS = re.compile(
    r"\b(NVL2?|DECODE|ADD_MONTHS|MONTHS_BETWEEN|LAST_DAY|NEXT_DAY|INSTR|DBMS_OUTPUT)\b"
    r"|\bFROM\s+(DUAL)\b",
    re.IGNORECASE,
)


@rule(
    "O2PG-AZ-005",
    "orafce could shorten the conversion",
    _AZ,
    Severity.INFO,
    0.0,
    description=(
        "The code uses Oracle built-ins that the orafce extension implements. orafce is on "
        "the Flexible Server allow-list."
    ),
    remediation=(
        "Allow-list orafce in azure.extensions and CREATE EXTENSION orafce. Use it as a "
        "bridge to get code running, then decide whether to keep the dependency; plain "
        "PostgreSQL equivalents are easier for the next team to maintain."
    ),
    extensions=("orafce",),
)
def _orafce_candidate(inventory: Inventory) -> Iterator[Hit]:
    found: set[str] = set()
    units = 0
    for unit in inventory.code_units:
        names = {
            (m.group(1) or m.group(2)).upper() for m in _ORAFCE_FUNCTIONS.finditer(unit.cleaned)
        }
        if names:
            units += 1
            found |= names
    if found:
        yield Hit(
            inventory.metadata.db_name or "(database)",
            "DATABASE",
            f"{units} code object(s) use {', '.join(sorted(found))}",
        )


_EXTERNAL_CALL_SPEC = re.compile(r"\bLANGUAGE\s+(?:JAVA|C)\b|\bAS\s+EXTERNAL\b", re.IGNORECASE)


@rule(
    "O2PG-AZ-006",
    "Java stored procedures and external C procedures",
    _AZ,
    Severity.BLOCKER,
    4.0,
    description=(
        "Java sources and call specs for Java or C routines. Azure Flexible Server does not "
        "support untrusted languages or loading your own shared libraries."
    ),
    remediation=(
        "Rewrite in PL/pgSQL if the logic is small, otherwise move it into an application "
        "service the database no longer calls directly."
    ),
)
def _external_code(inventory: Inventory) -> Iterator[Hit]:
    for unit in inventory.code_units:
        if unit.kind.startswith("JAVA"):
            yield Hit(unit.qualified, unit.kind, "Java source")
        elif unit.plsql and _EXTERNAL_CALL_SPEC.search(unit.cleaned):
            yield Hit(unit.qualified, unit.kind, "external call spec")
