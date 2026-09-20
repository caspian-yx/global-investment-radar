from __future__ import annotations

import re
from collections.abc import Sequence, Set
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from radar.discovery import DiscoveryCluster


class _CoverageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CoverageMissReason(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NOT_INGESTED = "not_ingested"
    DEDUP_ERROR = "dedup_error"
    CLASSIFICATION_FAILURE = "classification_failure"
    FRESHNESS_REJECTION = "freshness_rejection"
    IMPORTANCE_REJECTION = "importance_rejection"
    EVIDENCE_FAILURE = "evidence_failure"
    GEOGRAPHY_GAP = "geography_gap"
    ASSET_CLASS_GAP = "asset_class_gap"


class BenchmarkStory(_CoverageModel):
    benchmark_id: str
    title: str
    provider_id: str
    source_url: str
    published_at: datetime
    entity_ids: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    region: str | None = None
    asset_classes: tuple[str, ...] = ()
    quality_sufficient: bool

    @field_validator("published_at")
    @classmethod
    def _published_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(UTC)


class CoverageMiss(_CoverageModel):
    benchmark_id: str
    title: str
    provider_ids: tuple[str, ...]
    reason: CoverageMissReason
    matched_cluster_id: str | None = None


class CoverageAudit(_CoverageModel):
    measurable: bool
    measurement_reason: str
    benchmark_count: int
    discovered_count: int
    evidence_resolved_count: int
    focus_count: int
    major_story_recall: float | None
    evidence_resolution_rate: float | None
    focus_recall: float | None
    misses: tuple[CoverageMiss, ...]


class _BenchmarkGroup(_CoverageModel):
    representative: BenchmarkStory
    benchmark_ids: tuple[str, ...]
    provider_ids: tuple[str, ...]


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALIASES = {"fed": "federal-reserve"}
_STOPWORDS = {"a", "after", "and", "by", "for", "in", "of", "on", "the", "to"}


def _tokens(title: str) -> frozenset[str]:
    return frozenset(
        _ALIASES.get(token, token)
        for token in _TOKEN_RE.findall(title.casefold())
        if token not in _STOPWORDS
    )


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _same_benchmark_event(left: BenchmarkStory, right: BenchmarkStory) -> bool:
    hours = abs((left.published_at - right.published_at).total_seconds()) / 3600
    if hours > 48:
        return False
    entity_overlap = bool(set(left.entity_ids) & set(right.entity_ids))
    topic_overlap = bool(set(left.topics) & set(right.topics))
    if entity_overlap and topic_overlap and left.region == right.region:
        return True
    return _similarity(left.title, right.title) >= 0.6


def _deduplicate_benchmark(stories: Sequence[BenchmarkStory]) -> tuple[_BenchmarkGroup, ...]:
    groups: list[list[BenchmarkStory]] = []
    for story in sorted(stories, key=lambda item: (item.published_at, item.benchmark_id)):
        for group in groups:
            if _same_benchmark_event(group[0], story):
                group.append(story)
                break
        else:
            groups.append([story])
    return tuple(
        _BenchmarkGroup(
            representative=items[0],
            benchmark_ids=tuple(sorted(item.benchmark_id for item in items)),
            provider_ids=tuple(sorted({item.provider_id for item in items})),
        )
        for items in groups
    )


def _match_cluster(story: BenchmarkStory, clusters: Sequence[DiscoveryCluster]) -> str | None:
    ranked: list[tuple[float, str]] = []
    for cluster in clusters:
        entity_overlap = bool(set(story.entity_ids) & set(cluster.entity_ids))
        topic_overlap = bool(set(story.topics) & set(cluster.topics))
        title_score = _similarity(story.title, cluster.title)
        if (entity_overlap and (topic_overlap or title_score >= 0.25)) or title_score >= 0.55:
            ranked.append((title_score + float(entity_overlap), cluster.cluster_id))
    if not ranked:
        return None
    return max(ranked, key=lambda item: (item[0], item[1]))[1]


def audit_coverage(
    benchmark: Sequence[BenchmarkStory],
    clusters: Sequence[DiscoveryCluster],
    *,
    evidence_resolved_ids: Set[str],
    focus_ids: Set[str],
) -> CoverageAudit:
    groups = _deduplicate_benchmark(benchmark)
    measurable = bool(groups) and all(
        item.quality_sufficient for group in groups for item in [group.representative]
    )
    reason = (
        "measured"
        if measurable
        else ("benchmark_quality_insufficient" if groups else "benchmark_unavailable")
    )
    cluster_by_id = {item.cluster_id: item for item in clusters}
    discovered = 0
    resolved = 0
    focused = 0
    misses: list[CoverageMiss] = []
    for group in groups:
        story = group.representative
        cluster_id = _match_cluster(story, clusters)
        if cluster_id is None:
            misses.append(
                CoverageMiss(
                    benchmark_id=group.benchmark_ids[0],
                    title=story.title,
                    provider_ids=group.provider_ids,
                    reason=CoverageMissReason.NOT_INGESTED,
                )
            )
            continue
        discovered += 1
        cluster = cluster_by_id[cluster_id]
        evidence_resolved = cluster_id in evidence_resolved_ids and cluster.evidence_resolved
        if not evidence_resolved:
            misses.append(
                CoverageMiss(
                    benchmark_id=group.benchmark_ids[0],
                    title=story.title,
                    provider_ids=group.provider_ids,
                    reason=CoverageMissReason.EVIDENCE_FAILURE,
                    matched_cluster_id=cluster_id,
                )
            )
            continue
        resolved += 1
        if cluster_id in focus_ids:
            focused += 1
        else:
            misses.append(
                CoverageMiss(
                    benchmark_id=group.benchmark_ids[0],
                    title=story.title,
                    provider_ids=group.provider_ids,
                    reason=CoverageMissReason.IMPORTANCE_REJECTION,
                    matched_cluster_id=cluster_id,
                )
            )

    count = len(groups)
    return CoverageAudit(
        measurable=measurable,
        measurement_reason=reason,
        benchmark_count=count,
        discovered_count=discovered,
        evidence_resolved_count=resolved,
        focus_count=focused,
        major_story_recall=round(discovered / count, 4) if measurable else None,
        evidence_resolution_rate=round(resolved / discovered, 4)
        if measurable and discovered
        else (0.0 if measurable else None),
        focus_recall=round(focused / count, 4) if measurable else None,
        misses=tuple(misses),
    )
