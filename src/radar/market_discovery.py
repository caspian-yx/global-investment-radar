from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from radar.domain import MarketObservation


class _MarketDiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AnomalyRule(StrEnum):
    ABSOLUTE_1D = "absolute_1d"
    STANDARDIZED_1D = "standardized_1d"


class MarketAnomaly(_MarketDiscoveryModel):
    cluster_key: str
    symbols: tuple[str, ...]
    change_1d: float
    change_5d: float | None = None
    z_score: float | None = None
    rule_ids: tuple[str, ...]
    observed_at: datetime


class CrossAssetDivergence(_MarketDiscoveryModel):
    code: str
    symbols: tuple[str, ...]
    detail: str
    observed_at: datetime


class EvidenceNeed(_MarketDiscoveryModel):
    cluster_key: str
    symbols: tuple[str, ...]
    reason: str
    resolved: bool = False


class MarketDiscoveryDiagnostics(_MarketDiscoveryModel):
    markets_evaluated: int
    anomalies: int
    divergences: int
    evidence_needs: int
    insufficient_history: int


class MarketDiscoveryResult(_MarketDiscoveryModel):
    anomalies: tuple[MarketAnomaly, ...]
    divergences: tuple[CrossAssetDivergence, ...]
    evidence_needs: tuple[EvidenceNeed, ...]
    diagnostics: MarketDiscoveryDiagnostics


def _change_5d(item: MarketObservation) -> float | None:
    if len(item.history) < 6 or item.history[5].close == 0:
        return None
    return round((item.close / item.history[5].close - 1) * 100, 2)


def _z_score(item: MarketObservation) -> float | None:
    if len(item.history) < 8:
        return None
    historical_returns = [
        (item.history[index].close / item.history[index + 1].close - 1) * 100
        for index in range(1, len(item.history) - 1)
        if item.history[index + 1].close != 0
    ]
    if len(historical_returns) < 6:
        return None
    mean = statistics.fmean(historical_returns)
    deviation = statistics.pstdev(historical_returns)
    denominator = max(deviation, 0.1)
    return round((item.change_pct - mean) / denominator, 2)


def _threshold(item: MarketObservation, assets: Mapping[str, Mapping[str, Any]]) -> float:
    asset = assets.get(item.symbol, {})
    asset_class = str(asset.get("asset_class", ""))
    if item.symbol in {"^VIX", "^VXN"}:
        return 5.0
    if asset_class == "RATE":
        return 2.5
    return 3.0


def _single_anomaly(
    item: MarketObservation,
    assets: Mapping[str, Mapping[str, Any]],
) -> MarketAnomaly | None:
    z_score = _z_score(item)
    rules: list[str] = []
    if abs(item.change_pct) >= _threshold(item, assets):
        rules.append(AnomalyRule.ABSOLUTE_1D.value)
    if z_score is not None and abs(z_score) >= 2.0:
        rules.append(AnomalyRule.STANDARDIZED_1D.value)
    if not rules:
        return None
    return MarketAnomaly(
        cluster_key=f"market:{item.symbol}",
        symbols=(item.symbol,),
        change_1d=item.change_pct,
        change_5d=_change_5d(item),
        z_score=z_score,
        rule_ids=tuple(rules),
        observed_at=item.observed_at,
    )


def _group_volatility(anomalies: list[MarketAnomaly]) -> list[MarketAnomaly]:
    by_symbol = {item.symbols[0]: item for item in anomalies}
    vix = by_symbol.get("^VIX")
    vxn = by_symbol.get("^VXN")
    if vix is None or vxn is None or vix.change_1d * vxn.change_1d <= 0:
        return anomalies
    grouped = MarketAnomaly(
        cluster_key="market:us-volatility",
        symbols=("^VIX", "^VXN"),
        change_1d=round((vix.change_1d + vxn.change_1d) / 2, 2),
        change_5d=None,
        z_score=max(
            (value for value in (vix.z_score, vxn.z_score) if value is not None),
            default=None,
            key=abs,
        ),
        rule_ids=tuple(sorted(set((*vix.rule_ids, *vxn.rule_ids)))),
        observed_at=max(vix.observed_at, vxn.observed_at),
    )
    return [
        *(item for item in anomalies if item.symbols[0] not in {"^VIX", "^VXN"}),
        grouped,
    ]


def _divergences(markets: Mapping[str, MarketObservation]) -> tuple[CrossAssetDivergence, ...]:
    result: list[CrossAssetDivergence] = []
    ndx = markets.get("^NDX")
    dow = markets.get("^DJI")
    if ndx is not None and dow is not None and ndx.change_pct - dow.change_pct >= 1.0:
        result.append(
            CrossAssetDivergence(
                code="US_TECH_RELATIVE_STRENGTH",
                symbols=("^NDX", "^DJI"),
                detail=f"Nasdaq 100 {ndx.change_pct:+.2f}% vs Dow {dow.change_pct:+.2f}%",
                observed_at=max(ndx.observed_at, dow.observed_at),
            )
        )
    ten_year = markets.get("^TNX") or markets.get("US10Y")
    if (
        ten_year is not None
        and ndx is not None
        and ten_year.change_pct >= 1.5
        and ndx.change_pct <= -1
    ):
        result.append(
            CrossAssetDivergence(
                code="RATES_EQUITY_PRESSURE",
                symbols=(ten_year.symbol, "^NDX"),
                detail=(
                    f"US 10Y {ten_year.change_pct:+.2f}% while Nasdaq 100 {ndx.change_pct:+.2f}%"
                ),
                observed_at=max(ten_year.observed_at, ndx.observed_at),
            )
        )
    return tuple(sorted(result, key=lambda item: item.code))


def discover_market_anomalies(
    markets: Sequence[MarketObservation],
    assets: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> MarketDiscoveryResult:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    asset_by_symbol = {str(item.get("symbol")): item for item in assets}
    raw_anomalies = [
        anomaly
        for item in markets
        if (anomaly := _single_anomaly(item, asset_by_symbol)) is not None
    ]
    anomalies = tuple(sorted(_group_volatility(raw_anomalies), key=lambda item: item.cluster_key))
    lookup = {item.symbol: item for item in markets}
    divergences = _divergences(lookup)
    evidence_needs = tuple(
        EvidenceNeed(
            cluster_key=item.cluster_key,
            symbols=item.symbols,
            reason="abnormal_market_move_requires_evidence_resolution",
        )
        for item in anomalies
    )
    return MarketDiscoveryResult(
        anomalies=anomalies,
        divergences=divergences,
        evidence_needs=evidence_needs,
        diagnostics=MarketDiscoveryDiagnostics(
            markets_evaluated=len(markets),
            anomalies=len(anomalies),
            divergences=len(divergences),
            evidence_needs=len(evidence_needs),
            insufficient_history=sum(len(item.history) < 8 for item in markets),
        ),
    )
