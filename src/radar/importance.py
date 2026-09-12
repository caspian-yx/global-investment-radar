from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from radar.domain import (
    AssetClass,
    Event,
    EventType,
    SourceDocument,
    SourceTier,
    Story,
    Topic,
    stable_id,
)

C7_RULE_VERSION = "c7-v1"


class ImportanceBand(StrEnum):
    CRITICAL = "CRITICAL"
    MATERIAL = "MATERIAL"
    ROUTINE = "ROUTINE"
    LOW = "LOW"


class RelevanceBand(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class PriorityBand(StrEnum):
    MUST_READ = "MUST_READ"
    IMPORTANT = "IMPORTANT"
    WATCH = "WATCH"
    BACKGROUND = "BACKGROUND"
    IGNORE = "IGNORE"


class ImportanceReason(StrEnum):
    HIGH_AUTHORITY_PRIMARY_SOURCE = "HIGH_AUTHORITY_PRIMARY_SOURCE"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    MAJOR_POLICY_DECISION = "MAJOR_POLICY_DECISION"
    MACRO_RELEASE = "MACRO_RELEASE"
    STORY_SCOPE = "STORY_SCOPE"
    CANONICAL_ENTITY_SIGNIFICANCE = "CANONICAL_ENTITY_SIGNIFICANCE"
    MULTI_SOURCE_CORROBORATION = "MULTI_SOURCE_CORROBORATION"
    FRESH_STORY = "FRESH_STORY"
    LOW_MATERIALITY = "LOW_MATERIALITY"
    OTHER_CONSERVATIVE = "OTHER_CONSERVATIVE"


class RelevanceReason(StrEnum):
    DIRECT_WATCH_ENTITY = "DIRECT_WATCH_ENTITY"
    SECTOR_THEME_MATCH = "SECTOR_THEME_MATCH"
    EVENT_TYPE_MATCH = "EVENT_TYPE_MATCH"
    ASSET_CLASS_MATCH = "ASSET_CLASS_MATCH"
    REGION_MATCH = "REGION_MATCH"
    WEAK_RELEVANCE = "WEAK_RELEVANCE"


class ViewProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str = Field(min_length=1, pattern=r".*\S.*")
    name: str = Field(min_length=1, pattern=r".*\S.*")
    watched_entity_ids: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    topics: tuple[Topic, ...] = ()
    event_types: tuple[EventType, ...] = ()
    asset_classes: tuple[AssetClass, ...] = ()

    @model_validator(mode="after")
    def validate_canonical_dimensions(self) -> ViewProfile:
        string_dimensions = (self.watched_entity_ids, self.regions)
        enum_dimensions = (self.topics, self.event_types, self.asset_classes)
        if any(
            tuple(sorted(set(item.casefold() for item in values)))
            != tuple(value.casefold() for value in values)
            for values in string_dimensions
        ):
            raise ValueError("ViewProfile string dimensions must be sorted and unique")
        if any(tuple(sorted(set(values))) != values for values in enum_dimensions):
            raise ValueError("ViewProfile enum dimensions must be sorted and unique")
        return self


class ScoreComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str = Field(min_length=1, pattern=r".*\S.*")
    points: float = Field(ge=0, le=100)


class C7Score(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    story_id: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    view_id: str = Field(min_length=1, pattern=r".*\S.*")
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    scored_at: datetime
    importance_score: float = Field(ge=0, le=100)
    importance_band: ImportanceBand
    importance_components: tuple[ScoreComponent, ...]
    importance_reason_codes: tuple[ImportanceReason, ...]
    relevance_score: float = Field(ge=0, le=100)
    relevance_band: RelevanceBand
    relevance_components: tuple[ScoreComponent, ...]
    relevance_reason_codes: tuple[RelevanceReason, ...]
    priority_band: PriorityBand
    priority_rank: int | None = Field(default=None, ge=1)
    view_snapshot: ViewProfile

    @field_validator("scored_at")
    @classmethod
    def normalize_scored_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("C7 score timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_score_contract(self) -> C7Score:
        if self.view_id != self.view_snapshot.id:
            raise ValueError("C7 view identity must match its snapshot")
        if len({item.name for item in self.importance_components}) != len(
            self.importance_components
        ):
            raise ValueError("Importance component names must be unique")
        if len({item.name for item in self.relevance_components}) != len(self.relevance_components):
            raise ValueError("Relevance component names must be unique")
        if len(set(self.importance_reason_codes)) != len(self.importance_reason_codes):
            raise ValueError("Importance reason codes must be unique")
        if len(set(self.relevance_reason_codes)) != len(self.relevance_reason_codes):
            raise ValueError("Relevance reason codes must be unique")
        return self


_IMPORTANCE_MATERIALITY: dict[EventType, float] = {
    EventType.POLICY: 30.0,
    EventType.RISK: 30.0,
    EventType.M_AND_A: 28.0,
    EventType.GUIDANCE: 28.0,
    EventType.EARNINGS: 27.0,
    EventType.LEGAL: 25.0,
    EventType.MACRO: 24.0,
    EventType.SUPPLY_CHAIN: 20.0,
    EventType.MARKET_MOVE: 18.0,
    EventType.PRODUCT: 12.0,
    EventType.OTHER: 5.0,
}
_TIER_POINTS: dict[SourceTier, float] = {
    SourceTier.A: 25.0,
    SourceTier.B: 17.0,
    SourceTier.C: 9.0,
    SourceTier.D: 3.0,
}
_IMPORTANCE_BAND_ORDER = {
    ImportanceBand.CRITICAL: 4,
    ImportanceBand.MATERIAL: 3,
    ImportanceBand.ROUTINE: 2,
    ImportanceBand.LOW: 1,
}
_RELEVANCE_BAND_ORDER = {
    RelevanceBand.HIGH: 4,
    RelevanceBand.MEDIUM: 3,
    RelevanceBand.LOW: 2,
    RelevanceBand.NONE: 1,
}
_PRIORITY_BAND_ORDER = {
    PriorityBand.MUST_READ: 5,
    PriorityBand.IMPORTANT: 4,
    PriorityBand.WATCH: 3,
    PriorityBand.BACKGROUND: 2,
    PriorityBand.IGNORE: 1,
}


def load_view_profiles(universe: Mapping[str, Any]) -> tuple[ViewProfile, ...]:
    """Validate configured investor views and return them in stable ID order."""
    entity_ids = {str(item["id"]) for item in universe.get("entities", [])}
    raw_views = universe.get("views", [])
    if not isinstance(raw_views, list):
        raise ValueError("universe views must be a list")
    profiles: list[ViewProfile] = []
    seen_ids: set[str] = set()
    for raw in raw_views:
        if not isinstance(raw, dict):
            raise ValueError("each universe view must be an object")
        raw_entity_ids = tuple(sorted({str(item) for item in raw.get("watched_entity_ids", [])}))
        unknown_entities = set(raw_entity_ids) - entity_ids
        if unknown_entities:
            raise ValueError(f"View references unknown entities: {sorted(unknown_entities)}")
        profile = ViewProfile(
            id=str(raw["id"]),
            name=str(raw["name"]),
            watched_entity_ids=raw_entity_ids,
            regions=tuple(sorted({str(item) for item in raw.get("regions", [])})),
            topics=tuple(sorted({Topic(str(item)) for item in raw.get("topics", [])})),
            event_types=tuple(
                sorted({EventType(str(item)) for item in raw.get("event_types", [])})
            ),
            asset_classes=tuple(
                sorted({AssetClass(str(item)) for item in raw.get("asset_classes", [])})
            ),
        )
        if profile.id in seen_ids:
            raise ValueError(f"Duplicate view ID: {profile.id}")
        seen_ids.add(profile.id)
        profiles.append(profile)
    if "global" not in seen_ids:
        profiles.append(ViewProfile(id="global", name="Global"))
    return tuple(sorted(profiles, key=lambda item: item.id))


def _documents_for_event(
    event: Event, documents: Mapping[str, SourceDocument]
) -> list[SourceDocument]:
    missing = sorted(set(event.source_document_ids) - set(documents))
    if missing:
        raise ValueError(f"C7 scoring is missing SourceDocument closure: {', '.join(missing)}")
    return [documents[item_id] for item_id in event.source_document_ids]


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _entity_points(event: Event, universe: Mapping[str, Any]) -> float:
    configured = {
        str(item["id"]): float(item.get("importance_points", 0))
        for item in universe.get("entities", [])
    }
    invalid = [item_id for item_id, points in configured.items() if not 0 <= points <= 10]
    if invalid:
        raise ValueError(f"Entity importance_points must be within 0..10: {sorted(invalid)}")
    return min(
        10.0, max((configured.get(item_id, 0.0) for item_id in event.entity_ids), default=0.0)
    )


def _importance(
    event: Event,
    documents: list[SourceDocument],
    universe: Mapping[str, Any],
    now: datetime,
) -> tuple[float, tuple[ScoreComponent, ...], tuple[ImportanceReason, ...]]:
    components: list[ScoreComponent] = []
    reasons: list[ImportanceReason] = []
    materiality = _IMPORTANCE_MATERIALITY[event.event_type]
    components.append(ScoreComponent(name="materiality", points=materiality))
    if event.event_type is EventType.POLICY:
        reasons.append(ImportanceReason.MAJOR_POLICY_DECISION)
    elif event.event_type is EventType.MACRO:
        reasons.append(ImportanceReason.MACRO_RELEASE)
    elif event.event_type is EventType.LEGAL and any(
        item.source_category.value == "regulatory" for item in documents
    ):
        reasons.append(ImportanceReason.REGULATORY_ACTION)
    if event.event_type is EventType.OTHER:
        reasons.append(ImportanceReason.OTHER_CONSERVATIVE)
    elif materiality <= 12:
        reasons.append(ImportanceReason.LOW_MATERIALITY)

    if documents:
        tier = min((item.source_tier for item in documents), key=lambda item: item.value)
        authority = _TIER_POINTS[tier]
        primary_categories = {
            item.source_category.value for item in documents if item.source_role.value == "primary"
        }
        if tier is SourceTier.A and primary_categories & {
            "official",
            "regulatory",
            "company_primary",
        }:
            authority = min(30.0, authority + 5.0)
            reasons.append(ImportanceReason.HIGH_AUTHORITY_PRIMARY_SOURCE)
        components.append(ScoreComponent(name="source_authority", points=authority))
    else:
        components.append(ScoreComponent(name="source_authority", points=4.0))

    distinct_regions = {_normalized(item.region) for item in documents if item.region}
    scope = (6.0 if len(documents) > 1 else 0.0) + (4.0 if len(distinct_regions) > 1 else 0.0)
    if scope:
        reasons.append(ImportanceReason.STORY_SCOPE)
    components.append(ScoreComponent(name="story_scope", points=scope))

    entity_points = _entity_points(event, universe)
    if entity_points:
        reasons.append(ImportanceReason.CANONICAL_ENTITY_SIGNIFICANCE)
    components.append(ScoreComponent(name="entity_significance", points=entity_points))

    publishers = {_normalized(item.publisher) for item in documents}
    categories = {item.source_category.value for item in documents}
    corroboration = min(8.0, max(0.0, 4.0 * (len(publishers) - 1))) + min(
        4.0, max(0.0, 2.0 * (len(categories) - 1))
    )
    if corroboration:
        reasons.append(ImportanceReason.MULTI_SOURCE_CORROBORATION)
    components.append(ScoreComponent(name="corroboration", points=corroboration))

    occurred_at = event.occurred_at.astimezone(UTC)
    age_hours = max(0.0, (now.astimezone(UTC) - occurred_at).total_seconds() / 3600)
    freshness = max(0.0, 5.0 - age_hours / 48.0)
    if freshness:
        reasons.append(ImportanceReason.FRESH_STORY)
    components.append(ScoreComponent(name="freshness", points=freshness))
    total = min(100.0, round(sum(item.points for item in components), 2))
    if event.event_type is EventType.OTHER:
        total = min(total, 39.0)
    return total, tuple(components), tuple(reasons)


def _relevance(
    event: Event,
    story: Story | None,
    documents: list[SourceDocument],
    view: ViewProfile,
    universe: Mapping[str, Any],
) -> tuple[float, tuple[ScoreComponent, ...], tuple[RelevanceReason, ...]]:
    components: list[ScoreComponent] = []
    reasons: list[RelevanceReason] = []
    event_entities = set(event.entity_ids)
    story_topics = set(story.topics) if story is not None else set()
    story_region = story.region if story is not None else None
    if not story_topics:
        assets_by_symbol = {str(item["symbol"]): item for item in universe.get("assets", [])}
        for entity_id in event_entities:
            asset = assets_by_symbol.get(entity_id)
            if asset is not None:
                story_topics.update(Topic(str(topic)) for topic in asset.get("topics", []))
                if story_region is None:
                    story_region = str(asset["region"])

    direct = 55.0 if event_entities & set(view.watched_entity_ids) else 0.0
    if direct:
        reasons.append(RelevanceReason.DIRECT_WATCH_ENTITY)
    components.append(ScoreComponent(name="direct_watch_entity", points=direct))

    topic_match = len(story_topics & set(view.topics))
    topic_points = min(20.0, 10.0 * topic_match)
    if topic_points:
        reasons.append(RelevanceReason.SECTOR_THEME_MATCH)
    components.append(ScoreComponent(name="sector_theme_match", points=topic_points))

    event_type_points = 15.0 if event.event_type in set(view.event_types) else 0.0
    if event_type_points:
        reasons.append(RelevanceReason.EVENT_TYPE_MATCH)
    components.append(ScoreComponent(name="event_type_match", points=event_type_points))

    asset_classes = set(view.asset_classes)
    configured_assets = {str(item["symbol"]): item for item in universe.get("assets", [])}
    event_asset_classes = {
        AssetClass(str(configured_assets[item_id]["asset_class"]))
        for item_id in event_entities
        if item_id in configured_assets
    }
    asset_class_points = 10.0 if event_asset_classes & asset_classes else 0.0
    if asset_class_points:
        reasons.append(RelevanceReason.ASSET_CLASS_MATCH)
    components.append(ScoreComponent(name="asset_class_match", points=asset_class_points))

    document_regions = {str(item.region) for item in documents if item.region}
    if story_region:
        document_regions.add(story_region)
    region_points = 10.0 if set(view.regions) & document_regions else 0.0
    if region_points:
        reasons.append(RelevanceReason.REGION_MATCH)
    components.append(ScoreComponent(name="region_match", points=region_points))

    total = min(100.0, round(sum(item.points for item in components), 2))
    if total and not reasons:
        reasons.append(RelevanceReason.WEAK_RELEVANCE)
    return total, tuple(components), tuple(reasons)


def _importance_band(score: float) -> ImportanceBand:
    if score >= 72:
        return ImportanceBand.CRITICAL
    if score >= 55:
        return ImportanceBand.MATERIAL
    if score >= 30:
        return ImportanceBand.ROUTINE
    return ImportanceBand.LOW


def _relevance_band(score: float) -> RelevanceBand:
    if score >= 45:
        return RelevanceBand.HIGH
    if score >= 25:
        return RelevanceBand.MEDIUM
    if score >= 10:
        return RelevanceBand.LOW
    return RelevanceBand.NONE


def _priority_band(importance: float, relevance: float) -> PriorityBand:
    if importance >= 72 or (importance >= 55 and relevance >= 45):
        return PriorityBand.MUST_READ
    if importance >= 55 or (importance >= 30 and relevance >= 45):
        return PriorityBand.IMPORTANT
    if importance >= 30 or relevance >= 25:
        return PriorityBand.WATCH
    if importance > 0 or relevance > 0:
        return PriorityBand.BACKGROUND
    return PriorityBand.IGNORE


def score_event(
    event: Event,
    story: Story | None,
    documents: Iterable[SourceDocument],
    view: ViewProfile,
    universe: Mapping[str, Any],
    now: datetime,
) -> C7Score:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("C7 scoring time must be timezone-aware")
    document_map = {item.id: item for item in documents}
    event_documents = _documents_for_event(event, document_map)
    importance, importance_components, importance_reasons = _importance(
        event, event_documents, universe, now
    )
    relevance, relevance_components, relevance_reasons = _relevance(
        event, story, event_documents, view, universe
    )
    return C7Score(
        event_id=event.id,
        story_id=story.id if story is not None else None,
        view_id=view.id,
        rule_version=C7_RULE_VERSION,
        scored_at=now,
        importance_score=importance,
        importance_band=_importance_band(importance),
        importance_components=importance_components,
        importance_reason_codes=importance_reasons,
        relevance_score=relevance,
        relevance_band=_relevance_band(relevance),
        relevance_components=relevance_components,
        relevance_reason_codes=relevance_reasons,
        priority_band=_priority_band(importance, relevance),
        priority_rank=None,
        view_snapshot=view,
    )


def score_events(
    events: Iterable[Event],
    stories: Iterable[Story],
    documents: Iterable[SourceDocument],
    views: ViewProfile | Iterable[ViewProfile],
    universe: Mapping[str, Any],
    now: datetime,
) -> list[C7Score]:
    profiles = [views] if isinstance(views, ViewProfile) else list(views)
    story_by_event_id = {stable_id("event", story.id): story for story in stories}
    event_items = sorted(events, key=lambda item: item.id)
    document_items = list(documents)
    result = [
        score_event(event, story_by_event_id.get(event.id), document_items, view, universe, now)
        for view in sorted(profiles, key=lambda item: item.id)
        for event in event_items
    ]
    return result


def rank_scores(scores: Iterable[C7Score]) -> list[C7Score]:
    items = list(scores)
    view_ids = {item.view_id for item in items}
    if len(view_ids) > 1:
        raise ValueError("rank_scores accepts one view at a time")
    ordered = sorted(
        items,
        key=lambda item: (
            -_PRIORITY_BAND_ORDER[item.priority_band],
            -_IMPORTANCE_BAND_ORDER[item.importance_band],
            -_RELEVANCE_BAND_ORDER[item.relevance_band],
            -item.importance_score,
            -item.relevance_score,
            item.event_id,
        ),
    )
    return [
        item.model_copy(update={"priority_rank": index}) for index, item in enumerate(ordered, 1)
    ]


def rank_events(events: Iterable[Event], scores: Iterable[C7Score]) -> list[Event]:
    ranked_scores = rank_scores(scores)
    score_by_event_id = {item.event_id: item for item in ranked_scores}
    event_items = list(events)
    missing = sorted({item.id for item in event_items} - set(score_by_event_id))
    if missing:
        raise ValueError(f"C7 ranking is missing Event scores: {', '.join(missing)}")
    return sorted(event_items, key=lambda item: score_by_event_id[item.id].priority_rank or 0)


def summarize_c7_scores(scores: Iterable[C7Score]) -> dict[str, int | str]:
    items = list(scores)
    summary: dict[str, int | str] = {"c7_scored": len(items), "rule_version": C7_RULE_VERSION}
    counters = {
        "importance_band": Counter(item.importance_band.value for item in items),
        "relevance_band": Counter(item.relevance_band.value for item in items),
        "priority_band": Counter(item.priority_band.value for item in items),
        "importance_reason": Counter(
            reason.value for item in items for reason in item.importance_reason_codes
        ),
        "relevance_reason": Counter(
            reason.value for item in items for reason in item.relevance_reason_codes
        ),
    }
    for prefix, counter in counters.items():
        for name, count in sorted(counter.items()):
            summary[f"{prefix}_{name}"] = count
    summary["direct_entity_matches"] = sum(
        RelevanceReason.DIRECT_WATCH_ENTITY in item.relevance_reason_codes for item in items
    )
    summary["corroborated_stories"] = sum(
        ImportanceReason.MULTI_SOURCE_CORROBORATION in item.importance_reason_codes
        for item in items
    )
    return summary
