from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from radar.briefing import (
    C9_RULE_VERSION,
    C9SelectionResult,
    SelectionReason,
    select_daily_stories,
)
from radar.domain import (
    DocumentStoryProfile,
    Event,
    EventType,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    Story,
    Topic,
    content_hash,
    stable_id,
)
from radar.importance import (
    C7_RULE_VERSION,
    C7Score,
    ImportanceBand,
    PriorityBand,
    RelevanceBand,
    ScoreComponent,
    ViewProfile,
)

NOW = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


def _document(
    key: str,
    *,
    category: SourceCategory = SourceCategory.OFFICIAL,
    region: str = "US",
) -> SourceDocument:
    title = f"{key} material development"
    return SourceDocument(
        id=f"doc-{key}",
        provider=f"provider-{key}",
        source_category=category,
        source_role=SourceRole.PRIMARY,
        source_tier=SourceTier.A,
        publisher=f"Publisher {key}",
        original_url=f"https://example.com/{key}",
        canonical_url=f"https://example.com/{key}",
        title=title,
        content=title,
        region=region,
        published_at=NOW - timedelta(hours=1),
        collected_at=NOW,
        content_hash=content_hash(title),
    )


def _story(
    key: str,
    *,
    event_type: EventType,
    region: str,
    entity: str,
    authority: str | None = None,
    release_domain: str | None = None,
    reference_period: str | None = None,
    action_terms: tuple[str, ...] = ("announces",),
    strict_numeric_facts: tuple[str, ...] = (),
    normalized_title_tokens: tuple[str, ...] | None = None,
    document: SourceDocument | None = None,
) -> Story:
    source = document or _document(key, region=region)
    profile = DocumentStoryProfile(
        document_id=source.id,
        event_type=event_type,
        canonical_entity_ids=(entity,),
        topics=(Topic.RATES,) if event_type is EventType.POLICY else (Topic.TECHNOLOGY,),
        region=region,
        published_at=source.published_at,
        normalized_title_tokens=normalized_title_tokens or tuple(sorted((key, "material"))),
        action_terms=action_terms,
        strict_numeric_facts=strict_numeric_facts,
        authority_id=authority,
        release_domain=release_domain,
        reference_period=reference_period,
        source_category=source.source_category,
        source_role=source.source_role,
        source_tier=source.source_tier,
    )
    story_id = stable_id("story", source.id)
    return Story(
        id=story_id,
        identity_seed_document_id=source.id,
        identity_profile=profile,
        presentation_document_id=source.id,
        event_type=event_type,
        canonical_entity_ids=(entity,),
        topics=profile.topics,
        region=region,
        source_document_ids=(source.id,),
        earliest_trusted_published_at=source.published_at,
        latest_published_at=source.published_at,
        first_seen_at=NOW,
        last_seen_at=NOW,
        rule_version="story-v1",
    )


def _event(key: str, story: Story, *, age_minutes: int = 0) -> Event:
    return Event(
        id=stable_id("event", story.id),
        event_key=f"event-key-{key}",
        event_type=story.event_type,
        title=f"{key} material development",
        occurred_at=NOW - timedelta(minutes=age_minutes),
        first_seen_at=NOW,
        last_seen_at=NOW,
        entity_ids=list(story.canonical_entity_ids),
        claim_ids=[f"claim-{key}"],
        evidence_ids=[f"evidence-{key}"],
        source_document_ids=list(story.source_document_ids),
        importance_score=50,
        importance_components={"source": 20},
        confidence=0.9,
    )


def _score(
    event: Event,
    story: Story,
    *,
    importance: float,
    importance_band: ImportanceBand,
    relevance: float,
    relevance_band: RelevanceBand,
    priority: PriorityBand,
) -> C7Score:
    return C7Score(
        event_id=event.id,
        story_id=story.id,
        view_id="global",
        rule_version=C7_RULE_VERSION,
        scored_at=NOW,
        importance_score=importance,
        importance_band=importance_band,
        importance_components=(ScoreComponent(name="materiality", points=importance),),
        importance_reason_codes=(),
        relevance_score=relevance,
        relevance_band=relevance_band,
        relevance_components=(),
        relevance_reason_codes=(),
        priority_band=priority,
        priority_rank=None,
        view_snapshot=ViewProfile(id="global", name="Global"),
    )


def _candidate(
    key: str,
    *,
    event_type: EventType,
    region: str,
    entity: str,
    importance: float,
    importance_band: ImportanceBand,
    relevance: float,
    relevance_band: RelevanceBand,
    priority: PriorityBand,
    age_minutes: int = 0,
) -> tuple[SourceDocument, Story, Event, C7Score]:
    document = _document(key, region=region)
    story = _story(
        key,
        event_type=event_type,
        region=region,
        entity=entity,
        document=document,
    )
    event = _event(key, story, age_minutes=age_minutes)
    score = _score(
        event,
        story,
        importance=importance,
        importance_band=importance_band,
        relevance=relevance,
        relevance_band=relevance_band,
        priority=priority,
    )
    return document, story, event, score


def _select(items: list[tuple[SourceDocument, Story, Event, C7Score]]) -> C9SelectionResult:
    return select_daily_stories(
        events=[item[2] for item in items],
        stories=[item[1] for item in items],
        documents=[item[0] for item in items],
        scores=[item[3] for item in items],
    )


def test_selection_fails_closed_when_event_provenance_document_is_missing() -> None:
    candidate = _candidate(
        "missing-provenance",
        event_type=EventType.POLICY,
        region="US",
        entity="federal-reserve",
        importance=90,
        importance_band=ImportanceBand.CRITICAL,
        relevance=0,
        relevance_band=RelevanceBand.NONE,
        priority=PriorityBand.MUST_READ,
    )

    with pytest.raises(ValueError, match="missing SourceDocument"):
        select_daily_stories(
            events=[candidate[2]],
            stories=[candidate[1]],
            documents=[],
            scores=[candidate[3]],
        )


def test_primary_selection_retains_every_critical_story_before_lower_strength() -> None:
    critical = [
        _candidate(
            f"critical-{index}",
            event_type=EventType.POLICY,
            region="US",
            entity=f"central-bank-{index}",
            importance=90 - index,
            importance_band=ImportanceBand.CRITICAL,
            relevance=0,
            relevance_band=RelevanceBand.NONE,
            priority=PriorityBand.MUST_READ,
        )
        for index in range(5)
    ]
    trivial_direct_watch = _candidate(
        "trivial-watch",
        event_type=EventType.PRODUCT,
        region="US",
        entity="watched-company",
        importance=20,
        importance_band=ImportanceBand.LOW,
        relevance=70,
        relevance_band=RelevanceBand.HIGH,
        priority=PriorityBand.IMPORTANT,
    )

    result = _select([*critical, trivial_direct_watch])

    assert [item.event.id for item in result.primary] == [item[2].id for item in critical]
    assert all(SelectionReason.PRIMARY_CRITICAL in item.reasons for item in result.primary)


def test_material_high_relevance_story_surfaces_without_forcing_low_background() -> None:
    material = _candidate(
        "semiconductor-supply",
        event_type=EventType.SUPPLY_CHAIN,
        region="KR",
        entity="semiconductors",
        importance=64,
        importance_band=ImportanceBand.MATERIAL,
        relevance=55,
        relevance_band=RelevanceBand.HIGH,
        priority=PriorityBand.MUST_READ,
    )
    low = _candidate(
        "minor-product",
        event_type=EventType.PRODUCT,
        region="US",
        entity="minor-company",
        importance=15,
        importance_band=ImportanceBand.LOW,
        relevance=5,
        relevance_band=RelevanceBand.NONE,
        priority=PriorityBand.BACKGROUND,
    )

    result = _select([low, material])

    assert [item.event.id for item in result.primary] == [material[2].id]
    assert low[2].id not in {item.event.id for item in result.remaining}


def test_selection_allows_two_strong_stories_without_filling_to_five() -> None:
    items = [
        _candidate(
            f"material-{index}",
            event_type=EventType.LEGAL if index == 0 else EventType.GUIDANCE,
            region="US",
            entity=f"entity-{index}",
            importance=60 - index,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
        )
        for index in range(2)
    ]

    assert len(_select(items).primary) == 2


def test_equivalent_strength_diversity_prevents_policy_monopoly() -> None:
    policies = [
        _candidate(
            f"policy-{index}",
            event_type=EventType.POLICY,
            region="EU",
            entity="ecb",
            importance=68 - index,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
            age_minutes=index,
        )
        for index in range(5)
    ]
    diverse = [
        _candidate(
            "china-regulation",
            event_type=EventType.LEGAL,
            region="CN",
            entity="china-regulator",
            importance=62,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
            age_minutes=8,
        ),
        _candidate(
            "us-guidance",
            event_type=EventType.GUIDANCE,
            region="US",
            entity="nvidia",
            importance=61,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
            age_minutes=9,
        ),
    ]

    result = _select([*policies, *diverse])

    selected_types = [item.event.event_type for item in result.primary]
    assert len(result.primary) == 5
    assert selected_types.count(EventType.POLICY) <= 3
    assert EventType.LEGAL in selected_types
    assert EventType.GUIDANCE in selected_types


def test_diversity_does_not_promote_weaker_tier_over_material_policy() -> None:
    policies = [
        _candidate(
            f"policy-{index}",
            event_type=EventType.POLICY,
            region="EU",
            entity="ecb",
            importance=60 - index,
            importance_band=ImportanceBand.MATERIAL,
            relevance=20,
            relevance_band=RelevanceBand.LOW,
            priority=PriorityBand.IMPORTANT,
        )
        for index in range(5)
    ]
    weak_diverse = _candidate(
        "weak-diverse",
        event_type=EventType.PRODUCT,
        region="JP",
        entity="small-company",
        importance=35,
        importance_band=ImportanceBand.ROUTINE,
        relevance=30,
        relevance_band=RelevanceBand.MEDIUM,
        priority=PriorityBand.WATCH,
    )

    result = _select([*policies, weak_diverse])

    assert [item.event.id for item in result.primary] == [item[2].id for item in policies]


def test_selection_is_permutation_invariant_with_stable_event_id_tie_break() -> None:
    items = [
        _candidate(
            key,
            event_type=EventType.LEGAL,
            region="US",
            entity=key,
            importance=60,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
        )
        for key in ("charlie", "alpha", "bravo")
    ]

    forward = _select(items)
    reverse = _select(list(reversed(items)))

    expected = sorted(item[2].id for item in items)
    assert [item.event.id for item in forward.primary] == expected
    assert [item.event.id for item in reverse.primary] == expected


def test_exact_release_identity_is_suppressed_only_for_noncritical_presentation() -> None:
    documents = [_document("statement", region="EU"), _document("decision", region="EU")]
    stories = [
        _story(
            "statement",
            event_type=EventType.POLICY,
            region="EU",
            entity="ecb",
            authority="ecb",
            release_domain="monetary-policy",
            reference_period="2026-09",
            normalized_title_tokens=("policy", "release"),
            document=documents[0],
        ),
        _story(
            "decision",
            event_type=EventType.POLICY,
            region="EU",
            entity="ecb",
            authority="ecb",
            release_domain="monetary-policy",
            reference_period="2026-09",
            normalized_title_tokens=("policy", "release"),
            document=documents[1],
        ),
    ]
    events = [_event("statement", stories[0]), _event("decision", stories[1])]
    scores = [
        _score(
            event,
            story,
            importance=60,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
        )
        for event, story in zip(events, stories, strict=True)
    ]

    result = select_daily_stories(
        events=events,
        stories=stories,
        documents=documents,
        scores=scores,
    )

    assert len(result.primary) == 1
    assert result.suppressed_event_ids == (max(event.id for event in events),)


def test_same_release_identity_with_distinct_action_is_not_suppressed() -> None:
    documents = [_document("raise"), _document("hold")]
    stories = [
        _story(
            "raise",
            event_type=EventType.POLICY,
            region="US",
            entity="federal-reserve",
            authority="fed",
            release_domain="monetary-policy",
            reference_period="2026-09",
            action_terms=("raises",),
            document=documents[0],
        ),
        _story(
            "hold",
            event_type=EventType.POLICY,
            region="US",
            entity="federal-reserve",
            authority="fed",
            release_domain="monetary-policy",
            reference_period="2026-09",
            action_terms=("holds",),
            document=documents[1],
        ),
    ]
    events = [_event("raise", stories[0]), _event("hold", stories[1])]
    scores = [
        _score(
            event,
            story,
            importance=60,
            importance_band=ImportanceBand.MATERIAL,
            relevance=30,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
        )
        for event, story in zip(events, stories, strict=True)
    ]

    result = select_daily_stories(
        events=events,
        stories=stories,
        documents=documents,
        scores=scores,
        max_primary=1,
    )

    assert len(result.primary) == 1
    assert len(result.remaining) == 1
    assert result.suppressed_event_ids == ()


def test_selection_models_are_strict_frozen_and_versioned() -> None:
    item = _candidate(
        "strict",
        event_type=EventType.LEGAL,
        region="US",
        entity="sec",
        importance=60,
        importance_band=ImportanceBand.MATERIAL,
        relevance=30,
        relevance_band=RelevanceBand.MEDIUM,
        priority=PriorityBand.IMPORTANT,
    )
    result = _select([item])

    assert result.rule_version == C9_RULE_VERSION
    with pytest.raises(ValidationError):
        result.primary[0].reasons = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        C9SelectionResult.model_validate(
            {
                **result.model_dump(),
                "unexpected": True,
            },
            strict=True,
        )
