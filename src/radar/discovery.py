from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from radar.domain import SourceCategory, SourceDocument, SourceRole, SourceTier, Story


class _DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ImportanceComponents(_DiscoveryModel):
    authority: float = Field(ge=0, le=1)
    source_diversity: float = Field(ge=0, le=1)
    evidence_strength: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    systemic_relevance: float = Field(ge=0, le=1)
    total: float = Field(ge=0, le=1)


class DiscoveryCluster(_DiscoveryModel):
    cluster_id: str
    story_id: str
    title: str
    event_type: str
    entity_ids: tuple[str, ...]
    topics: tuple[str, ...]
    region: str | None
    source_document_ids: tuple[str, ...]
    source_publishers: tuple[str, ...]
    source_categories: tuple[str, ...]
    primary_source_ids: tuple[str, ...]
    latest_published_at: datetime
    evidence_resolved: bool
    focus_eligible: bool
    importance: ImportanceComponents
    rejection_reasons: tuple[str, ...] = ()


class DiscoveryDiagnostics(_DiscoveryModel):
    stories: int
    clusters: int
    evidence_resolved: int
    focus_candidates: int
    evidence_rejected: int


class DiscoveryResult(_DiscoveryModel):
    clusters: tuple[DiscoveryCluster, ...]
    diagnostics: DiscoveryDiagnostics


_AUTHORITY = {
    SourceTier.A: 1.0,
    SourceTier.B: 0.7,
    SourceTier.C: 0.4,
    SourceTier.D: 0.2,
}
_SYSTEMIC_EVENT_TYPES = {"policy", "macro_release", "risk", "market_move"}


def _freshness(latest: datetime, now: datetime) -> float:
    age_hours = max(0.0, (now - latest).total_seconds() / 3600)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.7
    return 0.3


def _importance(
    story: Story,
    documents: tuple[SourceDocument, ...],
    *,
    evidence_resolved: bool,
    now: datetime,
) -> ImportanceComponents:
    publishers = {item.publisher.casefold() for item in documents}
    authority = max((_AUTHORITY[item.source_tier] for item in documents), default=0.0)
    source_diversity = min(1.0, len(publishers) / 5)
    evidence_strength = 1.0 if evidence_resolved else min(0.4, source_diversity)
    freshness = _freshness(story.latest_published_at, now)
    systemic = 1.0 if story.event_type.value in _SYSTEMIC_EVENT_TYPES else 0.5
    total = round(
        authority * 0.25
        + source_diversity * 0.15
        + evidence_strength * 0.3
        + freshness * 0.2
        + systemic * 0.1,
        4,
    )
    return ImportanceComponents(
        authority=authority,
        source_diversity=source_diversity,
        evidence_strength=evidence_strength,
        freshness=freshness,
        systemic_relevance=systemic,
        total=total,
    )


def build_discovery_clusters(
    stories: Sequence[Story],
    documents: Sequence[SourceDocument],
    *,
    now: datetime,
) -> DiscoveryResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    document_by_id = {item.id: item for item in documents}
    clusters: list[DiscoveryCluster] = []
    for story in stories:
        members = tuple(
            sorted(
                (
                    document_by_id[item_id]
                    for item_id in story.source_document_ids
                    if item_id in document_by_id
                ),
                key=lambda item: item.id,
            )
        )
        if not members:
            continue
        presentation = document_by_id.get(story.presentation_document_id, members[0])
        primary_ids = tuple(
            sorted(item.id for item in members if item.source_role is SourceRole.PRIMARY)
        )
        professional_publishers = {
            item.publisher.casefold()
            for item in members
            if item.source_category is SourceCategory.PROFESSIONAL_NEWS
            and item.source_tier in {SourceTier.A, SourceTier.B}
        }
        evidence_resolved = bool(primary_ids) or len(professional_publishers) >= 2
        importance = _importance(
            story,
            members,
            evidence_resolved=evidence_resolved,
            now=now,
        )
        rejection_reasons = () if evidence_resolved else ("evidence_unresolved",)
        focus_eligible = evidence_resolved and importance.total >= 0.5
        clusters.append(
            DiscoveryCluster(
                cluster_id=story.id,
                story_id=story.id,
                title=presentation.title,
                event_type=story.event_type.value,
                entity_ids=story.canonical_entity_ids,
                topics=tuple(topic.value for topic in story.topics),
                region=story.region,
                source_document_ids=tuple(item.id for item in members),
                source_publishers=tuple(sorted({item.publisher for item in members})),
                source_categories=tuple(sorted({item.source_category.value for item in members})),
                primary_source_ids=primary_ids,
                latest_published_at=story.latest_published_at,
                evidence_resolved=evidence_resolved,
                focus_eligible=focus_eligible,
                importance=importance,
                rejection_reasons=rejection_reasons,
            )
        )
    ordered = tuple(
        sorted(
            clusters,
            key=lambda item: (
                not item.focus_eligible,
                -item.importance.total,
                item.cluster_id,
            ),
        )
    )
    return DiscoveryResult(
        clusters=ordered,
        diagnostics=DiscoveryDiagnostics(
            stories=len(stories),
            clusters=len(ordered),
            evidence_resolved=sum(item.evidence_resolved for item in ordered),
            focus_candidates=sum(item.focus_eligible for item in ordered),
            evidence_rejected=sum(
                "evidence_unresolved" in item.rejection_reasons for item in ordered
            ),
        ),
    )
