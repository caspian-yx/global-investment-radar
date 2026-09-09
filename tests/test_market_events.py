from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from radar.deterministic import extract_market_events
from radar.domain import (
    ClaimType,
    EventType,
    MarketObservation,
    SourceKind,
    stable_id,
)

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


def observation(
    symbol: str,
    change_pct: float,
    *,
    observed_at: datetime = NOW,
    name: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        id=stable_id("market", f"test|{symbol}|{observed_at.isoformat()}"),
        symbol=symbol,
        name=name or symbol,
        region="test",
        observed_at=observed_at,
        close=100 * (1 + change_pct / 100),
        previous_close=100,
        change_pct=change_pct,
        currency="USD",
        provider="test-market",
    )


@pytest.mark.parametrize(
    ("asset_class", "notable", "high"),
    [
        ("EQUITY_INDEX", 2.0, 4.0),
        ("EQUITY_ETF_PROXY", 2.0, 4.0),
        ("FX", 1.0, 2.0),
        ("COMMODITY", 2.0, 4.0),
        ("CRYPTO", 5.0, 10.0),
    ],
)
def test_market_thresholds_are_inclusive_for_every_asset_class(
    asset_class: str, notable: float, high: float
) -> None:
    assets = [{"symbol": "TEST", "name": "Test Asset", "asset_class": asset_class}]

    below = extract_market_events([observation("TEST", notable - 0.01)], assets, NOW)
    exact = extract_market_events([observation("TEST", notable)], assets, NOW)
    negative = extract_market_events([observation("TEST", -notable)], assets, NOW)
    high_event = extract_market_events([observation("TEST", high)], assets, NOW)

    assert below == ([], [], [])
    assert len(exact[0]) == 1
    assert len(negative[0]) == 1
    assert high_event[0][0].importance_score > exact[0][0].importance_score


def test_market_event_uses_actual_observation_and_only_derived_claims() -> None:
    item = observation("BTC-USD", -6.25, name="Bitcoin")
    assets = [
        {
            "symbol": "BTC-USD",
            "name": "Bitcoin",
            "region": "Crypto",
            "asset_class": "CRYPTO",
        }
    ]

    events, claims, evidence = extract_market_events([item], assets, NOW)

    assert len(events) == len(claims) == len(evidence) == 1
    assert events[0].event_type is EventType.MARKET_MOVE
    assert events[0].entity_ids == ["BTC-USD"]
    assert events[0].source_document_ids == []
    assert claims[0].claim_type is ClaimType.DERIVED
    assert all(claim.claim_type is not ClaimType.INTERPRETATION for claim in claims)
    assert evidence[0].source_kind is SourceKind.MARKET_OBSERVATION
    assert evidence[0].source_id == item.id
    assert evidence[0].source_document_id is None
    assert evidence[0].locator["symbol"] == "BTC-USD"
    assert evidence[0].locator["previous_close"] == 100
    assert evidence[0].locator["latest_close"] == item.close
    assert evidence[0].locator["change_pct"] == -6.25
    assert evidence[0].locator["provider"] == "test-market"


def test_market_event_identity_is_stable_per_bar_and_changes_for_another_bar() -> None:
    assets = [{"symbol": "TEST", "name": "Test", "asset_class": "EQUITY_INDEX"}]
    first = observation("TEST", 2.5)
    same_bar = first.model_copy(update={"id": "a-provider-specific-id"})
    another_bar = observation("TEST", 2.5, observed_at=NOW + timedelta(days=1))

    first_event = extract_market_events([first], assets, NOW)[0][0]
    same_event = extract_market_events([same_bar], assets, NOW)[0][0]
    another_event = extract_market_events([another_bar], assets, NOW)[0][0]

    assert first_event.id == same_event.id
    assert first_event.event_key == same_event.event_key
    assert another_event.id != first_event.id
    assert another_event.event_key != first_event.event_key


@pytest.mark.parametrize(
    ("change_pct", "expected_count", "expected_score"),
    [
        (4.99, 0, None),
        (5.0, 1, 55.0),
        (9.99, 1, 55.0),
        (10.0, 1, 75.0),
        (-10.0, 0, None),
    ],
)
def test_vix_special_rule(
    change_pct: float, expected_count: int, expected_score: float | None
) -> None:
    assets = [{"symbol": "^VIX", "name": "VIX", "asset_class": "EQUITY_INDEX"}]

    events, claims, _ = extract_market_events(
        [observation("^VIX", change_pct, name="VIX")], assets, NOW
    )

    assert len(events) == expected_count
    if events:
        assert events[0].event_type is EventType.RISK
        assert events[0].importance_score == expected_score
        assert claims[0].claim_type is ClaimType.DERIVED
        assert sum(event.event_type is EventType.MARKET_MOVE for event in events) == 0


def test_controlled_volatile_fixture_generates_expected_event_mix() -> None:
    assets = [
        {"symbol": "^GSPC", "name": "S&P 500", "asset_class": "EQUITY_INDEX"},
        {"symbol": "EURUSD=X", "name": "EUR/USD", "asset_class": "FX"},
        {"symbol": "BTC-USD", "name": "Bitcoin", "asset_class": "CRYPTO"},
        {"symbol": "^VIX", "name": "VIX", "asset_class": "EQUITY_INDEX"},
    ]
    observations = [
        observation("^GSPC", 2.0, name="S&P 500"),
        observation("EURUSD=X", -1.0, name="EUR/USD"),
        observation("BTC-USD", 10.0, name="Bitcoin"),
        observation("^VIX", 10.0, name="VIX"),
    ]

    events, claims, evidence = extract_market_events(observations, assets, NOW)

    assert len(events) == len(claims) == len(evidence) == 4
    assert sum(event.event_type is EventType.MARKET_MOVE for event in events) == 3
    assert sum(event.event_type is EventType.RISK for event in events) == 1


@pytest.mark.parametrize(
    ("asset_class", "change_pct", "expected_count", "expected_score"),
    [
        ("EQUITY_INDEX", 1.9999, 0, None),
        ("EQUITY_INDEX", 2.0000, 1, 55.0),
        ("EQUITY_INDEX", 3.9999, 1, 55.0),
        ("EQUITY_INDEX", 4.0000, 1, 75.0),
        ("FX", 0.9999, 0, None),
        ("FX", 1.0000, 1, 55.0),
        ("FX", 1.9999, 1, 55.0),
        ("FX", 2.0000, 1, 75.0),
        ("COMMODITY", 1.9999, 0, None),
        ("COMMODITY", 2.0000, 1, 55.0),
        ("COMMODITY", 3.9999, 1, 55.0),
        ("COMMODITY", 4.0000, 1, 75.0),
        ("CRYPTO", 4.9999, 0, None),
        ("CRYPTO", 5.0000, 1, 55.0),
        ("CRYPTO", 9.9999, 1, 55.0),
        ("CRYPTO", 10.0000, 1, 75.0),
    ],
)
def test_market_thresholds_compare_raw_boundary_values(
    asset_class: str,
    change_pct: float,
    expected_count: int,
    expected_score: float | None,
) -> None:
    assets = [{"symbol": "TEST", "name": "Test", "asset_class": asset_class}]

    events = extract_market_events([observation("TEST", change_pct)], assets, NOW)[0]

    assert len(events) == expected_count
    if events:
        assert events[0].importance_score == expected_score


@pytest.mark.parametrize("asset", [{"symbol": "TEST"}, {"symbol": "TEST", "asset_class": "X"}])
def test_missing_or_unknown_asset_class_fails_explicitly(asset: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="asset_class.*TEST"):
        extract_market_events([observation("TEST", 20.0)], [asset], NOW)


def test_observation_without_universe_metadata_is_skipped_with_diagnostic(caplog) -> None:
    result = extract_market_events([observation("UNCONFIGURED", 20.0)], [], NOW)

    assert result == ([], [], [])
    assert "without universe asset_class" in caplog.text


def test_vix_identity_is_stable_across_reruns_for_same_bar() -> None:
    assets = [{"symbol": "^VIX", "name": "VIX", "asset_class": "EQUITY_INDEX"}]
    item = observation("^VIX", 10.0, name="VIX")

    first = extract_market_events([item], assets, NOW)[0][0]
    rerun = extract_market_events([item], assets, NOW + timedelta(hours=6))[0][0]

    assert first.id == rerun.id
    assert first.event_key == rerun.event_key
