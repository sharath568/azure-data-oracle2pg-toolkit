"""Model providers: Azure OpenAI, and replay from saved answers.

``azure-openai`` calls the Azure OpenAI chat completions REST API with the
standard library (no SDK), structured JSON output, and retries on 429/5xx.
It reads the same environment variables as azure-data-nl2sql:

    AZURE_OPENAI_ENDPOINT          https://<name>.openai.azure.com
    AZURE_OPENAI_API_KEY           key, or AZURE_OPENAI_AD_TOKEN (Entra ID bearer token)
    AZURE_OPENAI_CHAT_DEPLOYMENT   deployment name (default gpt-5-mini)
    AZURE_OPENAI_CHAT_MODEL        model name for pricing, if it differs from the deployment
    AZURE_OPENAI_API_VERSION       default 2024-10-21 (2025-04-01-preview for reasoning models)
    AZURE_OPENAI_REASONING_EFFORT  reasoning models: minimal | low | medium | high (default medium)

``replay`` reads answers from a directory, one JSON file per unit, so a run can
be repeated exactly (and tested) without a model. ``--record DIR`` on
``ai-convert`` saves every live answer in that format.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from o2pg.ai.prompt import RESPONSE_SCHEMA, Answer, Request

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
DEFAULT_DEPLOYMENT = "gpt-5-mini"
DEFAULT_API_VERSION = "2024-10-21"
REASONING_API_VERSION = "2025-04-01-preview"
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# USD per 1M tokens (input, output): published list prices, checked 2025-10 for
# azure-data-nl2sql. Global Standard deployments are in this range; regional and
# Data Zone deployments can cost a little more. Check the Azure OpenAI pricing page.
PRICES_CHECKED = "2025-10"
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5": (1.25, 10.00),
}


class ProviderError(RuntimeError):
    """The provider could not produce an answer."""


@dataclass
class Usage:
    """Token counts for one call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )


@dataclass
class Generation:
    """One model answer (or the reason there is none)."""

    answer: Answer | None
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    error: str = ""
    raw: str = ""


def cost_usd(usage: Usage, model: str | None) -> float | None:
    """Estimated cost of ``usage`` at list price, or None for an unknown model."""
    if not model:
        return None
    name = model.lower()
    for known in sorted(PRICES_PER_1M, key=len, reverse=True):
        if name.startswith(known):
            price_in, price_out = PRICES_PER_1M[known]
            return (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6
    return None


def is_reasoning_model(name: str) -> bool:
    """gpt-5 and o-series models take max_completion_tokens and reasoning_effort."""
    return name.lower().startswith(REASONING_PREFIXES)


def parse_content(content: str, model: str, usage: Usage, latency: float) -> Generation:
    """Turn the message content into a Generation (an error if it is not valid JSON)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return Generation(None, model, usage, latency, "model did not return valid JSON", content)
    if not isinstance(data, dict):
        return Generation(
            None, model, usage, latency, "model did not return a JSON object", content
        )
    answer = Answer.from_json(data)
    if not answer.sql:
        return Generation(None, model, usage, latency, "model returned no SQL", content)
    return Generation(answer, model, usage, latency, raw=content)


class Provider:
    """Base class: ``complete`` one request."""

    name = "base"
    model = ""

    def complete(self, request: Request) -> Generation:  # pragma: no cover - interface
        raise NotImplementedError


Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


def _urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


class AzureOpenAIProvider(Provider):
    """Azure OpenAI chat completions with JSON-schema output."""

    name = "azure-openai"

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        ad_token: str | None = None,
        deployment: str = DEFAULT_DEPLOYMENT,
        api_version: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        timeout_s: float = 180.0,
        max_retries: int = 3,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not endpoint:
            raise ProviderError("AZURE_OPENAI_ENDPOINT is not set")
        if not api_key and not ad_token:
            raise ProviderError("set AZURE_OPENAI_API_KEY or AZURE_OPENAI_AD_TOKEN")
        self.endpoint = endpoint.rstrip("/")
        self.deployment = deployment
        self.model = model or deployment
        self.reasoning = is_reasoning_model(self.model)
        self.api_version = api_version or (
            REASONING_API_VERSION if self.reasoning else DEFAULT_API_VERSION
        )
        self.reasoning_effort = reasoning_effort or "medium"
        self.max_tokens = max_tokens or (16000 if self.reasoning else 4000)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._transport = transport or _urllib_transport
        self._sleep = sleep
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["api-key"] = api_key
        else:
            self.headers["Authorization"] = f"Bearer {ad_token}"

    @classmethod
    def from_env(cls, **overrides: Any) -> AzureOpenAIProvider:
        """Build from AZURE_OPENAI_* environment variables (overrides win)."""
        kwargs: dict[str, Any] = {
            "endpoint": os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            "api_key": os.environ.get("AZURE_OPENAI_API_KEY") or None,
            "ad_token": os.environ.get("AZURE_OPENAI_AD_TOKEN") or None,
            "deployment": os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT") or DEFAULT_DEPLOYMENT,
            "api_version": os.environ.get("AZURE_OPENAI_API_VERSION") or None,
            "model": os.environ.get("AZURE_OPENAI_CHAT_MODEL") or None,
            "reasoning_effort": os.environ.get("AZURE_OPENAI_REASONING_EFFORT") or None,
        }
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kwargs)

    @property
    def url(self) -> str:
        """The chat completions URL for the deployment."""
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
            f"?api-version={self.api_version}"
        )

    def body(self, request: Request) -> dict[str, Any]:
        """The JSON request body."""
        body: dict[str, Any] = {
            "messages": request.messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "o2pg_conversion",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            },
        }
        if self.reasoning:
            body["max_completion_tokens"] = self.max_tokens
            body["reasoning_effort"] = self.reasoning_effort
        else:
            body["temperature"] = 0
            body["max_tokens"] = self.max_tokens
        return body

    def _post(self, payload: bytes) -> dict[str, Any]:
        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                status, headers, data = self._transport(
                    self.url, self.headers, payload, self.timeout_s
                )
            except OSError as exc:
                last = f"network error: {exc}"
                status, headers, data = 0, {}, b""
            if 0 < status < 400:
                return json.loads(data.decode("utf-8"))
            text = data.decode("utf-8", "replace")[:300]
            if status and status not in RETRY_STATUS:
                raise ProviderError(f"Azure OpenAI returned {status}: {text}")
            if status:
                last = f"Azure OpenAI returned {status}: {text}"
            if attempt < self.max_retries:
                retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else 2.0**attempt
                except ValueError:
                    delay = 2.0**attempt
                self._sleep(min(delay, 30.0))
        raise ProviderError(f"request failed after {self.max_retries + 1} tries: {last}")

    def complete(self, request: Request) -> Generation:
        """Ask the deployment for one answer."""
        payload = json.dumps(self.body(request)).encode("utf-8")
        start = time.perf_counter()
        data = self._post(payload)
        latency = (time.perf_counter() - start) * 1000
        raw_usage = data.get("usage") or {}
        usage = Usage(
            int(raw_usage.get("prompt_tokens", 0)), int(raw_usage.get("completion_tokens", 0))
        )
        model = str(data.get("model") or self.model)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from exc
        if message.get("refusal"):
            return Generation(None, model, usage, latency, f"model refused: {message['refusal']}")
        return parse_content(message.get("content") or "", model, usage, latency)


def unit_filename(unit: str) -> str:
    """File name for a unit's saved answers, e.g. ``lab.highest_paid.json``."""
    base = re.sub(r"\s*\(trigger on [^)]*\)", "", unit.lower())
    return re.sub(r"[^a-z0-9_.$#-]", "_", base) + ".json"


class ReplayProvider(Provider):
    """Answers read from ``DIR/<unit>.json``: ``{"attempts": [answer, ...]}``.

    Each attempt is a model answer object (the RESPONSE_SCHEMA shape), optionally
    with ``"usage"`` and ``"model"`` keys as saved by ``--record``.
    """

    name = "replay"

    def __init__(self, directory: Path, model: str = "replay") -> None:
        if not directory.is_dir():
            raise ProviderError(f"replay directory {directory} does not exist")
        self.directory = directory
        self.model = model

    def complete(self, request: Request) -> Generation:
        """Return the saved answer for this unit and attempt number."""
        path = self.directory / unit_filename(request.unit)
        if not path.exists():
            return Generation(None, self.model, error=f"no saved answer ({path.name})")
        saved = json.loads(path.read_text(encoding="utf-8"))
        attempts = saved.get("attempts") or []
        if request.attempt > len(attempts):
            return Generation(
                None, self.model, error=f"no saved answer for attempt {request.attempt}"
            )
        item = dict(attempts[request.attempt - 1])
        usage_raw = item.pop("usage", {}) or {}
        model = str(item.pop("model", None) or saved.get("model") or self.model)
        usage = Usage(
            int(usage_raw.get("prompt_tokens", 0)), int(usage_raw.get("completion_tokens", 0))
        )
        return parse_content(json.dumps(item), model, usage, 0.0)


class RecordingProvider(Provider):
    """Wrap a provider and save every answer under ``directory`` in replay format."""

    def __init__(self, inner: Provider, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.name = inner.name
        self.model = inner.model
        directory.mkdir(parents=True, exist_ok=True)

    def complete(self, request: Request) -> Generation:
        """Ask the wrapped provider, then append the answer to the unit's file."""
        generation = self.inner.complete(request)
        path = self.directory / unit_filename(request.unit)
        saved: dict[str, Any] = {
            "unit": request.unit,
            "prompt": request.fingerprint,
            "attempts": [],
        }
        if request.attempt > 1 and path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
        entry: dict[str, Any]
        if generation.answer is not None:
            entry = generation.answer.to_json()
        else:
            entry = {"sql": "", "error": generation.error, "raw": generation.raw[:2000]}
        entry["model"] = generation.model
        entry["usage"] = {
            "prompt_tokens": generation.usage.prompt_tokens,
            "completion_tokens": generation.usage.completion_tokens,
        }
        saved["attempts"] = [*saved.get("attempts", [])[: request.attempt - 1], entry]
        path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        return generation


def get_provider(name: str, replay_dir: Path | None = None, **overrides: Any) -> Provider:
    """``azure-openai`` or ``replay``."""
    lowered = name.lower()
    if lowered in {"azure-openai", "azure", "aoai"}:
        return AzureOpenAIProvider.from_env(**overrides)
    if lowered == "replay":
        if replay_dir is None:
            raise ProviderError("--provider replay needs --replay DIR")
        return ReplayProvider(replay_dir)
    raise ProviderError(f"unknown provider {name!r}; choose azure-openai or replay")
