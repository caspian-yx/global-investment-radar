from __future__ import annotations

from collections.abc import Iterable

from radar.domain import (
    DriverCandidate,
    DriverConfidence,
    DriverRelevance,
    DriverTemporalRelation,
)

type DriverCandidateRankKey = tuple[str, int, int, int, int, int, str]

_CONFLICT_PRIORITY = {"NO": 0, "YES": 1}
_SOURCE_QUALITY_PRIORITY = {"A_B": 0, "LOW_OR_UNKNOWN": 1}
_TEMPORAL_PRIORITY = {
    DriverTemporalRelation.PRIOR: 0,
    DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS: 1,
}
_RELEVANCE_PRIORITY = {
    DriverRelevance.EXACT_ENTITY: 0,
    DriverRelevance.STRONG_TOPIC: 1,
}
_CONFIDENCE_PRIORITY = {
    DriverConfidence.HIGH: 0,
    DriverConfidence.MEDIUM: 1,
    DriverConfidence.LOW: 2,
}


def driver_candidate_rank_key(candidate: DriverCandidate) -> DriverCandidateRankKey:
    """Prioritize validated C2.1 semantics without recomputing them."""
    components = candidate.confidence_components
    return (
        candidate.target_event_id,
        _CONFLICT_PRIORITY[components["conflict"]],
        _SOURCE_QUALITY_PRIORITY[components["source_quality"]],
        _TEMPORAL_PRIORITY[candidate.temporal_relation],
        _RELEVANCE_PRIORITY[candidate.relevance_relation],
        _CONFIDENCE_PRIORITY[candidate.confidence],
        candidate.id,
    )


def rank_driver_candidates(
    candidates: Iterable[DriverCandidate],
) -> list[DriverCandidate]:
    """Return a new, total semantic order; confidence alone collapses distinct risks."""
    return sorted(candidates, key=driver_candidate_rank_key)
