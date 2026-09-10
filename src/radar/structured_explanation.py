from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from radar.domain import (
    CandidateExplanation,
    ExplanationBundle,
    GenerationMetadata,
    StructuredExplanation,
)
from radar.llm import LLMProvider, LLMRequest, LLMResponse

DEFAULT_MAX_OUTPUT_TOKENS = 512

_PROMPT_INSTRUCTIONS = """You explain a finalized deterministic investment event bundle.
Use ONLY the supplied INPUT_JSON. Do not use outside knowledge or invent facts, prices,
percentages, events, companies, causes, or dates. Distinguish supplied evidence from
interpretation and express uncertainty when support is weak or conflicting.
Keep all generated text concise.
Preserve candidate order and include every candidate exactly once. Copy target_event_id and
candidate_id values exactly. Return one JSON object only with this shape:
{"target_event_id":"...","headline":"...","summary":"...","candidate_explanations":[{"candidate_id":"...","explanation":"..."}],"uncertainty":null}
Do not output Claim IDs or Evidence IDs. Do not add fields. Do not emit markdown fences.
INPUT_JSON:
"""


class StructuredExplanationError(ValueError):
    """Base error for deterministic C3.2 parsing and closure checks."""


class MalformedStructuredOutputError(StructuredExplanationError):
    pass


class StructuredOutputSchemaError(StructuredExplanationError):
    pass


class StructuredOutputReferenceError(StructuredExplanationError):
    pass


class _StructuredExplanationPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    summary: str = Field(min_length=1, pattern=r".*\S.*")
    candidate_explanations: tuple[CandidateExplanation, ...]
    uncertainty: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")


def build_explanation_prompt(bundle: ExplanationBundle) -> str:
    """Build the canonical, compact C3.2 prompt without mutating its C2 bundle."""
    payload = {
        "target": {
            "target_event_id": bundle.target_event.id,
            "event_type": bundle.target_event.event_type.value,
            "title": bundle.target_event.title,
            "confidence": bundle.target_event.confidence,
        },
        "candidates": [
            {
                "candidate_id": candidate.id,
                "interpretation_claim_id": candidate.interpretation_claim_id,
                "target_evidence_ids": candidate.target_evidence_ids,
                "candidate_evidence_ids": candidate.candidate_evidence_ids,
                "temporal_relation": candidate.temporal_relation.value,
                "relevance_relation": candidate.relevance_relation.value,
                "confidence": candidate.confidence.value,
                "confidence_reasons": candidate.confidence_reasons,
            }
            for candidate in bundle.driver_candidates
        ],
        "claims": [
            {
                "id": claim.id,
                "type": claim.claim_type.value,
                "text": claim.text,
                "confidence": claim.confidence,
            }
            for claim in bundle.claims
        ],
        "evidence": [
            {
                "id": evidence.id,
                "source_kind": evidence.source_kind.value,
                "excerpt": evidence.excerpt,
                "quality_score": evidence.quality_score,
            }
            for evidence in bundle.evidence
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _PROMPT_INSTRUCTIONS + serialized


def build_explanation_request(
    bundle: ExplanationBundle,
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> LLMRequest:
    return LLMRequest(
        prompt=build_explanation_prompt(bundle),
        max_output_tokens=max_output_tokens,
        reasoning=False,
    )


def parse_structured_explanation(
    bundle: ExplanationBundle,
    response: LLMResponse,
) -> StructuredExplanation:
    try:
        raw_payload = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise MalformedStructuredOutputError(
            "LLM structured output must be one valid JSON value."
        ) from exc

    try:
        payload = _StructuredExplanationPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise StructuredOutputSchemaError(
            "LLM structured output does not match the required schema."
        ) from exc

    if payload.target_event_id != bundle.target_event.id:
        raise StructuredOutputReferenceError(
            "LLM structured output target_event_id does not match the bundle."
        )

    expected_ids = [candidate.id for candidate in bundle.driver_candidates]
    actual_ids = [item.candidate_id for item in payload.candidate_explanations]
    if len(actual_ids) != len(set(actual_ids)):
        raise StructuredOutputReferenceError(
            "LLM structured output contains a duplicate candidate ID."
        )
    unknown_ids = sorted(set(actual_ids) - set(expected_ids))
    if unknown_ids:
        raise StructuredOutputReferenceError(
            "LLM structured output contains an unknown candidate ID."
        )
    missing_ids = sorted(set(expected_ids) - set(actual_ids))
    if missing_ids:
        raise StructuredOutputReferenceError(
            "LLM structured output is missing candidate ID coverage."
        )
    if actual_ids != expected_ids:
        raise StructuredOutputReferenceError(
            "LLM structured output does not preserve C2 candidate order."
        )

    usage = response.usage
    return StructuredExplanation(
        target_event_id=payload.target_event_id,
        headline=payload.headline,
        summary=payload.summary,
        candidate_explanations=payload.candidate_explanations,
        uncertainty=payload.uncertainty,
        generation=GenerationMetadata(
            provider=response.provider,
            requested_model=response.requested_model,
            returned_model=response.returned_model,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            total_tokens=usage.total_tokens if usage is not None else None,
            latency_seconds=response.latency_seconds,
        ),
    )


def generate_structured_explanation(
    bundle: ExplanationBundle,
    provider: LLMProvider,
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> StructuredExplanation:
    request = build_explanation_request(
        bundle,
        max_output_tokens=max_output_tokens,
    )
    response = provider.generate(request)
    return parse_structured_explanation(bundle, response)
