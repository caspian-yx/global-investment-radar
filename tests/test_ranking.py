from __future__ import annotations

from itertools import permutations

import pytest

from radar.domain import (
    DriverCandidate,
    DriverConfidence,
    DriverRelevance,
    DriverTemporalRelation,
)
from radar.ranking import driver_candidate_rank_key, rank_driver_candidates


def candidate(
    candidate_id: str,
    *,
    target_event_id: str = "target-1",
    conflict: str = "NO",
    source_quality: str = "A_B",
    temporal: DriverTemporalRelation = DriverTemporalRelation.PRIOR,
    relevance: DriverRelevance = DriverRelevance.EXACT_ENTITY,
    confidence: DriverConfidence = DriverConfidence.MEDIUM,
) -> DriverCandidate:
    candidate_evidence_id = f"candidate-evidence-{candidate_id}"
    target_evidence_id = f"target-evidence-{target_event_id}"
    return DriverCandidate(
        id=candidate_id,
        target_event_id=target_event_id,
        candidate_event_id=f"source-event-{candidate_id}",
        interpretation_claim_id=f"claim-{candidate_id}",
        evidence_ids=sorted([candidate_evidence_id, target_evidence_id]),
        target_evidence_ids=[target_evidence_id],
        candidate_evidence_ids=[candidate_evidence_id],
        temporal_relation=temporal,
        relevance_relation=relevance,
        confidence=confidence,
        confidence_components={
            "temporal": temporal.value,
            "relevance": relevance.value,
            "source_quality": source_quality,
            "independent_publishers": (
                "2" if confidence is DriverConfidence.HIGH else "1"
            ),
            "conflict": conflict,
        },
        confidence_reasons=["Ranking fixture with explicit validated C2.1 components."],
        generator="deterministic-driver-candidate",
        rule_version="c2.1-driver-v1",
        as_of="2026-09-09T08:00:00Z",
    )


@pytest.mark.parametrize(
    ("preferred", "other"),
    [
        (
            candidate(
                "z-clean",
                conflict="NO",
                source_quality="LOW_OR_UNKNOWN",
                confidence=DriverConfidence.LOW,
            ),
            candidate(
                "a-conflicted",
                conflict="YES",
                source_quality="LOW_OR_UNKNOWN",
                confidence=DriverConfidence.LOW,
            ),
        ),
        (
            candidate(
                "z-high-quality",
                temporal=DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
                confidence=DriverConfidence.LOW,
            ),
            candidate(
                "a-low-quality",
                source_quality="LOW_OR_UNKNOWN",
                temporal=DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
                confidence=DriverConfidence.LOW,
            ),
        ),
        (
            candidate("z-prior", conflict="YES", confidence=DriverConfidence.LOW),
            candidate(
                "a-ambiguous",
                conflict="YES",
                temporal=DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
                confidence=DriverConfidence.LOW,
            ),
        ),
        (
            candidate("z-exact"),
            candidate("a-topic", relevance=DriverRelevance.STRONG_TOPIC),
        ),
    ],
    ids=("conflict", "source-quality", "temporal", "relevance"),
)
def test_rank_prioritizes_each_semantic_dimension(
    preferred: DriverCandidate, other: DriverCandidate
) -> None:
    assert [item.id for item in rank_driver_candidates([other, preferred])] == [
        preferred.id,
        other.id,
    ]


def test_rank_uses_explicit_confidence_priority_after_other_dimensions() -> None:
    items = [
        candidate("a-low", confidence=DriverConfidence.LOW),
        candidate("b-medium", confidence=DriverConfidence.MEDIUM),
        candidate("z-high", confidence=DriverConfidence.HIGH),
    ]

    assert [item.id for item in rank_driver_candidates(items)] == [
        "z-high",
        "b-medium",
        "a-low",
    ]


def test_rank_uses_candidate_id_as_final_tie_break() -> None:
    first = candidate("a-candidate")
    second = candidate("z-candidate")

    assert driver_candidate_rank_key(first) < driver_candidate_rank_key(second)
    assert [item.id for item in rank_driver_candidates([second, first])] == [
        "a-candidate",
        "z-candidate",
    ]


def test_rank_returns_a_new_list_without_mutating_input() -> None:
    original = [candidate("z-candidate"), candidate("a-candidate")]
    snapshot = list(original)

    ranked = rank_driver_candidates(original)

    assert original == snapshot
    assert ranked is not original
    assert [item.id for item in ranked] == ["a-candidate", "z-candidate"]


def test_rank_is_permutation_invariant_and_preserves_cardinality() -> None:
    items = [
        candidate("candidate-clean"),
        candidate("candidate-conflict", conflict="YES", confidence=DriverConfidence.LOW),
        candidate(
            "candidate-ambiguous",
            temporal=DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
            confidence=DriverConfidence.LOW,
        ),
        candidate("candidate-topic", relevance=DriverRelevance.STRONG_TOPIC),
    ]
    expected_ids = [item.id for item in rank_driver_candidates(items)]

    for ordering in permutations(items):
        ranked = rank_driver_candidates(ordering)
        assert [item.id for item in ranked] == expected_ids
        assert len(ranked) == len(ordering)
        assert {id(item) for item in ranked} == {id(item) for item in ordering}

    repeated = [items[0], items[0], *items[1:]]
    ranked_repeated = rank_driver_candidates(repeated)
    assert len(ranked_repeated) == len(repeated)
    assert ranked_repeated.count(items[0]) == 2


def test_rank_does_not_depend_on_as_of() -> None:
    first = candidate("a-candidate")
    second = candidate("z-candidate")
    first_newer = first.model_copy(update={"as_of": first.as_of.replace(year=2030)})
    second_older = second.model_copy(update={"as_of": second.as_of.replace(year=2020)})
    first_older = first.model_copy(update={"as_of": first.as_of.replace(year=2020)})
    second_newer = second.model_copy(update={"as_of": second.as_of.replace(year=2030)})

    assert [item.id for item in rank_driver_candidates([second_older, first_newer])] == [
        first.id,
        second.id,
    ]
    assert [item.id for item in rank_driver_candidates([second_newer, first_older])] == [
        first.id,
        second.id,
    ]


def test_rank_groups_targets_without_cross_target_priority() -> None:
    target_b_high = candidate(
        "a-target-b-high",
        target_event_id="target-b",
        confidence=DriverConfidence.HIGH,
    )
    target_a_low = candidate(
        "z-target-a-low",
        target_event_id="target-a",
        source_quality="LOW_OR_UNKNOWN",
        confidence=DriverConfidence.LOW,
    )

    assert [
        item.id for item in rank_driver_candidates([target_b_high, target_a_low])
    ] == ["z-target-a-low", "a-target-b-high"]


def test_medium_exact_outranks_high_strong_topic() -> None:
    exact = candidate("z-medium-exact", confidence=DriverConfidence.MEDIUM)
    broad = candidate(
        "a-high-topic",
        relevance=DriverRelevance.STRONG_TOPIC,
        confidence=DriverConfidence.HIGH,
    )

    assert [item.id for item in rank_driver_candidates([broad, exact])] == [
        exact.id,
        broad.id,
    ]


def test_clean_ambiguous_outranks_conflicted_prior() -> None:
    clean = candidate(
        "z-clean-ambiguous",
        temporal=DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
        confidence=DriverConfidence.LOW,
    )
    conflicted = candidate(
        "a-conflicted-prior",
        conflict="YES",
        confidence=DriverConfidence.LOW,
    )

    assert [item.id for item in rank_driver_candidates([conflicted, clean])] == [
        clean.id,
        conflicted.id,
    ]


def test_high_quality_ambiguous_outranks_low_quality_prior() -> None:
    high_quality = candidate(
        "z-high-quality-ambiguous",
        temporal=DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
        confidence=DriverConfidence.LOW,
    )
    low_quality = candidate(
        "a-low-quality-prior",
        source_quality="LOW_OR_UNKNOWN",
        confidence=DriverConfidence.LOW,
    )

    assert [item.id for item in rank_driver_candidates([low_quality, high_quality])] == [
        high_quality.id,
        low_quality.id,
    ]
