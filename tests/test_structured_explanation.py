from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from radar.domain import (
    CandidateExplanation,
    Claim,
    ClaimType,
    DriverCandidate,
    DriverConfidence,
    DriverRelevance,
    DriverTemporalRelation,
    Event,
    EventType,
    Evidence,
    ExplanationBundle,
    GenerationMetadata,
    SourceKind,
    StructuredExplanation,
)
from radar.llm import FakeLLMProvider, LLMResponse, LLMTimeoutError, LLMUsage
from radar.structured_explanation import (
    MalformedStructuredOutputError,
    StructuredOutputReferenceError,
    StructuredOutputSchemaError,
    build_explanation_prompt,
    build_explanation_request,
    generate_structured_explanation,
    parse_structured_explanation,
)

AS_OF = datetime(2026, 9, 10, 8, tzinfo=UTC)


def _candidate(
    candidate_id: str,
    *,
    claim_id: str,
    source_evidence_id: str,
    relevance: DriverRelevance,
    confidence: DriverConfidence,
) -> DriverCandidate:
    target_evidence_id = "evidence-target"
    return DriverCandidate(
        id=candidate_id,
        target_event_id="target-1",
        candidate_event_id=f"event-{candidate_id}",
        interpretation_claim_id=claim_id,
        evidence_ids=sorted([target_evidence_id, source_evidence_id]),
        target_evidence_ids=[target_evidence_id],
        candidate_evidence_ids=[source_evidence_id],
        temporal_relation=DriverTemporalRelation.PRIOR,
        relevance_relation=relevance,
        confidence=confidence,
        confidence_components={
            "temporal": "PRIOR",
            "relevance": relevance.value,
            "source_quality": "A_B",
            "independent_publishers": "2" if confidence is DriverConfidence.HIGH else "1",
            "conflict": "NO",
        },
        confidence_reasons=[f"Reason for {candidate_id}."],
        generator="deterministic-driver-candidate",
        rule_version="c2.1-driver-v1",
        as_of=AS_OF,
    )


def _bundle() -> ExplanationBundle:
    exact = _candidate(
        "candidate-z-exact",
        claim_id="claim-z-exact",
        source_evidence_id="evidence-z-exact",
        relevance=DriverRelevance.EXACT_ENTITY,
        confidence=DriverConfidence.MEDIUM,
    )
    topic = _candidate(
        "candidate-a-topic",
        claim_id="claim-a-topic",
        source_evidence_id="evidence-a-topic",
        relevance=DriverRelevance.STRONG_TOPIC,
        confidence=DriverConfidence.HIGH,
    )
    target = Event(
        id="target-1",
        event_key="market:^GSPC:2026-09-10",
        event_type=EventType.MARKET_MOVE,
        title="S&P 500 declined",
        occurred_at=AS_OF,
        first_seen_at=AS_OF,
        last_seen_at=AS_OF,
        entity_ids=["^GSPC"],
        claim_ids=[],
        evidence_ids=["evidence-target"],
        source_document_ids=[],
        importance_score=82,
        importance_components={"magnitude": 82},
        confidence=0.9,
    )
    claims = [
        Claim(
            id="claim-a-topic",
            claim_type=ClaimType.INTERPRETATION,
            text="Policy uncertainty may have weighed on broad risk appetite.",
            evidence_ids=topic.evidence_ids,
            confidence=0.85,
            as_of=AS_OF,
        ),
        Claim(
            id="claim-z-exact",
            claim_type=ClaimType.INTERPRETATION,
            text="The company-specific announcement may have contributed to the move.",
            evidence_ids=exact.evidence_ids,
            confidence=0.65,
            as_of=AS_OF,
        ),
    ]
    evidence = [
        Evidence(
            id="evidence-a-topic",
            source_kind=SourceKind.DOCUMENT,
            source_id="document-topic",
            source_document_id="document-topic",
            locator={"section": "body"},
            excerpt="Officials described continued policy uncertainty.",
            checksum="a" * 64,
            quality_score=0.92,
        ),
        Evidence(
            id="evidence-target",
            source_kind=SourceKind.MARKET_OBSERVATION,
            source_id="market-observation",
            locator={"symbol": "^GSPC", "close": 6500},
            excerpt="S&P 500 moved -1.20% during the observed session.",
            checksum="b" * 64,
            quality_score=0.75,
        ),
        Evidence(
            id="evidence-z-exact",
            source_kind=SourceKind.DOCUMENT,
            source_id="document-exact",
            source_document_id="document-exact",
            locator={"section": "headline"},
            excerpt="The company announced a reduction in guidance.",
            checksum="c" * 64,
            quality_score=0.95,
        ),
    ]
    return ExplanationBundle(
        target_event=target,
        driver_candidates=[exact, topic],
        claims=claims,
        evidence=evidence,
    )


def _prompt_payload(prompt: str) -> dict[str, object]:
    marker = "INPUT_JSON:\n"
    assert prompt.count(marker) == 1
    return json.loads(prompt.split(marker, maxsplit=1)[1])


def test_prompt_is_byte_stable_preserves_candidate_order_and_does_not_mutate() -> None:
    bundle = _bundle()
    snapshot = bundle.model_dump(mode="json")

    first = build_explanation_prompt(bundle)
    second = build_explanation_prompt(bundle.model_copy(deep=True))
    payload = _prompt_payload(first)

    assert first == second
    assert [item["candidate_id"] for item in payload["candidates"]] == [
        "candidate-z-exact",
        "candidate-a-topic",
    ]
    assert bundle.model_dump(mode="json") == snapshot


def test_prompt_uses_only_compact_authorized_fields_and_stable_closure() -> None:
    payload = _prompt_payload(build_explanation_prompt(_bundle()))

    assert payload["target"] == {
        "confidence": 0.9,
        "event_type": "market_move",
        "target_event_id": "target-1",
        "title": "S&P 500 declined",
    }
    assert payload["claims"] == [
        {
            "confidence": 0.85,
            "id": "claim-a-topic",
            "text": "Policy uncertainty may have weighed on broad risk appetite.",
            "type": "INTERPRETATION",
        },
        {
            "confidence": 0.65,
            "id": "claim-z-exact",
            "text": "The company-specific announcement may have contributed to the move.",
            "type": "INTERPRETATION",
        },
    ]
    assert payload["evidence"] == [
        {
            "excerpt": "Officials described continued policy uncertainty.",
            "id": "evidence-a-topic",
            "quality_score": 0.92,
            "source_kind": "DOCUMENT",
        },
        {
            "excerpt": "S&P 500 moved -1.20% during the observed session.",
            "id": "evidence-target",
            "quality_score": 0.75,
            "source_kind": "MARKET_OBSERVATION",
        },
        {
            "excerpt": "The company announced a reduction in guidance.",
            "id": "evidence-z-exact",
            "quality_score": 0.95,
            "source_kind": "DOCUMENT",
        },
    ]
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "confidence_components",
        "candidate_event_id",
        "independent_publishers",
        "generator",
        "rule_version",
        "as_of",
        "locator",
        "checksum",
        "source_id",
        "importance_score",
    ):
        assert forbidden not in serialized


def test_prompt_contains_strict_output_instructions_without_markdown_fences() -> None:
    prompt = build_explanation_prompt(_bundle())

    assert "Use ONLY the supplied INPUT_JSON" in prompt
    assert "Keep all generated text concise" in prompt
    assert "Preserve candidate order" in prompt
    assert "Do not emit markdown fences" in prompt
    assert "Do not output Claim IDs or Evidence IDs" in prompt


def test_request_uses_deterministic_prompt_and_512_token_default() -> None:
    bundle = _bundle()

    request = build_explanation_request(bundle)

    assert request.prompt == build_explanation_prompt(bundle)
    assert request.max_output_tokens == 512
    assert request.reasoning is False


def _model_payload(
    *,
    target_event_id: str = "target-1",
    candidate_ids: list[str] | None = None,
) -> dict[str, object]:
    ids = (
        ["candidate-z-exact", "candidate-a-topic"]
        if candidate_ids is None
        else candidate_ids
    )
    return {
        "target_event_id": target_event_id,
        "headline": "Two supplied developments may help explain the decline",
        "summary": "The exact-entity announcement ranks ahead of the broader topic link.",
        "candidate_explanations": [
            {
                "candidate_id": candidate_id,
                "explanation": f"Explanation for {candidate_id}.",
            }
            for candidate_id in ids
        ],
        "uncertainty": "The supplied evidence supports interpretation, not proven causality.",
    }


def _llm_response(payload: object) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload),
        provider="deepseek",
        requested_model="deepseek-v4-flash",
        returned_model="deepseek-flash",
        usage=LLMUsage(input_tokens=100, output_tokens=80, total_tokens=180),
        latency_seconds=1.25,
    )


def test_parse_builds_immutable_explanation_and_preserves_trusted_metadata() -> None:
    bundle = _bundle()
    snapshot = bundle.model_dump(mode="json")

    result = parse_structured_explanation(bundle, _llm_response(_model_payload()))

    assert result == StructuredExplanation(
        target_event_id="target-1",
        headline="Two supplied developments may help explain the decline",
        summary="The exact-entity announcement ranks ahead of the broader topic link.",
        candidate_explanations=(
            CandidateExplanation(
                candidate_id="candidate-z-exact",
                explanation="Explanation for candidate-z-exact.",
            ),
            CandidateExplanation(
                candidate_id="candidate-a-topic",
                explanation="Explanation for candidate-a-topic.",
            ),
        ),
        uncertainty="The supplied evidence supports interpretation, not proven causality.",
        generation=GenerationMetadata(
            provider="deepseek",
            requested_model="deepseek-v4-flash",
            returned_model="deepseek-flash",
            input_tokens=100,
            output_tokens=80,
            total_tokens=180,
            latency_seconds=1.25,
        ),
    )
    assert bundle.model_dump(mode="json") == snapshot
    with pytest.raises(ValidationError, match="frozen"):
        result.headline = "mutated"
    with pytest.raises(ValidationError, match="frozen"):
        result.candidate_explanations[0].explanation = "mutated"


def test_structured_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CandidateExplanation(
            candidate_id="candidate-z-exact",
            explanation="Valid explanation.",
            claim_id="not-allowed",
        )


def test_parse_rejects_unknown_candidate_id() -> None:
    payload = _model_payload(candidate_ids=["candidate-z-exact", "candidate-unknown"])

    with pytest.raises(StructuredOutputReferenceError, match="unknown candidate ID"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


def test_parse_rejects_duplicate_candidate_id() -> None:
    payload = _model_payload(candidate_ids=["candidate-z-exact", "candidate-z-exact"])

    with pytest.raises(StructuredOutputReferenceError, match="duplicate candidate ID"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


def test_parse_rejects_missing_candidate_id() -> None:
    payload = _model_payload(candidate_ids=["candidate-z-exact"])

    with pytest.raises(StructuredOutputReferenceError, match="missing candidate ID"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


def test_parse_rejects_reordered_candidates() -> None:
    payload = _model_payload(candidate_ids=["candidate-a-topic", "candidate-z-exact"])

    with pytest.raises(StructuredOutputReferenceError, match="C2 candidate order"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


def test_parse_rejects_target_mismatch() -> None:
    payload = _model_payload(target_event_id="other-target")

    with pytest.raises(StructuredOutputReferenceError, match="target_event_id"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


def test_parse_rejects_malformed_json_without_repair() -> None:
    response = _llm_response(_model_payload())
    response.content = "```json\n" + response.content + "\n```"

    with pytest.raises(MalformedStructuredOutputError, match="valid JSON"):
        parse_structured_explanation(_bundle(), response)


def test_parse_rejects_missing_required_field() -> None:
    payload = _model_payload()
    del payload["summary"]

    with pytest.raises(StructuredOutputSchemaError, match="schema"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("top", "generation"),
        ("top", "requested_model"),
        ("top", "claims"),
        ("candidate", "evidence_ids"),
        ("candidate", "claim_id"),
    ],
)
def test_parse_rejects_unsupported_or_model_injected_fields(
    location: str,
    field: str,
) -> None:
    payload = _model_payload()
    if location == "top":
        payload[field] = {"provider": "model-injected"}
    else:
        candidates = payload["candidate_explanations"]
        assert isinstance(candidates, list)
        candidates[0][field] = ["model-injected-id"]

    with pytest.raises(StructuredOutputSchemaError, match="schema"):
        parse_structured_explanation(_bundle(), _llm_response(payload))


def test_generate_uses_fake_once_with_512_limit_and_preserves_bundle() -> None:
    bundle = _bundle()
    snapshot = bundle.model_dump(mode="json")
    provider = FakeLLMProvider(response=_llm_response(_model_payload()))

    result = generate_structured_explanation(bundle, provider)

    assert result.target_event_id == "target-1"
    assert result.generation.requested_model == "deepseek-v4-flash"
    assert result.generation.returned_model == "deepseek-flash"
    assert provider.requests == [build_explanation_request(bundle)]
    assert provider.requests[0].max_output_tokens == 512
    assert bundle.model_dump(mode="json") == snapshot


def test_generate_propagates_provider_error_after_one_attempt() -> None:
    bundle = _bundle()
    error = LLMTimeoutError("synthetic timeout")
    provider = FakeLLMProvider(error=error)

    with pytest.raises(LLMTimeoutError) as caught:
        generate_structured_explanation(bundle, provider)

    assert caught.value is error
    assert len(provider.requests) == 1


def test_parse_accepts_exact_empty_candidate_coverage() -> None:
    source = _bundle()
    empty_bundle = ExplanationBundle(
        target_event=source.target_event,
        driver_candidates=[],
        claims=[],
        evidence=[],
    )
    payload = _model_payload(candidate_ids=[])

    result = parse_structured_explanation(empty_bundle, _llm_response(payload))

    assert result.candidate_explanations == ()
