from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from radar.briefing import (
    BriefingStoryCard,
    DailyIntelligenceBrief,
    MarketSnapshotGroup,
    WatchItem,
)
from radar.domain import MarketHistoryPoint, MarketObservation

WEB_SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("market_regime", "Market Regime"),
    ("market_strip", "Global Market Strip"),
    ("china", "China / A-Shares"),
    ("risk", "Volatility & Rates"),
    ("overnight", "Overnight Intelligence"),
    ("cross_asset", "Cross-Asset Intelligence"),
    ("watch_today", "Watch Today"),
    ("next", "What Matters Next"),
    ("secondary", "Secondary Developments"),
    ("sources", "Sources / Data Quality"),
)


class FreshnessBand(StrEnum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"


class SignalState(StrEnum):
    OBSERVED_FACT = "OBSERVED_FACT"
    DETERMINISTIC_SIGNAL = "DETERMINISTIC_SIGNAL"
    EVIDENCE_BACKED_INTERPRETATION = "EVIDENCE_BACKED_INTERPRETATION"
    DRIVER_UNRESOLVED = "DRIVER_UNRESOLVED"


class ChartKind(StrEnum):
    CHINA_SECTORS = "CHINA_SECTORS"
    EXCEPTIONAL_MOVE = "EXCEPTIONAL_MOVE"
    RISK_PULSE = "RISK_PULSE"


@dataclass(frozen=True)
class AbnormalMoveConfig:
    absolute_pct: float = 3.0
    relative_strength_pct: float = 1.0
    volatility_move_pct: float = 2.0


@dataclass(frozen=True)
class FreshnessAssessment:
    band: FreshnessBand
    age_hours: float
    headline_eligible: bool
    information_gain: bool


@dataclass(frozen=True)
class AbnormalMove:
    symbol: str
    name: str
    change_pct: float
    state: SignalState
    reason: str


@dataclass(frozen=True)
class CoverageGap:
    code: str
    symbol: str | None
    reason: str


@dataclass(frozen=True)
class CrossAssetSignal:
    code: str
    label: str
    state: SignalState
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class MarketRegime:
    label: str
    state: SignalState
    supporting_symbols: tuple[str, ...]


@dataclass(frozen=True)
class ChartEligibility:
    kind: ChartKind
    eligible: bool
    reason: str
    source_keys: tuple[str, ...]


@dataclass(frozen=True)
class ChinaSectorObservation:
    sector: str
    change_1d: float
    change_5d: float | None = None
    activity: float | None = None
    style: str | None = None
    taxonomy: str = "SW1"
    provider: str = "validated-sector-feed"
    source_document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sector.strip():
            raise ValueError("sector must not be blank")
        if not self.provider.strip():
            raise ValueError("provider must not be blank")
        if not self.taxonomy.strip():
            raise ValueError("taxonomy must not be blank")
        if any(not item.strip() for item in self.source_document_ids):
            raise ValueError("source_document_ids must not contain blank values")


@dataclass(frozen=True)
class ChinaIntelligence:
    indices: tuple[MarketObservation, ...]
    sectors: tuple[ChinaSectorObservation, ...]
    breadth: tuple[int, int]
    dispersion: float
    leadership: str | None
    available: bool


@dataclass(frozen=True)
class MultiDayContext:
    symbol: str
    name: str
    close: float
    change_1d: float
    change_5d: float | None
    change_7d: float | None
    regime: str


@dataclass(frozen=True)
class NextItem:
    event_id: str
    title: str
    text: str
    confirmation: str
    invalidation: str


@dataclass(frozen=True)
class NextHorizons:
    next_24_hours: tuple[NextItem, ...]
    next_7_days: tuple[NextItem, ...]


@dataclass(frozen=True)
class WebReportModel:
    section_labels: tuple[tuple[str, str], ...]
    primary_stories: tuple[BriefingStoryCard, ...]
    overnight: tuple[str, ...]
    watch_today: tuple[WatchItem, ...]
    market_snapshot: tuple[MarketSnapshotGroup, ...]
    market_observations: tuple[MarketObservation, ...]
    freshness: dict[str, FreshnessAssessment] = field(default_factory=dict)
    abnormal_moves: tuple[AbnormalMove, ...] = ()
    coverage_gaps: tuple[CoverageGap, ...] = ()
    cross_asset_signals: tuple[CrossAssetSignal, ...] = ()
    regime: MarketRegime = field(
        default_factory=lambda: MarketRegime(
            label="UNRESOLVED",
            state=SignalState.DETERMINISTIC_SIGNAL,
            supporting_symbols=(),
        )
    )
    china: ChinaIntelligence = field(
        default_factory=lambda: ChinaIntelligence((), (), (0, 0), 0.0, None, False)
    )
    multi_day: tuple[MultiDayContext, ...] = ()
    next_horizons: NextHorizons = field(default_factory=lambda: NextHorizons((), ()))
    sparkline_points: dict[str, str] = field(default_factory=dict)
    major_visuals: tuple[ChartEligibility, ...] = ()
    risk_pulse_visual_enabled: bool = False
    china_sectors_visual_enabled: bool = False
    exceptional_move_visual_enabled: bool = False
    story_explanations: dict[str, str] = field(default_factory=dict)
    decayed_stories: tuple[BriefingStoryCard, ...] = ()


def build_web_report_model(
    briefing: DailyIntelligenceBrief,
    markets: Iterable[MarketObservation] = (),
    assets: Iterable[Mapping[str, Any]] = (),
) -> WebReportModel:
    _ = tuple(assets)
    return WebReportModel(
        section_labels=WEB_SECTION_LABELS,
        primary_stories=briefing.primary_stories,
        overnight=tuple(item.text for item in briefing.overnight),
        watch_today=briefing.watch_today,
        market_snapshot=briefing.market_snapshot,
        market_observations=tuple(markets),
    )


def _report_end(report_date: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(report_date).date()
    except ValueError as exc:
        raise ValueError("report_date must be an ISO date") from exc
    return datetime.combine(parsed, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)


def _freshness(
    briefing: DailyIntelligenceBrief, report_date: str
) -> dict[str, FreshnessAssessment]:
    report_end = _report_end(report_date)
    result: dict[str, FreshnessAssessment] = {}
    for story in briefing.primary_stories:
        age_hours = max(
            0.0, (report_end - story.occurred_at.astimezone(UTC)).total_seconds() / 3600
        )
        if age_hours <= 24:
            band = FreshnessBand.FRESH
        elif age_hours <= 48:
            band = FreshnessBand.AGING
        else:
            band = FreshnessBand.STALE
        result[story.event_id] = FreshnessAssessment(
            band=band,
            age_hours=round(age_hours, 2),
            headline_eligible=band is not FreshnessBand.STALE,
            information_gain=band is FreshnessBand.FRESH or story.supporting_source_count > 0,
        )
    return result


def _market_lookup(markets: tuple[MarketObservation, ...]) -> dict[str, MarketObservation]:
    return {item.symbol: item for item in markets}


def _find_market(
    lookup: Mapping[str, MarketObservation], *symbols_or_names: str
) -> MarketObservation | None:
    for key in symbols_or_names:
        if key in lookup:
            return lookup[key]
    for item in lookup.values():
        normalized = item.name.casefold()
        if any(key.casefold() in normalized for key in symbols_or_names):
            return item
    return None


def _abnormal_moves(
    briefing: DailyIntelligenceBrief,
    markets: tuple[MarketObservation, ...],
    config: AbnormalMoveConfig,
) -> tuple[AbnormalMove, ...]:
    linked_symbols = {item.target_symbol for item in briefing.market_reactions}
    result = [
        AbnormalMove(
            symbol=item.symbol,
            name=item.name,
            change_pct=item.change_pct,
            state=(
                SignalState.EVIDENCE_BACKED_INTERPRETATION
                if item.symbol in linked_symbols
                else SignalState.DRIVER_UNRESOLVED
            ),
            reason=(
                "Evidence-backed linked reaction is available."
                if item.symbol in linked_symbols
                else "Driver unresolved — no sufficiently strong evidence identified."
            ),
        )
        for item in markets
        if abs(item.change_pct) >= config.absolute_pct
    ]
    return tuple(sorted(result, key=lambda item: (-abs(item.change_pct), item.symbol)))


def _coverage_gaps(
    briefing: DailyIntelligenceBrief,
    markets: tuple[MarketObservation, ...],
    abnormal_moves: tuple[AbnormalMove, ...],
) -> tuple[CoverageGap, ...]:
    linked_symbols = {item.target_symbol for item in briefing.market_reactions}
    gaps: list[CoverageGap] = []
    for move in abnormal_moves:
        if move.symbol not in linked_symbols:
            gaps.append(
                CoverageGap(
                    code="UNEXPLAINED_MAJOR_MOVE",
                    symbol=move.symbol,
                    reason=(
                        f"{move.name} moved {move.change_pct:+.2f}% without a validated "
                        "linked explanation."
                    ),
                )
            )
    lookup = _market_lookup(markets)
    vix = _find_market(lookup, "^VIX", "VIX")
    vxn = _find_market(lookup, "^VXN", "VXN")
    if vix is not None and vxn is None:
        gaps.append(
            CoverageGap(
                "VOLATILITY_COVERAGE_GAP", vix.symbol, "VIX is present without VXN context."
            )
        )
    required = (
        ("MISSING_VIX", "^VIX", "VIX risk series is unavailable."),
        ("MISSING_VXN", "^VXN", "VXN risk series is unavailable."),
        ("MISSING_US2Y", "US2Y", "US 2Y Treasury yield series is unavailable."),
        ("MISSING_US10Y", "US10Y", "US 10Y Treasury yield series is unavailable."),
        ("MISSING_WTI", "CL=F", "WTI crude series is unavailable."),
    )
    for code, symbol, reason in required:
        aliases: tuple[str, ...] = (symbol,)
        if symbol == "US2Y":
            aliases = ("US2Y", "^UST2Y", "2Y")
        elif symbol == "US10Y":
            aliases = ("US10Y", "^TNX", "10Y")
        if _find_market(lookup, *aliases) is None:
            gaps.append(CoverageGap(code, symbol, reason))
    return tuple(gaps)


def _cross_asset_signals(
    markets: tuple[MarketObservation, ...], config: AbnormalMoveConfig
) -> tuple[CrossAssetSignal, ...]:
    lookup = _market_lookup(markets)
    ndx = _find_market(lookup, "^NDX", "Nasdaq 100")
    dow = _find_market(lookup, "^DJI", "Dow Jones")
    vix = _find_market(lookup, "^VIX", "VIX")
    vxn = _find_market(lookup, "^VXN", "VXN")
    wti = _find_market(lookup, "CL=F", "WTI")
    ten_year = _find_market(lookup, "US10Y", "10Y")
    signals: list[CrossAssetSignal] = []
    if (
        ndx is not None
        and dow is not None
        and ndx.change_pct - dow.change_pct >= config.relative_strength_pct
    ):
        signals.append(
            CrossAssetSignal(
                "US_TECH_RELATIVE_STRENGTH",
                "US technology relative strength",
                SignalState.DETERMINISTIC_SIGNAL,
                (ndx.symbol, dow.symbol),
            )
        )
    if (
        vix is not None
        and vxn is not None
        and vix.change_pct - vxn.change_pct >= config.volatility_move_pct
    ):
        signals.append(
            CrossAssetSignal(
                "TECH_RISK_CONCENTRATION_CANDIDATE",
                "VIX is rising faster than VXN",
                SignalState.DETERMINISTIC_SIGNAL,
                (vix.symbol, vxn.symbol),
            )
        )
    if wti is not None and ten_year is not None and wti.change_pct > 0 and ten_year.change_pct > 0:
        signals.append(
            CrossAssetSignal(
                "INFLATION_PRESSURE_CANDIDATE",
                "Oil and Treasury yields are rising together",
                SignalState.DETERMINISTIC_SIGNAL,
                (wti.symbol, ten_year.symbol),
            )
        )
    return tuple(signals)


def _regime(
    markets: tuple[MarketObservation, ...],
    signals: tuple[CrossAssetSignal, ...],
    config: AbnormalMoveConfig,
) -> MarketRegime:
    lookup = _market_lookup(markets)
    vix = _find_market(lookup, "^VIX", "VIX")
    ten_year = _find_market(lookup, "US10Y", "10Y")
    equity_moves = [
        item.change_pct for item in markets if item.region == "US" and "VIX" not in item.name
    ]
    cautious = (
        bool(equity_moves and sum(equity_moves) < 0) or vix is not None and vix.change_pct > 0
    )
    rates_led = (
        ten_year is not None and abs(ten_year.change_pct) >= config.relative_strength_pct / 10
    ) or any(item.code == "INFLATION_PRESSURE_CANDIDATE" for item in signals)
    panic = vix is not None and vix.change_pct >= 10
    if cautious and rates_led and not panic:
        label = "CAUTIOUS · RATES-LED · NOT PANIC"
    elif cautious:
        label = "CAUTIOUS · MIXED DRIVERS · NOT PANIC"
    else:
        label = "BALANCED · NO DOMINANT REGIME"
    supporting = tuple(item.symbol for item in (vix, ten_year) if item is not None)
    return MarketRegime(label, SignalState.DETERMINISTIC_SIGNAL, supporting)


def _china_intelligence(
    markets: tuple[MarketObservation, ...], sectors: tuple[ChinaSectorObservation, ...]
) -> ChinaIntelligence:
    indices = tuple(item for item in markets if item.region.casefold() in {"cn", "hk"})
    ordered = tuple(sorted(sectors, key=lambda item: (-item.change_1d, item.sector)))
    up = sum(item.change_1d > 0 for item in ordered)
    down = sum(item.change_1d < 0 for item in ordered)
    values = [item.change_1d for item in ordered]
    dispersion = round(max(values) - min(values), 2) if values else 0.0
    by_style: dict[str, list[float]] = {}
    for item in ordered:
        if item.style is not None:
            by_style.setdefault(item.style, []).append(item.change_1d)
    leadership = None
    if by_style:
        leadership = max(
            by_style,
            key=lambda style: (sum(by_style[style]) / len(by_style[style]), style),
        )
    return ChinaIntelligence(indices, ordered, (up, down), dispersion, leadership, bool(ordered))


def _multi_day(markets: tuple[MarketObservation, ...]) -> tuple[MultiDayContext, ...]:
    result: list[MultiDayContext] = []
    for item in sorted(markets, key=lambda market: market.symbol):
        change_5d = None
        change_7d = None
        if len(item.history) >= 6 and item.history[5].close:
            change_5d = round((item.close / item.history[5].close - 1) * 100, 2)
        if len(item.history) >= 8 and item.history[7].close:
            change_7d = round((item.close / item.history[7].close - 1) * 100, 2)
        trend = (
            "UPTREND" if (change_5d or 0) > 0 else "DOWNTREND" if (change_5d or 0) < 0 else "MIXED"
        )
        result.append(
            MultiDayContext(
                item.symbol, item.name, item.close, item.change_pct, change_5d, change_7d, trend
            )
        )
    return tuple(result)


def _next_horizons(briefing: DailyIntelligenceBrief) -> NextHorizons:
    next_24_hours = tuple(
        NextItem(
            event_id=item.event_id,
            title=item.headline,
            text=item.text,
            confirmation="Further official or company evidence clarifies or extends the item.",
            invalidation="No new evidence appears in the next observation window.",
        )
        for item in briefing.watch_today
    )
    return NextHorizons(next_24_hours=next_24_hours, next_7_days=())


def _story_explanations(briefing: DailyIntelligenceBrief) -> dict[str, str]:
    return {
        story.event_id: (
            "Low-information explanation rejected; driver unresolved."
            if is_low_information_explanation(story.why_it_matters)
            else story.why_it_matters
        )
        for story in briefing.primary_stories
    }


def is_low_information_explanation(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    generic = (
        "may change regulatory, financial, or operating constraints",
        "could affect markets",
        "may impact investors",
    )
    return any(phrase in normalized for phrase in generic) or len(normalized) < 35


def build_sparkline_points(
    history: Iterable[MarketHistoryPoint], *, width: float = 120, height: float = 42
) -> str:
    # Providers may retain a longer rolling window; the Web contract is explicitly 7 days.
    ordered = tuple(reversed(tuple(history)[:8]))
    if len(ordered) < 8:
        return ""
    values = [item.close for item in ordered]
    low = min(values)
    high = max(values)
    span = high - low or 1.0
    last_index = len(values) - 1
    points = []
    for index, value in enumerate(values):
        x = width * index / last_index
        y = height - (value - low) / span * height
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def select_major_visuals(
    intelligence: WebReportModel, *, max_visuals: int = 2
) -> tuple[ChartEligibility, ...]:
    if max_visuals < 0:
        raise ValueError("max_visuals must be non-negative")
    risk_keys = tuple(
        item.symbol
        for item in intelligence.market_observations
        if item.symbol in {"^VIX", "^VXN", "US10Y", "^TNX", "CL=F"}
        and len(item.history) >= 8
    )
    candidates: list[ChartEligibility] = []
    if risk_keys:
        candidates.append(
            ChartEligibility(
                ChartKind.RISK_PULSE, True, "Risk series have usable history.", risk_keys
            )
        )
    if intelligence.china.available:
        candidates.append(
            ChartEligibility(
                ChartKind.CHINA_SECTORS,
                True,
                "Validated sector observations are ranked.",
                tuple(item.sector for item in intelligence.china.sectors),
            )
        )
    if intelligence.abnormal_moves:
        candidates.append(
            ChartEligibility(
                ChartKind.EXCEPTIONAL_MOVE,
                True,
                "An abnormal move clears the deterministic threshold.",
                tuple(item.symbol for item in intelligence.abnormal_moves),
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.kind.value)[:max_visuals])


def build_web_intelligence(
    briefing: DailyIntelligenceBrief,
    markets: Iterable[MarketObservation] = (),
    assets: Iterable[Mapping[str, Any]] = (),
    *,
    report_date: str | None = None,
    abnormal_config: AbnormalMoveConfig | None = None,
    china_sectors: Iterable[ChinaSectorObservation] = (),
) -> WebReportModel:
    market_items = tuple(markets)
    abnormal_config = abnormal_config or AbnormalMoveConfig()
    report_date = report_date or briefing.report_date
    base = build_web_report_model(briefing, markets=market_items, assets=assets)
    abnormal_moves = _abnormal_moves(briefing, market_items, abnormal_config)
    gaps = _coverage_gaps(briefing, market_items, abnormal_moves)
    signals = _cross_asset_signals(market_items, abnormal_config)
    sector_items = tuple(china_sectors)
    freshness = _freshness(briefing, report_date)
    eligible_stories = tuple(
        story for story in base.primary_stories if freshness[story.event_id].headline_eligible
    )
    decayed_stories = tuple(
        story for story in base.primary_stories if not freshness[story.event_id].headline_eligible
    )
    intelligence = replace(
        base,
        primary_stories=eligible_stories,
        overnight=tuple(
            item.text
            for item in briefing.overnight
            if item.event_id in {story.event_id for story in eligible_stories}
        ),
        freshness=freshness,
        abnormal_moves=abnormal_moves,
        coverage_gaps=gaps,
        cross_asset_signals=signals,
        regime=_regime(market_items, signals, abnormal_config),
        china=_china_intelligence(market_items, sector_items),
        multi_day=_multi_day(market_items),
        next_horizons=_next_horizons(briefing),
        sparkline_points={
            item.symbol: build_sparkline_points(item.history)
            for item in market_items
            if item.symbol in {"^VIX", "^VXN", "US10Y", "^TNX", "CL=F"}
        },
        story_explanations=_story_explanations(briefing),
        decayed_stories=decayed_stories,
    )
    major_visuals = select_major_visuals(intelligence)
    visual_kinds = {item.kind for item in major_visuals if item.eligible}
    return replace(
        intelligence,
        major_visuals=major_visuals,
        risk_pulse_visual_enabled=ChartKind.RISK_PULSE in visual_kinds,
        china_sectors_visual_enabled=ChartKind.CHINA_SECTORS in visual_kinds,
        exceptional_move_visual_enabled=ChartKind.EXCEPTIONAL_MOVE in visual_kinds,
    )
