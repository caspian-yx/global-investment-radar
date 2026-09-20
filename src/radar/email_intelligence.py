from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.briefing import BriefingSource, BriefingStoryCard, DailyIntelligenceBrief
from radar.calendar import ScheduledCatalyst
from radar.china_market import CapabilityState, ChinaMarketIntelligence
from radar.domain import MarketObservation
from radar.market_discovery import MarketDiscoveryResult

C9_EMAIL_RULE_VERSION = "c9-email-v1"
_SPECIFIC_CATALYST = re.compile(
    r"\b(scheduled|at\s+\d{1,2}:\d{2}|on\s+\d{4}-\d{2}-\d{2}|"
    r"decision|meeting|release|auction|earnings|vote|deadline|published\s+rate)\b",
    re.IGNORECASE,
)


class EmailFocusItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    cluster_key: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    what_happened: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    affected: str = Field(min_length=1)
    market_reaction_zh: str | None = None
    market_reaction_en: str | None = None
    unknown_zh: str = Field(min_length=1)
    unknown_en: str = Field(min_length=1)
    sources: tuple[BriefingSource, ...] = ()


class EmailMarketSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    cluster_key: str = Field(min_length=1)
    title_zh: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    symbols: tuple[str, ...] = Field(min_length=1)
    observed: str = Field(min_length=1)
    interpretation_zh: str = Field(min_length=1)
    interpretation_en: str = Field(min_length=1)
    unknown_zh: str = Field(min_length=1)
    unknown_en: str = Field(min_length=1)


class EmailCatalyst(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    source_url: str | None = None


class EmailChinaIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: str
    name_zh: str
    name_en: str
    close: float
    change_pct: float


class EmailChinaSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider: str
    indices: tuple[EmailChinaIndex, ...]
    breadth_zh: str | None = None
    breadth_en: str | None = None
    turnover_zh: str | None = None
    turnover_en: str | None = None
    leadership_zh: tuple[str, ...] = ()
    leadership_en: tuple[str, ...] = ()
    sectors_available: bool
    limitations_zh: tuple[str, ...] = ()
    limitations_en: tuple[str, ...] = ()


class EmailIntelligenceDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidates: int = Field(ge=0)
    focus_selected: int = Field(ge=0)
    clusters_suppressed: int = Field(ge=0)
    stale_or_demoted_event_ids: tuple[str, ...]
    unresolved_market_symbols: tuple[str, ...]
    rule_version: str = C9_EMAIL_RULE_VERSION


class EmailIntelligence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    focus: tuple[EmailFocusItem, ...] = Field(max_length=5)
    market_signals: tuple[EmailMarketSignal, ...]
    next_catalysts: tuple[EmailCatalyst, ...] = Field(max_length=5)
    china: EmailChinaSummary | None = None
    market_brief_zh: tuple[str, ...] = Field(min_length=1, max_length=4)
    market_brief_en: tuple[str, ...] = Field(min_length=1, max_length=4)
    diagnostics: EmailIntelligenceDiagnostics

    @model_validator(mode="after")
    def sections_are_disjoint(self) -> EmailIntelligence:
        focus = {item.cluster_key for item in self.focus}
        signals = {item.cluster_key for item in self.market_signals}
        if focus & signals:
            raise ValueError("Email intelligence clusters must be unique across sections")
        return self


def _report_end(report_date: str) -> datetime:
    value = datetime.fromisoformat(report_date).date()
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)


def _is_current(story: BriefingStoryCard, report_end: datetime) -> bool:
    age = report_end - story.occurred_at.astimezone(UTC)
    return age <= timedelta(hours=24)


def _story_cluster(story: BriefingStoryCard) -> str:
    if story.story_id:
        return f"story:{story.story_id}"
    return f"event:{story.event_id}"


def _affected(story: BriefingStoryCard) -> str:
    targets = [*story.entity_ids]
    if story.region and story.region not in targets:
        targets.append(story.region)
    return "、".join(targets) if targets else "相关资产与行业"


def _market_reaction(story: BriefingStoryCard, *, locale: str) -> str | None:
    if not story.market_reactions:
        return None
    if locale == "zh-CN":
        return "；".join(
            f"{item.target_name} {item.percentage_move:+.2f}%（观察到相关反应，非因果结论）"
            for item in story.market_reactions
        )
    return "; ".join(
        f"{item.target_name} {item.percentage_move:+.2f}% (associated observation, not causal)"
        for item in story.market_reactions
    )


def _focus_item(story: BriefingStoryCard) -> EmailFocusItem:
    reaction_zh = _market_reaction(story, locale="zh-CN")
    reaction_en = _market_reaction(story, locale="en")
    unknown_zh = (
        "市场反应已有验证记录，但驱动与因果边界仍需后续证据确认。"
        if reaction_zh
        else "尚无足够证据确认直接市场影响或完整传导路径。"
    )
    return EmailFocusItem(
        cluster_key=_story_cluster(story),
        event_id=story.event_id,
        headline=story.headline,
        what_happened=story.what_happened,
        why_it_matters=story.why_it_matters,
        affected=_affected(story),
        market_reaction_zh=reaction_zh,
        market_reaction_en=reaction_en,
        unknown_zh=unknown_zh,
        unknown_en=(
            "A validated reaction is present, but its driver and causal boundary remain open."
            if reaction_en
            else (
                "Evidence is insufficient to confirm a direct market effect or full "
                "transmission path."
            )
        ),
        sources=story.sources,
    )


def _market_signals(
    markets: tuple[MarketObservation, ...],
    occupied: set[str],
    evidence_symbols: set[str],
) -> tuple[EmailMarketSignal, ...]:
    abnormal = tuple(item for item in markets if abs(item.change_pct) >= 3.0)
    volatility = tuple(item for item in abnormal if item.symbol in {"^VIX", "^VXN"})
    result: list[EmailMarketSignal] = []
    lookup = {item.symbol: item for item in markets}
    ndx = lookup.get("^NDX")
    dow = lookup.get("^DJI")
    if (
        ndx is not None
        and dow is not None
        and ndx.change_pct - dow.change_pct >= 1.0
        and "market:us-equity-divergence" not in occupied
    ):
        result.append(
            EmailMarketSignal(
                cluster_key="market:us-equity-divergence",
                title_zh="美国科技股相对韧性",
                title_en="US technology relative resilience",
                symbols=("^NDX", "^DJI"),
                observed=(f"Nasdaq 100 {ndx.change_pct:+.2f}%；Dow Jones {dow.change_pct:+.2f}%"),
                interpretation_zh="科技股相对传统蓝筹更强，这是由指数表现直接得出的分化观察。",
                interpretation_en=(
                    "Technology outperformed traditional blue chips in the observed index returns."
                ),
                unknown_zh="分化原因仍需结合利率、行业与事件证据判断。",
                unknown_en="The driver still requires rates, sector and event evidence.",
            )
        )
    if volatility and "market:volatility" not in occupied:
        ordered = tuple(sorted(volatility, key=lambda item: item.symbol))
        result.append(
            EmailMarketSignal(
                cluster_key="market:volatility",
                title_zh="波动率共同变化",
                title_en="Joint volatility move",
                symbols=tuple(item.symbol for item in ordered),
                observed="；".join(f"{item.name} {item.change_pct:+.2f}%" for item in ordered),
                interpretation_zh="波动率指标正在共同重定价风险，但方向本身不证明具体驱动。",
                interpretation_en=(
                    "The volatility gauges are repricing risk together, but direction "
                    "alone does not prove a driver."
                ),
                unknown_zh="尚无足够证据确认单一驱动。",
                unknown_en="Evidence is insufficient to confirm a single driver.",
            )
        )
    for item in sorted(abnormal, key=lambda value: (-abs(value.change_pct), value.symbol)):
        if item in volatility:
            continue
        key = f"market:{item.symbol}"
        if key in occupied:
            continue
        result.append(
            EmailMarketSignal(
                cluster_key=key,
                title_zh=f"{item.name} 异常波动",
                title_en=f"Abnormal move in {item.name}",
                symbols=(item.symbol,),
                observed=f"{item.name} {item.change_pct:+.2f}%",
                interpretation_zh=(
                    "该变动已达到异常波动阈值，且已有结构化市场反应证据支持关联观察。"
                    if item.symbol in evidence_symbols
                    else "该变动已达到异常波动阈值，应优先寻找可靠证据。"
                ),
                interpretation_en=(
                    "The move cleared the abnormality threshold and has a structured "
                    "market-reaction record supporting an associated observation."
                    if item.symbol in evidence_symbols
                    else "The move cleared the abnormality threshold and warrants evidence "
                    "retrieval."
                ),
                unknown_zh=(
                    "反应已有记录，但主要驱动与直接因果关系仍未被证实。"
                    if item.symbol in evidence_symbols
                    else "尚无足够证据确认主要驱动。"
                ),
                unknown_en=(
                    "The reaction is recorded, but the main driver and direct causality "
                    "remain unconfirmed."
                    if item.symbol in evidence_symbols
                    else "Evidence is insufficient to confirm the main driver."
                ),
            )
        )
    return tuple(result)


def _future_catalysts(
    briefing: DailyIntelligenceBrief, current_ids: set[str]
) -> tuple[EmailCatalyst, ...]:
    result: list[EmailCatalyst] = []
    for item in briefing.watch_today:
        if item.event_id not in current_ids or not _SPECIFIC_CATALYST.search(item.text):
            continue
        result.append(EmailCatalyst(event_id=item.event_id, title=item.headline, trigger=item.text))
    return tuple(result[:5])


def _scheduled_catalysts(events: Sequence[ScheduledCatalyst]) -> tuple[EmailCatalyst, ...]:
    return tuple(
        EmailCatalyst(
            event_id=item.id,
            title=item.title,
            trigger=f"{item.scheduled_at:%Y-%m-%d %H:%M UTC} · {item.provider_name}",
            source_url=item.source_url,
        )
        for item in events[:5]
    )


_CHINA_INDEX_NAMES = {
    "000001": "上证综指",
    "399001": "深证成指",
    "000300": "沪深300",
    "000510": "中证A500",
    "399006": "创业板指",
    "000688": "科创50",
    "000905": "中证500",
    "000852": "中证1000",
}
_LEADERSHIP_LABELS = {
    "GROWTH_LEADERSHIP": ("成长风格相对占优", "Growth style leadership"),
    "SMALL_CAP_LEADERSHIP": ("小盘风格相对占优", "Small-cap leadership"),
}


def _china_summary(china: ChinaMarketIntelligence | None) -> EmailChinaSummary | None:
    if china is None:
        return None
    breadth_zh = breadth_en = None
    if china.breadth is not None:
        breadth_zh = (
            f"上涨 {china.breadth.advancers} / 下跌 {china.breadth.decliners} / "
            f"平盘 {china.breadth.unchanged}"
        )
        breadth_en = (
            f"Advancers {china.breadth.advancers} / decliners {china.breadth.decliners} / "
            f"unchanged {china.breadth.unchanged}"
        )
    turnover_zh = turnover_en = None
    if china.activity is not None:
        trillions = china.activity.total_turnover / 1_000_000_000_000
        turnover_zh = f"全市场成交额约 {trillions:.2f} 万亿元"
        turnover_en = f"Aggregate turnover approximately CNY {trillions:.2f}tn"
    leadership_pairs = tuple(
        _LEADERSHIP_LABELS[item]
        for item in (china.leadership.signals if china.leadership is not None else ())
        if item in _LEADERSHIP_LABELS
    )
    limitations_zh: list[str] = []
    limitations_en: list[str] = []
    if china.capabilities.sectors is not CapabilityState.AVAILABLE:
        limitations_zh.append("申万行业分类数据不可用，未用代理行业填充。")
        limitations_en.append("Shenwan sector data is unavailable; no proxy sectors were used.")
    if china.capabilities.breadth is not CapabilityState.AVAILABLE:
        limitations_zh.append("市场涨跌结构不可用。")
        limitations_en.append("Market breadth is unavailable.")
    return EmailChinaSummary(
        provider=china.provider,
        indices=tuple(
            EmailChinaIndex(
                code=item.code,
                name_zh=_CHINA_INDEX_NAMES.get(item.code, item.name),
                name_en=item.name,
                close=item.close,
                change_pct=item.change_pct,
            )
            for item in china.indices
        ),
        breadth_zh=breadth_zh,
        breadth_en=breadth_en,
        turnover_zh=turnover_zh,
        turnover_en=turnover_en,
        leadership_zh=tuple(item[0] for item in leadership_pairs),
        leadership_en=tuple(item[1] for item in leadership_pairs),
        sectors_available=china.capabilities.sectors is CapabilityState.AVAILABLE,
        limitations_zh=tuple(limitations_zh),
        limitations_en=tuple(limitations_en),
    )


def _discovered_market_signals(
    discovery: MarketDiscoveryResult,
    markets: tuple[MarketObservation, ...],
    evidence_symbols: set[str],
) -> tuple[EmailMarketSignal, ...]:
    lookup = {item.symbol: item for item in markets}
    result: list[EmailMarketSignal] = []
    for divergence in discovery.divergences:
        if divergence.code == "US_TECH_RELATIVE_STRENGTH":
            result.append(
                EmailMarketSignal(
                    cluster_key=f"market:{divergence.code.casefold()}",
                    title_zh="美国科技股相对韧性",
                    title_en="US technology relative resilience",
                    symbols=divergence.symbols,
                    observed=divergence.detail,
                    interpretation_zh="这是由指数回报直接识别的市场分化，不是对原因的推断。",
                    interpretation_en=(
                        "This is a deterministic divergence in observed index returns, "
                        "not an inferred cause."
                    ),
                    unknown_zh="分化原因仍需利率、行业与事件证据确认。",
                    unknown_en="The driver still requires rates, sector and event evidence.",
                )
            )
        elif divergence.code == "RATES_EQUITY_PRESSURE":
            result.append(
                EmailMarketSignal(
                    cluster_key="market:rates-equity-pressure",
                    title_zh="利率与科技股压力同步",
                    title_en="Rates and technology equity pressure",
                    symbols=divergence.symbols,
                    observed=divergence.detail,
                    interpretation_zh="利率与科技股出现同步压力，但共同变化不等于因果。",
                    interpretation_en=(
                        "Rates and technology shares moved together; co-movement is not causality."
                    ),
                    unknown_zh="直接传导路径仍需事件证据确认。",
                    unknown_en="The direct transmission path remains unconfirmed.",
                )
            )
    for anomaly in discovery.anomalies:
        names = tuple(
            lookup[symbol].name if symbol in lookup else symbol for symbol in anomaly.symbols
        )
        has_evidence = any(symbol in evidence_symbols for symbol in anomaly.symbols)
        label = " / ".join(names)
        context = f"；5日 {anomaly.change_5d:+.2f}%" if anomaly.change_5d is not None else ""
        result.append(
            EmailMarketSignal(
                cluster_key=anomaly.cluster_key,
                title_zh=f"{label} 异常波动",
                title_en=f"Abnormal move in {label}",
                symbols=anomaly.symbols,
                observed=f"1日 {anomaly.change_1d:+.2f}%{context}",
                interpretation_zh=(
                    "异常波动已触发证据检索；现有结构化反应记录仅支持关联观察。"
                    if has_evidence
                    else "异常波动已由透明阈值识别，并触发证据检索。"
                ),
                interpretation_en=(
                    "The anomaly opened evidence retrieval; an existing reaction record "
                    "supports association only."
                    if has_evidence
                    else (
                        "Transparent thresholds identified the anomaly and opened "
                        "evidence retrieval."
                    )
                ),
                unknown_zh="尚无足够证据确认主要驱动。",
                unknown_en="Evidence is insufficient to confirm the main driver.",
            )
        )
    return tuple(result)


def _market_brief(
    markets: tuple[MarketObservation, ...],
    focus: tuple[EmailFocusItem, ...],
    signals: tuple[EmailMarketSignal, ...],
    catalysts: tuple[EmailCatalyst, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if markets:
        largest = max(markets, key=lambda item: (abs(item.change_pct), item.symbol))
        zh = [
            f"已验证 {len(markets)} 个市场观察；波动最大的是 "
            f"{largest.name} {largest.change_pct:+.2f}%。"
        ]
        en = [
            f"Across {len(markets)} validated market observations, the largest move was "
            f"{largest.name} at {largest.change_pct:+.2f}%."
        ]
    else:
        zh = ["当前没有足够的已验证市场观察形成市场概览。"]
        en = ["Validated market observations are insufficient for a market overview."]
    zh.append(
        f"今日有 {len(focus)} 个新闻主题通过信息价值与证据门槛，"
        f"另有 {len(signals)} 个独立市场信号。"
    )
    en.append(
        f"{len(focus)} news themes cleared the information-value and evidence gates; "
        f"{len(signals)} distinct market signals remain."
    )
    if catalysts:
        zh.append(f"接下来有 {len(catalysts)} 个具备明确触发条件的催化剂值得跟踪。")
        en.append(f"{len(catalysts)} next catalysts have explicit trigger conditions.")
    else:
        zh.append("当前没有得到验证的未来催化剂，不以旧新闻填充。")
        en.append("No future catalyst is validated; stale news is not used as filler.")
    return tuple(zh), tuple(en)


def build_email_intelligence(
    briefing: DailyIntelligenceBrief,
    markets: Iterable[MarketObservation] = (),
    *,
    market_discovery: MarketDiscoveryResult | None = None,
    catalysts: Sequence[ScheduledCatalyst] = (),
    china: ChinaMarketIntelligence | None = None,
) -> EmailIntelligence:
    report_end = _report_end(briefing.report_date)
    current = tuple(
        story
        for story in briefing.primary_stories
        if _is_current(story, report_end) and story.sources
    )
    stale_ids = tuple(story.event_id for story in briefing.primary_stories if story not in current)
    seen: set[str] = set()
    focus: list[EmailFocusItem] = []
    suppressed = 0
    for story in current:
        key = _story_cluster(story)
        if key in seen:
            suppressed += 1
            continue
        seen.add(key)
        focus.append(_focus_item(story))
        if len(focus) == 5:
            break
    market_items = tuple(markets)
    evidence_symbols = {
        reaction.target_symbol
        for story in briefing.primary_stories
        for reaction in story.market_reactions
        if reaction.association_confidence.value in {"HIGH", "MEDIUM"}
    }
    market_signals = (
        _discovered_market_signals(market_discovery, market_items, evidence_symbols)
        if market_discovery is not None
        else _market_signals(market_items, seen, evidence_symbols)
    )
    current_ids = {story.event_id for story in current}
    catalyst_items = (
        _scheduled_catalysts(catalysts) if catalysts else _future_catalysts(briefing, current_ids)
    )
    focus_items = tuple(focus)
    brief_zh, brief_en = _market_brief(market_items, focus_items, market_signals, catalyst_items)
    unresolved = tuple(symbol for signal in market_signals for symbol in signal.symbols)
    return EmailIntelligence(
        focus=focus_items,
        market_signals=market_signals,
        next_catalysts=catalyst_items,
        china=_china_summary(china),
        market_brief_zh=brief_zh,
        market_brief_en=brief_en,
        diagnostics=EmailIntelligenceDiagnostics(
            candidates=len(briefing.primary_stories),
            focus_selected=len(focus),
            clusters_suppressed=suppressed,
            stale_or_demoted_event_ids=stale_ids,
            unresolved_market_symbols=unresolved,
        ),
    )
