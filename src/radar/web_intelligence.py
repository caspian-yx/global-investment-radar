from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from radar.assets import CanonicalAsset, canonical_asset
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
    MARKET_TREND = "MARKET_TREND"
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
    previous_rank: int = 0
    new_rank: int | None = None
    section: str = "Primary"
    decision: str = "RETAIN_PRIMARY"
    decay_reason: str = ""


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
    label_zh: str = "跨资产信号"
    detail_zh: str = "已观察到跨资产关系，因果解释仍需证据。"


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
class MarketCard:
    canonical_key: str
    provider_symbol: str
    label_zh: str
    label_en: str
    region: str
    level: float | None
    absolute_change: float | None
    absolute_unit: str
    change_pct: float | None
    direction: str
    session: str
    available: bool


@dataclass(frozen=True)
class TrendSeries:
    canonical_key: str
    provider_symbol: str
    label_zh: str
    label_en: str
    range_key: str
    points: tuple[tuple[str, float], ...]
    path: str
    area_path: str


@dataclass(frozen=True)
class PriorityHighlight:
    kind: str
    title_zh: str
    title_en: str
    detail_zh: str
    detail_en: str
    state: SignalState
    source_keys: tuple[str, ...]


@dataclass(frozen=True)
class ChinaCapabilities:
    source: str | None
    core_indices: bool
    sectors: bool
    breadth: bool
    turnover: bool
    leadership: bool
    unavailable: tuple[str, ...]


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
    market_cards: tuple[MarketCard, ...] = ()
    trend_series: tuple[TrendSeries, ...] = ()
    default_trend_key: str | None = None
    china_capabilities: ChinaCapabilities = field(
        default_factory=lambda: ChinaCapabilities(None, False, False, False, False, False, ())
    )
    ai_market_brief: tuple[str, ...] = ()
    localization_unavailable: bool = False
    priority_highlights: tuple[PriorityHighlight, ...] = ()


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
    for previous_rank, story in enumerate(briefing.primary_stories, 1):
        age_hours = max(
            0.0, (report_end - story.occurred_at.astimezone(UTC)).total_seconds() / 3600
        )
        if age_hours <= 24:
            band = FreshnessBand.FRESH
        elif age_hours <= 48:
            band = FreshnessBand.AGING
        else:
            band = FreshnessBand.STALE
        information_gain = age_hours <= 24 or (
            band is FreshnessBand.AGING and story.supporting_source_count > 0
        )
        eligible = information_gain and band is not FreshnessBand.STALE
        if eligible:
            decision = "RETAIN_PRIMARY" if band is FreshnessBand.FRESH else "RETAIN_WITH_DECAY"
            reason = (
                "New observation within 24h."
                if band is FreshnessBand.FRESH
                else "Recent corroboration is present within the 48h aging window."
            )
        elif band is FreshnessBand.AGING:
            decision = "DEMOTE_SECONDARY"
            reason = "No new information gain in the Day+1 aging window."
        else:
            decision = "DEMOTE_SECONDARY"
            reason = "Story age exceeds the headline window without a material follow-up."
        result[story.event_id] = FreshnessAssessment(
            band=band,
            age_hours=round(age_hours, 2),
            headline_eligible=eligible,
            information_gain=information_gain,
            previous_rank=previous_rank,
            section="Primary" if eligible else "Secondary / Decayed",
            decision=decision,
            decay_reason=reason,
        )
    new_rank = 0
    for story in briefing.primary_stories:
        assessment = result[story.event_id]
        if assessment.headline_eligible:
            new_rank += 1
            result[story.event_id] = replace(assessment, new_rank=new_rank)
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
            aliases = ("DGS2", "US2Y", "^UST2Y", "2Y")
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
                "科技股相对韧性",
                (
                    f"纳斯达克100 {ndx.change_pct:+.2f}% 对比道琼斯 "
                    f"{dow.change_pct:+.2f}%，科技股相对更强。"
                ),
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
                "广义风险上升快于科技波动",
                (
                    f"VIX {vix.change_pct:+.2f}% 对比 VXN {vxn.change_pct:+.2f}%，"
                    "风险上升并非均匀分布。"
                ),
            )
        )
    if wti is not None and ten_year is not None and wti.change_pct > 0 and ten_year.change_pct > 0:
        signals.append(
            CrossAssetSignal(
                "INFLATION_PRESSURE_CANDIDATE",
                "Oil and Treasury yields are rising together",
                SignalState.DETERMINISTIC_SIGNAL,
                (wti.symbol, ten_year.symbol),
                "能源与利率同向",
                (
                    f"WTI {wti.change_pct:+.2f}%、美国10年期 "
                    f"{ten_year.change_pct:+.2f}%，通胀压力信号仍需进一步证据。"
                ),
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
    return ChinaIntelligence(
        indices, ordered, (up, down), dispersion, leadership, bool(indices or ordered)
    )


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


def _svg_path(
    history: tuple[MarketHistoryPoint, ...], *, width: float = 720, height: float = 280
) -> tuple[str, str]:
    ordered = tuple(reversed(history[:32]))
    if len(ordered) < 2:
        return "", ""
    values = [item.close for item in ordered]
    low, high = min(values), max(values)
    span = high - low or 1.0
    points = [
        (
            width * index / (len(values) - 1),
            height - ((value - low) / span * (height - 24) + 12),
        )
        for index, value in enumerate(values)
    ]
    path = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in points)
    area = f"{path} L {points[-1][0]:.2f} {height:.2f} L {points[0][0]:.2f} {height:.2f} Z"
    return path, area


def _market_cards(markets: tuple[MarketObservation, ...]) -> tuple[MarketCard, ...]:
    order = {
        "SP500": 1,
        "NASDAQ100": 2,
        "DOW": 3,
        "RUSSELL2000": 4,
        "VIX": 5,
        "VXN": 6,
        "US_2Y": 7,
        "US_10Y": 8,
        "DXY": 9,
        "WTI": 10,
        "GOLD": 11,
    }
    cards: list[MarketCard] = []
    for item in markets:
        identity = canonical_asset(item.symbol) or canonical_asset(item.name)
        if identity is None:
            identity = CanonicalAsset(item.symbol, item.name, item.name, "unknown", (item.symbol,))
        is_rate = identity.asset_class == "rate"
        cards.append(
            MarketCard(
                canonical_key=identity.key,
                provider_symbol=item.symbol,
                label_zh=identity.label_zh,
                label_en=identity.label_en,
                region=item.region,
                level=item.close,
                absolute_change=round(
                    (item.close - item.previous_close) * (100 if is_rate else 1), 4
                ),
                absolute_unit="bp" if is_rate else (item.currency or ""),
                change_pct=item.change_pct,
                direction="up"
                if item.change_pct > 0
                else "down"
                if item.change_pct < 0
                else "flat",
                session="latest observation",
                available=True,
            )
        )
    return tuple(sorted(cards, key=lambda card: (order.get(card.canonical_key, 50), card.label_en)))


def _trend_series(markets: tuple[MarketObservation, ...]) -> tuple[TrendSeries, ...]:
    result: list[TrendSeries] = []
    allowed = {"SP500", "NASDAQ100", "VIX", "VXN", "US_10Y", "WTI", "GOLD"}
    for item in markets:
        identity = canonical_asset(item.symbol) or canonical_asset(item.name)
        if identity is None or identity.key not in allowed:
            continue
        for range_key, required, history in (
            ("1D", 2, item.history[:2]),
            ("1W", 8, item.history[:8]),
            ("1M", 20, item.history[:20]),
        ):
            if len(history) < required:
                continue
            path, area = _svg_path(history)
            points = tuple(
                (point.observed_at.isoformat(), point.close) for point in reversed(history)
            )
            result.append(
                TrendSeries(
                    identity.key,
                    item.symbol,
                    identity.label_zh,
                    identity.label_en,
                    range_key,
                    points,
                    path,
                    area,
                )
            )
    return tuple(result)


def _china_capabilities(china: ChinaIntelligence) -> ChinaCapabilities:
    return ChinaCapabilities(
        source=china.sectors[0].provider
        if china.sectors
        else (china.indices[0].provider if china.indices else None),
        core_indices=bool(china.indices),
        sectors=bool(china.sectors),
        breadth=False,
        turnover=False,
        leadership=china.leadership is not None,
        unavailable=tuple(
            name
            for name, available in (
                ("core indices", bool(china.indices)),
                ("sector ranking", bool(china.sectors)),
                ("market breadth", False),
                ("turnover", False),
                ("leadership", china.leadership is not None),
            )
            if not available
        ),
    )


def _ai_market_brief(
    markets: tuple[MarketObservation, ...],
    abnormal_moves: tuple[AbnormalMove, ...],
    signals: tuple[CrossAssetSignal, ...],
    regime: MarketRegime,
) -> tuple[str, ...]:
    if not markets and not abnormal_moves and not signals:
        return ("AI synthesis unavailable: validated market observations were not supplied.",)
    paragraphs = [f"Deterministic market context: {regime.label}."]
    if abnormal_moves:
        paragraphs.append(
            "异常波动："
            + "；".join(f"{item.name} {item.change_pct:+.2f}%" for item in abnormal_moves)
            + "。驱动仅在有证据时确认。"
        )
    if signals:
        paragraphs.append("跨资产观察：" + "；".join(item.detail_zh for item in signals) + "")
    return tuple(paragraphs[:4])


def _priority_highlights(
    abnormal_moves: tuple[AbnormalMove, ...],
    signals: tuple[CrossAssetSignal, ...],
) -> tuple[PriorityHighlight, ...]:
    """Build presentation-only attention items from already-detected signals."""
    highlights: list[PriorityHighlight] = []
    for move in abnormal_moves:
        highlights.append(
            PriorityHighlight(
                kind="EXCEPTIONAL_MOVE",
                title_zh=f"异常波动 · {move.name} {move.change_pct:+.2f}%",
                title_en=f"Exceptional move · {move.name} {move.change_pct:+.2f}%",
                detail_zh=(
                    "已有证据支持的市场反应。"
                    if move.state is SignalState.EVIDENCE_BACKED_INTERPRETATION
                    else "主要驱动尚未确认；报告不生成未经证实的原因。"
                ),
                detail_en=(
                    "Evidence-backed linked reaction is available."
                    if move.state is SignalState.EVIDENCE_BACKED_INTERPRETATION
                    else "Driver unresolved; no unverified cause is presented."
                ),
                state=move.state,
                source_keys=(move.symbol,),
            )
        )
    for signal in signals:
        highlights.append(
            PriorityHighlight(
                kind=signal.code,
                title_zh=signal.label_zh,
                title_en=signal.label,
                detail_zh=signal.detail_zh,
                detail_en=(
                    f"{signal.label}; deterministic observation from "
                    f"{' / '.join(signal.symbols)}."
                ),
                state=signal.state,
                source_keys=signal.symbols,
            )
        )
    return tuple(highlights)


def select_major_visuals(
    intelligence: WebReportModel, *, max_visuals: int = 2
) -> tuple[ChartEligibility, ...]:
    if max_visuals < 0:
        raise ValueError("max_visuals must be non-negative")
    trend_keys = tuple(
        item.provider_symbol for item in intelligence.trend_series if item.range_key == "1W"
    )
    candidates: list[ChartEligibility] = []
    if trend_keys:
        candidates.append(
            ChartEligibility(
                ChartKind.MARKET_TREND, True, "Validated market history is available.", trend_keys
            )
        )
    dynamic: list[tuple[float, ChartEligibility]] = []
    if intelligence.abnormal_moves:
        move = max(intelligence.abnormal_moves, key=lambda item: abs(item.change_pct))
        dynamic.append(
            (
                100.0 + abs(move.change_pct),
                ChartEligibility(
                    ChartKind.EXCEPTIONAL_MOVE,
                    True,
                    "An abnormal move clears the deterministic threshold.",
                    tuple(item.symbol for item in intelligence.abnormal_moves),
                ),
            )
        )
    if intelligence.china.available and intelligence.china.sectors:
        dynamic.append(
            (
                intelligence.china.dispersion,
                ChartEligibility(
                    ChartKind.CHINA_SECTORS,
                    True,
                    "Validated sector observations are ranked.",
                    tuple(item.sector for item in intelligence.china.sectors),
                ),
            )
        )
    candidates.extend(item[1] for item in sorted(dynamic, key=lambda item: -item[0]))
    if max_visuals == 0:
        return ()
    return tuple(candidates[:max_visuals])


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
        market_cards=_market_cards(market_items),
        trend_series=_trend_series(market_items),
        priority_highlights=_priority_highlights(abnormal_moves, signals),
    )
    intelligence = replace(
        intelligence,
        china_capabilities=_china_capabilities(intelligence.china),
        ai_market_brief=_ai_market_brief(
            market_items, abnormal_moves, signals, intelligence.regime
        ),
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
