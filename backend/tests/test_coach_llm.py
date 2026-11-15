from decimal import Decimal

import httpx
import pytest
import respx

from app.coach.llm import (
    AzureLlmClient,
    FakeLlmClient,
    LlmContentFiltered,
    LlmError,
    LlmRateLimited,
    LlmResult,
    cost_eur,
)
from app.config import Settings


def make_settings(**overrides):
    defaults = {
        "azure_openai_endpoint": "https://example-resource.openai.azure.com",
        "azure_openai_deployment": "coach-mini",
        "azure_openai_api_version": "2024-10-21",
        "azure_openai_api_key": "test-key",
        "coach_monthly_cap_eur": Decimal(2),
        "coach_price_in_per_1k": Decimal("0.0004"),
        "coach_price_out_per_1k": Decimal("0.0016"),
        "coach_log_text": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_fake_client_returns_in_order_and_records_calls():
    client = FakeLlmClient(responses=["first", "second"])

    result1 = client.complete(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        json_schema={"name": "coach_review", "schema": {}},
        max_tokens=800,
        temperature=0.3,
    )
    result2 = client.complete(
        system="sys2",
        messages=[{"role": "user", "content": "hi2"}],
        json_schema={"name": "coach_review", "schema": {}},
        max_tokens=800,
        temperature=0.3,
    )

    assert result1.text == "first"
    assert result2.text == "second"
    assert len(client.calls) == 2
    assert client.calls[0]["system"] == "sys"
    assert client.calls[1]["system"] == "sys2"


def test_fake_client_raises_recorded_exceptions():
    boom = LlmError("boom")
    client = FakeLlmClient(responses=[boom])

    with pytest.raises(LlmError):
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )
    assert len(client.calls) == 1


@respx.mock
def test_azure_client_sends_json_schema_and_parses_usage():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    route = respx.post(url__regex=rf"^{url}.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"ok": true}'}},
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
                "model": "coach-mini",
            },
        )
    )

    client = AzureLlmClient(settings)
    result = client.complete(
        system="You are a coach.",
        messages=[{"role": "user", "content": "Review my week."}],
        json_schema={"name": "coach_review", "schema": {"type": "object"}},
        max_tokens=800,
        temperature=0.3,
    )

    assert route.called
    sent = route.calls.last.request
    import json as _json

    body = _json.loads(sent.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "coach_review"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["max_tokens"] == 800
    assert body["temperature"] == 0.3

    assert isinstance(result, LlmResult)
    assert result.text == '{"ok": true}'
    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.latency_ms >= 0


@respx.mock
def test_azure_client_missing_usage_raises_LlmError():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"ok": true}'}},
                ],
                "model": "coach-mini",
            },
        )
    )

    client = AzureLlmClient(settings)
    with pytest.raises(LlmError, match="missing usage"):
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )


@respx.mock
def test_azure_client_wraps_429_as_LlmError():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )

    client = AzureLlmClient(settings)
    with pytest.raises(LlmError):
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )


@respx.mock
def test_azure_client_wraps_timeout_as_LlmError():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(side_effect=httpx.TimeoutException("timed out"))

    client = AzureLlmClient(settings)
    with pytest.raises(LlmError):
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )


@respx.mock
def test_azure_client_raises_rate_limited_on_429_with_retry_after():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"code": "rate_limit_exceeded", "message": "rate limited"}},
            headers={"retry-after": "7"},
        )
    )

    client = AzureLlmClient(settings)
    with pytest.raises(LlmRateLimited) as exc_info:
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )
    assert exc_info.value.retry_after_seconds == 7.0
    # LlmRateLimited is still an LlmError: the service's existing except clause keeps
    # working unchanged.
    assert isinstance(exc_info.value, LlmError)


@respx.mock
def test_azure_client_rate_limited_reads_retry_after_ms_and_ratelimit_reset_headers():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "rl"}},
                            headers={"retry-after-ms": "2500"}),
            httpx.Response(429, json={"error": {"message": "rl"}},
                            headers={"x-ratelimit-reset-requests": "9"}),
            httpx.Response(429, json={"error": {"message": "rl"}}),
        ]
    )

    client = AzureLlmClient(settings)
    kwargs = {
        "system": "sys", "messages": [{"role": "user", "content": "hi"}],
        "json_schema": {"name": "coach_review", "schema": {}},
        "max_tokens": 800, "temperature": 0.3,
    }
    with pytest.raises(LlmRateLimited) as first:
        client.complete(**kwargs)
    assert first.value.retry_after_seconds == 2.5

    with pytest.raises(LlmRateLimited) as second:
        client.complete(**kwargs)
    assert second.value.retry_after_seconds == 9.0

    with pytest.raises(LlmRateLimited) as third:
        client.complete(**kwargs)
    assert third.value.retry_after_seconds is None


@respx.mock
def test_azure_client_raises_content_filtered_on_400_content_filter():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "code": "content_filter",
                    "message": "The response was filtered due to the prompt triggering "
                    "Azure OpenAI's content management policy.",
                    "status": 400,
                    "innererror": {
                        "code": "ResponsibleAIPolicyViolation",
                        "content_filter_result": {
                            "jailbreak": {"filtered": True, "detected": True},
                            "hate": {"filtered": False, "detected": False},
                        },
                    },
                }
            },
        )
    )

    client = AzureLlmClient(settings)
    with pytest.raises(LlmContentFiltered) as exc_info:
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )
    assert exc_info.value.categories == frozenset({"jailbreak"})
    # Still an LlmError: the service's existing fallback path keeps working unchanged.
    assert isinstance(exc_info.value, LlmError)


@respx.mock
def test_azure_client_other_400_is_plain_LlmError_not_content_filtered():
    settings = make_settings()
    url = (
        f"{settings.azure_openai_endpoint}/openai/deployments/"
        f"{settings.azure_openai_deployment}/chat/completions"
    )
    respx.post(url__regex=rf"^{url}.*").mock(
        return_value=httpx.Response(
            400, json={"error": {"code": "invalid_request_error", "message": "bad schema"}}
        )
    )

    client = AzureLlmClient(settings)
    with pytest.raises(LlmError) as exc_info:
        client.complete(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"name": "coach_review", "schema": {}},
            max_tokens=800,
            temperature=0.3,
        )
    assert not isinstance(exc_info.value, LlmContentFiltered)


def test_cost_eur_uses_decimal_prices():
    settings = make_settings(
        coach_price_in_per_1k=Decimal("0.0004"),
        coach_price_out_per_1k=Decimal("0.0016"),
    )
    result = LlmResult(
        text="x",
        input_tokens=1000,
        output_tokens=500,
        latency_ms=10,
        model="coach-mini",
    )

    assert cost_eur(result, settings) == Decimal("0.0012")


def test_fake_client_raises_content_filter_sentinel():
    client = FakeLlmClient([{"content_filter": ["jailbreak"]}])
    with pytest.raises(LlmContentFiltered) as excinfo:
        client.complete("sys", [], {}, 100, 0.0)
    assert "jailbreak" in excinfo.value.categories
