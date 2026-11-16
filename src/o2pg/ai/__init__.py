"""AI-assisted conversion of the units ``o2pg convert`` could not finish (``o2pg ai-convert``).

The mechanical converter stays the source of truth for what it can do. This
package sends only the units with review points to a model, together with the
Oracle source, the mechanical draft and the review points, and accepts nothing
until it has passed three gates: static checks, a compile in PostgreSQL, and
behaviour equivalence against results recorded on Oracle (``o2pg equiv``).
"""

from __future__ import annotations

from o2pg.ai.gates import GateResult, Signature, oracle_leftovers, signatures, static_gate
from o2pg.ai.pipeline import (
    COMPILES,
    ERROR,
    REJECTED,
    SKIPPED,
    STATIC_ONLY,
    UNRESOLVED,
    VERIFIED,
    AiRun,
    GateDatabase,
    UnitOutcome,
    oracle_sources,
    run,
    summary_line,
)
from o2pg.ai.prompt import Answer, Request, build_request
from o2pg.ai.providers import (
    AzureOpenAIProvider,
    Provider,
    ProviderError,
    RecordingProvider,
    ReplayProvider,
    get_provider,
)
from o2pg.ai.report import report_markdown, write_outputs
from o2pg.ai.scope import Scope, scope_for

__all__ = [
    "COMPILES",
    "ERROR",
    "REJECTED",
    "SKIPPED",
    "STATIC_ONLY",
    "UNRESOLVED",
    "VERIFIED",
    "AiRun",
    "Answer",
    "AzureOpenAIProvider",
    "GateDatabase",
    "GateResult",
    "Provider",
    "ProviderError",
    "RecordingProvider",
    "ReplayProvider",
    "Request",
    "Scope",
    "Signature",
    "UnitOutcome",
    "build_request",
    "get_provider",
    "oracle_leftovers",
    "oracle_sources",
    "report_markdown",
    "run",
    "scope_for",
    "signatures",
    "static_gate",
    "summary_line",
    "write_outputs",
]
