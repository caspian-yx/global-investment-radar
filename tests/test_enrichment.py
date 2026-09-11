from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from radar.config import Settings
from radar.domain import Event, EventType, ExplanationBundle
from radar.enrichment import (
    generate_structured_explanations,
    run_structured_explanation_enrichment,
)
from radar.llm import (
    FakeLLMProvider,
    LLMConfigurationError,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
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

    def __init__(
        self,
        *,
        failures: set[str] | None = None,
        malformed: set[str] | None = None,
        usages: dict[str, LLMUsage] | None = None,
    ) -> None:
        self.failures = failures or set()
        self.malformed = malformed or set()
        self.usages = usages or {}
        self.requests = []

    def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        payload = json.loads(request.prompt.split("INPUT_JSON:\n", maxsplit=1)[1])
        target_id = payload["target"]["target_event_id"]
        if target_id in self.failures:
            raise LLMTimeoutError(f"synthetic failure for {target_id}")
        if target_id in self.malformed:
            content = "not-json"
        else:
            content = json.dumps(
                {
                    "target_event_id": target_id,
                    "headline": f"Headline for {target_id}",
                    "summary": "Deterministic fixture summary.",
                    "candidate_explanations": [],
                    "uncertainty": None,
                }
            )
        return LLMResponse(
            content=content,
            provider=self.name,
            requested_model="fixture-model",
            returned_model="fixture-model",
            usage=self.usages.get(target_id),
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


@pytest.mark.parametrize(
    ("maximum", "bundle_count", "expected_attempts", "expected_skipped"),
    [
        (0, 3, 0, 3),
        (1, 3, 1, 2),
        (3, 10, 3, 7),
    ],
)
def test_request_budget_is_a_hard_provider_call_limit(
    maximum: int,
    bundle_count: int,
    expected_attempts: int,
    expected_skipped: int,
) -> None:
    provider = BundleProvider()
    bundles = [_bundle(f"target-{index}") for index in range(bundle_count)]

    result = run_structured_explanation_enrichment(
        bundles,
        provider,
        max_requests=maximum,
    )

    assert len(provider.requests) == expected_attempts
    assert result.usage.requests_attempted == expected_attempts
    assert result.usage.targets_skipped_due_to_budget == expected_skipped
    assert result.usage.budget_exhausted is (expected_skipped > 0)


def test_failed_and_malformed_requests_consume_request_budget_without_retry() -> None:
    provider = BundleProvider(
        failures={"target-failed"},
        malformed={"target-malformed"},
        usages={
            "target-malformed": LLMUsage(
                input_tokens=7,
                output_tokens=3,
                total_tokens=10,
            )
        },
    )

    result = run_structured_explanation_enrichment(
        [
            _bundle("target-failed"),
            _bundle("target-malformed"),
            _bundle("target-skipped"),
        ],
        provider,
        max_requests=2,
    )

    assert len(provider.requests) == 2
    assert result.explanations == {}
    assert result.usage.requests_attempted == 2
    assert result.usage.requests_failed == 2
    assert result.usage.requests_succeeded == 0
    assert result.usage.targets_skipped_due_to_budget == 1
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 10
    assert result.usage.budget_exhausted is True


def test_mixed_results_preserve_successes_and_aggregate_only_response_usage() -> None:
    provider = BundleProvider(
        failures={"target-b"},
        usages={
            "target-a": LLMUsage(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
            ),
            "target-c": LLMUsage(
                input_tokens=11,
                output_tokens=21,
                total_tokens=32,
            ),
        },
    )

    result = run_structured_explanation_enrichment(
        [
            _bundle("target-a"),
            _bundle("target-b"),
            _bundle("target-c"),
            _bundle("target-d"),
        ],
        provider,
        max_requests=3,
    )

    assert list(result.explanations) == ["target-a", "target-c"]
    assert len(provider.requests) == 3
    assert result.usage.requests_attempted == 3
    assert result.usage.requests_succeeded == 2
    assert result.usage.requests_failed == 1
    assert result.usage.targets_skipped_due_to_budget == 1
    assert result.usage.input_tokens == 21
    assert result.usage.output_tokens == 41
    assert result.usage.total_tokens == 62
    assert result.usage.budget_exhausted is True


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "usage"),
    [
        (
            "max_input_tokens",
            10,
            LLMUsage(input_tokens=10, output_tokens=1, total_tokens=11),
        ),
        (
            "max_output_tokens",
            10,
            LLMUsage(input_tokens=1, output_tokens=10, total_tokens=11),
        ),
    ],
)
def test_reaching_either_token_cap_stops_all_subsequent_calls(
    limit_name: str,
    limit_value: int,
    usage: LLMUsage,
) -> None:
    provider = BundleProvider(usages={"target-1": usage})
    limits: dict[str, int | None] = {
        "max_requests": 3,
        "max_input_tokens": None,
        "max_output_tokens": None,
    }
    limits[limit_name] = limit_value

    result = run_structured_explanation_enrichment(
        [_bundle("target-1"), _bundle("target-2"), _bundle("target-3")],
        provider,
        **limits,
    )

    assert len(provider.requests) == 1
    assert list(result.explanations) == ["target-1"]
    assert result.usage.requests_attempted == 1
    assert result.usage.targets_skipped_due_to_budget == 2
    assert result.usage.budget_exhausted is True


@pytest.mark.parametrize("limit_name", ["max_input_tokens", "max_output_tokens"])
def test_zero_token_cap_prevents_every_provider_call(limit_name: str) -> None:
    provider = BundleProvider()
    limits: dict[str, int | None] = {
        "max_requests": 3,
        "max_input_tokens": None,
        "max_output_tokens": None,
    }
    limits[limit_name] = 0

    result = run_structured_explanation_enrichment(
        [_bundle("target-1"), _bundle("target-2")],
        provider,
        **limits,
    )

    assert provider.requests == []
    assert result.usage.requests_attempted == 0
    assert result.usage.targets_skipped_due_to_budget == 2
    assert result.usage.budget_exhausted is True


def test_provider_failure_without_response_does_not_increase_tokens() -> None:
    provider = BundleProvider(failures={"target-1"})

    result = run_structured_explanation_enrichment(
        [_bundle("target-1")],
        provider,
        max_requests=1,
    )

    assert result.usage.requests_attempted == 1
    assert result.usage.requests_failed == 1
    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0
    assert result.usage.total_tokens == 0
    assert result.usage.budget_exhausted is False


def test_disabled_budgeted_enrichment_performs_zero_calls() -> None:
    result = run_structured_explanation_enrichment(
        [_bundle("target-1")],
        None,
        max_requests=10,
    )

    assert result.explanations == {}
    assert result.usage.requests_attempted == 0
    assert result.usage.targets_skipped_due_to_budget == 0
    assert result.usage.budget_exhausted is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("AI_MAX_REQUESTS_PER_RUN", -1),
        ("AI_MAX_INPUT_TOKENS_PER_RUN", -1),
        ("AI_MAX_OUTPUT_TOKENS_PER_RUN", -1),
    ],
)
def test_negative_ai_run_limits_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_zero_and_unset_ai_run_limits_are_allowed() -> None:
    settings = Settings(
        _env_file=None,
        AI_MAX_REQUESTS_PER_RUN=0,
        AI_MAX_INPUT_TOKENS_PER_RUN=None,
        AI_MAX_OUTPUT_TOKENS_PER_RUN=0,
    )

    assert settings.ai_max_requests_per_run == 0
    assert settings.ai_max_input_tokens_per_run is None
    assert settings.ai_max_output_tokens_per_run == 0


def test_empty_optional_token_limits_from_dotenv_are_treated_as_unset() -> None:
    settings = Settings(
        _env_file=None,
        AI_MAX_INPUT_TOKENS_PER_RUN="",
        AI_MAX_OUTPUT_TOKENS_PER_RUN="",
    )

    assert settings.ai_max_input_tokens_per_run is None
    assert settings.ai_max_output_tokens_per_run is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("AI_MAX_REQUESTS_PER_RUN", 0),
        ("AI_MAX_INPUT_TOKENS_PER_RUN", 0),
        ("AI_MAX_OUTPUT_TOKENS_PER_RUN", 0),
    ],
)
def test_zero_run_cap_disables_provider_construction_gate(field: str, value: int) -> None:
    settings = Settings(
        _env_file=None,
        AI_ENABLED=True,
        DAILY_LLM_BUDGET=1,
        **{field: value},
    )

    assert settings.effective_ai_enabled is False


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
