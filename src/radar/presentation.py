"""Human-readable presentation labels; canonical intelligence is never rewritten here."""

from __future__ import annotations

import re

_LABELS: dict[str, tuple[str, str]] = {
    "Global": ("全球市场", "Global markets"),
    "AI / Semiconductors": ("AI / 半导体", "AI / Semiconductors"),
    "DETERMINISTIC_SIGNAL": ("依据市场数据识别", "Observed market signal"),
    "OBSERVED_FACT": ("已验证事实", "Validated observation"),
    "EVIDENCE_BACKED_INTERPRETATION": ("有证据支持的解读", "Evidence-backed interpretation"),
    "DRIVER_UNRESOLVED": ("主要驱动未确认", "Driver unresolved"),
    "EXCEPTIONAL_MOVE": ("异常波动", "Exceptional move"),
    "US_TECH_RELATIVE_STRENGTH": ("美国科技股相对韧性", "US technology relative strength"),
    "TECH_RISK_CONCENTRATION_CANDIDATE": (
        "波动率变化不均",
        "Uneven volatility shift",
    ),
    "INFLATION_PRESSURE_CANDIDATE": ("能源与利率同向", "Oil and rates moving together"),
    "FRESH": ("新近信息", "Fresh information"),
    "AGING": ("时效下降", "Aging information"),
    "STALE": ("已过主标题时效", "Outside headline window"),
    "DEMOTE_SECONDARY": ("降为次要进展", "Moved to secondary"),
    "RETAIN_PRIMARY": ("保留为主要进展", "Retained as primary"),
    "RETAIN_WITH_DECAY": ("保留并提示时效", "Retained with freshness caution"),
    "MUST_READ": ("必读", "Must read"),
    "IMPORTANT": ("重要", "Important"),
    "WATCH": ("关注", "Watch"),
    "BACKGROUND": ("背景", "Background"),
    "HEALTHY": ("数据覆盖正常", "Data coverage healthy"),
    "DEGRADED": ("部分数据源受限", "Some data sources limited"),
    "NO_DATA": ("本期无新数据", "No new data this period"),
    "INSUFFICIENT": ("数据不足，报告不可投递", "Insufficient data for delivery"),
    "UNKNOWN": ("状态待确认", "Status unavailable"),
    "enabled": ("已启用", "Enabled"),
    "disabled": ("未启用", "Disabled"),
    "partial": ("部分可用", "Partially available"),
    "failed": ("未完成", "Unavailable"),
    "BALANCED · NO DOMINANT REGIME": (
        "市场信号均衡，暂无主导主线",
        "Balanced signals; no dominant market theme",
    ),
    "CAUTIOUS · RATES-LED · NOT PANIC": (
        "市场偏谨慎，利率变化较突出，尚非恐慌状态",
        "Cautious, rates-led, not a panic regime",
    ),
    "CAUTIOUS · MIXED DRIVERS · NOT PANIC": (
        "市场偏谨慎，多种因素交织，尚非恐慌状态",
        "Cautious, mixed drivers, not a panic regime",
    ),
    "UPTREND": ("近期上行", "Recent uptrend"),
    "DOWNTREND": ("近期下行", "Recent downtrend"),
    "MIXED": ("走势交错", "Mixed direction"),
    "latest observation": ("最近一个交易日", "Latest observation"),
    "core indices": ("核心指数", "Core indices"),
    "sector ranking": ("行业排名", "Sector ranking"),
    "market breadth": ("涨跌广度", "Market breadth"),
    "turnover": ("成交额", "Turnover"),
    "leadership": ("领涨结构", "Leadership"),
}

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def presentation_label(value: object, *, zh: bool) -> str:
    """Map an internal label without leaking unknown machine-style codes."""

    raw = str(value)
    mapped = _LABELS.get(raw)
    if mapped is not None:
        return mapped[0 if zh else 1]
    if _CODE_PATTERN.fullmatch(raw):
        return "状态待确认" if zh else "Status unavailable"
    return raw


def presentation_copy(value: object, *, zh: bool) -> str:
    """Translate system-generated explanatory copy, not source titles or proper nouns."""

    raw = str(value)
    if raw.startswith("Deterministic market context: "):
        regime = raw.removeprefix("Deterministic market context: ").removesuffix(".")
        label = presentation_label(regime, zh=zh)
        return f"当前市场状态：{label}。" if zh else f"Market context: {label}."
    if raw.startswith("Monitor whether further official or company updates clarify or extend: "):
        title = raw.removeprefix(
            "Monitor whether further official or company updates clarify or extend: "
        )
        return f"关注官方或公司后续信息能否澄清或延续：{title}" if zh else raw
    if not zh:
        if raw.startswith("异常波动：") and raw.endswith("。驱动仅在有证据时确认。"):
            moves = raw.removeprefix("异常波动：").removesuffix("。驱动仅在有证据时确认。")
            return f"Abnormal moves: {moves}. Driver confirmation requires evidence."
        if raw.startswith("跨资产观察："):
            return (
                "Cross-asset observations are present; see the linked market signals for details."
            )
        if re.search(r"[\u4e00-\u9fff]", raw):
            return "Additional system-generated explanation is unavailable in English."
    fixed = {
        "No new information gain in the Day+1 aging window.": (
            "隔日观察窗口内没有新的实质信息。",
            "No material update in the next-day window.",
        ),
        "Story age exceeds the headline window without a material follow-up.": (
            "超过主标题时效，且没有实质后续进展。",
            "Outside the headline window without a material follow-up.",
        ),
        "New observation within 24h.": (
            "24 小时内出现新信息。",
            "New observation within 24 hours.",
        ),
        "Recent corroboration is present within the 48h aging window.": (
            "48 小时观察窗口内有新的佐证。",
            "Recent corroboration within the 48-hour window.",
        ),
        "Further official or company evidence clarifies or extends the item.": (
            "官方或公司提供了进一步的可核验证据。",
            "Further official or company evidence clarifies the item.",
        ),
        "No new evidence appears in the next observation window.": (
            "下一观察窗口内没有新的可核验证据。",
            "No new evidence in the next observation window.",
        ),
        "No new information gain.": ("没有新的实质信息。", "No new material information."),
        "Low-information explanation rejected; driver unresolved.": (
            "现有解释信息不足，主要驱动仍未确认。",
            "The explanation lacks evidence; the driver remains unresolved.",
        ),
        "AI synthesis unavailable: validated market observations were not supplied.": (
            "已验证市场观察不足，暂无法形成市场综合简报。",
            "Insufficient validated observations for a market synthesis.",
        ),
    }
    mapped = fixed.get(raw)
    if mapped is not None:
        return mapped[0 if zh else 1]
    return presentation_label(raw, zh=zh)


def coverage_gap_copy(code: str, symbol: str | None, reason: str, *, zh: bool) -> str:
    """Explain a missing capability without exposing the diagnostic code."""

    if not zh:
        return reason
    names = {
        "MISSING_VIX": "VIX 风险序列暂不可用。",
        "MISSING_VXN": "VXN 风险序列暂不可用。",
        "MISSING_US2Y": "美国 2 年期国债收益率暂不可用。",
        "MISSING_US10Y": "美国 10 年期国债收益率暂不可用。",
        "MISSING_WTI": "WTI 原油价格暂不可用。",
        "VOLATILITY_COVERAGE_GAP": "已有 VIX，但缺少 VXN 的对照数据。",
        "UNEXPLAINED_MAJOR_MOVE": f"{symbol or '市场'} 出现显著波动，尚无已验证的原因。",
    }
    return names.get(code, "部分市场数据或解释暂不可用。")
