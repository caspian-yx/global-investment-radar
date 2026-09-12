from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.domain import (
    DriverCandidate,
    Event,
    EventType,
    ExplanationBundle,
    MarketObservation,
    ProviderRunStatus,
    ProviderStatus,
    ReportQualityAssessment,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    Story,
    StructuredExplanation,
    stable_id,
)
from radar.impact import (
    AssociationConfidence,
    EpistemicStatus,
    ImpactMagnitude,
    ImpactReason,
    ImpactTargetKind,
    MarketImpactRecord,
)
from radar.importance import (
    C7Score,
    ImportanceBand,
    PriorityBand,
    RelevanceBand,
)

C9_RULE_VERSION = "c9-v1"


class SelectionReason(StrEnum):
    PRIMARY_CRITICAL = "PRIMARY_CRITICAL"
    PRIMARY_HIGH_PRIORITY = "PRIMARY_HIGH_PRIORITY"
    DIVERSITY_REGION = "DIVERSITY_REGION"
    DIVERSITY_EVENT_TYPE = "DIVERSITY_EVENT_TYPE"
    SUPPRESSED_REDUNDANT = "SUPPRESSED_REDUNDANT"
    UNDER_RADAR_MATERIAL = "UNDER_RADAR_MATERIAL"
    UNDER_RADAR_RELEVANT = "UNDER_RADAR_RELEVANT"
    WATCH_UNRESOLVED = "WATCH_UNRESOLVED"
    WATCH_MARKET_REACTION = "WATCH_MARKET_REACTION"


class ExplanationBasis(StrEnum):
    STRUCTURED_AI = "STRUCTURED_AI"
    C2_EVIDENCE = "C2_EVIDENCE"
    DETERMINISTIC_SEMANTICS = "DETERMINISTIC_SEMANTICS"
    CONSERVATIVE_FALLBACK = "CONSERVATIVE_FALLBACK"


class BriefingSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    publisher: str = Field(min_length=1, pattern=r".*\S.*")
    url: str = Field(min_length=1, pattern=r".*\S.*")
    category: SourceCategory
    role: SourceRole
    tier: SourceTier


class BriefingMarketReaction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    impact_id: str = Field(min_length=1, pattern=r".*\S.*")
    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    target_symbol: str = Field(min_length=1, pattern=r".*\S.*")
    target_name: str = Field(min_length=1, pattern=r".*\S.*")
    percentage_move: float
    magnitude: ImpactMagnitude
    association_confidence: AssociationConfidence
    epistemic_status: EpistemicStatus
    summary: str = Field(min_length=1, pattern=r".*\S.*")
    uncertainty: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    c8_rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    reason_codes: tuple[ImpactReason, ...]


class BriefingStoryCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    story_id: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    event_type: EventType
    region: str | None = None
    entity_ids: tuple[str, ...]
    occurred_at: datetime
    priority_band: PriorityBand
    importance_band: ImportanceBand
    relevance_band: RelevanceBand
    importance_score: float
    relevance_score: float
    what_happened: str = Field(min_length=1, pattern=r".*\S.*")
    why_it_matters: str = Field(min_length=1, pattern=r".*\S.*")
    explanation_basis: ExplanationBasis
    explanation_reference_ids: tuple[str, ...]
    market_reactions: tuple[BriefingMarketReaction, ...] = Field(max_length=2)
    evidence_label: str = Field(min_length=1, pattern=r".*\S.*")
    sources: tuple[BriefingSource, ...] = Field(max_length=3)
    supporting_source_count: int = Field(ge=0)
    selection_reasons: tuple[SelectionReason, ...]

    @model_validator(mode="after")
    def validate_card(self) -> BriefingStoryCard:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("C9 Story card time must be timezone-aware")
        if tuple(sorted(set(self.entity_ids))) != self.entity_ids:
            raise ValueError("C9 Story card entities must be canonical")
        if tuple(sorted(set(self.explanation_reference_ids))) != self.explanation_reference_ids:
            raise ValueError("C9 explanation references must be canonical")
        return self


class OvernightBullet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    text: str = Field(min_length=1, pattern=r".*\S.*")


class UnderRadarItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    note: str = Field(min_length=1, pattern=r".*\S.*")
    reasons: tuple[SelectionReason, ...] = Field(min_length=1)
    sources: tuple[BriefingSource, ...] = Field(max_length=2)


class WatchItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    text: str = Field(min_length=1, pattern=r".*\S.*")
    reason: SelectionReason
    target_symbols: tuple[str, ...]

    @model_validator(mode="after")
    def validate_monitoring_action(self) -> WatchItem:
        if self.text.strip().casefold() == self.headline.strip().casefold():
            raise ValueError("C9 Watch item must add a monitoring action")
        action_terms = ("monitor", "watch", "observe", "follow", "clarify")
        if not any(term in self.text.casefold() for term in action_terms):
            raise ValueError("C9 Watch item must communicate a monitoring action")
        return self


class MarketSnapshotItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    symbol: str = Field(min_length=1, pattern=r".*\S.*")
    name: str = Field(min_length=1, pattern=r".*\S.*")
    close: float
    change_pct: float
    currency: str | None = None
    observed_at: datetime


class MarketSnapshotGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str = Field(min_length=1, pattern=r".*\S.*")
    items: tuple[MarketSnapshotItem, ...] = Field(min_length=1)


class BriefingDataQuality(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: str = Field(min_length=1, pattern=r".*\S.*")
    documents: int = Field(ge=0)
    events: int = Field(ge=0)
    markets: int = Field(ge=0)
    sources_healthy: int = Field(ge=0)
    sources_total: int = Field(ge=0)
    ai_status: str = Field(min_length=1, pattern=r".*\S.*")
    has_validated_market_reactions: bool


class C9Diagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    eligible_stories: int = Field(ge=0)
    primary_selected: int = Field(ge=0)
    under_radar_selected: int = Field(ge=0)
    suppressed_redundant: int = Field(ge=0)
    priority_distribution: dict[str, int]
    region_diversity: int = Field(ge=0)
    event_type_diversity: int = Field(ge=0)
    stories_with_c8_impacts: int = Field(ge=0)
    stories_with_structured_ai: int = Field(ge=0)
    deterministic_fallback_count: int = Field(ge=0)
    watch_today_count: int = Field(ge=0)
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")


class DailyIntelligenceBrief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str = Field(min_length=1, pattern=r".*\S.*")
    run_id: str = Field(min_length=1, pattern=r".*\S.*")
    report_date: str = Field(min_length=1, pattern=r".*\S.*")
    view_id: str = Field(min_length=1, pattern=r".*\S.*")
    view_name: str = Field(min_length=1, pattern=r".*\S.*")
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    composed_at: datetime
    ai_status: str = Field(min_length=1, pattern=r".*\S.*")
    primary_stories: tuple[BriefingStoryCard, ...]
    overnight: tuple[OvernightBullet, ...] = Field(max_length=5)
    market_reactions: tuple[BriefingMarketReaction, ...]
    under_radar: tuple[UnderRadarItem, ...] = Field(max_length=3)
    watch_today: tuple[WatchItem, ...] = Field(max_length=3)
    market_snapshot: tuple[MarketSnapshotGroup, ...]
    data_quality: BriefingDataQuality
    diagnostics: C9Diagnostics

    @model_validator(mode="after")
    def validate_identity(self) -> DailyIntelligenceBrief:
        expected = stable_id("c9-briefing", f"{self.run_id}|{self.view_id}|{self.rule_version}")
        if self.id != expected:
            raise ValueError("C9 briefing identity must be deterministic")
        if self.composed_at.tzinfo is None or self.composed_at.utcoffset() is None:
            raise ValueError("C9 composition time must be timezone-aware")
        primary_ids = tuple(item.event_id for item in self.primary_stories)
        if tuple(item.event_id for item in self.overnight) != primary_ids[:5]:
            raise ValueError("C9 overnight bullets must follow primary order")
        if set(primary_ids) & {item.event_id for item in self.under_radar}:
            raise ValueError("C9 primary and Under Radar identities must be disjoint")
        return self


class SelectedBriefingCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event: Event
    story: Story | None
    score: C7Score
    documents: tuple[SourceDocument, ...]
    reasons: tuple[SelectionReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate(self) -> SelectedBriefingCandidate:
        if self.event.id != self.score.event_id:
            raise ValueError("C9 candidate Event must match its C7 score")
        if (self.story.id if self.story is not None else None) != self.score.story_id:
            raise ValueError("C9 candidate Story must match its C7 score")
        if tuple(sorted(set(self.reasons), key=lambda item: item.value)) != self.reasons:
            raise ValueError("C9 selection reasons must be canonical")
        document_ids = tuple(item.id for item in self.documents)
        if tuple(sorted(set(document_ids))) != document_ids:
            raise ValueError("C9 candidate documents must be canonical")
        return self


class C9SelectionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    view_id: str = Field(min_length=1, pattern=r".*\S.*")
    primary: tuple[SelectedBriefingCandidate, ...]
    remaining: tuple[SelectedBriefingCandidate, ...]
    suppressed_event_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_selection(self) -> C9SelectionResult:
        primary_ids = tuple(item.event.id for item in self.primary)
        remaining_ids = tuple(item.event.id for item in self.remaining)
        if len(set(primary_ids)) != len(primary_ids):
            raise ValueError("C9 primary Events must be unique")
        if len(set(remaining_ids)) != len(remaining_ids):
            raise ValueError("C9 remaining Events must be unique")
        if set(primary_ids) & set(remaining_ids):
            raise ValueError("C9 primary and remaining Events must be disjoint")
        if tuple(sorted(set(self.suppressed_event_ids))) != self.suppressed_event_ids:
            raise ValueError("C9 suppressed Event IDs must be canonical")
        return self


_PRIORITY_ORDER = {
    PriorityBand.MUST_READ: 5,
    PriorityBand.IMPORTANT: 4,
    PriorityBand.WATCH: 3,
    PriorityBand.BACKGROUND: 2,
    PriorityBand.IGNORE: 1,
}
_IMPORTANCE_ORDER = {
    ImportanceBand.CRITICAL: 4,
    ImportanceBand.MATERIAL: 3,
    ImportanceBand.ROUTINE: 2,
    ImportanceBand.LOW: 1,
}
_RELEVANCE_ORDER = {
    RelevanceBand.HIGH: 4,
    RelevanceBand.MEDIUM: 3,
    RelevanceBand.LOW: 2,
    RelevanceBand.NONE: 1,
}


def _base_key(item: SelectedBriefingCandidate) -> tuple[object, ...]:
    score = item.score
    return (
        -_PRIORITY_ORDER[score.priority_band],
        -_IMPORTANCE_ORDER[score.importance_band],
        -_RELEVANCE_ORDER[score.relevance_band],
        -score.importance_score,
        -score.relevance_score,
        -item.event.occurred_at.timestamp(),
        item.event.id,
    )


def _strength(item: SelectedBriefingCandidate) -> tuple[int, int]:
    return (
        _PRIORITY_ORDER[item.score.priority_band],
        _IMPORTANCE_ORDER[item.score.importance_band],
    )


def _suppression_key(item: SelectedBriefingCandidate) -> tuple[object, ...] | None:
    if item.story is None:
        return None
    profile = item.story.identity_profile
    if not (
        profile.authority_id
        and profile.release_domain
        and profile.reference_period
        and item.story.region
    ):
        return None
    return (
        profile.authority_id,
        profile.release_domain,
        profile.reference_period,
        item.story.event_type.value,
        item.story.region,
        tuple(profile.canonical_entity_ids),
        tuple(topic.value for topic in profile.topics),
        tuple(profile.normalized_title_tokens),
        tuple(profile.action_terms),
        tuple(profile.strict_numeric_facts),
    )


def _canonical_reasons(*reasons: SelectionReason) -> tuple[SelectionReason, ...]:
    return tuple(sorted(set(reasons), key=lambda item: item.value))


def _with_reasons(
    item: SelectedBriefingCandidate, *reasons: SelectionReason
) -> SelectedBriefingCandidate:
    return item.model_copy(update={"reasons": _canonical_reasons(*reasons)})


def _primary_eligible(item: SelectedBriefingCandidate) -> bool:
    return item.score.priority_band in {PriorityBand.MUST_READ, PriorityBand.IMPORTANT}


def _remaining_eligible(item: SelectedBriefingCandidate) -> bool:
    if item.score.priority_band in {PriorityBand.MUST_READ, PriorityBand.IMPORTANT}:
        return True
    return (
        item.score.priority_band is PriorityBand.WATCH
        and item.score.importance_band is not ImportanceBand.LOW
        and item.score.relevance_band is RelevanceBand.HIGH
    )


def _candidate_records(
    events: Iterable[Event],
    stories: Iterable[Story],
    documents: Iterable[SourceDocument],
    scores: Iterable[C7Score],
    requested_view_id: str | None,
) -> tuple[str, list[SelectedBriefingCandidate]]:
    event_map = {item.id: item for item in events}
    story_map = {stable_id("event", item.id): item for item in stories}
    document_map = {item.id: item for item in documents}
    score_items = list(scores)
    view_ids = {item.view_id for item in score_items}
    if len(view_ids) > 1:
        raise ValueError("C9 selection requires exactly one C7 view")
    if view_ids:
        score_view_id = next(iter(view_ids))
        if requested_view_id is not None and requested_view_id != score_view_id:
            raise ValueError("C9 requested view does not match its C7 scores")
        resolved_view_id = score_view_id
    elif requested_view_id is not None:
        resolved_view_id = requested_view_id
    else:
        raise ValueError("C9 empty selection requires an explicit view")
    score_ids = [item.event_id for item in score_items]
    if len(set(score_ids)) != len(score_ids):
        raise ValueError("C9 selection requires one C7 score per Event")
    candidates: list[SelectedBriefingCandidate] = []
    for score in score_items:
        event = event_map.get(score.event_id)
        if event is None or event.event_type in {EventType.MARKET_MOVE, EventType.RISK}:
            continue
        story = story_map.get(event.id)
        if score.story_id != (story.id if story is not None else None):
            raise ValueError("C9 C7 Story identity is unavailable or inconsistent")
        missing_document_ids = sorted(set(event.source_document_ids) - set(document_map))
        if missing_document_ids:
            raise ValueError(
                "C9 Event provenance is missing SourceDocument: " + ", ".join(missing_document_ids)
            )
        event_documents = tuple(
            document_map[item_id]
            for item_id in sorted(set(event.source_document_ids))
            if item_id in document_map
        )
        candidates.append(
            SelectedBriefingCandidate(
                event=event,
                story=story,
                score=score,
                documents=event_documents,
                reasons=(SelectionReason.PRIMARY_HIGH_PRIORITY,),
            )
        )
    return resolved_view_id, sorted(candidates, key=_base_key)


def _diversity_key(
    item: SelectedBriefingCandidate,
    selected: list[SelectedBriefingCandidate],
) -> tuple[object, ...]:
    event_type_counts = Counter(candidate.event.event_type for candidate in selected)
    region_counts = Counter(
        candidate.story.region if candidate.story else None for candidate in selected
    )
    entity_counts = Counter(
        entity_id for candidate in selected for entity_id in candidate.event.entity_ids[:1]
    )
    region = item.story.region if item.story else None
    entity = item.event.entity_ids[0] if item.event.entity_ids else None
    saturation = (
        int(event_type_counts[item.event.event_type] >= 2)
        + int(region is not None and region_counts[region] >= 3)
        + int(entity is not None and entity_counts[entity] >= 2)
    )
    novelty = int(item.event.event_type not in event_type_counts) + int(
        region is not None and region not in region_counts
    )
    return (saturation, -novelty, *_base_key(item))


def select_daily_stories(
    *,
    events: Iterable[Event],
    stories: Iterable[Story],
    documents: Iterable[SourceDocument],
    scores: Iterable[C7Score],
    max_primary: int = 5,
    view_id: str | None = None,
) -> C9SelectionResult:
    if max_primary < 1:
        raise ValueError("C9 primary limit must be positive")
    resolved_view_id, candidates = _candidate_records(events, stories, documents, scores, view_id)
    primary_pool = [item for item in candidates if _primary_eligible(item)]
    critical = [
        _with_reasons(item, SelectionReason.PRIMARY_CRITICAL)
        for item in primary_pool
        if item.score.importance_band is ImportanceBand.CRITICAL
    ]
    selected: list[SelectedBriefingCandidate] = list(critical)
    critical_ids = {item.event.id for item in critical}
    seen_suppression_keys = {
        key for item in critical if (key := _suppression_key(item)) is not None
    }
    deduplicated: list[SelectedBriefingCandidate] = []
    suppressed_ids: list[str] = []
    for item in primary_pool:
        if item.event.id in critical_ids:
            continue
        key = _suppression_key(item)
        if key is not None and key in seen_suppression_keys:
            suppressed_ids.append(item.event.id)
            continue
        if key is not None:
            seen_suppression_keys.add(key)
        deduplicated.append(item)

    remaining_pool = deduplicated
    while remaining_pool and len(selected) < max(max_primary, len(critical)):
        best_strength = max(_strength(item) for item in remaining_pool)
        same_strength = [item for item in remaining_pool if _strength(item) == best_strength]
        chosen = min(same_strength, key=lambda item: _diversity_key(item, selected))
        reasons = [SelectionReason.PRIMARY_HIGH_PRIORITY]
        if selected:
            selected_regions = {
                item.story.region for item in selected if item.story and item.story.region
            }
            selected_types = {item.event.event_type for item in selected}
            if chosen.story and chosen.story.region not in selected_regions:
                reasons.append(SelectionReason.DIVERSITY_REGION)
            if chosen.event.event_type not in selected_types:
                reasons.append(SelectionReason.DIVERSITY_EVENT_TYPE)
        selected.append(_with_reasons(chosen, *reasons))
        remaining_pool = [item for item in remaining_pool if item.event.id != chosen.event.id]

    selected_ids = {item.event.id for item in selected}
    remaining = tuple(
        item
        for item in candidates
        if item.event.id not in selected_ids
        and item.event.id not in set(suppressed_ids)
        and _remaining_eligible(item)
    )
    return C9SelectionResult(
        rule_version=C9_RULE_VERSION,
        view_id=resolved_view_id,
        primary=tuple(selected),
        remaining=remaining,
        suppressed_event_ids=tuple(sorted(suppressed_ids)),
    )


_EVENT_TYPE_CONTEXT = {
    EventType.POLICY: "It may reset policy expectations across rates and risk assets.",
    EventType.MACRO: "It updates the macro path used to assess growth and policy risk.",
    EventType.EARNINGS: "It updates the company's reported operating trajectory.",
    EventType.GUIDANCE: "It changes the forward operating signal available to investors.",
    EventType.M_AND_A: "It may alter industry structure and capital allocation.",
    EventType.PRODUCT: "Its importance depends on adoption and measurable commercial impact.",
    EventType.SUPPLY_CHAIN: "It may affect capacity, availability, or industry cost conditions.",
    EventType.LEGAL: "It may change regulatory, financial, or operating constraints.",
}
_MAGNITUDE_ORDER = {
    ImpactMagnitude.EXTREME: 5,
    ImpactMagnitude.LARGE: 4,
    ImpactMagnitude.MODERATE: 3,
    ImpactMagnitude.SMALL: 2,
    ImpactMagnitude.FLAT: 1,
}
_ASSOCIATION_ORDER = {
    AssociationConfidence.HIGH: 4,
    AssociationConfidence.MEDIUM: 3,
    AssociationConfidence.LOW: 2,
    AssociationConfidence.NONE: 1,
}
_GROUP_ORDER = ("US", "Asia", "Europe", "FX", "Commodities", "Crypto", "Risk")


def _source_snapshot(document: SourceDocument) -> BriefingSource:
    return BriefingSource(
        publisher=document.publisher,
        url=document.canonical_url,
        category=document.source_category,
        role=document.source_role,
        tier=document.source_tier,
    )


def _sources(candidate: SelectedBriefingCandidate, limit: int) -> tuple[BriefingSource, ...]:
    documents = list(candidate.documents)
    if candidate.story is not None:
        presentation_document_id = candidate.story.presentation_document_id
        documents.sort(
            key=lambda item: (
                item.id != presentation_document_id,
                item.source_role is not SourceRole.PRIMARY,
                item.source_tier.value,
                item.publisher.casefold(),
                item.id,
            )
        )
    return tuple(_source_snapshot(item) for item in documents[:limit])


def _explanation(
    event_id: str,
    bundles: Iterable[ExplanationBundle],
    structured: Mapping[str, StructuredExplanation],
    event_type: EventType,
) -> tuple[str, ExplanationBasis, tuple[str, ...]]:
    matching: list[tuple[ExplanationBundle, DriverCandidate]] = []
    for bundle in bundles:
        for candidate in bundle.driver_candidates:
            if candidate.candidate_event_id == event_id:
                matching.append((bundle, candidate))
    matching.sort(
        key=lambda item: (
            item[0].target_event.occurred_at,
            item[0].target_event.id,
            item[1].id,
        ),
        reverse=True,
    )
    for bundle, candidate in matching:
        candidate_id = candidate.id
        ai = structured.get(bundle.target_event.id)
        if ai is None or ai.target_event_id != bundle.target_event.id:
            continue
        candidate_copy = next(
            (item for item in ai.candidate_explanations if item.candidate_id == candidate_id),
            None,
        )
        if candidate_copy is not None:
            return (
                candidate_copy.explanation,
                ExplanationBasis.STRUCTURED_AI,
                tuple(
                    sorted(
                        {
                            bundle.target_event.id,
                            candidate_id,
                            *candidate.evidence_ids,
                        }
                    )
                ),
            )
    for bundle, candidate in matching:
        candidate_id = candidate.id
        claim_id = candidate.interpretation_claim_id
        claim = next((item for item in bundle.claims if item.id == claim_id), None)
        if claim is not None:
            return (
                claim.text,
                ExplanationBasis.C2_EVIDENCE,
                tuple(
                    sorted(
                        {
                            bundle.target_event.id,
                            candidate_id,
                            claim.id,
                            *candidate.evidence_ids,
                        }
                    )
                ),
            )
    deterministic = _EVENT_TYPE_CONTEXT.get(event_type)
    if deterministic is not None:
        return deterministic, ExplanationBasis.DETERMINISTIC_SEMANTICS, ()
    return (
        "No evidence-backed market-driver interpretation is available yet.",
        ExplanationBasis.CONSERVATIVE_FALLBACK,
        (),
    )


def _reaction_key(record: MarketImpactRecord) -> tuple[object, ...]:
    kind = record.transmission_basis.target_kind if record.transmission_basis is not None else None
    kind_order = {
        ImpactTargetKind.DIRECT_TARGET_REACTION: 3,
        ImpactTargetKind.REGIONAL_MARKET_REACTION: 2,
        ImpactTargetKind.MACRO_MARKET_REACTION: 2,
        ImpactTargetKind.SECTOR_REACTION: 1,
        ImpactTargetKind.RISK_REACTION: 1,
        None: 0,
    }
    return (
        -kind_order[kind],
        -_MAGNITUDE_ORDER[record.magnitude],
        -_ASSOCIATION_ORDER[record.association_confidence],
        record.target.symbol,
        record.id,
    )


def _reaction_snapshot(record: MarketImpactRecord) -> BriefingMarketReaction:
    move = record.measurement.percentage_move
    direction = "rose" if move > 0 else "fell" if move < 0 else "was unchanged"
    if record.magnitude in {ImpactMagnitude.FLAT, ImpactMagnitude.SMALL}:
        summary = (
            f"{record.target.name} {direction} {abs(move):.2f}% in the completed daily "
            "bar; the immediate reaction was limited."
        )
    elif record.epistemic_status is EpistemicStatus.ASSOCIATED:
        summary = (
            f"{record.target.name} {direction} {abs(move):.2f}% in the completed daily "
            "bar; C8 records an associated, not causal, reaction."
        )
    else:
        summary = f"{record.target.name} {direction} {abs(move):.2f}% in the completed daily bar."
    ambiguous = ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS in record.reason_codes
    uncertainty = None
    if (
        record.association_confidence
        in {
            AssociationConfidence.LOW,
            AssociationConfidence.NONE,
        }
        or ambiguous
    ):
        uncertainty = "Attribution remains uncertain because the association is weak or ambiguous."
    return BriefingMarketReaction(
        impact_id=record.id,
        event_id=record.event_id,
        target_symbol=record.target.symbol,
        target_name=record.target.name,
        percentage_move=move,
        magnitude=record.magnitude,
        association_confidence=record.association_confidence,
        epistemic_status=record.epistemic_status,
        summary=summary,
        uncertainty=uncertainty,
        c8_rule_version=record.rule_version,
        reason_codes=record.reason_codes,
    )


def _card(
    candidate: SelectedBriefingCandidate,
    *,
    bundles: tuple[ExplanationBundle, ...],
    structured: Mapping[str, StructuredExplanation],
    impacts_by_event: Mapping[str, tuple[MarketImpactRecord, ...]],
) -> BriefingStoryCard:
    event = candidate.event
    explanation, basis, reference_ids = _explanation(
        event.id, bundles, structured, event.event_type
    )
    impacts = tuple(sorted(impacts_by_event.get(event.id, ()), key=_reaction_key)[:2])
    sources = _sources(candidate, 3)
    return BriefingStoryCard(
        event_id=event.id,
        story_id=candidate.story.id if candidate.story is not None else None,
        headline=event.title,
        event_type=event.event_type,
        region=candidate.story.region if candidate.story is not None else None,
        entity_ids=tuple(sorted(set(event.entity_ids))),
        occurred_at=event.occurred_at.astimezone(UTC),
        priority_band=candidate.score.priority_band,
        importance_band=candidate.score.importance_band,
        relevance_band=candidate.score.relevance_band,
        importance_score=candidate.score.importance_score,
        relevance_score=candidate.score.relevance_score,
        what_happened=event.title,
        why_it_matters=explanation,
        explanation_basis=basis,
        explanation_reference_ids=reference_ids,
        market_reactions=tuple(_reaction_snapshot(item) for item in impacts),
        evidence_label=(
            f"{len(candidate.documents)} verified source"
            f"{'s' if len(candidate.documents) != 1 else ''}"
        ),
        sources=sources,
        supporting_source_count=max(0, len(candidate.documents) - len(sources)),
        selection_reasons=candidate.reasons,
    )


def _under_radar(selection: C9SelectionResult) -> tuple[UnderRadarItem, ...]:
    items: list[UnderRadarItem] = []
    for candidate in selection.remaining:
        if len(items) >= 3:
            break
        if (
            candidate.score.importance_band
            in {
                ImportanceBand.CRITICAL,
                ImportanceBand.MATERIAL,
            }
            or candidate.score.priority_band is PriorityBand.IMPORTANT
        ):
            reason = SelectionReason.UNDER_RADAR_MATERIAL
        elif (
            candidate.score.importance_band is ImportanceBand.ROUTINE
            and candidate.score.relevance_band is RelevanceBand.HIGH
        ):
            reason = SelectionReason.UNDER_RADAR_RELEVANT
        else:
            continue
        items.append(
            UnderRadarItem(
                event_id=candidate.event.id,
                headline=candidate.event.title,
                note=(
                    "Material context that did not displace a stronger primary item."
                    if reason is SelectionReason.UNDER_RADAR_MATERIAL
                    else "Highly relevant context to keep in view."
                ),
                reasons=(reason,),
                sources=_sources(candidate, 2),
            )
        )
    return tuple(items)


def _watch_items(
    selection: C9SelectionResult,
    impacts_by_event: Mapping[str, tuple[MarketImpactRecord, ...]],
) -> tuple[WatchItem, ...]:
    results: list[WatchItem] = []
    allowed_types = {
        EventType.POLICY,
        EventType.MACRO,
        EventType.LEGAL,
        EventType.GUIDANCE,
        EventType.SUPPLY_CHAIN,
    }
    for candidate in (*selection.primary, *selection.remaining):
        if len(results) >= 3:
            break
        impacts = impacts_by_event.get(candidate.event.id, ())
        if impacts:
            symbols = tuple(sorted({item.target.symbol for item in impacts}))
            results.append(
                WatchItem(
                    event_id=candidate.event.id,
                    headline=candidate.event.title,
                    text=(
                        f"Observe whether {', '.join(symbols)} show follow-through "
                        "or reversal after the observed reaction."
                    ),
                    reason=SelectionReason.WATCH_MARKET_REACTION,
                    target_symbols=symbols,
                )
            )
        elif candidate.event.event_type in allowed_types and candidate.score.importance_band in {
            ImportanceBand.CRITICAL,
            ImportanceBand.MATERIAL,
        }:
            results.append(
                WatchItem(
                    event_id=candidate.event.id,
                    headline=candidate.event.title,
                    text=(
                        "Monitor whether further official or company updates "
                        f"clarify or extend: {candidate.event.title}"
                    ),
                    reason=SelectionReason.WATCH_UNRESOLVED,
                    target_symbols=(),
                )
            )
    return tuple(results)


def _market_group(observation: MarketObservation) -> str | None:
    if observation.symbol == "^VIX":
        return "Risk"
    region = observation.region.casefold()
    if region == "us":
        return "US"
    if region in {"cn", "hk", "jp", "kr", "asia"}:
        return "Asia"
    if region in {"eu", "uk", "europe"}:
        return "Europe"
    if region == "fx":
        return "FX"
    if region in {"commodity", "commodities"}:
        return "Commodities"
    if region == "crypto":
        return "Crypto"
    return None


def _market_snapshot(
    markets: Iterable[MarketObservation], assets: Iterable[Mapping[str, Any]]
) -> tuple[MarketSnapshotGroup, ...]:
    configured = {str(item.get("symbol")): item for item in assets}
    grouped: dict[str, list[MarketSnapshotItem]] = {name: [] for name in _GROUP_ORDER}
    for market in sorted(markets, key=lambda item: (item.symbol, item.id)):
        asset = configured.get(market.symbol, {})
        region = str(asset.get("region", market.region))
        effective = market.model_copy(update={"region": region})
        group = _market_group(effective)
        if group is None:
            continue
        grouped[group].append(
            MarketSnapshotItem(
                symbol=market.symbol,
                name=market.name,
                close=market.close,
                change_pct=market.change_pct,
                currency=market.currency,
                observed_at=market.observed_at.astimezone(UTC),
            )
        )
    return tuple(
        MarketSnapshotGroup(name=name, items=tuple(grouped[name]))
        for name in _GROUP_ORDER
        if grouped[name]
    )


def compose_daily_brief(
    *,
    selection: C9SelectionResult,
    run_id: str,
    report_date: str,
    view_name: str,
    composed_at: datetime,
    ai_status: str,
    quality: ReportQualityAssessment,
    document_count: int,
    event_count: int,
    provider_statuses: Iterable[ProviderRunStatus] = (),
    markets: Iterable[MarketObservation] = (),
    assets: Iterable[Mapping[str, Any]] = (),
    market_impacts: Iterable[MarketImpactRecord] = (),
    explanation_bundles: Iterable[ExplanationBundle] = (),
    structured_explanations: Mapping[str, StructuredExplanation] | None = None,
) -> DailyIntelligenceBrief:
    if composed_at.tzinfo is None or composed_at.utcoffset() is None:
        raise ValueError("C9 composition time must be timezone-aware")
    market_items = tuple(markets)
    provider_items = tuple(provider_statuses)
    bundle_items = tuple(explanation_bundles)
    structured_items = structured_explanations or {}
    impacts: dict[str, list[MarketImpactRecord]] = {}
    for record in market_impacts:
        impacts.setdefault(record.event_id, []).append(record)
    impacts_by_event = {
        event_id: tuple(sorted(records, key=_reaction_key)) for event_id, records in impacts.items()
    }
    cards = tuple(
        _card(
            item,
            bundles=bundle_items,
            structured=structured_items,
            impacts_by_event=impacts_by_event,
        )
        for item in selection.primary
    )
    overnight = tuple(
        OvernightBullet(
            event_id=card.event_id,
            text=f"{card.headline} — {card.why_it_matters}",
        )
        for card in cards[:5]
    )
    under_radar = _under_radar(selection)
    watch_today = _watch_items(selection, impacts_by_event)
    displayed_reactions = tuple(reaction for card in cards for reaction in card.market_reactions)
    priority_distribution = dict(
        sorted(Counter(item.score.priority_band.value for item in selection.primary).items())
    )
    diagnostics = C9Diagnostics(
        eligible_stories=len(selection.primary) + len(selection.remaining),
        primary_selected=len(cards),
        under_radar_selected=len(under_radar),
        suppressed_redundant=len(selection.suppressed_event_ids),
        priority_distribution=priority_distribution,
        region_diversity=len({item.region for item in cards if item.region}),
        event_type_diversity=len({item.event_type for item in cards}),
        stories_with_c8_impacts=sum(bool(item.market_reactions) for item in cards),
        stories_with_structured_ai=sum(
            item.explanation_basis is ExplanationBasis.STRUCTURED_AI for item in cards
        ),
        deterministic_fallback_count=sum(
            item.explanation_basis
            in {
                ExplanationBasis.DETERMINISTIC_SEMANTICS,
                ExplanationBasis.CONSERVATIVE_FALLBACK,
            }
            for item in cards
        ),
        watch_today_count=len(watch_today),
        rule_version=selection.rule_version,
    )
    return DailyIntelligenceBrief(
        id=stable_id(
            "c9-briefing",
            f"{run_id}|{selection.view_id}|{selection.rule_version}",
        ),
        run_id=run_id,
        report_date=report_date,
        view_id=selection.view_id,
        view_name=view_name,
        rule_version=selection.rule_version,
        composed_at=composed_at.astimezone(UTC),
        ai_status=ai_status,
        primary_stories=cards,
        overnight=overnight,
        market_reactions=displayed_reactions,
        under_radar=under_radar,
        watch_today=watch_today,
        market_snapshot=_market_snapshot(market_items, assets),
        data_quality=BriefingDataQuality(
            status=quality.status.value,
            documents=document_count,
            events=event_count,
            markets=len(market_items),
            sources_healthy=sum(item.status is ProviderStatus.OK for item in provider_items),
            sources_total=len(provider_items),
            ai_status=ai_status,
            has_validated_market_reactions=bool(displayed_reactions),
        ),
        diagnostics=diagnostics,
    )
