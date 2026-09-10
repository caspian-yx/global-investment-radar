from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from radar.domain import (
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
    SourceKind,
)
from radar.explanation import build_explanation_bundles

AS_OF = datetime(2026, 9, 10, 8, tzinfo=UTC)


def target_event(event_id: str, event_type: EventType = EventType.MARKET_MOVE) -> Event:
    return Event(
        id=event_id,
        event_key=f"event-key-{event_id}",
        event_type=event_type,
        title=f"Target {event_id}",
        occurred_at=AS_OF,
        first_seen_at=AS_OF,
        last_seen_at=AS_OF,
        entity_ids=["^GSPC"],
        claim_ids=[],
        evidence_ids=[f"evidence-{event_id}"],
        source_document_ids=[],
        importance_score=80,
        importance_components={"magnitude": 80},
        confidence=0.95,
    )


def driver_candidate(
    candidate_id: str,
    *,
    target_id: str = "target-1",
    claim_id: str | None = None,
    candidate_evidence_id: str | None = None,
    relevance: DriverRelevance = DriverRelevance.EXACT_ENTITY,
    confidence: DriverConfidence = DriverConfidence.MEDIUM,
    conflict: str = "NO",
) -> DriverCandidate:
    interpretation_claim_id = claim_id or f"claim-{candidate_id}"
    target_evidence_id = f"evidence-{target_id}"
    source_evidence_id = candidate_evidence_id or f"evidence-{candidate_id}"
    evidence_ids = sorted({target_evidence_id, source_evidence_id})
    return DriverCandidate(
        id=candidate_id,
        target_event_id=target_id,
        candidate_event_id=f"source-event-{candidate_id}",
        interpretation_claim_id=interpretation_claim_id,
        evidence_ids=evidence_ids,
        target_evidence_ids=[target_evidence_id],
        candidate_evidence_ids=[source_evidence_id],
        temporal_relation=DriverTemporalRelation.PRIOR,
        relevance_relation=relevance,
        confidence=confidence,
        confidence_components={
            "temporal": "PRIOR",
            "relevance": relevance.value,
            "source_quality": "A_B",
            "independent_publishers": (
                "2" if confidence is DriverConfidence.HIGH else "1"
            ),
            "conflict": conflict,
        },
        confidence_reasons=["Existing C2.1-shaped candidate fixture."],
        generator="deterministic-driver-candidate",
        rule_version="c2.1-driver-v1",
        as_of=AS_OF,
    )


def claim_for(candidate: DriverCandidate) -> Claim:
    return Claim(
        id=candidate.interpretation_claim_id,
        claim_type=ClaimType.INTERPRETATION,
        text=f"Interpretation for {candidate.id}",
        evidence_ids=candidate.evidence_ids,
        confidence={
            DriverConfidence.LOW: 0.35,
            DriverConfidence.MEDIUM: 0.65,
            DriverConfidence.HIGH: 0.85,
        }[candidate.confidence],
        created_by="deterministic-driver-candidate",
        as_of=AS_OF,
    )


def evidence(
    evidence_id: str,
    source_kind: SourceKind = SourceKind.DOCUMENT,
) -> Evidence:
    source_id = f"source-{evidence_id}"
    return Evidence(
        id=evidence_id,
        source_kind=source_kind,
        source_id=source_id,
        source_document_id=source_id if source_kind is SourceKind.DOCUMENT else None,
        locator={"observed_at": AS_OF.isoformat()},
        excerpt=f"Evidence {evidence_id}",
        checksum="a" * 64,
        quality_score=0.9,
    )


def evidence_for(*candidates: DriverCandidate) -> list[Evidence]:
    evidence_kinds = {
        evidence_id: source_kind
        for candidate in candidates
        for source_kind, evidence_ids in (
            (SourceKind.MARKET_OBSERVATION, candidate.target_evidence_ids),
            (SourceKind.DOCUMENT, candidate.candidate_evidence_ids),
        )
        for evidence_id in evidence_ids
    }
    return [
        evidence(evidence_id, evidence_kinds[evidence_id])
        for evidence_id in sorted(evidence_kinds)
    ]


def test_explanation_bundle_exposes_only_the_approved_contract_fields() -> None:
    assert set(ExplanationBundle.model_fields) == {
        "target_event",
        "driver_candidates",
        "claims",
        "evidence",
    }


def test_explanation_bundle_rejects_candidates_outside_c2_2_order() -> None:
    target = target_event("target-1")
    medium_exact = driver_candidate("z-medium-exact")
    high_topic = driver_candidate(
        "a-high-topic",
        relevance=DriverRelevance.STRONG_TOPIC,
        confidence=DriverConfidence.HIGH,
    )
    candidates = [high_topic, medium_exact]

    with pytest.raises(ValidationError, match="C2.2 semantic order"):
        ExplanationBundle(
            target_event=target,
            driver_candidates=candidates,
            claims=sorted((claim_for(item) for item in candidates), key=lambda item: item.id),
            evidence=evidence_for(*candidates),
        )


def test_build_bundles_ranks_candidates_and_selects_only_referenced_closure() -> None:
    target = target_event("target-1")
    medium_exact = driver_candidate("z-medium-exact")
    high_topic = driver_candidate(
        "a-high-topic",
        relevance=DriverRelevance.STRONG_TOPIC,
        confidence=DriverConfidence.HIGH,
        candidate_evidence_id=medium_exact.candidate_evidence_ids[0],
    )
    unrelated_claim = claim_for(driver_candidate("unrelated"))
    unrelated_evidence = evidence("unrelated-evidence")

    bundles = build_explanation_bundles(
        target_events=[target],
        driver_candidates=[high_topic, medium_exact],
        claims=[unrelated_claim, claim_for(high_topic), claim_for(medium_exact)],
        evidence=[
            unrelated_evidence,
            *reversed(evidence_for(high_topic, medium_exact)),
        ],
    )

    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle.target_event == target
    assert [item.id for item in bundle.driver_candidates] == [
        medium_exact.id,
        high_topic.id,
    ]
    assert [item.id for item in bundle.claims] == sorted(
        [medium_exact.interpretation_claim_id, high_topic.interpretation_claim_id]
    )
    assert [item.id for item in bundle.evidence] == sorted(
        set([*medium_exact.evidence_ids, *high_topic.evidence_ids])
    )
    assert unrelated_claim not in bundle.claims
    assert unrelated_evidence not in bundle.evidence


def test_build_bundles_is_permutation_invariant_and_does_not_mutate_inputs() -> None:
    targets = [target_event("target-b"), target_event("target-a", EventType.RISK)]
    candidates = [
        driver_candidate("candidate-b", target_id="target-b"),
        driver_candidate(
            "candidate-a",
            target_id="target-a",
            confidence=DriverConfidence.LOW,
            conflict="YES",
        ),
    ]
    claims = [claim_for(item) for item in candidates]
    evidence_items = evidence_for(*candidates)
    snapshots = [
        [item.model_dump(mode="json") for item in items]
        for items in (targets, candidates, claims, evidence_items)
    ]

    first = build_explanation_bundles(
        target_events=targets,
        driver_candidates=candidates,
        claims=claims,
        evidence=evidence_items,
    )
    second = build_explanation_bundles(
        target_events=list(reversed(targets)),
        driver_candidates=list(reversed(candidates)),
        claims=list(reversed(claims)),
        evidence=list(reversed(evidence_items)),
    )

    assert [item.target_event.id for item in first] == ["target-a", "target-b"]
    assert [item.model_dump_json() for item in first] == [
        item.model_dump_json() for item in second
    ]
    assert snapshots == [
        [item.model_dump(mode="json") for item in items]
        for items in (targets, candidates, claims, evidence_items)
    ]
    assert first[0].driver_candidates[0].confidence is DriverConfidence.LOW


def test_build_bundles_includes_empty_bundle_for_each_eligible_target() -> None:
    targets = [target_event("target-b", EventType.RISK), target_event("target-a")]

    bundles = build_explanation_bundles(
        target_events=targets,
        driver_candidates=[],
        claims=[],
        evidence=[],
    )

    assert [item.target_event.id for item in bundles] == ["target-a", "target-b"]
    assert all(item.driver_candidates == [] for item in bundles)
    assert all(item.claims == [] for item in bundles)
    assert all(item.evidence == [] for item in bundles)


@pytest.mark.parametrize(
    "targets",
    [
        [target_event("other-target")],
        [target_event("target-1", EventType.POLICY)],
    ],
    ids=("unknown-target", "ineligible-target"),
)
def test_build_bundles_rejects_candidate_without_matching_eligible_target(
    targets: list[Event],
) -> None:
    candidate = driver_candidate("candidate-1")

    with pytest.raises(ValueError, match="unknown or ineligible target Event"):
        build_explanation_bundles(
            target_events=targets,
            driver_candidates=[candidate],
            claims=[claim_for(candidate)],
            evidence=evidence_for(candidate),
        )


def test_build_bundles_rejects_duplicate_candidate_id() -> None:
    target = target_event("target-1")
    candidate = driver_candidate("candidate-1")

    with pytest.raises(ValueError, match="Duplicate DriverCandidate ID candidate-1"):
        build_explanation_bundles(
            target_events=[target],
            driver_candidates=[candidate, candidate.model_copy(deep=True)],
            claims=[claim_for(candidate)],
            evidence=evidence_for(candidate),
        )


def test_build_bundles_rejects_missing_interpretation_claim() -> None:
    candidate = driver_candidate("candidate-1")

    with pytest.raises(ValueError, match="missing interpretation Claim"):
        build_explanation_bundles(
            target_events=[target_event("target-1")],
            driver_candidates=[candidate],
            claims=[],
            evidence=evidence_for(candidate),
        )


def test_build_bundles_rejects_missing_evidence() -> None:
    candidate = driver_candidate("candidate-1")

    with pytest.raises(ValueError, match="missing Evidence"):
        build_explanation_bundles(
            target_events=[target_event("target-1")],
            driver_candidates=[candidate],
            claims=[claim_for(candidate)],
            evidence=[],
        )


@pytest.mark.parametrize("duplicate_kind", ["claim", "evidence"])
def test_build_bundles_rejects_conflicting_duplicate_linked_objects(
    duplicate_kind: str,
) -> None:
    candidate = driver_candidate("candidate-1")
    linked_claim = claim_for(candidate)
    linked_evidence = evidence_for(candidate)
    claims = [linked_claim]
    evidence_items = linked_evidence
    if duplicate_kind == "claim":
        claims.append(linked_claim.model_copy(update={"text": "Conflicting text"}))
    else:
        first = linked_evidence[0]
        evidence_items = [
            *linked_evidence,
            first.model_copy(update={"excerpt": "Conflicting excerpt"}),
        ]

    with pytest.raises(ValueError, match="Conflicting duplicate payload for ID"):
        build_explanation_bundles(
            target_events=[target_event("target-1")],
            driver_candidates=[candidate],
            claims=claims,
            evidence=evidence_items,
        )
