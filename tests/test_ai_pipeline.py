"""ai-convert without a database: scope, prompt, providers, static-only runs, report, CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from o2pg import ai
from o2pg.ai.prompt import RESPONSE_SCHEMA, build_request, relevant_ddl, retry_request
from o2pg.ai.providers import (
    AzureOpenAIProvider,
    RecordingProvider,
    ReplayProvider,
    Usage,
    cost_usd,
)
from o2pg.cli import main
from o2pg.convert import convert_sql
from o2pg.convert.engine import split_script

AI = Path(__file__).parent / "fixtures" / "ai"
LAB_SQL = (AI / "lab.sql").read_text(encoding="utf-8")
LAB = convert_sql(LAB_SQL)
SOURCES = ai.oracle_sources(split_script(LAB_SQL))
PG_DDL = (AI / "pg_schema.sql").read_text(encoding="utf-8")


def _unit(name: str):
    return next(u for u in LAB.units if u.name == name)


# ---------------------------------------------------------------- scope


def test_scope_sends_code_rewrites_and_skips_design_decisions() -> None:
    scopes = {u.name: ai.scope_for(u) for u in LAB.units}
    assert not scopes["lab.log_event"].eligible
    assert "autonomous transaction" in scopes["lab.log_event"].reason
    assert sum(s.eligible for s in scopes.values()) == 13
    assert scopes["lab.hired_since"].contract_change_allowed
    assert not scopes["lab.highest_paid"].contract_change_allowed


def test_blocking_only_skips_review_only_units() -> None:
    assert not ai.scope_for(_unit("lab.avg_split"), include_review=False).eligible
    assert ai.scope_for(_unit("lab.nth_pos"), include_review=False).eligible


def test_scope_skips_clean_units_and_package_state() -> None:
    clean = convert_sql(
        "CREATE OR REPLACE FUNCTION app.one RETURN NUMBER IS BEGIN RETURN 1; END one;\n/\n"
    )
    assert ai.scope_for(clean.units[0]).reason == "no review points"


# ---------------------------------------------------------------- prompt


def test_prompt_carries_source_draft_review_points_and_only_relevant_ddl() -> None:
    unit = _unit("lab.depts_without_earner")
    request = build_request(
        unit, SOURCES[("LAB", "DEPTS_WITHOUT_EARNER")], PG_DDL, ("O2PG-RV-007",)
    )
    system, user = request.messages
    assert system["role"] == "system"
    assert "(+) outer join" in system["content"]
    content = user["content"]
    assert "e.salary (+) > p_min" in content  # Oracle source
    assert "CREATE OR REPLACE FUNCTION lab.depts_without_earner" in content  # draft
    assert "O2PG-RV-007 Oracle outer join operator (blocking)" in content
    assert "CREATE TABLE lab.dept" in content
    assert "event_log" not in content


def test_relevant_ddl_matches_whole_words() -> None:
    ddl = "CREATE TABLE s.emp (a int);\nCREATE TABLE s.emp_hist (a int);\n"
    assert relevant_ddl(ddl, "select * from emp") == "CREATE TABLE s.emp (a int);"


def test_retry_request_appends_the_previous_answer_and_the_failure() -> None:
    unit = _unit("lab.nth_pos")
    first = build_request(unit, "x", "", ())
    again = retry_request(first, ai.Answer(sql="SELECT 1"), "compile", "syntax error")
    assert again.attempt == 2
    assert [m["role"] for m in again.messages] == ["system", "user", "assistant", "user"]
    assert "failed the compile check" in again.messages[-1]["content"]
    assert again.fingerprint == first.fingerprint


def test_response_schema_is_strict() -> None:
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    assert set(RESPONSE_SCHEMA["required"]) == set(RESPONSE_SCHEMA["properties"])


# ---------------------------------------------------------------- providers


class _Transport:
    def __init__(self, replies: list[tuple[int, dict[str, str], dict | str]]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict[str, str], dict]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.calls.append((url, headers, json.loads(body)))
        status, reply_headers, payload = self.replies.pop(0)
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return status, reply_headers, data.encode()


def _completion(content: dict | str, model: str = "gpt-5-mini-2025-08-07") -> dict:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "model": model,
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        "choices": [{"message": {"content": text}}],
    }


ANSWER = {
    "sql": "CREATE OR REPLACE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql;",
    "changes": [],
    "signature_changed": False,
    "signature_reason": "",
    "unresolved": [],
    "assumptions": [],
}


def test_azure_request_for_a_reasoning_model() -> None:
    transport = _Transport([(200, {}, _completion(ANSWER))])
    provider = AzureOpenAIProvider(
        "https://x.openai.azure.com/", api_key="k", deployment="gpt-5-mini", transport=transport
    )
    generation = provider.complete(build_request(_unit("lab.nth_pos"), "x", "", ()))
    url, headers, body = transport.calls[0]
    assert url == (
        "https://x.openai.azure.com/openai/deployments/gpt-5-mini/chat/completions"
        "?api-version=2025-04-01-preview"
    )
    assert headers["api-key"] == "k"
    assert body["reasoning_effort"] == "medium"
    assert "temperature" not in body
    assert body["response_format"]["json_schema"]["strict"] is True
    assert generation.answer is not None
    assert generation.usage == Usage(1000, 500)
    assert generation.model == "gpt-5-mini-2025-08-07"


def test_azure_request_for_a_chat_model_with_entra_token() -> None:
    transport = _Transport([(200, {}, _completion(ANSWER, "gpt-4.1-mini"))])
    provider = AzureOpenAIProvider(
        "https://x", ad_token="t", deployment="gpt-4.1-mini", transport=transport
    )
    provider.complete(build_request(_unit("lab.nth_pos"), "x", "", ()))
    url, headers, body = transport.calls[0]
    assert url.endswith("api-version=2024-10-21")
    assert headers["Authorization"] == "Bearer t"
    assert body["temperature"] == 0


def test_azure_retries_on_429_and_honours_retry_after() -> None:
    sleeps: list[float] = []
    transport = _Transport([(429, {"Retry-After": "7"}, "busy"), (200, {}, _completion(ANSWER))])
    provider = AzureOpenAIProvider(
        "https://x", api_key="k", transport=transport, sleep=sleeps.append
    )
    assert provider.complete(build_request(_unit("lab.nth_pos"), "x", "", ())).answer
    assert sleeps == [7.0]


def test_azure_errors() -> None:
    bad = AzureOpenAIProvider("https://x", api_key="k", transport=_Transport([(401, {}, "no")]))
    with pytest.raises(ai.ProviderError, match="401"):
        bad.complete(build_request(_unit("lab.nth_pos"), "x", "", ()))
    junk = AzureOpenAIProvider(
        "https://x", api_key="k", transport=_Transport([(200, {}, _completion("not json"))])
    )
    generation = junk.complete(build_request(_unit("lab.nth_pos"), "x", "", ()))
    assert generation.answer is None
    assert generation.error == "model did not return valid JSON"
    with pytest.raises(ai.ProviderError, match="ENDPOINT"):
        AzureOpenAIProvider("", api_key="k")


def test_cost_estimate() -> None:
    assert cost_usd(Usage(1_000_000, 1_000_000), "gpt-5-mini-2025-08-07") == pytest.approx(2.25)
    assert cost_usd(Usage(1, 1), "something-else") is None


def test_replay_and_recording_round_trip(tmp_path: Path) -> None:
    replay = ReplayProvider(AI / "responses" / "reference")
    recorder = RecordingProvider(replay, tmp_path)
    request = build_request(_unit("lab.nth_pos"), "x", "", ())
    first = recorder.complete(request)
    assert first.answer is not None
    saved = json.loads((tmp_path / "lab.nth_pos.json").read_text())
    assert saved["unit"] == "lab.nth_pos"
    assert saved["attempts"][0]["sql"] == first.answer.sql
    again = ReplayProvider(tmp_path).complete(request)
    assert again.answer is not None
    assert again.answer.sql == first.answer.sql
    missing = ReplayProvider(tmp_path).complete(retry_request(request, first.answer, "x", "y"))
    assert missing.answer is None
    assert "attempt 2" in missing.error


# ---------------------------------------------------------------- static-only runs


def _run(directory: str, **kwargs: Any) -> ai.AiRun:
    provider = ReplayProvider(AI / "responses" / directory)
    return ai.run(LAB, SOURCES, provider, ddl=PG_DDL, **kwargs)


def test_reference_answers_pass_the_static_gate() -> None:
    run = _run("reference")
    statuses = {o.name: o.status for o in run.outcomes}
    assert statuses.pop("lab.log_event") == ai.SKIPPED
    assert set(statuses.values()) == {ai.STATIC_ONLY}
    hired = next(o for o in run.outcomes if o.name == "lab.hired_since")
    assert hired.contract_change


def test_flawed_answers_without_a_database() -> None:
    run = _run("flawed")
    by_name = {o.name: o for o in run.outcomes}
    assert by_name["lab.highest_paid"].status == ai.REJECTED
    assert "signature changed" in by_name["lab.highest_paid"].reason
    assert by_name["lab.nth_pos"].status == ai.REJECTED
    assert "INSTR()" in by_name["lab.nth_pos"].reason
    assert by_name["lab.code_kind"].status == ai.UNRESOLVED
    hired = by_name["lab.hired_since"]
    assert hired.status == ai.STATIC_ONLY
    assert hired.attempts == 2
    assert "DROP FUNCTION" in hired.gates[0].detail
    # Without a database the missing parenthesis in chain_of_command goes unnoticed:
    # that is the compile gate's job.
    assert by_name["lab.chain_of_command"].status == ai.STATIC_ONLY


def test_one_attempt_means_no_retry() -> None:
    run = _run("flawed", max_attempts=1, only="lab.hired_since")
    (outcome,) = run.outcomes
    assert outcome.status == ai.REJECTED
    assert outcome.attempts == 1


def test_report_and_files(tmp_path: Path) -> None:
    run = _run("flawed")
    paths = ai.write_outputs(run, tmp_path)
    names = {p.name for p in paths}
    assert {"ai-report.md", "ai-report.json", "lab.hired_since.sql"} <= names
    assert "verified.sql" not in names  # nothing is verified without a database
    report = (tmp_path / "ai-report.md").read_text()
    assert "No database was given" in report
    assert "## Needs a person" in report
    assert "`lab.log_event`: O2PG-RV-002 (autonomous transaction)" in report
    assert "## Fixed after feedback" in report
    data = json.loads((tmp_path / "ai-report.json").read_text())
    assert data["model"] == "flawed"
    candidate = (tmp_path / "lab.hired_since.sql").read_text()
    assert "SIGNATURE CHANGED" in candidate
    assert "Status: static only" in candidate


# ---------------------------------------------------------------- CLI


def test_cli_ai_convert_with_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("O2PG_AI_DSN", raising=False)
    out = tmp_path / "ai"
    result = CliRunner().invoke(
        main,
        [
            "ai-convert",
            str(AI / "lab.sql"),
            "-o",
            str(out),
            "--provider",
            "replay",
            "--replay",
            str(AI / "responses" / "reference"),
        ],
    )
    assert result.exit_code == 1  # static only is not verified
    assert "13 static only" in result.output
    assert (out / "ai-report.md").exists()


def test_cli_ai_convert_needs_a_replay_directory(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main, ["ai-convert", str(AI / "lab.sql"), "-o", str(tmp_path), "--provider", "replay"]
    )
    assert result.exit_code != 0
    assert "--replay DIR" in result.output


def test_cli_ai_convert_azure_needs_an_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = CliRunner().invoke(main, ["ai-convert", str(AI / "lab.sql"), "-o", str(tmp_path)])
    assert result.exit_code != 0
    assert "AZURE_OPENAI_ENDPOINT" in result.output
