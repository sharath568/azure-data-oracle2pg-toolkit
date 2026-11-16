"""Run the model over the units the mechanical converter could not finish.

For each unit: decide the scope, build the prompt, ask the provider, and pass
the answer through the gates. A static or compile failure is fed back to the
model for another attempt (``max_attempts`` in total). A behaviour failure is
not fed back by default: telling the model which test failed invites answers
that fit the tests rather than Oracle, so the unit is reported as rejected and a
person decides.

Nothing here edits the mechanical output. Candidates are written to their own
files and the report says which ones earned trust.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from o2pg.ai.gates import (
    BEHAVIOUR,
    COMPILE,
    FORMAT,
    STATIC,
    GateResult,
    Signature,
    signatures,
    static_gate,
)
from o2pg.ai.prompt import PROMPT_VERSION, Answer, build_request, retry_request
from o2pg.ai.providers import Provider, ProviderError, Usage, cost_usd
from o2pg.ai.scope import scope_for
from o2pg.convert import ConversionResult, ConvertedObject, ConvertedUnit
from o2pg.equiv import CaseFile, CaseResult, PostgresRunner, compare, prepare_postgres
from o2pg.inventory import SourceObject

VERIFIED = "verified"
COMPILES = "compiles"
STATIC_ONLY = "static only"
UNRESOLVED = "unresolved"
REJECTED = "rejected"
SKIPPED = "skipped"
ERROR = "error"
STATUS_ORDER = [VERIFIED, COMPILES, STATIC_ONLY, UNRESOLVED, REJECTED, ERROR, SKIPPED]


@dataclass
class UnitOutcome:
    """Everything that happened to one unit."""

    name: str
    kind: str
    object: str
    rules: tuple[str, ...]
    status: str
    reason: str = ""
    attempts: int = 0
    gates: list[GateResult] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)
    contract_change: bool = False
    answer: Answer | None = None
    preamble: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def cases_passed(self) -> int:
        """Behaviour cases that matched Oracle."""
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed_gate(self) -> str:
        """The gate that rejected the last answer, if any."""
        failed = [g.gate for g in self.gates if not g.passed]
        return failed[-1] if failed else ""

    @property
    def sent(self) -> bool:
        """True when the unit went to the model."""
        return self.status != SKIPPED


@dataclass
class AiRun:
    """All unit outcomes plus run-level facts for the report."""

    outcomes: list[UnitOutcome]
    provider: str
    model: str
    max_attempts: int
    database: bool
    cases_file: str = ""
    prompt_version: str = PROMPT_VERSION
    setup_notes: list[str] = field(default_factory=list)

    @property
    def usage(self) -> Usage:
        """Total tokens."""
        total = Usage()
        for outcome in self.outcomes:
            total = total + outcome.usage
        return total

    @property
    def cost(self) -> float | None:
        """Estimated cost at list price."""
        return cost_usd(self.usage, self.model)

    def count(self, status: str) -> int:
        """Units with ``status``."""
        return sum(1 for o in self.outcomes if o.status == status)


# --------------------------------------------------------------------------------------
# Scratch database
# --------------------------------------------------------------------------------------


class GateDatabase:
    """A throwaway PostgreSQL database for the compile and behaviour gates."""

    def __init__(self, admin_dsn: str, keep: bool = False) -> None:
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        self._psycopg = psycopg
        self.admin_dsn = admin_dsn
        self.keep = keep
        self.name = f"o2pg_ai_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name)))
        self.dsn = make_conninfo(admin_dsn, dbname=self.name)
        self.conn = psycopg.connect(self.dsn)

    def setup(self, files: Iterable[Path] = (), ddl: str = "") -> list[str]:
        """Load setup files (must succeed) and best-effort DDL. Returns notes."""
        notes = []
        prepare_postgres(self.conn, list(files))
        if ddl.strip():
            from o2pg.ai.gates import split_statements

            failed = 0
            for statement in split_statements(ddl):
                try:
                    with self.conn.cursor() as cur:
                        cur.execute(statement)
                    self.conn.commit()
                except self._psycopg.Error:
                    self.conn.rollback()
                    failed += 1
            if failed:
                notes.append(f"{failed} DDL statement(s) did not load in the scratch database")
        return notes

    def load_units(self, pairs: Iterable[tuple[ConvertedObject, ConvertedUnit]]) -> int:
        """Create mechanical units so candidates can call their siblings. Returns failures."""
        failures = 0
        for obj, unit in pairs:
            try:
                with self.conn.cursor() as cur:
                    for statement in obj.preamble:
                        cur.execute(statement)
                    cur.execute(unit.sql)
                self.conn.commit()
            except self._psycopg.Error:
                self.conn.rollback()
                failures += 1
        return failures

    def compile(self, preamble: list[str], candidate: str) -> GateResult:
        """CREATE the candidate with check_function_bodies on, then roll back."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET LOCAL check_function_bodies = on")
                cur.execute("SET LOCAL statement_timeout = 10000")
                for statement in preamble:
                    cur.execute(statement)
                cur.execute(candidate)
            return GateResult(COMPILE, True)
        except self._psycopg.Error as exc:
            diag = getattr(exc, "diag", None)
            message = (getattr(diag, "message_primary", None) or str(exc)).strip()
            position = getattr(diag, "context", None) or ""
            detail = message + (f" ({position.strip().splitlines()[0]})" if position else "")
            return GateResult(COMPILE, False, detail)
        finally:
            self.conn.rollback()

    def runner(self, search_path: str) -> PostgresRunner:
        """A case runner on this database."""
        return PostgresRunner(self.conn, search_path)

    def close(self) -> None:
        """Close and drop the database (unless ``keep``)."""
        from psycopg import sql

        self.conn.close()
        if self.keep:
            return
        with self._psycopg.connect(self.admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(self.name))
            )


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def oracle_sources(sources: Iterable[SourceObject]) -> dict[tuple[str, str], str]:
    """Oracle text per object (spec before body), keyed by (OWNER, NAME)."""
    order = {"PACKAGE": 0, "TYPE": 0, "PACKAGE BODY": 1, "TYPE BODY": 1}
    grouped: dict[tuple[str, str], list[SourceObject]] = {}
    for source in sources:
        grouped.setdefault((source.owner.upper(), source.name.upper()), []).append(source)
    return {
        key: "\n\n".join(s.text.strip() for s in sorted(parts, key=lambda s: order.get(s.type, 0)))
        for key, parts in grouped.items()
    }


def expected_signature(unit: ConvertedUnit) -> Signature | None:
    """The mechanical draft's header (the contract the answer must keep)."""
    sigs = signatures(unit.sql)
    return sigs[0] if sigs else None


def run(
    result: ConversionResult,
    sources: dict[tuple[str, str], str],
    provider: Provider,
    *,
    ddl: str = "",
    database: GateDatabase | None = None,
    cases: CaseFile | None = None,
    max_attempts: int = 2,
    include_review: bool = True,
    behaviour_feedback: bool = False,
    only: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> AiRun:
    """Send every eligible unit to ``provider`` and gate the answers."""
    outcomes = []
    search_path = cases.search_path if cases else "public"
    for obj in result.objects:
        for unit in obj.units:
            if only and unit.name.split(" (")[0] != only.lower():
                continue
            outcome = _one_unit(
                obj,
                unit,
                sources.get((obj.owner.upper(), obj.name.upper()), ""),
                provider,
                ddl=ddl,
                database=database,
                cases=cases,
                search_path=search_path,
                max_attempts=max_attempts,
                include_review=include_review,
                behaviour_feedback=behaviour_feedback,
            )
            outcomes.append(outcome)
            if progress:
                progress(f"{outcome.name}: {outcome.status}")
    return AiRun(
        outcomes=outcomes,
        provider=provider.name,
        model=_run_model(outcomes, provider),
        max_attempts=max_attempts,
        database=database is not None,
        cases_file=str(cases.path) if cases else "",
    )


def _run_model(outcomes: list[UnitOutcome], provider: Provider) -> str:
    models = [o.model for o in outcomes if o.model]
    return models[0] if models else provider.model


def _one_unit(
    obj: ConvertedObject,
    unit: ConvertedUnit,
    oracle_text: str,
    provider: Provider,
    *,
    ddl: str,
    database: GateDatabase | None,
    cases: CaseFile | None,
    search_path: str,
    max_attempts: int,
    include_review: bool,
    behaviour_feedback: bool,
) -> UnitOutcome:
    scope = scope_for(unit, include_review)
    outcome = UnitOutcome(
        name=unit.name,
        kind=unit.kind,
        object=obj.qualified,
        rules=scope.rules,
        status=SKIPPED,
        reason=scope.reason,
        preamble=list(obj.preamble),
    )
    if not scope.eligible:
        return outcome
    expected = expected_signature(unit)
    is_trigger = unit.kind == "TRIGGER"
    routine = unit.name.split(" (")[0]
    unit_cases = cases.for_unit(routine) if cases else []
    request = build_request(unit, oracle_text, ddl, scope.rules)
    while True:
        outcome.attempts = request.attempt
        try:
            generation = provider.complete(request)
        except ProviderError as exc:
            outcome.status, outcome.reason = ERROR, str(exc)
            return outcome
        outcome.usage = outcome.usage + generation.usage
        outcome.model = generation.model or outcome.model
        answer = generation.answer
        if answer is None:
            if generation.raw:
                outcome.gates.append(GateResult(FORMAT, False, generation.error, request.attempt))
                if request.attempt < max_attempts:
                    previous = Answer(sql=generation.raw[:4000])
                    request = retry_request(request, previous, FORMAT, generation.error)
                    continue
            outcome.status, outcome.reason = ERROR, generation.error
            return outcome
        outcome.answer = answer
        check = static_gate(
            answer.sql, expected, is_trigger, scope.contract_change_allowed, answer.unresolved
        )
        check.result.attempt = request.attempt
        outcome.gates.append(check.result)
        outcome.contract_change = check.contract_change
        if not check.result.passed:
            if request.attempt < max_attempts:
                request = retry_request(request, answer, STATIC, check.result.detail)
                continue
            outcome.status, outcome.reason = REJECTED, f"static: {check.result.detail}"
            return outcome
        if database is not None:
            compiled = database.compile(outcome.preamble, answer.sql)
            compiled.attempt = request.attempt
            outcome.gates.append(compiled)
            if not compiled.passed:
                if request.attempt < max_attempts:
                    request = retry_request(request, answer, COMPILE, compiled.detail)
                    continue
                outcome.status, outcome.reason = REJECTED, f"compile: {compiled.detail}"
                return outcome
        if answer.unresolved or check.leftover_markers:
            outcome.status = UNRESOLVED
            outcome.reason = "; ".join(answer.unresolved) or "review markers left in place"
            return outcome
        if database is None:
            outcome.status = STATIC_ONLY
            return outcome
        if not unit_cases:
            outcome.status = COMPILES
            outcome.reason = "no equivalence cases for this routine"
            return outcome
        runner = database.runner(search_path)
        data = cases.data if cases else []
        before = [*outcome.preamble, answer.sql]
        outcome.cases = [
            CaseResult(c, o, compare(c, o))
            for c, o in ((c, runner.run(c, data, before)) for c in unit_cases)
        ]
        failed = [c for c in outcome.cases if not c.passed]
        if not failed:
            outcome.gates.append(
                GateResult(BEHAVIOUR, True, f"{len(outcome.cases)} cases", request.attempt)
            )
            outcome.status = VERIFIED
            return outcome
        detail = "; ".join(f"{c.case.id}: {', '.join(c.diffs)}" for c in failed)
        outcome.gates.append(GateResult(BEHAVIOUR, False, detail, request.attempt))
        if behaviour_feedback and request.attempt < max_attempts:
            request = retry_request(request, answer, BEHAVIOUR, detail)
            outcome.cases = []
            continue
        outcome.status = REJECTED
        outcome.reason = (
            f"behaviour: {len(failed)} of {len(outcome.cases)} cases differ from Oracle"
        )
        return outcome


def executable_pairs(result: ConversionResult) -> list[tuple[ConvertedObject, ConvertedUnit]]:
    """Mechanical units that should CREATE as they are (for sibling calls)."""
    return [(o, u) for o in result.objects for u in o.units if u.executable]


def summary_line(run_: AiRun) -> str:
    """One line for the terminal."""
    sent = [o for o in run_.outcomes if o.sent]
    parts = [f"{len(sent)} of {len(run_.outcomes)} units sent to {run_.model or run_.provider}"]
    for status in (VERIFIED, COMPILES, STATIC_ONLY, UNRESOLVED, REJECTED, ERROR):
        if run_.count(status):
            parts.append(f"{run_.count(status)} {status}")
    usage = run_.usage
    tokens = f"{usage.prompt_tokens + usage.completion_tokens} tokens"
    cost = run_.cost
    parts.append(tokens + (f" (~${cost:.4f})" if cost is not None else ""))
    return ", ".join(parts)


def as_dict(run_: AiRun) -> dict[str, Any]:
    """JSON-ready form of the run."""
    return {
        "provider": run_.provider,
        "model": run_.model,
        "prompt_version": run_.prompt_version,
        "max_attempts": run_.max_attempts,
        "database_gates": run_.database,
        "cases_file": run_.cases_file,
        "usage": {
            "prompt_tokens": run_.usage.prompt_tokens,
            "completion_tokens": run_.usage.completion_tokens,
        },
        "estimated_cost_usd": run_.cost,
        "units": [
            {
                "unit": o.name,
                "kind": o.kind,
                "object": o.object,
                "status": o.status,
                "reason": o.reason,
                "review_rules": list(o.rules),
                "attempts": o.attempts,
                "contract_change": o.contract_change,
                "gates": [
                    {"gate": g.gate, "attempt": g.attempt, "passed": g.passed, "detail": g.detail}
                    for g in o.gates
                ],
                "cases": [{"id": c.case.id, "passed": c.passed, "diffs": c.diffs} for c in o.cases],
                "changes": o.answer.changes if o.answer else [],
                "assumptions": o.answer.assumptions if o.answer else [],
                "unresolved": o.answer.unresolved if o.answer else [],
                "signature_reason": o.answer.signature_reason if o.answer else "",
                "usage": {
                    "prompt_tokens": o.usage.prompt_tokens,
                    "completion_tokens": o.usage.completion_tokens,
                },
            }
            for o in run_.outcomes
        ],
    }
