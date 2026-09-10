from __future__ import annotations

import logging
from collections.abc import Iterable

from radar.domain import ExplanationBundle, StructuredExplanation
from radar.llm import LLMError, LLMProvider
from radar.structured_explanation import (
    StructuredExplanationError,
    generate_structured_explanation,
)

logger = logging.getLogger(__name__)


def generate_structured_explanations(
    bundles: Iterable[ExplanationBundle],
    provider: LLMProvider | None,
) -> dict[str, StructuredExplanation]:
    """Generate optional explanations without changing deterministic bundle state."""
    if provider is None:
        return {}

    explanations: dict[str, StructuredExplanation] = {}
    for bundle in bundles:
        try:
            explanations[bundle.target_event.id] = generate_structured_explanation(
                bundle,
                provider,
            )
        except (LLMError, StructuredExplanationError) as exc:
            logger.warning(
                "Optional structured explanation failed target_event_id=%s error_type=%s",
                bundle.target_event.id,
                type(exc).__name__,
            )
    return explanations
