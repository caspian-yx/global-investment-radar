from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radar.domain import (
    Event,
    EventType,
    Evidence,
    SourceDocument,
    SourceKind,
    SourceTier,
    content_hash,
    stable_id,
)
from radar.explanation import build_driver_candidates, build_explanation_bundles
from radar.relevance import (
    RELATIONSHIP_RULE_VERSION,
    EventTargetDecision,
    RelationshipReason,
    TransmissionChannel,
    build_event_target_relationships,
)

TARGET_AT = datetime(2026, 9, 10, 20, tzinfo=UTC)
AS_OF = TARGET_AT + timedelta(hours=8)

ASSETS = [
    {
        "symbol": "3033.HK",
        "name": "Hang Seng TECH ETF proxy",
        "region": "HK",
        "asset_class": "EQUITY_ETF_PROXY",
        "session_kind": "EXCHANGE",
        "timezone": "Asia/Hong_Kong",
        "close_time": "16:00",
        "topics": ["CHINA", "TECHNOLOGY", "REGULATION"],
    },
    {
        "symbol": "^GSPC",
        "name": "S&P 500",
        "region": "US",
        "asset_class": "EQUITY_INDEX",
        "session_kind": "EXCHANGE",
        "timezone": "America/New_York",
        "close_time": "16:00",
        "topics": ["RATES", "INFLATION", "EMPLOYMENT", "TECHNOLOGY", "US"],
    },
    {
        "symbol": "^STOXX50E",
        "name": "EURO STOXX 50",
        "region": "EU",
        "asset_class": "EQUITY_INDEX",
        "session_kind": "EXCHANGE",
        "timezone": "Europe/Berlin",
        "close_time": "17:30",
        "topics": ["RATES", "INFLATION", "ENERGY", "TECHNOLOGY"],
    },
    {
        "symbol": "^VIX",
        "name": "VIX",
        "region": "US",
        "asset_class": "EQUITY_INDEX",
        "session_kind": "EXCHANGE",
        "timezone": "America/New_York",
        "close_time": "16:00",
        "topics": ["RATES", "REGULATION", "US"],
    },
    {
        "symbol": "AMZN",
        "name": "Amazon.com Inc.",
        "entity_aliases": ["Amazon"],
        "region": "US",
        "asset_class": "EQUITY_ETF_PROXY",
        "session_kind": "EXCHANGE",
        "timezone": "America/New_York",
        "close_time": "16:00",
        "topics": ["TECHNOLOGY", "US"],
    },
    {
        "symbol": "MSFT",
        "name": "Microsoft Corp.",
        "entity_aliases": ["Microsoft"],
        "region": "US",
        "asset_class": "EQUITY_ETF_PROXY",
        "session_kind": "EXCHANGE",
        "timezone": "America/New_York",
        "close_time": "16:00",
        "topics": ["TECHNOLOGY", "US"],
    },
]


def _document(
    document_id: str,
    *,
    title: str,
    content: str,
    publisher: str,
    region: str,
    published_at: datetime | None = None,
    source_tier: SourceTier = SourceTier.A,
) -> SourceDocument:
    return SourceDocument(
        id=document_id,
        provider="official-test",
        source_tier=source_tier,
        publisher=publisher,
        canonical_url=f"https://example.test/{document_id}",
        title=title,
        content=content,
        region=region,
        published_at=published_at or TARGET_AT - timedelta(hours=24),
        collected_at=AS_OF,
        content_hash=content_hash(content),
    )


def _document_evidence(document: SourceDocument) -> Evidence:
    excerpt = document.content or document.title
    return Evidence(
        id=stable_id("evidence", f"{document.id}|{content_hash(excerpt)}"),
        source_kind=SourceKind.DOCUMENT,
        source_id=document.id,
        source_document_id=document.id,
        locator={"type": "test", "title": document.title},
        excerpt=excerpt,
        checksum=content_hash(excerpt),
        quality_score=1,
    )


def _candidate(
    candidate_id: str,
    document: SourceDocument,
    *,
    event_type: EventType,
    entity_ids: list[str] | None = None,
    importance_score: float = 60,
) -> tuple[Event, Evidence]:
    evidence = _document_evidence(document)
    return (
        Event(
            id=candidate_id,
            event_key=candidate_id,
            event_type=event_type,
            title=document.title,
            occurred_at=document.published_at,
            first_seen_at=document.collected_at,
            last_seen_at=document.collected_at,
            entity_ids=entity_ids or [],
            claim_ids=[],
            evidence_ids=[evidence.id],
            source_document_ids=[document.id],
            importance_score=importance_score,
            importance_components={"materiality": importance_score},
            confidence=0.9,
        ),
        evidence,
    )


def _target(
    symbol: str,
    *,
    event_type: EventType = EventType.MARKET_MOVE,
) -> tuple[Event, Evidence]:
    evidence = Evidence(
        id=f"evidence-{symbol}",
        source_kind=SourceKind.MARKET_OBSERVATION,
        source_id=f"market-{symbol}",
        locator={"type": "market_bar", "symbol": symbol},
        excerpt=f"{symbol} moved materially.",
        checksum=content_hash(symbol),
        quality_score=0.75,
    )
    return (
        Event(
            id=f"target-{symbol}",
            event_key=f"target-{symbol}",
            event_type=event_type,
            title=f"{symbol} moved materially",
            occurred_at=TARGET_AT,
            first_seen_at=AS_OF,
            last_seen_at=AS_OF,
            entity_ids=[symbol],
            claim_ids=[],
            evidence_ids=[evidence.id],
            source_document_ids=[],
            importance_score=65,
            importance_components={"materiality": 65},
            confidence=0.99,
        ),
        evidence,
    )


def _relationship(
    target: Event,
    candidate: Event,
    documents: list[SourceDocument],
):
    relationships = build_event_target_relationships(
        target_events=[target],
        candidate_events=[candidate],
        documents=documents,
        assets=ASSETS,
    )
    assert len(relationships) == 1
    return relationships[0]


def test_sec_local_ponzi_is_rejected_for_hang_seng_tech_regardless_of_importance_or_time() -> None:
    target, _ = _target("3033.HK")
    source = _document(
        "sec-ponzi",
        title=(
            "SEC Charges Founder and His Two New Jersey-Based Companies "
            "in Alleged $16 Million Ponzi Scheme"
        ),
        content="The Securities and Exchange Commission announced an enforcement action.",
        publisher="U.S. SEC",
        region="US",
        published_at=TARGET_AT - timedelta(hours=24),
    )
    candidate, _ = _candidate(
        "event-sec-ponzi",
        source,
        event_type=EventType.LEGAL,
        entity_ids=["us-sec"],
        importance_score=100,
    )

    relationship = _relationship(target, candidate, [source])

    assert relationship.decision is EventTargetDecision.REJECT
    assert relationship.transmission_channel is None
    assert relationship.reason_codes == (
        RelationshipReason.LEGAL_TOPIC_ONLY,
        RelationshipReason.UNSUPPORTED_CROSS_REGION,
    )
    assert relationship.rule_version == RELATIONSHIP_RULE_VERSION


def test_compatible_ecb_fed_and_fed_vix_policy_relationships_are_allowed() -> None:
    cases = [
        (
            "^STOXX50E",
            EventType.MARKET_MOVE,
            "ecb",
            "European Central Bank",
            "EU",
            "european-central-bank",
            RelationshipReason.CENTRAL_BANK_REGION_MATCH,
        ),
        (
            "^GSPC",
            EventType.MARKET_MOVE,
            "fed-equity",
            "Federal Reserve",
            "US",
            "federal-reserve",
            RelationshipReason.CENTRAL_BANK_REGION_MATCH,
        ),
        (
            "^VIX",
            EventType.RISK,
            "fed-vix",
            "Federal Reserve",
            "US",
            "federal-reserve",
            RelationshipReason.CENTRAL_BANK_BROAD_RISK,
        ),
    ]
    for symbol, target_type, suffix, publisher, region, entity_id, reason in cases:
        target, _ = _target(symbol, event_type=target_type)
        source = _document(
            f"source-{suffix}",
            title=f"{publisher} announces monetary policy decision",
            content="The central bank announced an interest rate decision.",
            publisher=publisher,
            region=region,
        )
        candidate, _ = _candidate(
            f"event-{suffix}",
            source,
            event_type=EventType.POLICY,
            entity_ids=[entity_id],
        )

        relationship = _relationship(target, candidate, [source])

        assert relationship.decision is EventTargetDecision.ALLOW
        assert relationship.transmission_channel is TransmissionChannel.CENTRAL_BANK_POLICY
        assert relationship.reason_codes == (reason,)


def test_direct_company_event_is_allowed_but_unrelated_company_is_rejected() -> None:
    source = _document(
        "amazon-filing",
        title="Amazon (AMZN) files a material 8-K",
        content="Amazon disclosed a material change to its guidance.",
        publisher="Amazon Investor Relations",
        region="US",
    )
    candidate, _ = _candidate(
        "event-amazon",
        source,
        event_type=EventType.GUIDANCE,
        entity_ids=["AMZN"],
    )
    amazon, _ = _target("AMZN")
    microsoft, _ = _target("MSFT")

    direct = _relationship(amazon, candidate, [source])
    unrelated = _relationship(microsoft, candidate, [source])

    assert direct.decision is EventTargetDecision.ALLOW
    assert direct.transmission_channel is TransmissionChannel.DIRECT_ENTITY
    assert direct.reason_codes == (RelationshipReason.DIRECT_ENTITY_MATCH,)
    assert unrelated.decision is EventTargetDecision.REJECT
    assert unrelated.reason_codes == (RelationshipReason.NO_SUPPORTED_TRANSMISSION,)


def test_direct_entity_legal_is_allowed_but_provider_metadata_cannot_establish_identity() -> None:
    target, _ = _target("AMZN")
    direct_source = _document(
        "amazon-legal",
        title="Amazon (AMZN) resolves a legal proceeding",
        content="Amazon disclosed the resolution in an official filing.",
        publisher="Amazon Investor Relations",
        region="US",
    )
    direct, _ = _candidate(
        "event-amazon-legal",
        direct_source,
        event_type=EventType.LEGAL,
        entity_ids=["AMZN"],
    )
    opaque_source = _document(
        "opaque-filing",
        title="Registrant files Form 8-K",
        content="The registrant disclosed a material legal proceeding.",
        publisher="U.S. SEC",
        region="US",
    ).model_copy(update={"provider_metadata": {"issuer_name": "Amazon.com Inc."}})
    opaque, _ = _candidate(
        "event-opaque-filing",
        opaque_source,
        event_type=EventType.LEGAL,
    )

    direct_relationship = _relationship(target, direct, [direct_source])
    opaque_relationship = _relationship(target, opaque, [opaque_source])

    assert direct_relationship.decision is EventTargetDecision.ALLOW
    assert direct_relationship.transmission_channel is TransmissionChannel.DIRECT_ENTITY
    assert opaque_relationship.decision is EventTargetDecision.REJECT
    assert opaque_relationship.reason_codes == (RelationshipReason.LEGAL_TOPIC_ONLY,)


def test_central_bank_policy_does_not_implicitly_authorize_fx_or_commodity_targets() -> None:
    assets = [
        {
            "symbol": "EURUSD=X",
            "name": "EUR/USD",
            "region": "FX",
            "asset_class": "FX",
            "topics": ["RATES", "INFLATION", "US"],
        },
        {
            "symbol": "GC=F",
            "name": "Gold Futures",
            "region": "Commodity",
            "asset_class": "COMMODITY",
            "topics": ["RATES", "INFLATION", "US"],
        },
    ]
    source = _document(
        "fed-fx",
        title="Federal Reserve announces monetary policy decision",
        content="The Federal Reserve announced an interest rate decision.",
        publisher="Federal Reserve",
        region="US",
    )
    candidate, _ = _candidate(
        "event-fed-fx",
        source,
        event_type=EventType.POLICY,
        entity_ids=["federal-reserve"],
    )
    targets = [_target("EURUSD=X")[0], _target("GC=F")[0]]

    relationships = build_event_target_relationships(
        target_events=targets,
        candidate_events=[candidate],
        documents=[source],
        assets=assets,
    )

    assert [item.decision for item in relationships] == [
        EventTargetDecision.REJECT,
        EventTargetDecision.REJECT,
    ]
    assert all(item.transmission_channel is None for item in relationships)


def test_fed_policy_does_not_authorize_every_us_company_by_region_alone() -> None:
    target, _ = _target("AMZN")
    source = _document(
        "fed-company",
        title="Federal Reserve announces monetary policy decision",
        content="The Federal Reserve announced an interest rate decision.",
        publisher="Federal Reserve",
        region="US",
    )
    candidate, _ = _candidate(
        "event-fed-company",
        source,
        event_type=EventType.POLICY,
        entity_ids=["federal-reserve"],
    )

    relationship = _relationship(target, candidate, [source])

    assert relationship.decision is EventTargetDecision.REJECT
    assert relationship.transmission_channel is None
    assert relationship.reason_codes == (RelationshipReason.NO_SUPPORTED_TRANSMISSION,)


def test_same_region_official_macro_release_is_allowed_for_matching_macro_topic() -> None:
    target, _ = _target("^GSPC")
    source = _document(
        "us-cpi",
        title="Consumer price index shows inflation pressures",
        content="The consumer price index rose during the latest month.",
        publisher="U.S. Statistics Office",
        region="US",
    )
    candidate, _ = _candidate(
        "event-us-cpi",
        source,
        event_type=EventType.MACRO,
    )

    relationship = _relationship(target, candidate, [source])

    assert relationship.decision is EventTargetDecision.ALLOW
    assert relationship.transmission_channel is TransmissionChannel.MACROECONOMIC_RELEASE
    assert relationship.reason_codes == (RelationshipReason.MACRO_REGION_TOPIC_MATCH,)


def test_small_local_event_is_rejected_for_unrelated_foreign_market() -> None:
    target, _ = _target("^STOXX50E")
    source = _document(
        "local-event",
        title="Local New Jersey agency changes a municipal permit",
        content="The change applies to one municipal facility.",
        publisher="New Jersey Local Agency",
        region="US",
    )
    candidate, _ = _candidate(
        "event-local",
        source,
        event_type=EventType.OTHER,
    )

    relationship = _relationship(target, candidate, [source])

    assert relationship.decision is EventTargetDecision.REJECT
    assert relationship.reason_codes == (
        RelationshipReason.UNSUPPORTED_CROSS_REGION,
        RelationshipReason.NO_SUPPORTED_TRANSMISSION,
    )


def test_relationships_and_reason_order_are_invariant_to_input_permutation() -> None:
    hk, _ = _target("3033.HK")
    us, _ = _target("^GSPC")
    sec_source = _document(
        "sec",
        title="SEC enforcement targets a local Ponzi scheme",
        content="The Securities and Exchange Commission announced a regulatory action.",
        publisher="U.S. SEC",
        region="US",
    )
    fed_source = _document(
        "fed",
        title="Federal Reserve announces monetary policy decision",
        content="The Federal Reserve announced an interest rate decision.",
        publisher="Federal Reserve",
        region="US",
    )
    sec, _ = _candidate("event-sec", sec_source, event_type=EventType.LEGAL)
    fed, _ = _candidate(
        "event-fed",
        fed_source,
        event_type=EventType.POLICY,
        entity_ids=["federal-reserve"],
    )

    first = build_event_target_relationships(
        target_events=[hk, us],
        candidate_events=[sec, fed],
        documents=[sec_source, fed_source],
        assets=ASSETS,
    )
    second = build_event_target_relationships(
        target_events=[us, hk],
        candidate_events=[fed, sec],
        documents=[fed_source, sec_source],
        assets=list(reversed(ASSETS)),
    )

    assert first == second
    assert [(item.target_event_id, item.candidate_event_id) for item in first] == sorted(
        (item.target_event_id, item.candidate_event_id) for item in first
    )


def test_authorization_preserves_c2_output_and_isolates_rejected_conflict() -> None:
    target, target_evidence = _target("^GSPC")
    fed_primary = _document(
        "fed-primary",
        title="Federal Reserve announces a rate hike",
        content="The Federal Reserve raises interest rates after its policy meeting.",
        publisher="Federal Reserve",
        region="US",
        source_tier=SourceTier.A,
    )
    fed_secondary = _document(
        "fed-secondary",
        title="Federal Reserve announces a rate hike",
        content="An independent report confirms the federal funds rate increase.",
        publisher="Independent Wire",
        region="US",
        source_tier=SourceTier.B,
    )
    primary_evidence = _document_evidence(fed_primary)
    secondary_evidence = _document_evidence(fed_secondary)
    allowed = Event(
        id="event-fed-hike",
        event_key="event-fed-hike",
        event_type=EventType.POLICY,
        title=fed_primary.title,
        occurred_at=fed_primary.published_at,
        first_seen_at=AS_OF,
        last_seen_at=AS_OF,
        entity_ids=["federal-reserve"],
        claim_ids=[],
        evidence_ids=[primary_evidence.id, secondary_evidence.id],
        source_document_ids=[fed_primary.id, fed_secondary.id],
        importance_score=60,
        importance_components={"materiality": 60},
        confidence=0.9,
    )
    rejected_source = _document(
        "legal-rate-cut",
        title="Federal Reserve legal order references a rate cut",
        content="A legal enforcement order references lower interest rates and a rate cut.",
        publisher="Local Court",
        region="US",
    )
    rejected, rejected_evidence = _candidate(
        "event-legal-rate-cut",
        rejected_source,
        event_type=EventType.LEGAL,
        entity_ids=["federal-reserve"],
    )
    documents = [fed_primary, fed_secondary, rejected_source]
    evidence = [
        target_evidence,
        primary_evidence,
        secondary_evidence,
        rejected_evidence,
    ]

    relationships = build_event_target_relationships(
        target_events=[target],
        candidate_events=[allowed, rejected],
        documents=documents,
        assets=ASSETS,
    )
    authorized_ids = {
        item.candidate_event_id
        for item in relationships
        if item.decision is EventTargetDecision.ALLOW
    }
    authorized_events = [
        item for item in [allowed, rejected] if item.id in authorized_ids
    ]
    expected_drivers, expected_claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[allowed],
        evidence=evidence,
        documents=documents,
        assets=ASSETS,
        as_of=AS_OF,
    )
    actual_drivers, actual_claims = build_driver_candidates(
        target_events=[target],
        candidate_events=authorized_events,
        evidence=evidence,
        documents=documents,
        assets=ASSETS,
        as_of=AS_OF,
    )
    raw_drivers, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[allowed, rejected],
        evidence=evidence,
        documents=documents,
        assets=ASSETS,
        as_of=AS_OF,
    )
    bundles = build_explanation_bundles(
        target_events=[target],
        driver_candidates=actual_drivers,
        claims=actual_claims,
        evidence=evidence,
    )

    assert actual_drivers == expected_drivers
    assert actual_claims == expected_claims
    assert len(actual_drivers) == 1
    assert actual_drivers[0].confidence_components["conflict"] == "NO"
    raw_survivor = next(
        item for item in raw_drivers if item.id == actual_drivers[0].id
    )
    assert raw_survivor.confidence_components["conflict"] == "YES"
    assert [item.id for item in bundles[0].driver_candidates] == [actual_drivers[0].id]
    assert [item.id for item in bundles[0].claims] == [actual_claims[0].id]
    assert [item.id for item in bundles[0].evidence] == actual_drivers[0].evidence_ids


def test_authorization_preserves_c2_ranking_for_multiple_surviving_candidates() -> None:
    target, target_evidence = _target("^GSPC")
    exact_source = _document(
        "fed-exact",
        title="Federal Reserve policy statement names the S&P 500",
        content="The Federal Reserve announced an interest rate decision.",
        publisher="Federal Reserve",
        region="US",
        published_at=TARGET_AT - timedelta(hours=30),
    )
    topic_source = _document(
        "fed-topic",
        title="Federal Reserve announces interest rate decision",
        content="The Federal Reserve announced a federal funds rate decision.",
        publisher="Federal Reserve",
        region="US",
        published_at=TARGET_AT - timedelta(hours=24),
    )
    exact, exact_evidence = _candidate(
        "event-fed-exact",
        exact_source,
        event_type=EventType.POLICY,
        entity_ids=["federal-reserve"],
    )
    topic, topic_evidence = _candidate(
        "event-fed-topic",
        topic_source,
        event_type=EventType.POLICY,
        entity_ids=["federal-reserve"],
    )
    documents = [exact_source, topic_source]
    evidence = [target_evidence, exact_evidence, topic_evidence]

    relationships = build_event_target_relationships(
        target_events=[target],
        candidate_events=[topic, exact],
        documents=list(reversed(documents)),
        assets=ASSETS,
    )
    authorized_ids = {
        item.candidate_event_id
        for item in relationships
        if item.decision is EventTargetDecision.ALLOW
    }
    authorized = [item for item in [topic, exact] if item.id in authorized_ids]
    expected, expected_claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[exact, topic],
        evidence=evidence,
        documents=documents,
        assets=ASSETS,
        as_of=AS_OF,
    )
    actual, actual_claims = build_driver_candidates(
        target_events=[target],
        candidate_events=authorized,
        evidence=evidence,
        documents=documents,
        assets=ASSETS,
        as_of=AS_OF,
    )

    expected_bundle = build_explanation_bundles(
        target_events=[target],
        driver_candidates=expected,
        claims=expected_claims,
        evidence=evidence,
    )[0]
    actual_bundle = build_explanation_bundles(
        target_events=[target],
        driver_candidates=actual,
        claims=actual_claims,
        evidence=evidence,
    )[0]

    assert actual_bundle == expected_bundle
    assert len(actual_bundle.driver_candidates) == 2
    assert [item.candidate_event_id for item in actual_bundle.driver_candidates] == [
        "event-fed-exact",
        "event-fed-topic",
    ]
