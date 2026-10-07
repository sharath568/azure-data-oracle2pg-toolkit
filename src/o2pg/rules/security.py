"""Security rules (O2PG-SE-xxx): VPD, TDE, grants and definer rights."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator

from o2pg.inventory import Inventory
from o2pg.rules.base import Category, Hit, Severity, pattern_rule, rule

_SE = Category.SECURITY


@rule(
    "O2PG-SE-001",
    "Virtual Private Database (VPD) policies",
    _SE,
    Severity.HIGH,
    3.0,
    description=(
        "VPD policies add predicates to queries through a policy function. PostgreSQL row "
        "level security is the equivalent, but policies are SQL expressions on the table, "
        "not functions returning predicate strings."
    ),
    remediation=(
        "ALTER TABLE ... ENABLE ROW LEVEL SECURITY and CREATE POLICY ... USING (...) per "
        "policy. Move the context values the policy function read from SYS_CONTEXT into "
        "current_setting('app.x'). Remember table owners bypass RLS unless FORCE ROW LEVEL "
        "SECURITY is set."
    ),
)
def _vpd(inventory: Inventory) -> Iterator[Hit]:
    for policy in inventory.policies:
        yield Hit(
            f"{policy.object_owner}.{policy.object_name}",
            "TABLE",
            f"policy {policy.policy_name} -> {policy.function}",
        )


@rule(
    "O2PG-SE-002",
    "TDE column encryption",
    _SE,
    Severity.MEDIUM,
    0.5,
    description=(
        "Transparent Data Encryption at column level. Azure Database for PostgreSQL encrypts "
        "all storage at rest by default (optionally with customer-managed keys in Key "
        "Vault), but has no transparent per-column encryption."
    ),
    remediation=(
        "If at-rest encryption was the requirement, Azure storage encryption covers it. If "
        "the column must stay unreadable to DBAs, encrypt in the application or use pgcrypto "
        "with keys held outside the database."
    ),
    extensions=("pgcrypto",),
)
def _tde_columns(inventory: Inventory) -> Iterator[Hit]:
    for col in inventory.encrypted_columns:
        yield Hit(f"{col.owner}.{col.table_name}.{col.column_name}", "COLUMN", col.encryption_alg)


@rule(
    "O2PG-SE-003",
    "Object privileges granted to PUBLIC",
    _SE,
    Severity.MEDIUM,
    0.25,
    description=(
        "Grants to PUBLIC are visible to every role. Carrying them over keeps an open door "
        "you may not have intended."
    ),
    remediation=(
        "Replace with grants to an explicit role. Also REVOKE CREATE ON SCHEMA public FROM "
        "PUBLIC on older PostgreSQL versions (15+ does this by default)."
    ),
)
def _public_grants(inventory: Inventory) -> Iterator[Hit]:
    for grant in inventory.grants:
        if grant.grantee.upper() == "PUBLIC":
            yield Hit(
                f"{grant.owner} -> PUBLIC",
                "GRANT",
                f"{grant.privilege} on {grant.object_count or '?'} object(s)",
            )


_AUTHID_CURRENT_USER = re.compile(r"\bAUTHID\s+CURRENT_USER\b", re.IGNORECASE)


@rule(
    "O2PG-SE-004",
    "Definer-rights program units",
    _SE,
    Severity.MEDIUM,
    0.25,
    description=(
        "Oracle program units run with the owner's privileges unless declared AUTHID "
        "CURRENT_USER. PostgreSQL functions default to SECURITY INVOKER, the opposite."
    ),
    remediation=(
        "Decide per unit. Where definer rights are really needed, use SECURITY DEFINER and "
        "always pin search_path (SET search_path = schema, pg_temp) to avoid hijacking. "
        "Elsewhere, grant the caller the privileges it needs."
    ),
    ai_risk=(
        "Oracle code runs as its owner by default; PostgreSQL code runs as the caller. "
        "Tell the model which units need SECURITY DEFINER, or permissions will break."
    ),
)
def _definer_rights(inventory: Inventory) -> Iterator[Hit]:
    for unit in inventory.code_units:
        if unit.kind in {"PACKAGE", "PROCEDURE", "FUNCTION"} and not _AUTHID_CURRENT_USER.search(
            unit.cleaned
        ):
            yield Hit(unit.qualified, unit.kind, "AUTHID DEFINER (default)")


pattern_rule(
    "O2PG-SE-005",
    "Fine-grained auditing, redaction and RLS APIs",
    _SE,
    Severity.MEDIUM,
    1.0,
    r"\bDBMS_(?:FGA|REDACT|RLS)\s*\.",
    plsql_only=True,
    description=(
        "Code that manages auditing policies (DBMS_FGA), data redaction (DBMS_REDACT) or VPD "
        "policies (DBMS_RLS) at runtime."
    ),
    remediation=(
        "DBMS_RLS -> CREATE POLICY. DBMS_REDACT -> views or column privileges that mask "
        "data. DBMS_FGA -> statement logging; check whether pgaudit is available for your "
        "server version before planning around it."
    ),
)


@rule(
    "O2PG-SE-006",
    "Object privileges to map to roles",
    _SE,
    Severity.INFO,
    0.1,
    description=(
        "Summary of who holds object privileges on the analysed schemas. Each grantee "
        "becomes a role (or a member of one) in PostgreSQL."
    ),
    remediation=(
        "Create group roles (NOLOGIN) for each grantee pattern, grant privileges to the "
        "group, and use ALTER DEFAULT PRIVILEGES so new objects inherit them."
    ),
)
def _grant_summary(inventory: Inventory) -> Iterator[Hit]:
    by_grantee: dict[str, list[str]] = defaultdict(list)
    for grant in inventory.grants:
        by_grantee[grant.grantee].append(f"{grant.privilege} x{grant.object_count or '?'}")
    for grantee in sorted(by_grantee):
        yield Hit(grantee, "ROLE", ", ".join(sorted(by_grantee[grantee])))
