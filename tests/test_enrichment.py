from __future__ import annotations

import json
from datetime import UTC, datetime

from radar.config import Settings
from radar.domain import Event, EventType, ExplanationBundle
from radar.enrichment import generate_structured_explanations
from radar.llm import (
    FakeLLMProvider,
    LLMConfigurationError,
    LLMResponse,
    LLMTimeoutError,
    build_generation_provider,
)

AS_OF = datetime(2026, 9, 10, 8, tzinfo=UTC)


def _bundle(target_id: str) -> ExplanationBundle:
    return ExplanationBundle(
        target_event=Event(
            id=target_id,
            event_key=f"market:{target_id}",
            event_type=EventType.MARKET_MOVE,
            title="Market move",
            occurred_at=AS_OF,
            first_seen_at=AS_OF,
            last_seen_at=AS_OF,
            entity_ids=["asset-1"],
            claim_ids=[],
            evidence_ids=[],
            source_document_ids=[],
            importance_score=80,
            importance_components={"magnitude": 80},
            confidence=0.9,
        ),
        driver_candidates=[],
        claims=[],
        evidence=[],
    )


class BundleProvider:
    name = "fake"

    def __init__(self, *, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.requests = []

    def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        payload = json.loads(request.prompt.split("INPUT_JSON:\n", maxsplit=1)[1])
        target_id = payload["target"]["target_event_id"]
        if target_id in self.failures:
            raise LLMTimeoutError(f"synthetic failure for {target_id}")
        return LLMResponse(
            content=json.dumps(
                {
                    "target_event_id": target_id,
                    "headline": f"Headline for {target_id}",
                    "summary": "Deterministic fixture summary.",
                    "candidate_explanations": [],
                    "uncertainty": None,
                }
            ),
            provider=self.name,
            requested_model="fixture-model",
            returned_model="fixture-model",
            latency_seconds=0,
        )


def test_disabled_enrichment_performs_zero_calls() -> None:
    provider = BundleProvider()

    result = generate_structured_explanations([_bundle("target-1")], None)

    assert result == {}
    assert provider.requests == []


def test_no_bundles_performs_zero_calls() -> None:
    provider = BundleProvider()

    result = generate_structured_explanations([], provider)

    assert result == {}
    assert provider.requests == []


def test_one_successful_bundle_performs_exactly_one_call() -> None:
    provider = BundleProvider()

    result = generate_structured_explanations([_bundle("target-1")], provider)

    assert list(result) == ["target-1"]
    assert result["target-1"].target_event_id == "target-1"
    assert len(provider.requests) == 1


def test_multiple_successful_bundles_preserve_target_mapping_and_call_count() -> None:
    provider = BundleProvider()

    result = generate_structured_explanations(
        [_bundle("target-1"), _bundle("target-2")],
        provider,
    )

    assert list(result) == ["target-1", "target-2"]
    assert len(provider.requests) == 2


def test_failed_bundle_is_omitted_without_retry() -> None:
    provider = BundleProvider(failures={"target-1"})

    result = generate_structured_explanations([_bundle("target-1")], provider)

    assert result == {}
    assert len(provider.requests) == 1


def test_structured_parser_failure_is_omitted_without_retry() -> None:
    class InvalidProvider(BundleProvider):
        def generate(self, request):
            self.requests.append(request.model_copy(deep=True))
            return LLMResponse(
                content="not-json",
                provider=self.name,
                requested_model="fixture-model",
                returned_model="fixture-model",
                latency_seconds=0,
            )

    provider = InvalidProvider()

    result = generate_structured_explanations([_bundle("target-1")], provider)

    assert result == {}
    assert len(provider.requests) == 1


def test_mixed_success_and_failure_preserves_successful_explanation() -> None:
    provider = BundleProvider(failures={"target-2"})

    result = generate_structured_explanations(
        [_bundle("target-1"), _bundle("target-2")],
        provider,
    )

    assert list(result) == ["target-1"]
    assert len(provider.requests) == 2


def test_generation_provider_is_disabled_without_effective_ai() -> None:
    settings = Settings(_env_file=None, AI_ENABLED=False, DAILY_LLM_BUDGET=10)

    assert build_generation_provider(settings) is None


def test_generation_provider_reuses_fake_provider_in_non_production() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        AI_ENABLED=True,
        DAILY_LLM_BUDGET=1,
        LLM_PROVIDER="fake",
    )

    assert isinstance(build_generation_provider(settings), FakeLLMProvider)


def test_generation_provider_surfaces_invalid_configuration_to_boundary() -> None:
    settings = Settings(
        _env_file=None,
        AI_ENABLED=True,
        DAILY_LLM_BUDGET=1,
        LLM_PROVIDER="deepseek",
        LLM_MODEL="deepseek-v4-flash",
    )

    try:
        build_generation_provider(settings)
    except LLMConfigurationError:
        pass
    else:
        raise AssertionError("missing DeepSeek credentials must be reported")
