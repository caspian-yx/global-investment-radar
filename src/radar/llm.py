from __future__ import annotations

import time
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator

from radar.config import Settings
from radar.domain import EvidencePack, StructuredAnalysis


class LLMProvider(Protocol):
    """Provider-neutral text generation boundary."""

    name: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...


class AnalysisProvider(Protocol):
    """Existing evidence-pack analysis boundary used by the current pipeline."""

    name: str

    def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis: ...


class LLMRequest(BaseModel):
    prompt: str
    max_output_tokens: int | None = Field(default=None, ge=1)
    reasoning: bool | None = None

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class LLMUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class LLMResponse(BaseModel):
    content: str
    provider: str
    requested_model: str
    returned_model: str | None = None
    usage: LLMUsage | None = None
    latency_seconds: float = Field(ge=0)

    @field_validator("content", "provider", "requested_model")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class LLMError(RuntimeError):
    """Base exception for the provider-neutral LLM boundary."""


class LLMConfigurationError(LLMError):
    pass


class LLMAuthenticationError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMInvalidModelError(LLMError):
    pass


class LLMProviderError(LLMError):
    pass


class LLMResponseError(LLMError):
    pass


class DisabledLLMProvider:
    name = "disabled"

    def __init__(self, reason: str = "AI analysis is disabled by configuration.") -> None:
        self.reason = reason

    def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis:
        return StructuredAnalysis(
            event_id=evidence_pack.event.id,
            status="disabled",
            provider=self.name,
            unavailable_reason=self.reason,
        )


class FakeLLMProvider:
    """A test-only provider that validates the structured AI data path without an API call."""

    name = "fake"

    def __init__(
        self,
        *,
        response: LLMResponse | None = None,
        error: LLMError | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        if self._error is not None:
            raise self._error
        response = self._response or LLMResponse(
            content="TEST DATA: deterministic fake LLM response.",
            provider=self.name,
            requested_model="deterministic-test-double",
            returned_model="deterministic-test-double",
            latency_seconds=0,
        )
        return response.model_copy(deep=True)

    def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis:
        event = evidence_pack.event
        claim_ids = [claim.id for claim in evidence_pack.claims]
        return StructuredAnalysis(
            event_id=event.id,
            status="test-data",
            why_it_matters=(
                f"TEST DATA: {event.event_type.value} event ranked "
                f"{event.importance_score:.1f}/100 "
                "from the supplied evidence pack."
            ),
            bull_case=(
                "TEST DATA: A constructive interpretation would require corroborating evidence."
            ),
            bear_case=(
                "TEST DATA: The event may be transient or already reflected in market prices."
            ),
            risk_notes=[
                "This output is synthetic and must never be treated as production research."
            ],
            cited_claim_ids=claim_ids,
            provider=self.name,
            model="deterministic-test-double",
            is_test_data=True,
        )


class DeepSeekProvider:
    """Synchronous DeepSeek chat-completions transport with no retries or fallback."""

    name = "deepseek"
    default_base_url = "https://api.deepseek.com"

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None,
        model: str | None,
        base_url: str = default_base_url,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        secret = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key or "")
        if not secret.get_secret_value():
            raise LLMConfigurationError(
                "DEEPSEEK_API_KEY is required when constructing DeepSeekProvider."
            )
        if model is None or not model.strip():
            raise LLMConfigurationError(
                "LLM_MODEL is required when constructing DeepSeekProvider."
            )
        if timeout_seconds <= 0:
            raise LLMConfigurationError("LLM_TIMEOUT_SECONDS must be greater than zero.")
        self._api_key = secret
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> DeepSeekProvider:
        return cls(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url or cls.default_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    def __repr__(self) -> str:
        return (
            f"DeepSeekProvider(model={self.model!r}, base_url={self.base_url!r}, "
            f"timeout_seconds={self.timeout_seconds!r})"
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.reasoning is not None:
            payload["thinking"] = {
                "type": "enabled" if request.reasoning else "disabled"
            }

        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("DeepSeek request timed out.") from exc
        except httpx.RequestError as exc:
            raise LLMProviderError("DeepSeek request failed before receiving a response.") from exc

        latency_seconds = time.perf_counter() - started
        self._raise_for_status(response)
        return self._normalize_response(response, latency_seconds)

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            raise LLMAuthenticationError("DeepSeek authentication failed.")
        if status == 429:
            raise LLMRateLimitError("DeepSeek rate limit was exceeded.")
        if status in {400, 404} and self._response_mentions_model(response):
            raise LLMInvalidModelError(
                f"DeepSeek rejected configured model {self.model!r}."
            )
        raise LLMProviderError(f"DeepSeek request failed with HTTP status {status}.")

    @staticmethod
    def _response_mentions_model(response: httpx.Response) -> bool:
        try:
            body = response.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False
        error = body.get("error")
        if not isinstance(error, dict):
            return False
        message = error.get("message")
        return isinstance(message, str) and "model" in message.casefold()

    def _normalize_response(
        self,
        response: httpx.Response,
        latency_seconds: float,
    ) -> LLMResponse:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError("DeepSeek returned invalid JSON.") from exc
        if not isinstance(body, dict):
            raise LLMResponseError("DeepSeek returned an invalid response shape.")

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMResponseError("DeepSeek response did not contain a completion choice.")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMResponseError("DeepSeek response did not contain a completion message.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("DeepSeek response contained no text content.")

        returned_model = body.get("model")
        usage = self._normalize_usage(body.get("usage"))
        return LLMResponse(
            content=content.strip(),
            provider=self.name,
            requested_model=self.model,
            returned_model=returned_model if isinstance(returned_model, str) else None,
            usage=usage,
            latency_seconds=latency_seconds,
        )

    @staticmethod
    def _normalize_usage(value: object) -> LLMUsage | None:
        if not isinstance(value, dict):
            return None

        def token_count(key: str) -> int | None:
            count = value.get(key)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                return count
            return None

        return LLMUsage(
            input_tokens=token_count("prompt_tokens"),
            output_tokens=token_count("completion_tokens"),
            total_tokens=token_count("total_tokens"),
        )


def build_llm_provider(settings: Settings) -> AnalysisProvider:
    if not settings.ai_enabled:
        return DisabledLLMProvider()
    if settings.daily_llm_budget <= 0:
        return DisabledLLMProvider("Daily LLM budget is zero; deterministic mode is active.")
    provider = settings.llm_provider.casefold().strip()
    if provider == "fake":
        if settings.app_env == "production":
            return DisabledLLMProvider("Fake LLM is blocked in production.")
        return FakeLLMProvider()
    if provider in {"", "disabled", "none"}:
        return DisabledLLMProvider("No LLM provider was configured.")
    return DisabledLLMProvider(
        f"LLM provider '{settings.llm_provider}' is not installed; "
        "deterministic fallback is active."
    )


def build_generation_provider(settings: Settings) -> LLMProvider | None:
    """Build the optional provider used by structured explanation generation."""
    if not settings.effective_ai_enabled:
        return None
    provider = settings.llm_provider.casefold().strip()
    if provider == "fake":
        if settings.app_env == "production":
            return None
        return FakeLLMProvider()
    if provider == "deepseek":
        return DeepSeekProvider.from_settings(settings)
    raise LLMConfigurationError(
        f"LLM provider '{settings.llm_provider}' is not configured for generation."
    )
