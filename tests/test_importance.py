from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from radar.domain import (
    Event,
    EventType,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    Topic,
    content_hash,
)
from radar.importance import (
    C7_RULE_VERSION,
    C7Score,
    ImportanceBand,
    ImportanceReason,
    PriorityBand,
    RelevanceBand,
    RelevanceReason,
    ViewProfile,
    load_view_profiles,
    rank_scores,
    score_event,
    score_events,
)
from radar.story import consolidate_stories

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)

UNIVERSE = {
    "entities": [
        {
            "id": "federal-reserve",
            "name": "Federal Reserve",
            "aliases": ["fed"],
            "importance_points": 10,
        },
        {"id": "apple", "name": "Apple Inc.", "aliases": ["apple"], "importance_points": 5},
        {
            "id": "ai-infrastructure",
            "name": "AI Infrastructure",
            "aliases": ["ai infrastructure"],
            "importance_points": 7,
        },
        {
            "id": "semiconductors",
            "name": "Semiconductors",
            "aliases": ["semiconductor"],
            "importance_points": 6,
        },
    ],
    "assets": [
        {
            "symbol": "^GSPC",
            "name": "S&P 500",
            "region": "US",
            "asset_class": "EQUITY_INDEX",
            "topics": ["RATES", "TECHNOLOGY"],
        }
    ],
    "views": [
        {
            "id": "global",
            "name": "Global",
            "watched_entity_ids": [],
            "regions": [],
            "topics": [],
            "event_types": [],
            "asset_classes": [],
        },
        {
            "id": "ai-semiconductors",
            "name": "AI / Semiconductors",
            "watched_entity_ids": ["ai-infrastructure", "semiconductors"],
            "regions": [],
            "topics": ["TECHNOLOGY"],
            "event_types": [],
            "asset_classes": [],
        },
    ],
}


def _document(
    document_id: str,
    *,
    title: str,
    content: str,
    publisher: str,
    source_category: SourceCategory = SourceCategory.OFFICIAL,
    source_role: SourceRole = SourceRole.PRIMARY,
    source_tier: SourceTier = SourceTier.A,
    region: str | None = "US",
    published_at: datetime = NOW - timedelta(hours=4),
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        provider="importance-test",
        source_category=source_category,
        source_role=source_role,
        source_tier=source_tier,
        publisher=publisher,
        original_url=f"https://example.test/{document_id}",
        canonical_url=f"https://example.test/{document_id}",
        title=title,
        content=content,
        region=region,
        published_at=published_at,
        collected_at=NOW,
        content_hash=content_hash(content),
    )


def _event(
    event_id: str,
    *,
    event_type: EventType,
    title: str,
    entity_ids: list[str] | None = None,
    source_document_ids: list[str] | None = None,
    occurred_at: datetime = NOW - timedelta(hours=4),
) -> Event:
    return Event(
        id=event_id,
        event_key=f"event:{event_id}",
        event_type=event_type,
        title=title,
        occurred_at=occurred_at,
        first_seen_at=NOW,
        last_seen_at=NOW,
        entity_ids=entity_ids or [],
        claim_ids=[],
        evidence_ids=[],
        source_document_ids=source_document_ids or [],
        importance_score=10,
        importance_components={"legacy": 10},
        confidence=0.9,
    )


def _story(event_id: str, document: SourceDocument):
    result = consolidate_stories([document], UNIVERSE["entities"], now=NOW)
    assert len(result.stories) == 1
    return result.stories[0]


def test_view_profiles_are_strict_and_loaded_deterministically() -> None:
    profiles = load_view_profiles(UNIVERSE)
    assert [item.id for item in profiles] == ["ai-semiconductors", "global"]
    assert profiles[0].watched_entity_ids == ("ai-infrastructure", "semiconductors")
    with pytest.raises(ValidationError):
        ViewProfile(id="bad", name="Bad", watched_entity_ids=["not-a-tuple"])


def test_c7_score_is_versioned_frozen_and_separates_dimensions() -> None:
    score = C7Score(
        event_id="event-a",
        story_id=None,
        view_id="global",
        rule_version=C7_RULE_VERSION,
        scored_at=NOW,
        importance_score=90,
        importance_band=ImportanceBand.CRITICAL,
        importance_components=(),
        importance_reason_codes=(ImportanceReason.MAJOR_POLICY_DECISION,),
        relevance_score=0,
        relevance_band=RelevanceBand.NONE,
        relevance_components=(),
        relevance_reason_codes=(),
        priority_band=PriorityBand.MUST_READ,
        priority_rank=None,
        view_snapshot=ViewProfile(id="global", name="Global"),
    )
    with pytest.raises(ValidationError):
        C7Score.model_validate(score.model_dump() | {"importance_score": 101})
    with pytest.raises((TypeError, ValidationError)):
        score.priority_band = PriorityBand.IGNORE


def test_major_policy_outranks_trivial_company_marketing_even_when_marketing_is_fresh() -> None:
    fed = _document(
        "fed",
        title="Federal Reserve policy rate decision",
        content="The Federal Reserve announced a monetary policy rate decision.",
        publisher="Federal Reserve",
        published_at=NOW - timedelta(hours=12),
    )
    marketing = _document(
        "apple",
        title="Apple announces a colorful new accessory",
        content="Apple announced a new accessory for customers.",
        publisher="Apple Newsroom",
        source_category=SourceCategory.COMPANY_PRIMARY,
        published_at=NOW - timedelta(minutes=1),
    )
    fed_score = score_event(
        _event(
            "fed-event", event_type=EventType.POLICY, title=fed.title, source_document_ids=[fed.id]
        ),
        None,
        [fed],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    marketing_score = score_event(
        _event(
            "apple-event",
            event_type=EventType.PRODUCT,
            title=marketing.title,
            entity_ids=["apple"],
            source_document_ids=[marketing.id],
            occurred_at=marketing.published_at,
        ),
        None,
        [marketing],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    assert fed_score.importance_score > marketing_score.importance_score
    assert fed_score.importance_band in {ImportanceBand.CRITICAL, ImportanceBand.MATERIAL}
    assert ImportanceReason.MAJOR_POLICY_DECISION in fed_score.importance_reason_codes
    assert fed_score.relevance_score == marketing_score.relevance_score == 0
    assert fed_score.relevance_band is marketing_score.relevance_band is RelevanceBand.NONE
    assert fed_score.priority_band is PriorityBand.IMPORTANT
    assert marketing_score.priority_band is PriorityBand.WATCH


def test_authoritative_regulatory_action_receives_materiality_reason() -> None:
    source = _document(
        "sec-action",
        title="SEC enforcement action",
        content="The Securities and Exchange Commission announced enforcement action.",
        publisher="U.S. SEC",
        source_category=SourceCategory.REGULATORY,
    )
    score = score_event(
        _event(
            "sec-action",
            event_type=EventType.LEGAL,
            title=source.title,
            source_document_ids=[source.id],
        ),
        None,
        [source],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    assert ImportanceReason.REGULATORY_ACTION in score.importance_reason_codes
    assert score.importance_band in {ImportanceBand.MATERIAL, ImportanceBand.CRITICAL}


def test_same_publisher_documents_do_not_inflate_corroboration() -> None:
    first = _document(
        "first",
        title="Apple earnings update",
        content="Apple earnings update",
        publisher="Apple Newsroom",
        source_category=SourceCategory.COMPANY_PRIMARY,
    )
    second = _document(
        "second",
        title="Apple earnings update mirror",
        content="Apple earnings update",
        publisher="Apple Newsroom",
        source_category=SourceCategory.COMPANY_PRIMARY,
    )
    event = _event(
        "earnings",
        event_type=EventType.EARNINGS,
        title=first.title,
        entity_ids=["apple"],
        source_document_ids=[first.id, second.id],
    )
    score = score_event(
        event, None, [first, second], ViewProfile(id="global", name="Global"), UNIVERSE, NOW
    )
    points = {item.name: item.points for item in score.importance_components}
    assert points["corroboration"] == 0
    assert ImportanceReason.MULTI_SOURCE_CORROBORATION not in score.importance_reason_codes


def test_independent_publishers_add_bounded_corroboration() -> None:
    first = _document(
        "first-independent",
        title="Fed policy update",
        content="Fed policy update",
        publisher="Federal Reserve",
    )
    second = _document(
        "second-independent",
        title="Fed policy update confirmed",
        content="Fed policy update confirmed",
        publisher="European Financial Bulletin",
        source_category=SourceCategory.PROFESSIONAL_NEWS,
        source_role=SourceRole.SECONDARY,
        source_tier=SourceTier.B,
    )
    event = _event(
        "independent",
        event_type=EventType.POLICY,
        title=first.title,
        source_document_ids=[first.id, second.id],
    )
    score = score_event(
        event,
        None,
        [first, second],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    points = {item.name: item.points for item in score.importance_components}
    assert points["corroboration"] > 0
    assert ImportanceReason.MULTI_SOURCE_CORROBORATION in score.importance_reason_codes


def test_view_relevance_is_separate_and_direct_entity_is_strong() -> None:
    source = _document(
        "semis",
        title="Semiconductor supply update",
        content="Semiconductor capacity update",
        publisher="Semiconductor Authority",
        source_category=SourceCategory.OFFICIAL,
    )
    story = _story("semis-event", source)
    event = _event(
        "semis-event",
        event_type=EventType.SUPPLY_CHAIN,
        title=source.title,
        entity_ids=["semiconductors"],
        source_document_ids=[source.id],
    )
    global_score = score_event(
        event, story, [source], ViewProfile(id="global", name="Global"), UNIVERSE, NOW
    )
    sector_view = ViewProfile(
        id="semis", name="Semis", watched_entity_ids=("semiconductors",), topics=(Topic.TECHNOLOGY,)
    )
    sector_score = score_event(event, story, [source], sector_view, UNIVERSE, NOW)
    assert global_score.importance_score == sector_score.importance_score
    assert sector_score.importance_band is ImportanceBand.MATERIAL
    assert global_score.relevance_band is RelevanceBand.NONE
    assert global_score.relevance_score < sector_score.relevance_score
    assert sector_score.relevance_band is RelevanceBand.HIGH
    assert RelevanceReason.DIRECT_WATCH_ENTITY in sector_score.relevance_reason_codes
    assert global_score.priority_band is PriorityBand.IMPORTANT
    assert sector_score.priority_band is PriorityBand.MUST_READ


def test_entity_significance_uses_only_validated_canonical_configuration() -> None:
    configured = score_event(
        _event(
            "configured-entity",
            event_type=EventType.PRODUCT,
            title="Configured",
            entity_ids=["apple"],
        ),
        None,
        [],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    unknown = score_event(
        _event(
            "unknown-entity",
            event_type=EventType.PRODUCT,
            title="Unknown",
            entity_ids=["unknown"],
        ),
        None,
        [],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    configured_components = {item.name: item.points for item in configured.importance_components}
    unknown_components = {item.name: item.points for item in unknown.importance_components}
    assert configured_components["entity_significance"] == 5
    assert unknown_components["entity_significance"] == 0

    invalid_universe = {
        **UNIVERSE,
        "entities": [{"id": "apple", "name": "Apple", "importance_points": 11}],
    }
    with pytest.raises(ValueError, match="importance_points must be within 0..10"):
        score_event(
            _event(
                "invalid-entity",
                event_type=EventType.PRODUCT,
                title="Invalid",
                entity_ids=["apple"],
            ),
            None,
            [],
            ViewProfile(id="global", name="Global"),
            invalid_universe,
            NOW,
        )


def test_region_only_relevance_is_bounded_and_generic_words_do_not_match() -> None:
    source = _document(
        "region",
        title="A generic company announcement",
        content="A generic announcement about rates",
        publisher="Company",
        source_category=SourceCategory.COMPANY_PRIMARY,
    )
    event = _event(
        "region-event",
        event_type=EventType.OTHER,
        title=source.title,
        source_document_ids=[source.id],
    )
    view = ViewProfile(id="us", name="US", regions=("US",), topics=(Topic.RATES,))
    score = score_event(event, None, [source], view, UNIVERSE, NOW)
    assert score.relevance_score <= 10
    assert score.relevance_band in {RelevanceBand.LOW, RelevanceBand.NONE}


def test_freshness_is_bounded_and_other_is_conservative() -> None:
    source = _document(
        "other",
        title="Fresh generic update",
        content="Fresh generic update",
        publisher="Unknown",
        source_category=SourceCategory.OTHER,
        source_tier=SourceTier.D,
    )
    event = _event(
        "other-event",
        event_type=EventType.OTHER,
        title=source.title,
        source_document_ids=[source.id],
        occurred_at=source.published_at,
    )
    score = score_event(
        event, None, [source], ViewProfile(id="global", name="Global"), UNIVERSE, NOW
    )
    points = {item.name: item.points for item in score.importance_components}
    assert points["freshness"] <= 5
    assert score.importance_band is ImportanceBand.LOW
    assert score.relevance_band is RelevanceBand.NONE
    assert score.priority_band is PriorityBand.BACKGROUND
    assert ImportanceReason.OTHER_CONSERVATIVE in score.importance_reason_codes


def test_generic_story_lifecycle_freshness_outranks_same_authority_repeat() -> None:
    source = _document(
        "lifecycle-source",
        title="A material market intelligence update",
        content="A material market intelligence update with unchanged facts.",
        publisher="Primary Publisher",
    )
    fresh = score_event(
        _event(
            "lifecycle-fresh",
            event_type=EventType.MACRO,
            title=source.title,
            source_document_ids=[source.id],
            occurred_at=NOW - timedelta(hours=4),
        ),
        None,
        [source],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    repeated = score_event(
        _event(
            "lifecycle-repeat",
            event_type=EventType.MACRO,
            title=source.title,
            source_document_ids=[source.id],
            occurred_at=NOW - timedelta(days=4),
        ),
        None,
        [source],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )

    ranked = rank_scores([repeated, fresh])
    assert fresh.importance_score > repeated.importance_score
    assert [item.event_id for item in ranked] == ["lifecycle-fresh", "lifecycle-repeat"]


def test_other_cannot_become_material_from_scope_or_corroboration_alone() -> None:
    first = _document(
        "other-official",
        title="Generic official update",
        content="Generic official update",
        publisher="Official Publisher",
    )
    second = _document(
        "other-news",
        title="Generic official update reported",
        content="Generic official update reported",
        publisher="Independent News",
        source_category=SourceCategory.PROFESSIONAL_NEWS,
        source_role=SourceRole.SECONDARY,
        source_tier=SourceTier.B,
    )
    third = _document(
        "other-regulatory",
        title="Generic official update review",
        content="Generic official update review",
        publisher="Regulatory Bulletin",
        source_category=SourceCategory.REGULATORY,
        source_role=SourceRole.PRIMARY,
        source_tier=SourceTier.A,
    )
    event = _event(
        "other-materiality",
        event_type=EventType.OTHER,
        title=first.title,
        source_document_ids=[first.id, second.id, third.id],
    )
    score = score_event(
        event,
        None,
        [first, second, third],
        ViewProfile(id="global", name="Global"),
        UNIVERSE,
        NOW,
    )
    assert score.importance_band is not ImportanceBand.MATERIAL


def test_rank_scores_is_permutation_invariant_and_uses_event_id_tie_break() -> None:
    view = ViewProfile(id="global", name="Global")
    event_a = _event("a", event_type=EventType.OTHER, title="A")
    event_b = _event("b", event_type=EventType.OTHER, title="B")
    scores = [
        score_event(event_b, None, [], view, UNIVERSE, NOW),
        score_event(event_a, None, [], view, UNIVERSE, NOW),
    ]
    ranked = rank_scores(scores)
    assert [item.event_id for item in ranked] == ["a", "b"]
    assert [item.priority_rank for item in ranked] == [1, 2]
    assert [item.event_id for item in rank_scores(list(reversed(scores)))] == ["a", "b"]


def test_critical_global_event_is_not_buried_by_trivial_direct_watch() -> None:
    fed = _document(
        "critical-fed",
        title="Federal Reserve policy rate decision",
        content="Federal Reserve monetary policy rate decision",
        publisher="Federal Reserve",
    )
    apple = _document(
        "trivial-apple",
        title="Apple launches a colorful accessory",
        content="Apple product launch announcement",
        publisher="Apple Newsroom",
        source_category=SourceCategory.COMPANY_PRIMARY,
    )
    view = ViewProfile(id="apple", name="Apple", watched_entity_ids=("apple",))
    scores = [
        score_event(
            _event(
                "trivial-apple",
                event_type=EventType.PRODUCT,
                title=apple.title,
                entity_ids=["apple"],
                source_document_ids=[apple.id],
            ),
            None,
            [apple],
            view,
            UNIVERSE,
            NOW,
        ),
        score_event(
            _event(
                "critical-fed",
                event_type=EventType.POLICY,
                title=fed.title,
                entity_ids=["federal-reserve"],
                source_document_ids=[fed.id],
            ),
            None,
            [fed],
            view,
            UNIVERSE,
            NOW,
        ),
    ]
    scores_by_id = {item.event_id: item for item in scores}
    assert scores_by_id["critical-fed"].importance_band is ImportanceBand.CRITICAL
    assert scores_by_id["critical-fed"].relevance_band is RelevanceBand.NONE
    assert scores_by_id["critical-fed"].priority_band is PriorityBand.MUST_READ
    assert scores_by_id["trivial-apple"].importance_band is ImportanceBand.ROUTINE
    assert scores_by_id["trivial-apple"].relevance_band is RelevanceBand.HIGH
    assert scores_by_id["trivial-apple"].priority_band is PriorityBand.IMPORTANT
    ranked = rank_scores(scores)
    assert ranked[0].event_id == "critical-fed"
    assert ranked[0].priority_band is PriorityBand.MUST_READ


def test_score_events_supports_multiple_views_without_mutating_events() -> None:
    source = _document(
        "apple-event",
        title="Apple earnings",
        content="Apple earnings",
        publisher="Apple Newsroom",
        source_category=SourceCategory.COMPANY_PRIMARY,
    )
    event = _event(
        "apple-event",
        event_type=EventType.EARNINGS,
        title=source.title,
        entity_ids=["apple"],
        source_document_ids=[source.id],
    )
    views = load_view_profiles(UNIVERSE)
    result = score_events([event], [], [source], views, UNIVERSE, NOW)
    assert {item.view_id for item in result} == {"global", "ai-semiconductors"}
    assert event.importance_score == 10
    assert all(item.rule_version == C7_RULE_VERSION for item in result)


def test_score_events_accepts_single_use_iterables_deterministically() -> None:
    first = _event("a", event_type=EventType.OTHER, title="A")
    second = _event("b", event_type=EventType.OTHER, title="B")
    result = score_events(
        iter([second, first]),
        iter([]),
        [],
        [ViewProfile(id="global", name="Global"), ViewProfile(id="us", name="US")],
        UNIVERSE,
        NOW,
    )
    assert [(item.view_id, item.event_id) for item in result] == [
        ("global", "a"),
        ("global", "b"),
        ("us", "a"),
        ("us", "b"),
    ]
