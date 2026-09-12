from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.domain import (
    Event,
    EventType,
    MarketObservation,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    content_hash,
)
from radar.impact import build_c8_market_target_events, evaluate_market_impacts
from radar.providers import make_document
from radar.relevance import EventTargetDecision, build_event_target_relationships

PREVIOUS = datetime(2026, 9, 10, 13, 30, tzinfo=UTC)
LATEST = datetime(2026, 9, 11, 13, 30, tzinfo=UTC)
EVENT_AT = datetime(2026, 9, 11, 15, tzinfo=UTC)
EVALUATED = datetime(2026, 9, 11, 22, tzinfo=UTC)


def _asset(
    symbol: str,
    name: str,
    region: str,
    *,
    topics: list[str],
    asset_class: str = "EQUITY_INDEX",
    timezone: str = "America/New_York",
    close_time: str = "16:00",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": name,
        "region": region,
        "asset_class": asset_class,
        "session_kind": "EXCHANGE",
        "timezone": timezone,
        "close_time": close_time,
        "topics": topics,
    }


def _document(
    document_id: str, title: str, publisher: str, region: str
) -> SourceDocument:
    return make_document(
        provider="official-test",
        tier=SourceTier.A,
        publisher=publisher,
        url=f"https://example.test/{document_id}",
        title=title,
        content=title,
        published_at=EVENT_AT,
        collected_at=EVALUATED,
        region=region,
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )


def _event(
    event_id: str, event_type: EventType, document: SourceDocument, entity_id: str
) -> Event:
    return Event(
        id=event_id,
        event_key=f"story:{event_id}",
        event_type=event_type,
        title=document.title,
        occurred_at=document.published_at,
        first_seen_at=document.collected_at,
        last_seen_at=document.collected_at,
        entity_ids=[entity_id],
        claim_ids=[],
        evidence_ids=[],
        source_document_ids=[document.id],
        importance_score=50,
        importance_components={},
        confidence=0.9,
    )


def _evaluate(
    event: Event, document: SourceDocument, asset: dict[str, object]
):
    symbol = str(asset["symbol"])
    market = MarketObservation(
        id=f"market-{symbol}",
        symbol=symbol,
        name=str(asset["name"]),
        region=str(asset["region"]),
        previous_observed_at=PREVIOUS,
        observed_at=LATEST,
        close=102.0,
        previous_close=100.0,
        change_pct=2.0,
        provider="test",
    )
    target = build_c8_market_target_events([market], EVALUATED)[0]
    relationships = build_event_target_relationships(
        target_events=[target],
        candidate_events=[event],
        documents=[document],
        assets=[asset],
    )
    result = evaluate_market_impacts(
        candidate_events=[event],
        market_events=[target],
        markets=[market],
        relationships=relationships,
        stories=[],
        assets=[asset],
        evaluated_at=EVALUATED,
    )
    return relationships[0], result


@pytest.mark.parametrize(
    ("case", "event_type", "entity_id", "title", "publisher", "region", "asset"),
    [
        (
            "fed",
            EventType.POLICY,
            "federal-reserve",
            "Federal Reserve announces policy rate decision",
            "Federal Reserve",
            "US",
            _asset("^GSPC", "S&P 500", "US", topics=["RATES", "INFLATION", "US"]),
        ),
        (
            "ecb",
            EventType.POLICY,
            "european-central-bank",
            "European Central Bank announces interest rate decision",
            "European Central Bank",
            "EU",
            _asset(
                "^STOXX50E",
                "EURO STOXX 50",
                "EU",
                topics=["RATES", "INFLATION"],
                timezone="Europe/Berlin",
                close_time="17:30",
            ),
        ),
        (
            "cpi",
            EventType.MACRO,
            "CUUR0000SA0",
            "US consumer price inflation CPI release",
            "U.S. Bureau of Labor Statistics",
            "US",
            _asset("^GSPC", "S&P 500", "US", topics=["RATES", "INFLATION", "US"]),
        ),
        (
            "nvidia",
            EventType.GUIDANCE,
            "nvidia",
            "NVIDIA raises revenue guidance",
            "NVIDIA",
            "US",
            _asset("NVDA", "NVIDIA", "US", topics=["TECHNOLOGY"]),
        ),
    ],
)
def test_c8_reuses_actual_c6_allow_semantics(
    case: str,
    event_type: EventType,
    entity_id: str,
    title: str,
    publisher: str,
    region: str,
    asset: dict[str, object],
) -> None:
    document = _document(case, title, publisher, region)
    event = _event(f"event-{case}", event_type, document, entity_id)

    relationship, result = _evaluate(event, document, asset)

    assert relationship.decision is EventTargetDecision.ALLOW
    assert len(result.records) == 1


@pytest.mark.parametrize(
    ("case", "event_type", "entity_id", "title", "publisher", "region", "asset"),
    [
        (
            "nvidia-oil",
            EventType.GUIDANCE,
            "nvidia",
            "NVIDIA raises revenue guidance",
            "NVIDIA",
            "US",
            _asset(
                "CL=F",
                "WTI Crude Futures",
                "Commodity",
                topics=["ENERGY"],
                asset_class="COMMODITY",
            ),
        ),
        (
            "sec-hk",
            EventType.LEGAL,
            "us-sec",
            "SEC charges local operator in Ponzi scheme",
            "U.S. SEC",
            "US",
            _asset(
                "3033.HK",
                "Hang Seng TECH ETF proxy",
                "HK",
                topics=["CHINA", "TECHNOLOGY"],
                asset_class="EQUITY_ETF_PROXY",
                timezone="Asia/Hong_Kong",
            ),
        ),
        (
            "apple-hk",
            EventType.PRODUCT,
            "apple",
            "Apple introduces a new phone",
            "Apple",
            "US",
            _asset(
                "^HSI",
                "Hang Seng",
                "HK",
                topics=["CHINA", "TECHNOLOGY"],
                timezone="Asia/Hong_Kong",
            ),
        ),
    ],
)
def test_c8_preserves_actual_c6_rejections(
    case: str,
    event_type: EventType,
    entity_id: str,
    title: str,
    publisher: str,
    region: str,
    asset: dict[str, object],
) -> None:
    document = _document(case, title, publisher, region)
    event = _event(f"event-{case}", event_type, document, entity_id)

    relationship, result = _evaluate(event, document, asset)

    assert relationship.decision is EventTargetDecision.REJECT
    assert result.records == ()


def test_fixture_provenance_does_not_depend_on_uncontrolled_metadata() -> None:
    document = SourceDocument(
        id="metadata-proof",
        provider="test",
        source_tier=SourceTier.A,
        publisher="Federal Reserve",
        canonical_url="https://example.test/proof",
        title="Federal Reserve policy rate decision",
        content="Federal Reserve policy rate decision",
        region="US",
        published_at=EVENT_AT,
        collected_at=EVALUATED,
        provider_metadata={"opaque": "ignored"},
        content_hash=content_hash("Federal Reserve policy rate decision"),
    )
    event = _event("metadata-event", EventType.POLICY, document, "federal-reserve")

    relationship, result = _evaluate(
        event,
        document,
        _asset("^GSPC", "S&P 500", "US", topics=["RATES"]),
    )

    assert relationship.decision is EventTargetDecision.ALLOW
    assert len(result.records) == 1
