from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radar.domain import MarketHistoryPoint, MarketObservation
from radar.market_discovery import discover_market_anomalies

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


def _market(
    symbol: str,
    change_pct: float,
    *,
    close: float = 100.0,
    history_closes: tuple[float, ...] = (),
) -> MarketObservation:
    history = tuple(
        MarketHistoryPoint(observed_at=NOW - timedelta(days=index), close=value)
        for index, value in enumerate(history_closes)
    )
    return MarketObservation(
        id=f"market-{symbol}",
        symbol=symbol,
        name=symbol,
        region="US",
        previous_observed_at=NOW - timedelta(days=1),
        observed_at=NOW,
        close=close,
        previous_close=close / (1 + change_pct / 100),
        change_pct=change_pct,
        provider="test-market",
        history=history,
    )


def test_abnormal_move_creates_unresolved_evidence_need_with_multiday_context() -> None:
    wti = _market(
        "CL=F",
        -4.06,
        close=70,
        history_closes=(70, 72.96, 73.2, 73.0, 73.4, 74.0, 73.8, 73.6),
    )

    result = discover_market_anomalies([wti], [], as_of=NOW)

    assert len(result.anomalies) == 1
    anomaly = result.anomalies[0]
    assert anomaly.symbols == ("CL=F",)
    assert anomaly.change_1d == -4.06
    assert anomaly.change_5d == -5.41
    assert "absolute_1d" in anomaly.rule_ids
    assert result.evidence_needs[0].cluster_key == anomaly.cluster_key
    assert result.evidence_needs[0].resolved is False


def test_normal_move_does_not_create_anomaly() -> None:
    market = _market("^GSPC", 0.4, history_closes=(100, 99.6, 99.4, 99.2, 99.0, 98.8))

    result = discover_market_anomalies([market], [], as_of=NOW)

    assert result.anomalies == ()
    assert result.evidence_needs == ()


def test_correlated_vix_vxn_moves_form_one_volatility_cluster() -> None:
    vix = _market("^VIX", 11.0)
    vxn = _market("^VXN", 9.0)

    result = discover_market_anomalies([vix, vxn], [], as_of=NOW)

    assert len(result.anomalies) == 1
    assert result.anomalies[0].cluster_key == "market:us-volatility"
    assert result.anomalies[0].symbols == ("^VIX", "^VXN")
    assert len(result.evidence_needs) == 1


def test_cross_index_and_rate_equity_divergences_are_deterministic() -> None:
    markets = [
        _market("^DJI", -1.21),
        _market("^NDX", 0.02),
        _market("^TNX", 2.1),
    ]

    result = discover_market_anomalies(markets, [], as_of=NOW)

    codes = {item.code for item in result.divergences}
    assert "US_TECH_RELATIVE_STRENGTH" in codes
    assert "RATES_EQUITY_PRESSURE" not in codes


def test_standardized_move_requires_sufficient_history() -> None:
    short = _market("BTC-USD", 2.0, history_closes=(100, 98))
    long = _market(
        "GC=F",
        2.0,
        history_closes=(102, 100, 100.1, 100.0, 100.2, 100.1, 100.0, 100.2, 100.1, 100.0),
    )

    result = discover_market_anomalies([short, long], [], as_of=NOW)

    by_symbol = {item.symbols[0]: item for item in result.anomalies}
    assert "BTC-USD" not in by_symbol
    assert "standardized_1d" in by_symbol["GC=F"].rule_ids
    assert by_symbol["GC=F"].z_score is not None
