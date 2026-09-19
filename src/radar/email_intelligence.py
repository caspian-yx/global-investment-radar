from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.briefing import BriefingSource, BriefingStoryCard, DailyIntelligenceBrief
from radar.domain import MarketObservation

C8_EMAIL_RULE_VERSION = "c8-email-v1"
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
    market_reaction: str | None = None
    unknown: str = Field(min_length=1)
    sources: tuple[BriefingSource, ...] = ()


class EmailMarketSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    cluster_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    symbols: tuple[str, ...] = Field(min_length=1)
    observed: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    unknown: str = Field(min_length=1)


class EmailCatalyst(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    trigger: str = Field(min_length=1)


class EmailIntelligenceDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidates: int = Field(ge=0)
    focus_selected: int = Field(ge=0)
    clusters_suppressed: int = Field(ge=0)
    stale_or_demoted_event_ids: tuple[str, ...]
    unresolved_market_symbols: tuple[str, ...]
    rule_version: str = C8_EMAIL_RULE_VERSION


class EmailIntelligence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    focus: tuple[EmailFocusItem, ...] = Field(max_length=5)
    market_signals: tuple[EmailMarketSignal, ...]
    next_catalysts: tuple[EmailCatalyst, ...] = Field(max_length=5)
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


def _market_reaction(story: BriefingStoryCard) -> str | None:
    if not story.market_reactions:
        return None
    return "；".join(
        f"{item.target_name} {item.percentage_move:+.2f}%（观察到相关反应，非因果结论）"
        for item in story.market_reactions
    )


def _focus_item(story: BriefingStoryCard) -> EmailFocusItem:
    reaction = _market_reaction(story)
    unknown = (
        "市场反应已有验证记录，但驱动与因果边界仍需后续证据确认。"
        if reaction
        else "尚无足够证据确认直接市场影响或完整传导路径。"
    )
    return EmailFocusItem(
        cluster_key=_story_cluster(story),
        event_id=story.event_id,
        headline=story.headline,
        what_happened=story.what_happened,
        why_it_matters=story.why_it_matters,
        affected=_affected(story),
        market_reaction=reaction,
        unknown=unknown,
        sources=story.sources,
    )


def _market_signals(
    markets: tuple[MarketObservation, ...], occupied: set[str]
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
                title="美国科技股相对韧性",
                symbols=("^NDX", "^DJI"),
                observed=(
                    f"Nasdaq 100 {ndx.change_pct:+.2f}%；"
                    f"Dow Jones {dow.change_pct:+.2f}%"
                ),
                interpretation="科技股相对传统蓝筹更强，属于确定性指数分化观察。",
                unknown="分化原因仍需结合利率、行业与事件证据判断。",
            )
        )
    if volatility and "market:volatility" not in occupied:
        ordered = tuple(sorted(volatility, key=lambda item: item.symbol))
        result.append(
            EmailMarketSignal(
                cluster_key="market:volatility",
                title="波动率共同变化",
                symbols=tuple(item.symbol for item in ordered),
                observed="；".join(
                    f"{item.name} {item.change_pct:+.2f}%" for item in ordered
                ),
                interpretation="波动率指标正在共同重定价风险，但方向本身不证明具体驱动。",
                unknown="尚无足够证据确认单一驱动。",
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
                title=f"{item.name} 异常波动",
                symbols=(item.symbol,),
                observed=f"{item.name} {item.change_pct:+.2f}%",
                interpretation="该变动已达到异常波动阈值，应优先寻找可靠证据。",
                unknown="尚无足够证据确认主要驱动。",
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
        result.append(
            EmailCatalyst(event_id=item.event_id, title=item.headline, trigger=item.text)
        )
    return tuple(result[:5])


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
) -> EmailIntelligence:
    report_end = _report_end(briefing.report_date)
    current = tuple(
        story
        for story in briefing.primary_stories
        if _is_current(story, report_end) and story.sources
    )
    stale_ids = tuple(
        story.event_id for story in briefing.primary_stories if story not in current
    )
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
    market_signals = _market_signals(market_items, seen)
    current_ids = {story.event_id for story in current}
    catalysts = _future_catalysts(briefing, current_ids)
    focus_items = tuple(focus)
    brief_zh, brief_en = _market_brief(
        market_items, focus_items, market_signals, catalysts
    )
    unresolved = tuple(
        symbol for signal in market_signals for symbol in signal.symbols
    )
    return EmailIntelligence(
        focus=focus_items,
        market_signals=market_signals,
        next_catalysts=catalysts,
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
