from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.briefing import DailyIntelligenceBrief
from radar.domain import content_hash, stable_id
from radar.llm import LLMProvider, LLMRequest, LLMResponse, LLMTimeoutError

C91_RULE_VERSION = "c9.1-zh-CN-v1"
C91_LOCALE: Literal["zh-CN"] = "zh-CN"
C91_LABELS = {
    "localized_briefing": "中文本地化简报",
    "english_reference": "English reference / 英文原文",
    "overnight": "隔夜重点",
    "things_that_matter": "重要事项",
    "why_it_matters": "重要性",
    "market_reaction": "市场反应",
    "under_radar": "雷达下方",
    "watch_today": "今日关注",
    "market_snapshot": "市场快照",
    "no_primary": "没有故事达到今日主要阈值；未填充低价值内容。",
    "no_reaction": "当前没有可验证的关联市场反应。",
    "no_under_radar": "没有其他事项达到保守的次级阈值。",
    "no_watch": "没有推导出需要继续监测的状态。",
    "no_market": "没有可用的已完成市场观察。",
    "association": "关联置信度",
    "importance": "重要性",
    "relevance": "相关性",
    "sources": "来源",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LocalizationPayloadText(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    text: str = Field(min_length=1, pattern=r".*\S.*")


class LocalizationPayloadStory(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    why_it_matters: str = Field(min_length=1, pattern=r".*\S.*")
    event_type: str = Field(min_length=1, pattern=r".*\S.*")
    region: str | None = None
    entity_ids: tuple[str, ...]


class LocalizationPayloadReaction(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    summary: str = Field(min_length=1, pattern=r".*\S.*")
    uncertainty: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    percentage_move: float
    direction: Literal["UP", "DOWN", "FLAT"]


class LocalizationPayloadUnderRadar(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    note: str = Field(min_length=1, pattern=r".*\S.*")


class LocalizationPayload(_StrictModel):
    locale: Literal["zh-CN"]
    briefing_id: str = Field(min_length=1, pattern=r".*\S.*")
    briefing_rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    overnight: tuple[LocalizationPayloadText, ...]
    primary_stories: tuple[LocalizationPayloadStory, ...]
    market_reactions: tuple[LocalizationPayloadReaction, ...]
    under_radar: tuple[LocalizationPayloadUnderRadar, ...]
    watch_today: tuple[LocalizationPayloadText, ...]
    market_names: tuple[LocalizationPayloadText, ...]


class LocalizedText(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    text: str = Field(min_length=1, pattern=r".*\S.*")


class LocalizedStory(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    why_it_matters: str = Field(min_length=1, pattern=r".*\S.*")


class LocalizedReaction(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    summary: str = Field(min_length=1, pattern=r".*\S.*")
    uncertainty: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")


class LocalizedUnderRadar(_StrictModel):
    key: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    note: str = Field(min_length=1, pattern=r".*\S.*")


class LocalizedBriefingContent(_StrictModel):
    locale: Literal["zh-CN"]
    briefing_id: str = Field(min_length=1, pattern=r".*\S.*")
    briefing_rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    overnight: tuple[LocalizedText, ...]
    primary_stories: tuple[LocalizedStory, ...]
    market_reactions: tuple[LocalizedReaction, ...]
    under_radar: tuple[LocalizedUnderRadar, ...]
    watch_today: tuple[LocalizedText, ...]
    market_names: tuple[LocalizedText, ...]


class LocalizationUsage(_StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class LocalizationSnapshot(_StrictModel):
    id: str = Field(min_length=1, pattern=r".*\S.*")
    briefing_id: str = Field(min_length=1, pattern=r".*\S.*")
    run_id: str = Field(min_length=1, pattern=r".*\S.*")
    view_id: str = Field(min_length=1, pattern=r".*\S.*")
    briefing_rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    locale: Literal["zh-CN"]
    localization_rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    requested_model: str = Field(min_length=1, pattern=r".*\S.*")
    returned_model: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    provider: str = Field(min_length=1, pattern=r".*\S.*")
    usage: LocalizationUsage
    latency_seconds: float = Field(ge=0)
    created_at: datetime
    payload_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    content: LocalizedBriefingContent

    @model_validator(mode="after")
    def validate_identity(self) -> LocalizationSnapshot:
        expected = stable_id(
            "c9-localization",
            f"{self.briefing_id}|{self.locale}|{self.localization_rule_version}|{self.requested_model}",
        )
        if self.id != expected:
            raise ValueError("C9.1 localization identity must be deterministic")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("C9.1 localization time must be timezone-aware")
        if self.content.briefing_id != self.briefing_id:
            raise ValueError("C9.1 localized content briefing identity is inconsistent")
        if self.content.locale != self.locale:
            raise ValueError("C9.1 localized content locale is inconsistent")
        return self


def serialize_localization_snapshot(snapshot: LocalizationSnapshot) -> str:
    return json.dumps(
        snapshot.model_dump(mode="json", exclude={"payload_hash"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class LocalizationDiagnostics(_StrictModel):
    enabled: bool
    locale: str
    requested_model: str | None = None
    returned_model: str | None = None
    provider: str | None = None
    status: str
    request_count: int = Field(ge=0, le=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float = Field(ge=0)
    snapshot_reused: bool
    failure_category: str | None = None
    failure_phase: str | None = None


@dataclass(frozen=True)
class LocalizationResult:
    content: LocalizedBriefingContent | None
    response: LLMResponse | None
    diagnostics: LocalizationDiagnostics


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate localization JSON key: {key}")
        result[key] = value
    return result


def _loads_strict(raw: str) -> Any:
    if "```" in raw:
        raise ValueError("localization response contains a Markdown fence")
    try:
        return json.loads(raw, object_pairs_hook=_duplicate_rejecting_pairs)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("localization response must be one valid JSON value") from exc


def _strict_sequence_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON arrays to immutable tuples before strict model validation."""
    sequence_fields = (
        "overnight",
        "primary_stories",
        "market_reactions",
        "under_radar",
        "watch_today",
        "market_names",
    )
    return {
        **value,
        **{
            name: tuple(value[name])
            for name in sequence_fields
            if name in value and isinstance(value[name], list)
        },
    }


def _key(prefix: str, value: str) -> str:
    return f"{prefix}:{value}"


def build_localization_payload(briefing: DailyIntelligenceBrief) -> LocalizationPayload:
    return LocalizationPayload(
        locale=C91_LOCALE,
        briefing_id=briefing.id,
        briefing_rule_version=briefing.rule_version,
        overnight=tuple(
            LocalizationPayloadText(key=_key("overnight", item.event_id), text=item.text)
            for item in briefing.overnight
        ),
        primary_stories=tuple(
            LocalizationPayloadStory(
                key=_key("story", item.event_id),
                headline=item.headline,
                why_it_matters=item.why_it_matters,
                event_type=item.event_type.value,
                region=item.region,
                entity_ids=item.entity_ids,
            )
            for item in briefing.primary_stories
        ),
        market_reactions=tuple(
            LocalizationPayloadReaction(
                key=_key("reaction", item.impact_id),
                summary=item.summary,
                uncertainty=item.uncertainty,
                percentage_move=item.percentage_move,
                direction=(
                    "UP"
                    if item.percentage_move > 0
                    else "DOWN"
                    if item.percentage_move < 0
                    else "FLAT"
                ),
            )
            for item in briefing.market_reactions
        ),
        under_radar=tuple(
            LocalizationPayloadUnderRadar(
                key=_key("under-radar", item.event_id),
                headline=item.headline,
                note=item.note,
            )
            for item in briefing.under_radar
        ),
        watch_today=tuple(
            LocalizationPayloadText(key=_key("watch", item.event_id), text=item.text)
            for item in briefing.watch_today
        ),
        market_names=tuple(
            LocalizationPayloadText(key=_key("market", item.symbol), text=item.name)
            for group in briefing.market_snapshot
            for item in group.items
        ),
    )


def build_localization_prompt(payload: LocalizationPayload) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return (
        "You are the Chinese localization editor for Global Investment Radar.\n"
        "Convert the already-decided evidence-backed English briefing below into fluent, "
        "concise Simplified Chinese financial-intelligence writing.\n"
        "You are not performing new research or analysis. The supplied payload is the "
        "complete authorized factual universe. Do not use external knowledge, browse, add "
        "facts, infer causes, change numbers, dates, entities, ordering, market moves, "
        "confidence or uncertainty, or add investment recommendations.\n"
        "Return only one JSON object matching the required response schema. No Markdown "
        "fences and no prose before or after JSON. Preserve every key exactly once and "
        "in the supplied order. Preserve exact factual values: June 2026 may become "
        "2026年6月, and $16 million may become 1600万美元. Do not change years, dates, "
        "currency, amount, percentages, symbols, market direction, or uncertainty.\n"
        "Use natural professional Chinese financial-news language, not literal translation, "
        "bureaucratic wording, sensational language, or social-media slang. For market "
        "reaction, say observed/associated rather than caused/driven. Never add advice.\n"
        "Payload:\n"
        f"{serialized}"
    )


def _ordered_keys(items: tuple[Any, ...]) -> list[str]:
    return [str(item.key) for item in items]


def _validate_keys(content: LocalizedBriefingContent, payload: LocalizationPayload) -> None:
    checks = (
        ("overnight", _ordered_keys(payload.overnight), _ordered_keys(content.overnight)),
        (
            "primary_stories",
            _ordered_keys(payload.primary_stories),
            _ordered_keys(content.primary_stories),
        ),
        (
            "market_reactions",
            _ordered_keys(payload.market_reactions),
            _ordered_keys(content.market_reactions),
        ),
        ("under_radar", _ordered_keys(payload.under_radar), _ordered_keys(content.under_radar)),
        ("watch_today", _ordered_keys(payload.watch_today), _ordered_keys(content.watch_today)),
        ("market_names", _ordered_keys(payload.market_names), _ordered_keys(content.market_names)),
    )
    for name, expected, actual in checks:
        if actual != expected:
            raise ValueError(f"localization {name} key coverage or order is invalid")


_NUMBER = re.compile(r"(?<![\d.])[-+]?\d[\d,]*(?:\.\d+)?(?![\d.])")
_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_EN_MONTHS = {
    **{name: index for index, name in enumerate(_MONTH_NAMES, start=1)},
    **{name[:3]: index for index, name in enumerate(_MONTH_NAMES, start=1)},
    "sept": 9,
}
_EN_DATE = re.compile(
    r"\b(" + "|".join(sorted(_EN_MONTHS, key=len, reverse=True))
    + r")\.?\s+(?:(\d{1,2}),?\s+)?(\d{4})\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_ZH_DATE = re.compile(r"(?<!\d)(\d{4})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?")
_EN_CURRENCY = re.compile(
    r"([$€£¥])\s*(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion)?", re.I
)
_ZH_CURRENCY = re.compile(
    r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(万|亿)?\s*(美元|美金|欧元|英镑)"
)
_PERCENT = re.compile(r"(?<!\d)([+-]?)(\d[\d,]*(?:\.\d+)?)\s*[%％]")
_EXPLICIT_SYMBOL = re.compile(
    r"(?<![A-Za-z0-9])(?:\$[A-Z]{1,6}|\^[A-Z0-9]{2,10}|[A-Z]{2,8}:[A-Z0-9.]{1,12})(?![A-Za-z0-9])"
)
_CAUSAL_PHRASES = (
    "受该消息推动",
    "受此消息推动",
    "因此上涨",
    "因此下跌",
    "投资者因此买入",
    "投资者因此抛售",
    "导致",
    "引发",
    "推动了",
    "造成",
)
_ADVICE_PHRASES = (
    "建议买入",
    "建议卖出",
    "应该加仓",
    "应该减仓",
    "抄底",
    "止盈",
    "目标价",
    "强烈看多",
    "强烈看空",
)


def _extract_dates(text: str) -> tuple[Counter[tuple[int, int, int | None]], str]:
    dates: Counter[tuple[int, int, int | None]] = Counter()
    remaining = text
    for pattern in (_EN_DATE, _ISO_DATE, _ZH_DATE):
        def consume(match: re.Match[str], english: bool = pattern is _EN_DATE) -> str:
            if english:
                year = int(match.group(3))
                month = _EN_MONTHS[match.group(1).casefold()]
                day = int(match.group(2)) if match.group(2) else None
            else:
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3)) if match.group(3) else None
            date(year, month, day or 1)
            dates[(year, month, day)] += 1
            return " "
        remaining = pattern.sub(consume, remaining)
    return dates, remaining


def _extract_currency(text: str) -> tuple[Counter[tuple[str, Decimal]], str]:
    amounts: Counter[tuple[str, Decimal]] = Counter()
    en_scale = {None: 1, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
    zh_scale = {None: 1, "万": 10_000, "亿": 100_000_000}
    currency_codes = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "YEN_OR_YUAN"}
    zh_currency_codes = {"美元": "USD", "美金": "USD", "欧元": "EUR", "英镑": "GBP"}

    def consume_en(match: re.Match[str]) -> str:
        amount = Decimal(match.group(2).replace(",", ""))
        unit = match.group(3)
        scale = en_scale[unit.casefold() if unit else None]
        amounts[(currency_codes[match.group(1)], amount * scale)] += 1
        return " "

    def consume_zh(match: re.Match[str]) -> str:
        amount = Decimal(match.group(1).replace(",", ""))
        amounts[(zh_currency_codes[match.group(3)], amount * zh_scale[match.group(2)])] += 1
        return " "

    remaining = _EN_CURRENCY.sub(consume_en, text)
    remaining = _ZH_CURRENCY.sub(consume_zh, remaining)
    return amounts, remaining


def _extract_percentages(text: str) -> tuple[list[tuple[Decimal, str]], str]:
    percentages: list[tuple[Decimal, str]] = []
    def consume(match: re.Match[str]) -> str:
        percentages.append((Decimal(match.group(2).replace(",", "")), match.group(1)))
        return " "
    return percentages, _PERCENT.sub(consume, text)


def _validate_factual_atoms(source: str, translated: str) -> None:
    if Counter(_EXPLICIT_SYMBOL.findall(source)) != Counter(_EXPLICIT_SYMBOL.findall(translated)):
        raise ValueError("localized factual symbol is missing or changed")
    source_dates, source_remaining = _extract_dates(source)
    translated_dates, translated_remaining = _extract_dates(translated)
    if source_dates != translated_dates:
        raise ValueError("localized factual date is missing or changed")
    source_currency, source_remaining = _extract_currency(source_remaining)
    translated_currency, translated_remaining = _extract_currency(translated_remaining)
    if source_currency != translated_currency:
        raise ValueError("localized factual currency amount is missing or changed")
    source_pct, source_remaining = _extract_percentages(source_remaining)
    translated_pct, translated_remaining = _extract_percentages(translated_remaining)
    if len(source_pct) != len(translated_pct):
        raise ValueError("localized factual percentage is missing or changed")
    unmatched = translated_pct.copy()
    for amount, direction in source_pct:
        candidate = next(
            (
                item for item in unmatched
                if item[0] == amount and (not item[1] or item[1] == direction)
            ),
            None,
        )
        if candidate is None:
            raise ValueError("localized factual percentage is missing or changed")
        unmatched.remove(candidate)
        if direction == "+" and any(term in translated for term in ("下跌", "下降", "走低")):
            raise ValueError("localized factual percentage direction is changed")
        if direction == "-" and any(term in translated for term in ("上涨", "上升", "走高")):
            raise ValueError("localized factual percentage direction is changed")
    source_numbers = Counter(
        Decimal(number.replace(",", "")) for number in _NUMBER.findall(source_remaining)
    )
    translated_numbers = Counter(
        Decimal(number.replace(",", "")) for number in _NUMBER.findall(translated_remaining)
    )
    if source_numbers != translated_numbers:
        raise ValueError("localized factual numeric atom is missing or changed")


def _all_localized_text(content: LocalizedBriefingContent) -> list[str]:
    values: list[str] = []
    values.extend(item.text for item in content.overnight)
    for story in content.primary_stories:
        values.extend((story.headline, story.why_it_matters))
    for reaction in content.market_reactions:
        values.append(reaction.summary)
        if reaction.uncertainty:
            values.append(reaction.uncertainty)
    for under_radar in content.under_radar:
        values.extend((under_radar.headline, under_radar.note))
    values.extend(watch.text for watch in content.watch_today)
    values.extend(market_name.text for market_name in content.market_names)
    return values


def validate_localized_content(
    content: LocalizedBriefingContent, briefing: DailyIntelligenceBrief
) -> None:
    payload = build_localization_payload(briefing)
    if content.locale != C91_LOCALE or content.briefing_id != briefing.id:
        raise ValueError("localized content briefing identity is invalid")
    if content.briefing_rule_version != briefing.rule_version:
        raise ValueError("localized content rule version is invalid")
    _validate_keys(content, payload)

    for overnight_source, overnight_translated in zip(
        payload.overnight, content.overnight, strict=True
    ):
        _validate_factual_atoms(overnight_source.text, overnight_translated.text)
    for story_source, story_translated in zip(
        payload.primary_stories, content.primary_stories, strict=True
    ):
        _validate_factual_atoms(story_source.headline, story_translated.headline)
        _validate_factual_atoms(story_source.why_it_matters, story_translated.why_it_matters)
    for reaction_source, reaction_translated in zip(
        payload.market_reactions, content.market_reactions, strict=True
    ):
        _validate_factual_atoms(reaction_source.summary, reaction_translated.summary)
        if (reaction_source.uncertainty is None) != (reaction_translated.uncertainty is None):
            raise ValueError("localized reaction uncertainty presence changed")
        if reaction_source.uncertainty and reaction_translated.uncertainty:
            _validate_factual_atoms(reaction_source.uncertainty, reaction_translated.uncertainty)
        if reaction_source.direction == "UP" and not any(
            term in reaction_translated.summary.casefold()
            for term in ("上涨", "上升", "走高", "rose")
        ):
            raise ValueError("localized reaction direction changed")
        if reaction_source.direction == "DOWN" and not any(
            term in reaction_translated.summary.casefold()
            for term in ("下跌", "下降", "走低", "fell")
        ):
            raise ValueError("localized reaction direction changed")
        if reaction_source.direction == "FLAT" and not any(
            term in reaction_translated.summary.casefold()
            for term in ("持平", "不变", "unchanged")
        ):
            raise ValueError("localized reaction direction changed")
        reaction_text = " ".join(
            item for item in (reaction_translated.summary, reaction_translated.uncertainty) if item
        )
        if any(phrase in reaction_text for phrase in _CAUSAL_PHRASES):
            raise ValueError("localized market reaction contains unsupported causal language")

    for radar_source, radar_translated in zip(
        payload.under_radar, content.under_radar, strict=True
    ):
        _validate_factual_atoms(radar_source.headline, radar_translated.headline)
        _validate_factual_atoms(radar_source.note, radar_translated.note)
    for watch_source, watch_translated in zip(
        payload.watch_today, content.watch_today, strict=True
    ):
        _validate_factual_atoms(watch_source.text, watch_translated.text)

    if any(phrase in text for text in _all_localized_text(content) for phrase in _ADVICE_PHRASES):
        raise ValueError("localized content contains unauthorized investment advice")


def validate_localization_snapshot(
    snapshot: LocalizationSnapshot, briefing: DailyIntelligenceBrief
) -> None:
    if (
        snapshot.briefing_id != briefing.id
        or snapshot.run_id != briefing.run_id
        or snapshot.view_id != briefing.view_id
        or snapshot.briefing_rule_version != briefing.rule_version
    ):
        raise ValueError("persisted localization snapshot identity is inconsistent")
    validate_localized_content(snapshot.content, briefing)


def parse_localization_response(
    raw: str, payload: LocalizationPayload, briefing: DailyIntelligenceBrief | None = None
) -> LocalizedBriefingContent:
    try:
        value = _loads_strict(raw)
        if not isinstance(value, dict):
            raise ValueError("localization response must be a JSON object")
        content = LocalizedBriefingContent.model_validate(_strict_sequence_fields(value))
        if briefing is not None:
            validate_localized_content(content, briefing)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("localization response does not match the strict schema") from exc
    if content.locale != payload.locale or content.briefing_id != payload.briefing_id:
        raise ValueError("localized response identity does not match payload")
    _validate_keys(content, payload)
    return content


def _zero_diagnostics(
    *, enabled: bool, locale: str, requested_model: str | None, status: str, failure: str | None
) -> LocalizationDiagnostics:
    return LocalizationDiagnostics(
        enabled=enabled,
        locale=locale,
        requested_model=requested_model,
        status=status,
        request_count=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_seconds=0,
        snapshot_reused=False,
        failure_category=failure,
    )


def localize_briefing(
    briefing: DailyIntelligenceBrief,
    provider: LLMProvider | None,
    *,
    enabled: bool,
    requested_model: str | None,
    max_output_tokens: int | None = None,
) -> LocalizationResult:
    if not enabled:
        return LocalizationResult(
            content=None,
            response=None,
            diagnostics=_zero_diagnostics(
                enabled=False,
                locale=C91_LOCALE,
                requested_model=requested_model,
                status="disabled",
                failure=None,
            ),
        )
    if provider is None or requested_model is None or not requested_model.strip():
        return LocalizationResult(
            content=None,
            response=None,
            diagnostics=_zero_diagnostics(
                enabled=True,
                locale=C91_LOCALE,
                requested_model=requested_model,
                status="failed",
                failure="configuration",
            ),
        )
    payload = build_localization_payload(briefing)
    started = time.perf_counter()
    try:
        response = provider.generate(
            LLMRequest(
                prompt=build_localization_prompt(payload),
                max_output_tokens=max_output_tokens,
            )
        )
    except Exception as exc:
        elapsed = max(0.0, time.perf_counter() - started)
        failure_phase = exc.phase if isinstance(exc, LLMTimeoutError) else None
        return LocalizationResult(
            content=None,
            response=None,
            diagnostics=LocalizationDiagnostics(
                enabled=True,
                locale=C91_LOCALE,
                requested_model=requested_model,
                provider=provider.name,
                status="failed",
                request_count=1,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                latency_seconds=elapsed,
                snapshot_reused=False,
                failure_category=type(exc).__name__,
                failure_phase=failure_phase,
            ),
        )
    usage = response.usage
    elapsed = max(0.0, time.perf_counter() - started)
    if response.requested_model != requested_model:
        return LocalizationResult(
            content=None,
            response=response,
            diagnostics=LocalizationDiagnostics(
                enabled=True,
                locale=C91_LOCALE,
                requested_model=requested_model,
                returned_model=response.returned_model,
                provider=response.provider,
                status="failed",
                request_count=1,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=usage.output_tokens if usage is not None else None,
                total_tokens=usage.total_tokens if usage is not None else None,
                latency_seconds=elapsed,
                snapshot_reused=False,
                failure_category="identity",
            ),
        )
    try:
        content = parse_localization_response(response.content, payload, briefing)
    except Exception as exc:
        return LocalizationResult(
            content=None,
            response=response,
            diagnostics=LocalizationDiagnostics(
                enabled=True,
                locale=C91_LOCALE,
                requested_model=response.requested_model,
                returned_model=response.returned_model,
                provider=response.provider,
                status="failed",
                request_count=1,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=usage.output_tokens if usage is not None else None,
                total_tokens=usage.total_tokens if usage is not None else None,
                latency_seconds=elapsed,
                snapshot_reused=False,
                failure_category=type(exc).__name__,
            ),
        )
    return LocalizationResult(
        content=content,
        response=response,
        diagnostics=LocalizationDiagnostics(
            enabled=True,
            locale=C91_LOCALE,
            requested_model=response.requested_model,
            returned_model=response.returned_model,
            provider=response.provider,
            status="success",
            request_count=1,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            total_tokens=usage.total_tokens if usage is not None else None,
            latency_seconds=elapsed,
            snapshot_reused=False,
        ),
    )


def build_localization_snapshot(
    briefing: DailyIntelligenceBrief,
    result: LocalizationResult,
    *,
    created_at: datetime,
) -> LocalizationSnapshot:
    if result.content is None or result.response is None:
        raise ValueError("cannot persist an unsuccessful localization")
    response = result.response
    if result.diagnostics.requested_model != response.requested_model:
        raise ValueError("cannot persist localization with inconsistent model identity")
    usage = response.usage
    requested_model = response.requested_model
    localization_id = stable_id(
        "c9-localization",
        f"{briefing.id}|{C91_LOCALE}|{C91_RULE_VERSION}|{requested_model}",
    )
    content = result.content
    snapshot = LocalizationSnapshot(
        id=localization_id,
        briefing_id=briefing.id,
        run_id=briefing.run_id,
        view_id=briefing.view_id,
        briefing_rule_version=briefing.rule_version,
        locale=C91_LOCALE,
        localization_rule_version=C91_RULE_VERSION,
        requested_model=requested_model,
        returned_model=response.returned_model,
        provider=response.provider,
        usage=LocalizationUsage(
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            total_tokens=usage.total_tokens if usage is not None else None,
        ),
        latency_seconds=result.diagnostics.latency_seconds,
        created_at=created_at.astimezone(UTC),
        payload_hash="0" * 64,
        content=content,
    )
    return snapshot.model_copy(
        update={"payload_hash": content_hash(serialize_localization_snapshot(snapshot))}
    )
