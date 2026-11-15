"""LLM client interface for the weekly coach.

Only this module is allowed to call Azure OpenAI (stage 4). Tests must use
``FakeLlmClient`` or mock the HTTP layer with ``respx`` -- never a real network call.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx
import openai

from app.config import Settings


@dataclass
class LlmResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str


class LlmError(Exception):
    """Raised for any SDK, HTTP or timeout error from an LLM client."""


class LlmContentFiltered(LlmError):
    """Raised when Azure's Prompt Shields blocks a request (HTTP 400,
    ``code == "content_filter"``). This is a correct, safe outcome -- not a model
    failure -- so callers must treat it as a normal fallback trigger, never as evidence
    the model itself is broken. ``categories`` holds whichever content-filter categories
    Azure reported as detected (e.g. ``{"jailbreak"}``)."""

    def __init__(self, message: str, categories: frozenset[str] = frozenset()) -> None:
        super().__init__(message)
        self.categories = categories


class LlmRateLimited(LlmError):
    """Raised specifically for an Azure 429. Carries the server's suggested wait, in
    seconds, when the response provides one (``retry-after``, ``retry-after-ms`` or any
    ``x-ratelimit-reset-*`` header). ``retry_after_seconds`` is ``None`` when the response
    gave no usable hint, and the caller should pick its own default wait."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _header_seconds(value: str, *, ms: bool = False) -> float | None:
    match = re.match(r"\s*([\d.]+)", value)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    return number / 1000.0 if ms else number


def _filtered_categories(body: Any) -> frozenset[str]:
    """Extracts the detected content-filter categories from a 400 error body's
    ``innererror.content_filter_result`` (Azure's Prompt Shields shape), e.g.
    ``{"jailbreak": {"filtered": true, "detected": true}}`` -> ``{"jailbreak"}``."""
    if not isinstance(body, dict):
        return frozenset()
    result = (body.get("innererror") or {}).get("content_filter_result")
    if not isinstance(result, dict):
        return frozenset()
    return frozenset(
        category for category, info in result.items()
        if isinstance(info, dict) and info.get("detected")
    )


def _retry_after_seconds(headers: httpx.Headers | None) -> float | None:
    """Extracts a suggested wait, in seconds, from a 429 response's headers."""
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value is not None:
        seconds = _header_seconds(value)
        if seconds is not None:
            return seconds
    value = headers.get("retry-after-ms")
    if value is not None:
        seconds = _header_seconds(value, ms=True)
        if seconds is not None:
            return seconds
    for key, header_value in headers.items():
        if key.lower().startswith("x-ratelimit-reset-"):
            seconds = _header_seconds(header_value)
            if seconds is not None:
                return seconds
    return None


class LlmClient(Protocol):
    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> LlmResult: ...


class AzureLlmClient:
    """LlmClient backed by Azure OpenAI's chat completions API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = openai.AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_deployment,
            timeout=30.0,
            max_retries=0,
        )

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> LlmResult:
        chat_messages = [{"role": "system", "content": system}, *messages]
        start = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self._settings.azure_openai_deployment,
                messages=chat_messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": json_schema["name"],
                        "schema": json_schema["schema"],
                        "strict": True,
                    },
                },
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except openai.RateLimitError as exc:
            headers = exc.response.headers if exc.response is not None else None
            raise LlmRateLimited(
                str(exc), retry_after_seconds=_retry_after_seconds(headers)
            ) from exc
        except openai.BadRequestError as exc:
            code = exc.code or (exc.body.get("code") if isinstance(exc.body, dict) else None)
            if code == "content_filter":
                raise LlmContentFiltered(
                    str(exc), categories=_filtered_categories(exc.body)
                ) from exc
            raise LlmError(str(exc)) from exc
        except (openai.OpenAIError, httpx.TimeoutException, httpx.HTTPError) as exc:
            raise LlmError(str(exc)) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        usage = response.usage
        if usage is None:
            raise LlmError("response missing usage")
        return LlmResult(
            text=response.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            latency_ms=latency_ms,
            model=response.model,
        )


@dataclass
class FakeLlmClient:
    """Test double. Returns queued responses in order; records every call."""

    responses: list[str | Exception | dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = field(default=0, init=False)

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> LlmResult:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "json_schema": json_schema,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        item = self.responses[self._index]
        self._index += 1
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            # Recorded content-filter sentinel: {"content_filter": ["<category>", ...]}.
            raise LlmContentFiltered(
                "content filtered (recorded)", frozenset(item.get("content_filter", []))
            )
        return LlmResult(
            text=item,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model="fake",
        )


def cost_eur(result: LlmResult, settings: Settings) -> Decimal:
    input_cost = (Decimal(result.input_tokens) / Decimal(1000)) * settings.coach_price_in_per_1k
    output_cost = (
        Decimal(result.output_tokens) / Decimal(1000)
    ) * settings.coach_price_out_per_1k
    return input_cost + output_cost
