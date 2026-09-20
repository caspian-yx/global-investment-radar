from __future__ import annotations

from datetime import UTC, datetime

from radar.china_market import CapabilityState, build_china_market_intelligence

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)
FRESH_TS = int(datetime(2026, 9, 18, 7, tzinfo=UTC).timestamp())
STALE_TS = int(datetime(2026, 9, 10, 7, tzinfo=UTC).timestamp())


def _index_payload(timestamp: int = FRESH_TS):
    return {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f14": "SSE Composite",
                    "f2": 3900,
                    "f3": 0.5,
                    "f6": 500,
                    "f124": timestamp,
                },
                {
                    "f12": "399001",
                    "f14": "Shenzhen Component",
                    "f2": 12500,
                    "f3": 1.0,
                    "f6": 600,
                    "f124": timestamp,
                },
                {
                    "f12": "000300",
                    "f14": "CSI 300",
                    "f2": 4700,
                    "f3": 0.3,
                    "f6": 300,
                    "f124": timestamp,
                },
                {
                    "f12": "000510",
                    "f14": "CSI A500",
                    "f2": 5200,
                    "f3": 0.6,
                    "f6": 200,
                    "f124": timestamp,
                },
                {
                    "f12": "399006",
                    "f14": "ChiNext",
                    "f2": 3100,
                    "f3": 2.0,
                    "f6": 250,
                    "f124": timestamp,
                },
                {
                    "f12": "000688",
                    "f14": "STAR 50",
                    "f2": 1400,
                    "f3": 1.6,
                    "f6": 180,
                    "f124": timestamp,
                },
                {
                    "f12": "000905",
                    "f14": "CSI 500",
                    "f2": 7100,
                    "f3": 0.8,
                    "f6": 220,
                    "f124": timestamp,
                },
                {
                    "f12": "000852",
                    "f14": "CSI 1000",
                    "f2": 7600,
                    "f3": 1.5,
                    "f6": 260,
                    "f124": timestamp,
                },
            ]
        }
    }


def _breadth_payload(timestamp: int = FRESH_TS):
    return {
        "data": {
            "total": 4,
            "diff": [
                {"f12": "600001", "f3": 1.2, "f6": 10, "f124": timestamp},
                {"f12": "600002", "f3": 0.0, "f6": 20, "f124": timestamp},
                {"f12": "000001", "f3": -0.8, "f6": 30, "f124": timestamp},
                {"f12": "000002", "f3": 2.1, "f6": 40, "f124": timestamp},
            ],
        }
    }


def test_full_index_breadth_turnover_and_style_capabilities() -> None:
    result = build_china_market_intelligence(
        _index_payload(),
        _breadth_payload(),
        provider="experimental-eastmoney-china-market",
        as_of=NOW,
    )

    assert result.capabilities.indices is CapabilityState.AVAILABLE
    assert [item.code for item in result.indices] == [
        "000001",
        "399001",
        "000300",
        "000510",
        "399006",
        "000688",
        "000905",
        "000852",
    ]
    assert result.breadth is not None
    assert (result.breadth.advancers, result.breadth.decliners, result.breadth.unchanged) == (
        2,
        1,
        1,
    )
    assert result.activity is not None
    assert result.activity.total_turnover == 100
    assert result.leadership is not None
    assert result.leadership.signals == ("GROWTH_LEADERSHIP", "SMALL_CAP_LEADERSHIP")
    assert result.capabilities.sectors is CapabilityState.UNAVAILABLE
    assert result.sectors == ()


def test_partial_capability_never_infers_missing_breadth_or_turnover() -> None:
    result = build_china_market_intelligence(
        _index_payload(),
        None,
        provider="experimental-eastmoney-china-market",
        as_of=NOW,
    )

    assert result.capabilities.indices is CapabilityState.AVAILABLE
    assert result.capabilities.breadth is CapabilityState.UNAVAILABLE
    assert result.capabilities.turnover is CapabilityState.UNAVAILABLE
    assert result.breadth is None
    assert result.activity is None


def test_stale_provider_data_is_rejected_not_relabelled_fresh() -> None:
    result = build_china_market_intelligence(
        _index_payload(STALE_TS),
        _breadth_payload(STALE_TS),
        provider="experimental-eastmoney-china-market",
        as_of=NOW,
    )

    assert result.indices == ()
    assert result.breadth is None
    assert result.capabilities.indices is CapabilityState.STALE
    assert result.capabilities.breadth is CapabilityState.STALE


def test_malformed_payload_fails_closed_for_every_capability() -> None:
    result = build_china_market_intelligence(
        {"data": None},
        {"unexpected": True},
        provider="experimental-eastmoney-china-market",
        as_of=NOW,
    )

    assert result.indices == ()
    assert result.sectors == ()
    assert result.breadth is None
    assert result.activity is None
    assert result.leadership is None
    assert result.capabilities.indices is CapabilityState.UNAVAILABLE
