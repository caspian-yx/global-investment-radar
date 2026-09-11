from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from radar.config import Settings, load_universe
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
    MacroObservation,
    MarketObservation,
    SourceDocument,
    SourceKind,
    SourceTier,
    Topic,
    content_hash,
    stable_id,
)
from radar.explanation import (
    DRIVER_RULE_VERSION,
    build_cross_asset_context,
    build_driver_candidates,
    build_macro_backdrop,
    build_observation_evidence,
    categorize_driver_confidence,
    classify_relevance,
    classify_temporal_relation,
    driver_candidate_id,
    evidence_window_allows,
    extract_topics,
    unique_by_id,
    validate_driver_candidate_bundle,
)

TARGET_AT = datetime(2026, 9, 7, 20, tzinfo=UTC)  # Monday, 16:00 New York.
AS_OF = datetime(2026, 9, 8, 6, tzinfo=UTC)
US_ASSET = {
    "symbol": "^GSPC",
    "name": "S&P 500",
    "region": "US",
    "asset_class": "EQUITY_INDEX",
    "session_kind": "EXCHANGE",
    "timezone": "America/New_York",
    "close_time": "16:00",
    "topics": ["RATES", "INFLATION", "EMPLOYMENT", "TECHNOLOGY", "US"],
}


def market_observation(
    symbol: str = "^GSPC",
    *,
    name: str = "S&P 500",
    change_pct: float = -2.5,
    observed_at: datetime = TARGET_AT,
) -> MarketObservation:
    return MarketObservation(
        id=stable_id("market", f"test|{symbol}|{observed_at.isoformat()}"),
        symbol=symbol,
        name=name,
        region="US",
        observed_at=observed_at,
        close=100 + change_pct,
        previous_close=100,
        change_pct=change_pct,
        currency="USD",
        provider="test-market",
    )


def macro_observation(
    series_id: str = "CUUR0000SA0",
    *,
    name: str = "CPI",
    value: float = 318.2,
    previous_value: float | None = 317.5,
    unit: str = "index",
) -> MacroObservation:
    change = None if previous_value is None else value - previous_value
    return MacroObservation(
        id=stable_id("macro", f"BLS|{series_id}|2026-08"),
        series_id=series_id,
        name=name,
        period="2026-08",
        observed_at=AS_OF,
        value=value,
        previous_value=previous_value,
        change=change,
        change_label="vs previous month" if change is not None else None,
        unit=unit,
        provider="bls-public-api",
    )


def document(
    document_id: str,
    published_at: datetime,
    *,
    title: str = "Federal Reserve announces interest rate decision",
    content: str = "The Federal Reserve announced a federal funds rate decision.",
    publisher: str = "Federal Reserve",
    source_tier: SourceTier = SourceTier.A,
    collected_at: datetime | None = None,
    url: str | None = None,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        provider="official-rss",
        source_tier=source_tier,
        publisher=publisher,
        canonical_url=url or f"https://example.test/{document_id}",
        title=title,
        content=content,
        published_at=published_at,
        collected_at=collected_at or AS_OF,
        content_hash=content_hash(content),
    )


def document_evidence(item: SourceDocument, suffix: str = "") -> Evidence:
    excerpt = item.content
    return Evidence(
        id=stable_id("evidence", f"{item.id}|{content_hash(excerpt)}|{suffix}"),
        source_kind=SourceKind.DOCUMENT,
        source_id=item.id,
        source_document_id=item.id,
        locator={"type": "rss_or_provider_record", "title": item.title},
        excerpt=excerpt,
        checksum=content_hash(excerpt),
        quality_score=1.0 if item.source_tier is SourceTier.A else 0.8,
    )


def event(
    event_id: str,
    event_type: EventType,
    occurred_at: datetime,
    *,
    title: str,
    entity_ids: list[str],
    evidence_ids: list[str],
    source_document_ids: list[str],
    claim_ids: list[str] | None = None,
) -> Event:
    return Event(
        id=event_id,
        event_key=event_id,
        event_type=event_type,
        title=title,
        occurred_at=occurred_at,
        first_seen_at=occurred_at,
        last_seen_at=occurred_at,
        entity_ids=entity_ids,
        claim_ids=claim_ids or [],
        evidence_ids=evidence_ids,
        source_document_ids=source_document_ids,
        importance_score=60,
        importance_components={"materiality": 60},
        confidence=0.9,
    )


def target_bundle() -> tuple[MarketObservation, Evidence, Event]:
    observation = market_observation()
    evidence = build_observation_evidence([observation], [])[0]
    target = event(
        "target-event",
        EventType.MARKET_MOVE,
        TARGET_AT,
        title="S&P 500 fell 2.50%",
        entity_ids=["^GSPC"],
        evidence_ids=[evidence.id],
        source_document_ids=[],
    )
    return observation, evidence, target


def candidate_bundle(
    *,
    published_at: datetime = TARGET_AT - timedelta(hours=24),
    title: str = "Federal Reserve announces interest rate decision",
    content: str = "The Federal Reserve announced a federal funds rate decision.",
    publisher: str = "Federal Reserve",
    source_tier: SourceTier = SourceTier.A,
    document_id: str = "document-1",
) -> tuple[SourceDocument, Evidence, Event]:
    item = document(
        document_id,
        published_at,
        title=title,
        content=content,
        publisher=publisher,
        source_tier=source_tier,
    )
    evidence = document_evidence(item)
    candidate = event(
        f"event-{document_id}",
        EventType.POLICY,
        published_at,
        title=title,
        entity_ids=["federal-reserve"],
        evidence_ids=[evidence.id],
        source_document_ids=[item.id],
    )
    return item, evidence, candidate


def build_one_driver(
    *,
    candidate_published_at: datetime = TARGET_AT - timedelta(hours=24),
    title: str = "Federal Reserve announces interest rate decision",
    content: str = "The Federal Reserve announced a federal funds rate decision.",
) -> tuple[DriverCandidate, Claim, dict[str, object]]:
    observation, target_evidence, target = target_bundle()
    source, source_evidence, candidate = candidate_bundle(
        published_at=candidate_published_at,
        title=title,
        content=content,
    )
    drivers, claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, source_evidence],
        documents=[source],
        assets=[US_ASSET],
        as_of=AS_OF,
    )
    assert len(drivers) == len(claims) == 1
    return drivers[0], claims[0], {
        "observation": observation,
        "target_evidence": target_evidence,
        "target": target,
        "source": source,
        "source_evidence": source_evidence,
        "candidate": candidate,
    }


def test_observation_evidence_is_reusable_and_c1_equivalent() -> None:
    market = market_observation()
    macro = macro_observation()

    first = build_observation_evidence([market], [macro])
    second = build_observation_evidence([market], [macro])

    assert first == second
    assert len({item.id for item in first}) == 2
    market_evidence, macro_evidence = first
    assert market_evidence.source_kind is SourceKind.MARKET_OBSERVATION
    assert market_evidence.source_id == market.id
    assert market_evidence.locator["symbol"] == "^GSPC"
    assert macro_evidence.source_kind is SourceKind.MACRO_OBSERVATION
    assert macro_evidence.source_id == macro.id
    assert "first observed by Radar" in macro_evidence.excerpt
    assert "release" not in macro_evidence.excerpt.casefold()


def test_identical_evidence_is_reused_but_conflicting_payload_is_rejected() -> None:
    item = build_observation_evidence([market_observation()], [])[0]

    assert unique_by_id([item, item.model_copy(deep=True)]) == [item]
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        unique_by_id([item, item.model_copy(update={"excerpt": "different"})])


def test_all_17_assets_have_cautious_session_and_topic_metadata() -> None:
    assets = load_universe(Settings())["assets"]
    actual = {
        item["symbol"]: (
            item["session_kind"],
            item["timezone"],
            item.get("close_time"),
            tuple(item["topics"]),
        )
        for item in assets
    }

    assert actual == {
        "^GSPC": (
            "EXCHANGE",
            "America/New_York",
            "16:00",
            ("RATES", "INFLATION", "EMPLOYMENT", "TECHNOLOGY", "US"),
        ),
        "^NDX": (
            "EXCHANGE",
            "America/New_York",
            "16:00",
            ("RATES", "INFLATION", "TECHNOLOGY", "US"),
        ),
        "^DJI": (
            "EXCHANGE",
            "America/New_York",
            "16:00",
            ("RATES", "INFLATION", "EMPLOYMENT", "ENERGY", "US"),
        ),
        "^RUT": ("EXCHANGE", "America/New_York", "16:00", ("RATES", "EMPLOYMENT", "US")),
        "^VIX": ("EXCHANGE", "America/New_York", "16:00", ("RATES", "REGULATION", "US")),
        "^HSI": ("EXCHANGE", "Asia/Hong_Kong", "16:00", ("CHINA", "TECHNOLOGY", "REGULATION")),
        "3033.HK": ("EXCHANGE", "Asia/Hong_Kong", "16:00", ("CHINA", "TECHNOLOGY", "REGULATION")),
        "000001.SS": ("EXCHANGE", "Asia/Shanghai", "15:00", ("CHINA", "RATES", "REGULATION")),
        "510300.SS": ("EXCHANGE", "Asia/Shanghai", "15:00", ("CHINA", "RATES", "REGULATION")),
        "^N225": ("EXCHANGE", "Asia/Tokyo", "15:30", ("RATES", "TECHNOLOGY")),
        "^KS11": ("EXCHANGE", "Asia/Seoul", "15:30", ("RATES", "TECHNOLOGY")),
        "^STOXX50E": (
            "EXCHANGE",
            "Europe/Berlin",
            "17:30",
            ("RATES", "INFLATION", "ENERGY", "TECHNOLOGY"),
        ),
        "EURUSD=X": ("NEAR_24_HOUR", "UTC", None, ("RATES", "INFLATION", "US")),
        "JPY=X": ("NEAR_24_HOUR", "UTC", None, ("RATES", "US")),
        "GC=F": ("NEAR_24_HOUR", "UTC", None, ("RATES", "INFLATION", "US")),
        "CL=F": ("NEAR_24_HOUR", "UTC", None, ("ENERGY", "CHINA", "US")),
        "BTC-USD": ("CONTINUOUS", "UTC", None, ("REGULATION", "TECHNOLOGY", "US")),
    }


@pytest.mark.parametrize(
    ("delta", "want"),
    [
        (timedelta(hours=47, minutes=59, seconds=59), DriverTemporalRelation.PRIOR),
        (timedelta(hours=48), DriverTemporalRelation.PRIOR),
        (timedelta(hours=48, seconds=1), DriverTemporalRelation.STALE),
    ],
)
def test_temporal_normal_window_has_exact_inclusive_48h_boundary(
    delta: timedelta, want: DriverTemporalRelation
) -> None:
    _, _, target = target_bundle()
    ordinary_target_at = TARGET_AT + timedelta(days=1)
    target = target.model_copy(update={"occurred_at": ordinary_target_at})
    _, _, candidate = candidate_bundle(published_at=ordinary_target_at - delta)

    assert classify_temporal_relation(target, candidate, US_ASSET) is want


@pytest.mark.parametrize(
    ("age", "weekend_extension", "want"),
    [
        (timedelta(hours=47, minutes=59, seconds=59), False, True),
        (timedelta(hours=48), False, True),
        (timedelta(hours=48, seconds=1), False, False),
        (timedelta(hours=95, minutes=59, seconds=59), True, True),
        (timedelta(hours=96), True, True),
        (timedelta(hours=96, seconds=1), True, False),
    ],
)
def test_evidence_window_exact_boundaries(
    age: timedelta, weekend_extension: bool, want: bool
) -> None:
    assert evidence_window_allows(age, weekend_extension=weekend_extension) is want


@pytest.mark.parametrize(
    ("published_at", "want"),
    [
        (datetime(2026, 9, 4, 20, 0, 1, tzinfo=UTC), DriverTemporalRelation.PRIOR),
        (datetime(2026, 9, 3, 20, 0, 0, tzinfo=UTC), DriverTemporalRelation.STALE),
        (datetime(2026, 9, 3, 19, 59, 59, tzinfo=UTC), DriverTemporalRelation.STALE),
        (datetime(2026, 9, 3, 22, tzinfo=UTC), DriverTemporalRelation.STALE),
        (datetime(2026, 9, 7, 14, tzinfo=UTC), DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS),
        (datetime(2026, 9, 7, 20, 0, 1, tzinfo=UTC), DriverTemporalRelation.POST_EVENT),
    ],
)
def test_temporal_monday_extension_only_admits_friday_or_weekend(
    published_at: datetime, want: DriverTemporalRelation
) -> None:
    _, _, target = target_bundle()
    _, _, candidate = candidate_bundle(published_at=published_at)

    assert classify_temporal_relation(target, candidate, US_ASSET) is want


def test_temporal_ignores_collection_time_and_handles_dst_by_iana_zone() -> None:
    _, _, target = target_bundle()
    source, evidence, candidate = candidate_bundle(
        published_at=datetime(2026, 9, 7, 18, tzinfo=UTC)
    )
    source = source.model_copy(update={"collected_at": TARGET_AT + timedelta(days=30)})

    assert source.collected_at > target.occurred_at
    assert classify_temporal_relation(target, candidate, US_ASSET) is (
        DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
    )

    dst_target = target.model_copy(
        update={"occurred_at": datetime(2026, 11, 2, 21, tzinfo=UTC)}
    )
    dst_candidate = candidate.model_copy(
        update={"occurred_at": datetime(2026, 11, 2, 20, 30, tzinfo=UTC)}
    )
    assert evidence.source_kind is SourceKind.DOCUMENT
    assert classify_temporal_relation(dst_target, dst_candidate, US_ASSET) is (
        DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
    )


@pytest.mark.parametrize(
    "text",
    [
        "Corporate rating methodology update",
        "Artificial island construction",
        "Small business survey",
        "China appears only in navigation boilerplate",
        "General energy update",
    ],
)
def test_topic_matching_rejects_generic_or_substring_false_positives(text: str) -> None:
    assert extract_topics(text) == set()


def test_topic_taxonomy_uses_specific_boundary_aware_aliases() -> None:
    text = (
        "Federal Reserve interest rate decision; consumer price index; nonfarm payrolls; "
        "SEC enforcement; semiconductor data centers; crude oil OPEC; Beijing policy; "
        "United States outlook"
    )

    assert extract_topics(text) == {
        Topic.RATES,
        Topic.INFLATION,
        Topic.EMPLOYMENT,
        Topic.REGULATION,
        Topic.TECHNOLOGY,
        Topic.ENERGY,
        Topic.CHINA,
        Topic.US,
    }


def test_boilerplate_marker_does_not_discard_valid_body_topics() -> None:
    text = "Federal Reserve announces an interest rate decision. Privacy policy"

    assert Topic.RATES in extract_topics(text)


@pytest.mark.parametrize(
    "text",
    [
        "Navigation: China policy | Site menu | Privacy Policy",
        "Footer China policy cookie",
    ],
)
def test_strong_topic_inside_boilerplate_only_segment_is_rejected(text: str) -> None:
    assert extract_topics(text) == set()


@pytest.mark.parametrize(
    "text",
    ["China | Policy", "data | center", "United | States"],
)
def test_topic_alias_cannot_be_synthesized_across_segment_boundaries(text: str) -> None:
    assert extract_topics(text) == set()


@pytest.mark.parametrize("text", ["U.S. economy", "U.S. market"])
def test_topic_sentence_splitting_preserves_abbreviation_aliases(text: str) -> None:
    assert Topic.US in extract_topics(text)


def test_single_generic_topic_is_weak_and_cannot_create_a_driver() -> None:
    _, target_evidence, target = target_bundle()
    weak_asset = {**US_ASSET, "topics": ["ENERGY"]}
    source, source_evidence, candidate = candidate_bundle(
        title="General energy update",
        content="A general energy update was published.",
    )

    assert classify_relevance(target, candidate, [source], weak_asset) is (
        DriverRelevance.WEAK
    )
    drivers, claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, source_evidence],
        documents=[source],
        assets=[weak_asset],
        as_of=AS_OF,
    )
    assert drivers == []
    assert claims == []


def test_exact_entity_outranks_strong_topic_and_symbol_collisions_do_not_match() -> None:
    _, _, target = target_bundle()
    exact_source, _, exact_event = candidate_bundle(
        title="S&P 500 responds to Federal Reserve decision",
        content="The S&P 500 was named in the official statement.",
    )
    topic_source, _, topic_event = candidate_bundle(document_id="document-2")
    collision_source, _, collision_event = candidate_bundle(
        document_id="document-3",
        title="The symbol GSPCXYZ appears in a table",
        content="No index name is present.",
    )

    assert classify_relevance(target, exact_event, [exact_source], US_ASSET) is (
        DriverRelevance.EXACT_ENTITY
    )
    assert classify_relevance(target, topic_event, [topic_source], US_ASSET) is (
        DriverRelevance.STRONG_TOPIC
    )
    shared_entity_event = topic_event.model_copy(update={"entity_ids": ["^GSPC"]})
    assert classify_relevance(target, shared_entity_event, [topic_source], US_ASSET) is (
        DriverRelevance.EXACT_ENTITY
    )
    assert classify_relevance(target, collision_event, [collision_source], US_ASSET) is (
        DriverRelevance.NONE
    )


def test_driver_identity_depends_only_on_semantic_relationship() -> None:
    candidate_evidence = ["evidence-b", "evidence-a"]
    first = driver_candidate_id(
        "target", "candidate", candidate_evidence, DRIVER_RULE_VERSION
    )
    reordered = driver_candidate_id(
        "target", "candidate", list(reversed(candidate_evidence)), DRIVER_RULE_VERSION
    )

    assert first == reordered
    assert first != driver_candidate_id(
        "other-target", "candidate", candidate_evidence, DRIVER_RULE_VERSION
    )
    assert first != driver_candidate_id(
        "target", "candidate", ["evidence-a"], DRIVER_RULE_VERSION
    )
    assert first != driver_candidate_id("target", "candidate", candidate_evidence, "v2")


def test_generated_driver_identity_ignores_as_of_collection_and_insertion_order() -> None:
    first, _, bundle = build_one_driver()
    changed_source = bundle["source"].model_copy(  # type: ignore[union-attr]
        update={"collected_at": AS_OF + timedelta(days=10)}
    )
    drivers, _ = build_driver_candidates(
        target_events=[bundle["target"]],  # type: ignore[list-item]
        candidate_events=[bundle["candidate"]],  # type: ignore[list-item]
        evidence=[bundle["source_evidence"], bundle["target_evidence"]],  # type: ignore[list-item]
        documents=[changed_source],
        assets=[US_ASSET],
        as_of=AS_OF + timedelta(days=10),
    )

    assert drivers[0].id == first.id
    assert drivers[0].as_of != first.as_of


@pytest.mark.parametrize(
    ("temporal", "relevance", "tiers", "publishers", "conflict", "want"),
    [
        ("PRIOR", "EXACT_ENTITY", ["A", "B"], ["one", "two"], False, "HIGH"),
        ("PRIOR", "STRONG_TOPIC", ["A"], ["one"], False, "MEDIUM"),
        ("SAME_WINDOW_AMBIGUOUS", "EXACT_ENTITY", ["A", "B"], ["one", "two"], False, "LOW"),
        ("PRIOR", "EXACT_ENTITY", ["A", "B"], ["one", "two"], True, "LOW"),
        ("PRIOR", "EXACT_ENTITY", ["C"], ["one"], False, "LOW"),
        ("UNKNOWN", "EXACT_ENTITY", ["A", "B"], ["one", "two"], False, "LOW"),
    ],
)
def test_confidence_mapping_has_explicit_ceilings(
    temporal: str,
    relevance: str,
    tiers: list[str],
    publishers: list[str],
    conflict: bool,
    want: str,
) -> None:
    confidence, components, reasons = categorize_driver_confidence(
        temporal=DriverTemporalRelation(temporal),
        relevance=DriverRelevance(relevance),
        source_tiers=[SourceTier(item) for item in tiers],
        publishers=publishers,
        conflict=conflict,
    )

    assert confidence is DriverConfidence(want)
    assert components == {
        "temporal": temporal,
        "relevance": relevance,
        "source_quality": "A_B" if set(tiers) <= {"A", "B"} else "LOW_OR_UNKNOWN",
        "independent_publishers": str(len(set(publishers))),
        "conflict": "YES" if conflict else "NO",
    }
    assert reasons


def test_driver_is_interpretation_with_cautious_language_and_canonical_source_event() -> None:
    driver, claim, bundle = build_one_driver()
    lowered = claim.text.casefold()

    assert driver.candidate_event_id == bundle["candidate"].id  # type: ignore[union-attr]
    assert claim.id == driver.interpretation_claim_id
    assert claim.claim_type is ClaimType.INTERPRETATION
    assert driver.confidence is DriverConfidence.MEDIUM
    assert "plausible candidate" in lowered
    assert "temporally consistent" in lowered
    for unsafe in (
        "because",
        "caused",
        "drove",
        "driven by",
        "led to",
        "resulted in",
        "triggered",
        "explains the move",
        "buy",
        "sell",
        "target price",
        "will rise",
        "will fall",
    ):
        assert unsafe not in lowered


def test_interpretation_does_not_embed_untrusted_causal_or_trading_title() -> None:
    driver, claim, _ = build_one_driver(
        title="Federal Reserve rate decision caused SELL target price forecast",
        content="The Federal Reserve announced a federal funds rate decision.",
    )

    assert driver.interpretation_claim_id == claim.id
    lowered = claim.text.casefold()
    for unsafe in ("caused", "sell", "target price", "forecast"):
        assert unsafe not in lowered


def test_ambiguous_driver_is_low_and_irrelevant_or_post_event_sources_are_rejected() -> None:
    ambiguous, _, _ = build_one_driver(
        candidate_published_at=datetime(2026, 9, 7, 18, tzinfo=UTC)
    )
    assert ambiguous.temporal_relation is DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
    assert ambiguous.confidence is DriverConfidence.LOW

    observation, target_evidence, target = target_bundle()
    source, source_evidence, candidate = candidate_bundle(
        published_at=TARGET_AT + timedelta(seconds=1),
        title="Unrelated corporate appointment",
        content="A company appointed a director.",
    )
    drivers, claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, source_evidence],
        documents=[source],
        assets=[US_ASSET],
        as_of=AS_OF,
    )
    assert observation.symbol == "^GSPC"
    assert drivers == []
    assert claims == []


def test_high_confidence_requires_distinct_publishers_and_distinct_urls() -> None:
    _, target_evidence, target = target_bundle()
    first = document(
        "source-a",
        TARGET_AT - timedelta(hours=24),
        title="S&P 500 and Federal Reserve interest rate decision",
        publisher="Federal Reserve",
    )
    second = document(
        "source-b",
        TARGET_AT - timedelta(hours=23),
        title="S&P 500 and Federal Reserve interest rate decision",
        publisher="Independent Newswire",
        source_tier=SourceTier.B,
    )
    source_evidence = [document_evidence(first), document_evidence(second)]
    candidate = event(
        "corroborated-event",
        EventType.POLICY,
        first.published_at,
        title=first.title,
        entity_ids=["federal-reserve"],
        evidence_ids=[item.id for item in source_evidence],
        source_document_ids=[first.id, second.id],
    )

    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, *source_evidence],
        documents=[first, second],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert drivers[0].confidence is DriverConfidence.HIGH

    duplicate_url = second.model_copy(
        update={"canonical_url": f"{first.canonical_url}?utm_source=duplicate"}
    )
    duplicate_url_evidence = document_evidence(duplicate_url)
    candidate = candidate.model_copy(
        update={
            "evidence_ids": [source_evidence[0].id, duplicate_url_evidence.id],
        }
    )
    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, source_evidence[0], duplicate_url_evidence],
        documents=[first, duplicate_url],
        assets=[US_ASSET],
        as_of=AS_OF,
    )
    assert drivers[0].confidence is DriverConfidence.MEDIUM


def test_post_event_cluster_document_is_excluded_from_driver_support() -> None:
    _, target_evidence, target = target_bundle()
    prior = document(
        "prior-document",
        TARGET_AT - timedelta(hours=24),
        title="S&P 500 and Federal Reserve interest rate decision",
        publisher="Federal Reserve",
        url="https://federalreserve.gov/prior",
    )
    post = document(
        "post-document",
        TARGET_AT + timedelta(hours=2),
        title="S&P 500 and Federal Reserve interest rate decision follow-up",
        publisher="Independent Newswire",
        source_tier=SourceTier.B,
        url="https://newswire.test/post",
    )
    prior_evidence = document_evidence(prior)
    post_evidence = document_evidence(post)
    candidate = event(
        "mixed-window-event",
        EventType.POLICY,
        prior.published_at,
        title=prior.title,
        entity_ids=["federal-reserve"],
        evidence_ids=[prior_evidence.id, post_evidence.id],
        source_document_ids=[prior.id, post.id],
    )

    drivers, claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, prior_evidence, post_evidence],
        documents=[prior, post],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert len(drivers) == len(claims) == 1
    assert drivers[0].candidate_evidence_ids == [prior_evidence.id]
    assert post_evidence.id not in drivers[0].evidence_ids
    assert drivers[0].confidence is DriverConfidence.MEDIUM


def test_uncited_cluster_document_cannot_increase_corroboration() -> None:
    _, target_evidence, target = target_bundle()
    cited = document(
        "cited-document",
        TARGET_AT - timedelta(hours=24),
        title="S&P 500 and Federal Reserve interest rate decision",
        publisher="Federal Reserve",
        url="https://federalreserve.gov/cited",
    )
    uncited = document(
        "uncited-document",
        TARGET_AT - timedelta(hours=23),
        title=cited.title,
        publisher="Independent Newswire",
        url="https://newswire.test/uncited",
    )
    cited_evidence = document_evidence(cited)
    candidate = event(
        "partially-cited-event",
        EventType.POLICY,
        cited.published_at,
        title=cited.title,
        entity_ids=["federal-reserve"],
        evidence_ids=[cited_evidence.id],
        source_document_ids=[cited.id, uncited.id],
    )

    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, cited_evidence],
        documents=[cited, uncited],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert drivers[0].confidence is DriverConfidence.MEDIUM
    assert drivers[0].confidence_components["independent_publishers"] == "1"


def test_opposite_stances_inside_one_event_trigger_conflict_ceiling() -> None:
    _, target_evidence, target = target_bundle()
    hike = document(
        "same-event-hike",
        TARGET_AT - timedelta(hours=24),
        title="Federal Reserve rate hike decision",
        content="The Federal Reserve announced a rate hike.",
        publisher="Federal Reserve",
        url="https://federalreserve.gov/hike",
    )
    cut = document(
        "same-event-cut",
        TARGET_AT - timedelta(hours=23),
        title="Federal Reserve rate cut decision",
        content="A rate cut was announced by the Federal Reserve.",
        publisher="Independent Newswire",
        source_tier=SourceTier.B,
        url="https://newswire.test/cut",
    )
    source_evidence = [document_evidence(hike), document_evidence(cut)]
    candidate = event(
        "internally-conflicted-event",
        EventType.POLICY,
        hike.published_at,
        title="Federal Reserve rate decision",
        entity_ids=["federal-reserve"],
        evidence_ids=[item.id for item in source_evidence],
        source_document_ids=[hike.id, cut.id],
    )

    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, *source_evidence],
        documents=[hike, cut],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert drivers[0].confidence is DriverConfidence.LOW
    assert drivers[0].confidence_components["conflict"] == "YES"


def test_publisher_aliases_do_not_create_false_independent_corroboration() -> None:
    _, target_evidence, target = target_bundle()
    first = document(
        "fed-one",
        TARGET_AT - timedelta(hours=24),
        title="S&P 500 and Federal Reserve interest rate decision",
        publisher="Federal Reserve",
        url="https://federalreserve.gov/one",
    )
    alias = document(
        "fed-two",
        TARGET_AT - timedelta(hours=23),
        title=first.title,
        publisher="Federal Reserve Board",
        url="https://federalreserve.example/two",
    )
    source_evidence = [document_evidence(first), document_evidence(alias)]
    candidate = event(
        "publisher-alias-event",
        EventType.POLICY,
        first.published_at,
        title=first.title,
        entity_ids=["federal-reserve"],
        evidence_ids=[item.id for item in source_evidence],
        source_document_ids=[first.id, alias.id],
    )

    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, *source_evidence],
        documents=[first, alias],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert drivers[0].confidence is DriverConfidence.MEDIUM
    assert drivers[0].confidence_components["independent_publishers"] == "1"


def test_only_eligible_opposite_rate_stances_are_conflicts() -> None:
    _, target_evidence, target = target_bundle()
    hike = document(
        "hike",
        TARGET_AT - timedelta(hours=24),
        title="Federal Reserve rate hike decision",
        content="The Federal Reserve announced a rate hike.",
        publisher="Publisher One",
    )
    stale_cut = document(
        "stale-cut",
        TARGET_AT - timedelta(days=8),
        title="Federal Reserve rate cut decision",
        content="The Federal Reserve announced a rate cut.",
        publisher="Publisher Two",
    )
    documents = [hike, stale_cut]
    source_evidence = [document_evidence(item) for item in documents]
    candidates = [
        event(
            f"event-{item.id}",
            EventType.POLICY,
            item.published_at,
            title=item.title,
            entity_ids=["federal-reserve"],
            evidence_ids=[evidence.id],
            source_document_ids=[item.id],
        )
        for item, evidence in zip(documents, source_evidence, strict=True)
    ]

    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=candidates,
        evidence=[target_evidence, *source_evidence],
        documents=documents,
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert len(drivers) == 1
    assert drivers[0].confidence is DriverConfidence.MEDIUM
    assert drivers[0].confidence_components["conflict"] == "NO"

    fresh_cut = stale_cut.model_copy(
        update={"published_at": TARGET_AT - timedelta(hours=20)}
    )
    fresh_cut_event = candidates[1].model_copy(
        update={"occurred_at": fresh_cut.published_at}
    )
    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidates[0], fresh_cut_event],
        evidence=[target_evidence, *source_evidence],
        documents=[hike, fresh_cut],
        assets=[US_ASSET],
        as_of=AS_OF,
    )
    assert len(drivers) == 2
    assert all(item.confidence is DriverConfidence.LOW for item in drivers)
    assert all(item.confidence_components["conflict"] == "YES" for item in drivers)

    compatible_hike = fresh_cut.model_copy(
        update={
            "id": "compatible-hike",
            "title": "Federal Reserve rate hike update",
            "content": "The Federal Reserve confirmed a rate hike.",
        }
    )
    compatible_evidence = document_evidence(compatible_hike)
    compatible_event = fresh_cut_event.model_copy(
        update={
            "id": "event-compatible-hike",
            "title": compatible_hike.title,
            "evidence_ids": [compatible_evidence.id],
            "source_document_ids": [compatible_hike.id],
        }
    )
    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidates[0], compatible_event],
        evidence=[target_evidence, source_evidence[0], compatible_evidence],
        documents=[hike, compatible_hike],
        assets=[US_ASSET],
        as_of=AS_OF,
    )
    assert len(drivers) == 2
    assert all(item.confidence_components["conflict"] == "NO" for item in drivers)


def test_opposite_rate_stances_for_different_policy_entities_are_not_conflicts() -> None:
    _, target_evidence, target = target_bundle()
    fed = document(
        "fed-hike",
        TARGET_AT - timedelta(hours=24),
        title="Federal Reserve rate hike decision",
        content="The Federal Reserve announced a rate hike.",
        publisher="Federal Reserve",
    )
    ecb = document(
        "ecb-cut",
        TARGET_AT - timedelta(hours=20),
        title="European Central Bank rate cut decision",
        content="The European Central Bank announced a rate cut.",
        publisher="European Central Bank",
    )
    source_evidence = [document_evidence(fed), document_evidence(ecb)]
    candidates = [
        event(
            "fed-event",
            EventType.POLICY,
            fed.published_at,
            title=fed.title,
            entity_ids=["federal-reserve"],
            evidence_ids=[source_evidence[0].id],
            source_document_ids=[fed.id],
        ),
        event(
            "ecb-event",
            EventType.POLICY,
            ecb.published_at,
            title=ecb.title,
            entity_ids=["ecb"],
            evidence_ids=[source_evidence[1].id],
            source_document_ids=[ecb.id],
        ),
    ]

    drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=candidates,
        evidence=[target_evidence, *source_evidence],
        documents=[fed, ecb],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert len(drivers) == 2
    assert all(item.confidence_components["conflict"] == "NO" for item in drivers)
    assert all(item.confidence is DriverConfidence.MEDIUM for item in drivers)


def test_duplicate_canonical_candidate_event_is_emitted_once() -> None:
    _, target_evidence, target = target_bundle()
    source, source_evidence, candidate = candidate_bundle()

    drivers, claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate, candidate.model_copy(deep=True)],
        evidence=[target_evidence, source_evidence],
        documents=[source],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert len(drivers) == len(claims) == 1


def test_unknown_session_metadata_produces_unknown_not_fake_ordering() -> None:
    _, _, target = target_bundle()
    _, _, candidate = candidate_bundle()

    assert classify_temporal_relation(target, candidate, {}) is (
        DriverTemporalRelation.UNKNOWN
    )


@pytest.mark.parametrize("session_kind", ["NEAR_24_HOUR", "CONTINUOUS"])
def test_non_exchange_daily_bar_same_utc_date_is_always_ambiguous(
    session_kind: str,
) -> None:
    _, _, target = target_bundle()
    _, _, candidate = candidate_bundle(
        published_at=TARGET_AT + timedelta(hours=1)
    )
    asset = {**US_ASSET, "session_kind": session_kind, "timezone": "UTC"}
    asset.pop("close_time", None)

    assert classify_temporal_relation(target, candidate, asset) is (
        DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
    )


def test_driver_bundle_validation_rejects_cross_reference_errors() -> None:
    driver, claim, bundle = build_one_driver()
    events = [bundle["target"], bundle["candidate"]]
    evidence = [bundle["target_evidence"], bundle["source_evidence"]]

    validate_driver_candidate_bundle(
        driver,
        events=events,  # type: ignore[arg-type]
        claims=[claim],
        evidence=evidence,  # type: ignore[arg-type]
        markets=[bundle["observation"]],  # type: ignore[list-item]
        documents=[bundle["source"]],  # type: ignore[list-item]
        assets=[US_ASSET],
    )

    bad_claim = claim.model_copy(update={"claim_type": ClaimType.DERIVED})
    with pytest.raises(ValueError, match="INTERPRETATION"):
        validate_driver_candidate_bundle(
            driver,
            events=events,  # type: ignore[arg-type]
            claims=[bad_claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    wrong_target = bundle["target"].model_copy(update={"event_type": EventType.POLICY})  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="MARKET_MOVE/RISK"):
        validate_driver_candidate_bundle(
            driver,
            events=[wrong_target, bundle["candidate"]],  # type: ignore[list-item]
            claims=[claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    with pytest.raises(ValueError, match="candidate evidence"):
        validate_driver_candidate_bundle(
            driver,
            events=events,  # type: ignore[arg-type]
            claims=[claim],
            evidence=[bundle["target_evidence"]],  # type: ignore[list-item]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    inconsistent_confidence = driver.model_copy(update={"confidence": DriverConfidence.HIGH})
    with pytest.raises(ValueError, match="confidence"):
        validate_driver_candidate_bundle(
            inconsistent_confidence,
            events=events,  # type: ignore[arg-type]
            claims=[claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    weak = driver.model_copy(update={"relevance_relation": DriverRelevance.WEAK})
    with pytest.raises(ValueError, match="eligible relevance"):
        validate_driver_candidate_bundle(
            weak,
            events=events,  # type: ignore[arg-type]
            claims=[claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    post = driver.model_copy(
        update={"temporal_relation": DriverTemporalRelation.POST_EVENT}
    )
    with pytest.raises(ValueError, match="eligible temporal"):
        validate_driver_candidate_bundle(
            post,
            events=events,  # type: ignore[arg-type]
            claims=[claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    incomplete_claim = claim.model_copy(
        update={"evidence_ids": driver.target_evidence_ids}
    )
    with pytest.raises(ValueError, match="Claim evidence"):
        validate_driver_candidate_bundle(
            driver,
            events=events,  # type: ignore[arg-type]
            claims=[incomplete_claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )

    post_source = bundle["source"].model_copy(  # type: ignore[union-attr]
        update={"published_at": TARGET_AT + timedelta(hours=2)}
    )
    with pytest.raises(ValueError, match="document timing"):
        validate_driver_candidate_bundle(
            driver,
            events=events,  # type: ignore[arg-type]
            claims=[claim],
            evidence=evidence,  # type: ignore[arg-type]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[post_source],
            assets=[US_ASSET],
        )

    rogue_evidence = bundle["source_evidence"].model_copy(  # type: ignore[union-attr]
        update={"id": "rogue-evidence"}
    )
    rogue_driver_id = driver_candidate_id(
        driver.target_event_id,
        driver.candidate_event_id,
        [rogue_evidence.id],
        driver.rule_version,
    )
    rogue_claim_id = stable_id("claim", f"driver:{rogue_driver_id}")
    rogue_driver = driver.model_copy(
        update={
            "id": rogue_driver_id,
            "interpretation_claim_id": rogue_claim_id,
            "candidate_evidence_ids": [rogue_evidence.id],
            "evidence_ids": sorted([*driver.target_evidence_ids, rogue_evidence.id]),
        }
    )
    rogue_claim = claim.model_copy(
        update={
            "id": rogue_claim_id,
            "evidence_ids": rogue_driver.evidence_ids,
        }
    )
    with pytest.raises(ValueError, match="candidate evidence"):
        validate_driver_candidate_bundle(
            rogue_driver,
            events=events,  # type: ignore[arg-type]
            claims=[rogue_claim],
            evidence=[bundle["target_evidence"], rogue_evidence],  # type: ignore[list-item]
            markets=[bundle["observation"]],  # type: ignore[list-item]
            documents=[bundle["source"]],  # type: ignore[list-item]
            assets=[US_ASSET],
        )


def test_driver_domain_rejects_empty_generator_rule_or_evidence() -> None:
    driver, _, _ = build_one_driver()
    for update in (
        {"generator": ""},
        {"rule_version": "   "},
        {"candidate_evidence_ids": []},
        {"target_evidence_ids": []},
    ):
        with pytest.raises(ValidationError):
            DriverCandidate.model_validate({**driver.model_dump(), **update})


def test_cross_asset_context_requires_complete_comparable_patterns() -> None:
    observations = [
        market_observation("^GSPC", name="S&P 500", change_pct=-2.0),
        market_observation("^NDX", name="Nasdaq 100", change_pct=-2.5),
        market_observation("^DJI", name="Dow Jones", change_pct=-1.5),
        market_observation("^RUT", name="Russell 2000", change_pct=-2.2),
        market_observation("^VIX", name="VIX", change_pct=8.0),
        market_observation("EURUSD=X", name="EUR/USD", change_pct=-1.0),
        market_observation("JPY=X", name="USD/JPY", change_pct=1.0),
    ]
    _, _, target = target_bundle()

    claims, evidence, by_target = build_cross_asset_context(
        observations, [target], [US_ASSET], AS_OF
    )

    assert len(claims) == 3
    assert all(item.claim_type is ClaimType.DERIVED for item in claims)
    assert len({item.id for item in evidence}) == len(observations)
    assert len(by_target[target.id]) == 3
    text = " ".join(item.text for item in claims).casefold()
    assert "caused" not in text
    assert "simultaneous" not in text
    assert "dxy" not in text

    incomplete = [item for item in observations if item.symbol not in {"^RUT", "JPY=X"}]
    incomplete_claims, _, _ = build_cross_asset_context(
        incomplete, [target], [US_ASSET], AS_OF
    )
    assert all("fx proxy" not in item.text.casefold() for item in incomplete_claims)


def test_cross_asset_context_rejects_stale_or_mismatched_sessions() -> None:
    target_market = market_observation()
    stale_vix = market_observation(
        "^VIX",
        name="VIX",
        change_pct=8.0,
        observed_at=TARGET_AT - timedelta(days=1),
    )
    _, _, target = target_bundle()

    claims, _, _ = build_cross_asset_context(
        [target_market, stale_vix], [target], [US_ASSET], AS_OF
    )

    assert all("VIX" not in item.text for item in claims)

    contradictory_fx = [
        market_observation("EURUSD=X", name="EUR/USD", change_pct=-1.0),
        market_observation("JPY=X", name="USD/JPY", change_pct=-1.0),
    ]
    claims, _, _ = build_cross_asset_context(
        contradictory_fx, [target], [US_ASSET], AS_OF
    )
    assert all("FX proxy" not in item.text for item in claims)


def test_cross_asset_context_compares_exchange_dates_in_iana_timezone() -> None:
    target_market = market_observation(
        change_pct=-2,
        observed_at=datetime(2026, 9, 10, 0, 30, tzinfo=UTC),
    )
    vix = market_observation(
        "^VIX",
        name="VIX",
        change_pct=8,
        observed_at=datetime(2026, 9, 9, 20, tzinfo=UTC),
    )
    target_evidence = build_observation_evidence([target_market], [])[0]
    target = event(
        "timezone-target",
        EventType.MARKET_MOVE,
        target_market.observed_at,
        title="S&P 500 fell 2.00%",
        entity_ids=["^GSPC"],
        evidence_ids=[target_evidence.id],
        source_document_ids=[],
    )
    vix_asset = {**US_ASSET, "symbol": "^VIX", "name": "VIX"}

    claims, _, _ = build_cross_asset_context(
        [target_market, vix], [target], [US_ASSET, vix_asset], AS_OF
    )

    assert any("rising VIX" in item.text for item in claims)


def test_cross_asset_breadth_requires_distinct_symbols() -> None:
    duplicates = [
        market_observation(
            "^GSPC",
            change_pct=-2,
            observed_at=TARGET_AT + timedelta(minutes=offset),
        )
        for offset in range(3)
    ]
    _, _, target = target_bundle()

    claims, _, _ = build_cross_asset_context(
        duplicates, [target], [US_ASSET], AS_OF
    )

    assert all("breadth" not in item.text.casefold() for item in claims)


def test_cross_asset_context_is_attached_only_to_relevant_target_assets() -> None:
    observations = [
        market_observation("^GSPC", name="S&P 500", change_pct=-2),
        market_observation("^NDX", name="Nasdaq 100", change_pct=-2),
        market_observation("^DJI", name="Dow Jones", change_pct=-2),
    ]
    _, _, us_target = target_bundle()
    hk_target = event(
        "hk-target",
        EventType.MARKET_MOVE,
        TARGET_AT + timedelta(days=1),
        title="Hang Seng fell 2.00%",
        entity_ids=["^HSI"],
        evidence_ids=["hk-market-evidence"],
        source_document_ids=[],
    )
    hsi_asset = {
        **US_ASSET,
        "symbol": "^HSI",
        "name": "Hang Seng",
        "region": "HK",
        "timezone": "Asia/Hong_Kong",
    }

    claims, _, by_target = build_cross_asset_context(
        observations, [us_target, hk_target], [US_ASSET, hsi_asset], AS_OF
    )

    assert len(claims) == 1
    assert by_target[us_target.id] == [claims[0].id]
    assert by_target[hk_target.id] == []


@pytest.mark.parametrize(
    ("macro", "unit_phrase"),
    [
        (macro_observation(), "index points"),
        (
            macro_observation(
                "LNS14000000",
                name="Unemployment rate",
                value=4.3,
                previous_value=4.2,
                unit="%",
            ),
            "percentage points",
        ),
    ],
)
def test_macro_backdrop_uses_radar_observation_units_without_causal_role(
    macro: MacroObservation, unit_phrase: str
) -> None:
    _, _, target = target_bundle()

    claims, evidence, by_target = build_macro_backdrop([macro], [target], AS_OF)

    assert len(claims) == len(evidence) == 1
    assert claims[0].claim_type is ClaimType.DERIVED
    assert claims[0].id in by_target[target.id]
    assert unit_phrase in claims[0].text
    lowered = claims[0].text.casefold()
    for forbidden in ("released today", "consensus", "surprise", "caused", "driver"):
        assert forbidden not in lowered


def test_macro_backdrop_handles_missing_previous_and_repeated_period_deterministically() -> None:
    current = macro_observation(previous_value=None)
    duplicate = current.model_copy(update={"observed_at": AS_OF + timedelta(hours=1)})
    _, _, target = target_bundle()

    claims, evidence, _ = build_macro_backdrop([duplicate, current], [target], AS_OF)

    assert len(claims) == len(evidence) == 1
    assert "change" not in claims[0].text.casefold()


def test_macro_backdrop_is_limited_to_supported_bls_cpi_and_unemployment() -> None:
    _, _, target = target_bundle()
    cpi = macro_observation()
    unemployment = macro_observation(
        "LNS14000000",
        name="Unemployment rate",
        value=4.3,
        previous_value=4.2,
        unit="%",
    )
    cpi_yoy = macro_observation(
        "CPI-YOY", name="CPI YoY", value=2.6, previous_value=2.7, unit="%"
    )
    treasury = macro_observation(
        "US10Y", name="U.S. 10Y Treasury Yield", value=4.08, unit="%"
    )

    claims, evidence, _ = build_macro_backdrop(
        [cpi, unemployment, cpi_yoy, treasury], [target], AS_OF
    )

    assert len(claims) == len(evidence) == 2
    text = " ".join(item.text for item in claims)
    assert "CUUR0000SA0" in text
    assert "LNS14000000" in text
    assert "CPI-YOY" not in text
    assert "US10Y" not in text


def test_no_relevant_official_evidence_prefers_no_driver() -> None:
    _, target_evidence, target = target_bundle()
    source, source_evidence, candidate = candidate_bundle(
        title="Board appoints a new director",
        content="The board appointed a new director after its annual meeting.",
    )

    drivers, claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[candidate],
        evidence=[target_evidence, source_evidence],
        documents=[source],
        assets=[US_ASSET],
        as_of=AS_OF,
    )

    assert drivers == []
    assert claims == []
