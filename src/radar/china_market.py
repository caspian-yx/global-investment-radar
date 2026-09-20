from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from radar.config import Settings


class _ChinaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CapabilityState(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"


class ChinaIndexSnapshot(_ChinaModel):
    code: str
    name: str
    close: float
    change_pct: float
    turnover: float | None
    observed_at: datetime
    provider: str

    @field_validator("observed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)


class ChinaSectorSnapshot(_ChinaModel):
    code: str
    name: str
    taxonomy: str
    change_pct: float
    observed_at: datetime
    provider: str


class ChinaBreadth(_ChinaModel):
    advancers: int
    decliners: int
    unchanged: int
    total: int
    observed_at: datetime
    provider: str


class ChinaActivity(_ChinaModel):
    total_turnover: float
    currency: str = "CNY"
    observed_at: datetime
    provider: str


class ChinaLeadership(_ChinaModel):
    signals: tuple[str, ...]
    observed_at: datetime
    provider: str


class ChinaCapabilitySummary(_ChinaModel):
    indices: CapabilityState
    sectors: CapabilityState
    breadth: CapabilityState
    turnover: CapabilityState
    style: CapabilityState


class ChinaMarketIntelligence(_ChinaModel):
    provider: str
    indices: tuple[ChinaIndexSnapshot, ...] = ()
    sectors: tuple[ChinaSectorSnapshot, ...] = ()
    breadth: ChinaBreadth | None = None
    activity: ChinaActivity | None = None
    leadership: ChinaLeadership | None = None
    capabilities: ChinaCapabilitySummary
    warnings: tuple[str, ...] = ()


_INDEX_ORDER = (
    ("000001", "SSE Composite"),
    ("399001", "Shenzhen Component"),
    ("000300", "CSI 300"),
    ("000510", "CSI A500"),
    ("399006", "ChiNext"),
    ("000688", "STAR 50"),
    ("000905", "CSI 500"),
    ("000852", "CSI 1000"),
)
_FRESHNESS_LIMIT = timedelta(days=4)


def _rows(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    rows = data.get("diff")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _timestamp(row: dict[str, Any]) -> datetime | None:
    raw = row.get("f124")
    if not isinstance(raw, int | float) or raw <= 0:
        return None
    try:
        return datetime.fromtimestamp(raw, UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _is_fresh(observed_at: datetime, as_of: datetime) -> bool:
    age = as_of - observed_at
    return timedelta(0) <= age <= _FRESHNESS_LIMIT


def _unavailable(provider: str, warning: str) -> ChinaMarketIntelligence:
    return ChinaMarketIntelligence(
        provider=provider,
        capabilities=ChinaCapabilitySummary(
            indices=CapabilityState.UNAVAILABLE,
            sectors=CapabilityState.UNAVAILABLE,
            breadth=CapabilityState.UNAVAILABLE,
            turnover=CapabilityState.UNAVAILABLE,
            style=CapabilityState.UNAVAILABLE,
        ),
        warnings=(warning,),
    )


def build_china_market_intelligence(
    index_payload: object,
    breadth_payload: object | None,
    *,
    provider: str,
    as_of: datetime,
) -> ChinaMarketIntelligence:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    index_rows = {str(row.get("f12")): row for row in _rows(index_payload)}
    indices: list[ChinaIndexSnapshot] = []
    stale_indices = False
    for code, name in _INDEX_ORDER:
        row = index_rows.get(code)
        if row is None:
            continue
        observed_at = _timestamp(row)
        if observed_at is None or not _is_fresh(observed_at, as_of):
            stale_indices = True
            continue
        close = row.get("f2")
        change_pct = row.get("f3")
        if not isinstance(close, int | float) or not isinstance(change_pct, int | float):
            continue
        turnover = row.get("f6")
        indices.append(
            ChinaIndexSnapshot(
                code=code,
                name=name,
                close=float(close),
                change_pct=float(change_pct),
                turnover=float(turnover) if isinstance(turnover, int | float) else None,
                observed_at=observed_at,
                provider=provider,
            )
        )

    breadth_rows = _rows(breadth_payload)
    fresh_breadth_rows = [
        row
        for row in breadth_rows
        if (observed_at := _timestamp(row)) is not None and _is_fresh(observed_at, as_of)
    ]
    breadth_stale = bool(breadth_rows) and not fresh_breadth_rows
    breadth = None
    activity = None
    if fresh_breadth_rows:
        changes = [
            float(row["f3"]) for row in fresh_breadth_rows if isinstance(row.get("f3"), int | float)
        ]
        timestamps = [
            timestamp for row in fresh_breadth_rows if (timestamp := _timestamp(row)) is not None
        ]
        observed_at = max(timestamps)
        breadth = ChinaBreadth(
            advancers=sum(value > 0 for value in changes),
            decliners=sum(value < 0 for value in changes),
            unchanged=sum(value == 0 for value in changes),
            total=len(changes),
            observed_at=observed_at,
            provider=provider,
        )
        turnovers = [
            float(row["f6"]) for row in fresh_breadth_rows if isinstance(row.get("f6"), int | float)
        ]
        if turnovers:
            activity = ChinaActivity(
                total_turnover=sum(turnovers),
                observed_at=observed_at,
                provider=provider,
            )

    index_by_code = {item.code: item for item in indices}
    leadership_signals: list[str] = []
    if (
        (chinext := index_by_code.get("399006")) is not None
        and (sse := index_by_code.get("000001")) is not None
        and chinext.change_pct - sse.change_pct >= 1.0
    ):
        leadership_signals.append("GROWTH_LEADERSHIP")
    if (
        (csi1000 := index_by_code.get("000852")) is not None
        and (csi300 := index_by_code.get("000300")) is not None
        and csi1000.change_pct - csi300.change_pct >= 1.0
    ):
        leadership_signals.append("SMALL_CAP_LEADERSHIP")
    leadership = (
        ChinaLeadership(
            signals=tuple(leadership_signals),
            observed_at=max(item.observed_at for item in indices),
            provider=provider,
        )
        if leadership_signals
        else None
    )

    if not indices and not stale_indices and not breadth_rows:
        return _unavailable(
            provider, "China market payload contained no validated capability data."
        )
    return ChinaMarketIntelligence(
        provider=provider,
        indices=tuple(indices),
        breadth=breadth,
        activity=activity,
        leadership=leadership,
        capabilities=ChinaCapabilitySummary(
            indices=(
                CapabilityState.AVAILABLE
                if indices
                else CapabilityState.STALE
                if stale_indices
                else CapabilityState.UNAVAILABLE
            ),
            sectors=CapabilityState.UNAVAILABLE,
            breadth=(
                CapabilityState.AVAILABLE
                if breadth is not None
                else CapabilityState.STALE
                if breadth_stale
                else CapabilityState.UNAVAILABLE
            ),
            turnover=(
                CapabilityState.AVAILABLE
                if activity is not None
                else CapabilityState.STALE
                if breadth_stale
                else CapabilityState.UNAVAILABLE
            ),
            style=(
                CapabilityState.AVAILABLE if leadership is not None else CapabilityState.UNAVAILABLE
            ),
        ),
        warnings=("Recognized sector taxonomy is unavailable; no sector ranking was inferred.",),
    )


class EastmoneyChinaMarketProvider:
    name = "experimental-eastmoney-china-market"
    index_url = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&"
        "fields=f12,f14,f2,f3,f4,f6,f124&"
        "secids=1.000001,0.399001,1.000300,1.000510,0.399006,1.000688,1.000905,1.000852"
    )
    breadth_url = (
        "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1&"
        "fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&"
        "fields=f12,f2,f3,f6,f124"
    )

    def __init__(self, timeout: float = 25.0, max_response_bytes: int = 4_000_000) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def _get(self, client: httpx.Client, url: str) -> object:
        response = client.get(url)
        response.raise_for_status()
        if len(response.content) > self.max_response_bytes:
            raise ValueError("China market response exceeded size limit")
        return response.json()

    def collect(self, now: datetime) -> ChinaMarketIntelligence:
        headers = {
            "User-Agent": "GlobalInvestmentRadar/0.1 (public-research-pipeline)",
            "Referer": "https://quote.eastmoney.com/",
        }
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                index_payload = self._get(client, self.index_url)
                try:
                    breadth_payload = self._get(client, self.breadth_url)
                except (httpx.HTTPError, ValueError):
                    breadth_payload = None
            return build_china_market_intelligence(
                index_payload,
                breadth_payload,
                provider=self.name,
                as_of=now,
            )
        except (httpx.HTTPError, TypeError, ValueError):
            return _unavailable(
                self.name,
                "Experimental China market endpoint was unavailable; capabilities failed closed.",
            )


def build_china_market_provider(settings: Settings) -> EastmoneyChinaMarketProvider | None:
    return EastmoneyChinaMarketProvider() if settings.data_mode == "live" else None
