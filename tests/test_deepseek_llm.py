from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from radar.config import Settings
from radar.llm import (
    DeepSeekProvider,
    FakeLLMProvider,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMInvalidModelError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    LLMUsage,
    build_llm_provider,
)
from radar.llm_smoke import run_smoke


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "LLM_PROVIDER": "deepseek",
        "LLM_MODEL": "deepseek-v4-flash",
        "LLM_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_API_KEY": "unit-test-key",
        "LLM_TIMEOUT_SECONDS": 7,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _provider(
    handler: httpx.MockTransport,
    *,
    model: str = "deepseek-v4-flash",
) -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key=SecretStr("unit-test-key"),
        model=model,
        base_url="https://api.deepseek.com",
        timeout_seconds=7,
        transport=handler,
    )


def test_request_and_response_contracts_are_small_and_validated() -> None:
    request = LLMRequest(prompt="Explain the move", max_output_tokens=64)
    response = LLMResponse(
        content="A concise answer.",
        provider="deepseek",
        requested_model="deepseek-v4-flash",
        returned_model="deepseek-v4-flash",
        usage=LLMUsage(input_tokens=4, output_tokens=5, total_tokens=9),
        latency_seconds=0.25,
    )

    assert set(request.model_dump()) == {"prompt", "max_output_tokens", "reasoning"}
    assert request.reasoning is None
    assert set(response.model_dump()) == {
        "content",
        "provider",
        "requested_model",
        "returned_model",
        "usage",
        "latency_seconds",
    }

    with pytest.raises(ValidationError):
        LLMRequest(prompt="   ")
    with pytest.raises(ValidationError):
        LLMRequest(prompt="valid", max_output_tokens=0)


def test_deepseek_settings_are_optional_and_secret_is_masked() -> None:
    settings = _settings()

    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "unit-test-key"
    assert "unit-test-key" not in repr(settings)
    assert settings.llm_timeout_seconds == 7

    defaults = Settings(_env_file=None)
    assert defaults.llm_api_key is None


def test_normal_provider_selection_does_not_require_deepseek_credentials() -> None:
    settings = Settings(
        _env_file=None,
        AI_ENABLED=True,
        DAILY_LLM_BUDGET=1,
        LLM_PROVIDER="deepseek",
        LLM_MODEL="deepseek-v4-flash",
    )

    provider = build_llm_provider(settings)

    assert provider.name == "disabled"


def test_explicit_deepseek_construction_validates_credentials_and_model() -> None:
    with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider.from_settings(_settings(DEEPSEEK_API_KEY=""))
    with pytest.raises(LLMConfigurationError, match="LLM_MODEL"):
        DeepSeekProvider.from_settings(_settings(LLM_MODEL=""))


def test_from_settings_preserves_exact_configured_model() -> None:
    model = "deepseek-v4-flash"
    provider = DeepSeekProvider.from_settings(_settings(LLM_MODEL=model))

    assert provider.model == model
    assert provider.base_url == "https://api.deepseek.com"
    assert provider.timeout_seconds == 7
    assert "unit-test-key" not in repr(provider)


def test_fake_provider_records_requests_and_returns_defensive_copies() -> None:
    expected = LLMResponse(
        content="fixture",
        provider="fake",
        requested_model="fixture-model",
        returned_model="fixture-model",
        latency_seconds=0,
    )
    provider = FakeLLMProvider(response=expected)
    request = LLMRequest(prompt="hello")

    first = provider.generate(request)
    first.content = "mutated"
    second = provider.generate(request)

    assert second == expected
    assert provider.requests == [request, request]
    assert provider.requests[0] is not request


def test_fake_provider_can_simulate_an_owned_error() -> None:
    error = LLMTimeoutError("simulated timeout")
    provider = FakeLLMProvider(error=error)

    with pytest.raises(LLMTimeoutError, match="simulated timeout"):
        provider.generate(LLMRequest(prompt="hello"))


def test_deepseek_generate_sends_exact_model_and_normalizes_response() -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-202609",
                "choices": [{"message": {"content": "  normalized answer  "}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    provider = _provider(httpx.MockTransport(handle))
    response = provider.generate(LLMRequest(prompt="Exact prompt", max_output_tokens=23))

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer unit-test-key"
    assert captured["payload"] == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "Exact prompt"}],
        "max_tokens": 23,
    }
    assert response.content == "normalized answer"
    assert response.provider == "deepseek"
    assert response.requested_model == "deepseek-v4-flash"
    assert response.returned_model == "deepseek-v4-flash-202609"
    assert response.usage == LLMUsage(input_tokens=11, output_tokens=7, total_tokens=18)
    assert response.latency_seconds >= 0


@pytest.mark.parametrize(
    ("reasoning", "expected_thinking"),
    [
        (None, None),
        (False, {"type": "disabled"}),
        (True, {"type": "enabled"}),
    ],
)
def test_deepseek_maps_provider_neutral_reasoning_mode(
    reasoning: bool | None,
    expected_thinking: dict[str, str] | None,
) -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    provider = _provider(httpx.MockTransport(handle))
    provider.generate(LLMRequest(prompt="hello", reasoning=reasoning))

    payload = captured["payload"]
    assert isinstance(payload, dict)
    if expected_thinking is None:
        assert "thinking" not in payload
    else:
        assert payload["thinking"] == expected_thinking


def test_reasoning_content_is_never_used_as_final_content() -> None:
    response = httpx.Response(
        200,
        json={
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "reasoning_content": "private reasoning must not leak",
                        "content": None,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 16,
                "total_tokens": 28,
            },
        },
    )
    provider = _provider(httpx.MockTransport(lambda request: response))

    with pytest.raises(LLMResponseError, match="no text content") as caught:
        provider.generate(LLMRequest(prompt="hello"))

    assert "private reasoning" not in str(caught.value)


@pytest.mark.parametrize(
    ("status_code", "body", "error_type"),
    [
        (401, {"error": {"message": "bad secret"}}, LLMAuthenticationError),
        (429, {"error": {"message": "slow down"}}, LLMRateLimitError),
        (400, {"error": {"message": "Model does not exist"}}, LLMInvalidModelError),
        (500, {"error": {"message": "internal details"}}, LLMProviderError),
    ],
)
def test_deepseek_http_errors_are_owned_and_sanitized(
    status_code: int,
    body: dict[str, object],
    error_type: type[Exception],
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code, json=body))
    provider = _provider(transport)

    with pytest.raises(error_type) as caught:
        provider.generate(LLMRequest(prompt="hello"))

    message = str(caught.value)
    assert "unit-test-key" not in message
    assert "bad secret" not in message
    assert "internal details" not in message


def test_deepseek_timeout_is_translated_without_retry() -> None:
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("contains transport details", request=request)

    provider = _provider(httpx.MockTransport(handle))

    with pytest.raises(LLMTimeoutError, match="timed out") as caught:
        provider.generate(LLMRequest(prompt="hello"))
    assert caught.value.phase == "read"
    assert attempts == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": " "}}]}),
    ],
)
def test_malformed_deepseek_responses_raise_owned_error(response: httpx.Response) -> None:
    provider = _provider(httpx.MockTransport(lambda request: response))

    with pytest.raises(LLMResponseError):
        provider.generate(LLMRequest(prompt="hello"))


def test_smoke_runner_requires_an_explicit_call_and_uses_small_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = LLMResponse(
        content="OK",
        provider="deepseek",
        requested_model="deepseek-v4-flash",
        returned_model="deepseek-v4-flash",
        latency_seconds=0,
    )
    fake = FakeLLMProvider(response=response)
    monkeypatch.setattr(
        DeepSeekProvider,
        "from_settings",
        staticmethod(lambda settings: fake),
    )

    result = run_smoke(_settings())

    assert result == response
    assert fake.requests == [LLMRequest(prompt="Reply with exactly: OK", max_output_tokens=16)]


def test_env_example_is_safe_and_uses_official_model_id() -> None:
    example = Path(__file__).parents[1].joinpath(".env.example").read_text(encoding="utf-8")

    assert "DEEPSEEK_API_KEY=\n" in example
    assert "LLM_PROVIDER=deepseek\n" in example
    assert "LLM_MODEL=deepseek-v4-flash\n" in example
    assert "LLM_BASE_URL=https://api.deepseek.com\n" in example
    assert "sk-" not in example
