from __future__ import annotations

import logging
from collections.abc import Iterable

from radar.domain import (
    AIEnrichmentResult,
    AIRunUsage,
    ExplanationBundle,
    StructuredExplanation,
)
from radar.llm import LLMError, LLMProvider
from radar.structured_explanation import (
    StructuredExplanationError,
    build_explanation_request,
    parse_structured_explanation,
)

logger = logging.getLogger(__name__)


def generate_structured_explanations(
    bundles: Iterable[ExplanationBundle],
    provider: LLMProvider | None,
) -> dict[str, StructuredExplanation]:
    """Generate optional explanations without changing deterministic bundle state."""
    materialized = list(bundles)
    return run_structured_explanation_enrichment(
        materialized,
        provider,
        max_requests=len(materialized),
    ).explanations


def run_structured_explanation_enrichment(
    bundles: Iterable[ExplanationBundle],
    provider: LLMProvider | None,
    *,
    max_requests: int,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> AIEnrichmentResult:
    """Generate explanations behind run-level hard request and observed-token caps."""
    limits = (max_requests, max_input_tokens, max_output_tokens)
    if any(limit is not None and limit < 0 for limit in limits):
        raise ValueError("AI enrichment limits must be non-negative")

    materialized = list(bundles)
    explanations: dict[str, StructuredExplanation] = {}
    requests_attempted = 0
    requests_succeeded = 0
    requests_failed = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    targets_skipped_due_to_budget = 0
    budget_exhausted = False

    for index, bundle in enumerate(materialized):
        request_limit_reached = requests_attempted >= max_requests
        input_limit_reached = (
            max_input_tokens is not None and input_tokens >= max_input_tokens
        )
        output_limit_reached = (
            max_output_tokens is not None and output_tokens >= max_output_tokens
        )
        if request_limit_reached or input_limit_reached or output_limit_reached:
            targets_skipped_due_to_budget = len(materialized) - index
            budget_exhausted = True
            break
        if provider is None:
            break

        request = build_explanation_request(bundle)
        requests_attempted += 1
        try:
            response = provider.generate(request)
        except LLMError as exc:
            requests_failed += 1
            logger.warning(
                "Optional structured explanation failed target_event_id=%s error_type=%s",
                bundle.target_event.id,
                type(exc).__name__,
            )
            continue

        usage = response.usage
        if usage is not None:
            input_tokens += usage.input_tokens or 0
            output_tokens += usage.output_tokens or 0
            total_tokens += usage.total_tokens or 0

        try:
            explanations[bundle.target_event.id] = parse_structured_explanation(
                bundle,
                response,
            )
        except StructuredExplanationError as exc:
            requests_failed += 1
            logger.warning(
                "Optional structured explanation failed target_event_id=%s error_type=%s",
                bundle.target_event.id,
                type(exc).__name__,
            )
        else:
            requests_succeeded += 1

    return AIEnrichmentResult(
        explanations=explanations,
        usage=AIRunUsage(
            requests_attempted=requests_attempted,
            requests_succeeded=requests_succeeded,
            requests_failed=requests_failed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            targets_skipped_due_to_budget=targets_skipped_due_to_budget,
            budget_exhausted=budget_exhausted,
        ),
    )
