from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from radar.domain import (
    AssetClass,
    Claim,
    ClaimType,
    DeterministicSummary,
    Event,
    EventType,
    Evidence,
    MacroObservation,
    MarketObservation,
    ProviderRunStatus,
    ProviderStatus,
    ReportQualityAssessment,
    ReportQualityStatus,
    SourceDocument,
    SourceKind,
    SourceTier,
    content_hash,
    stable_id,
)

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
EVENT_RULES: list[tuple[EventType, tuple[str, ...]]] = [
    (EventType.EARNINGS, ("earnings", "revenue", "eps", "margin", "profit")),
    (EventType.GUIDANCE, ("guidance", "outlook", "forecast", "raises", "cuts")),
    (EventType.M_AND_A, ("acquire", "acquisition", "merger", "takeover")),
    (EventType.POLICY, ("rate", "fomc", "central bank", "policy", "regulation")),
    (EventType.MACRO, ("inflation", "cpi", "employment", "gdp", "pce", "payroll")),
    (EventType.SUPPLY_CHAIN, ("supply", "shortage", "inventory", "capacity")),
    (EventType.PRODUCT, ("launch", "product", "approval", "trial result")),
    (EventType.LEGAL, ("sec", "lawsuit", "fine", "enforcement", "investigation")),
    (EventType.RISK, ("war", "tariff", "sanction", "default", "bankruptcy")),
]
TIER_POINTS = {SourceTier.A: 30.0, SourceTier.B: 22.0, SourceTier.C: 12.0, SourceTier.D: 4.0}
TYPE_POINTS = {
    EventType.MARKET_MOVE: 20.0,
    EventType.EARNINGS: 20.0,
    EventType.GUIDANCE: 24.0,
    EventType.M_AND_A: 24.0,
    EventType.POLICY: 22.0,
    EventType.MACRO: 20.0,
    EventType.SUPPLY_CHAIN: 16.0,
    EventType.PRODUCT: 14.0,
    EventType.LEGAL: 18.0,
    EventType.RISK: 22.0,
    EventType.OTHER: 8.0,
}
MARKET_THRESHOLDS = {
    AssetClass.EQUITY_INDEX: (2.0, 4.0),
    AssetClass.EQUITY_ETF_PROXY: (2.0, 4.0),
    AssetClass.FX: (1.0, 2.0),
    AssetClass.COMMODITY: (2.0, 4.0),
    AssetClass.CRYPTO: (5.0, 10.0),
}
NOTABLE_MARKET_IMPORTANCE = 55.0
HIGH_MARKET_IMPORTANCE = 75.0


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMS and not key.casefold().startswith("utm_")
    ]
    return urlunsplit((parts.scheme, parts.netloc.casefold(), parts.path, urlencode(query), ""))


def title_similarity(left: str, right: str) -> float:
    a, b = set(normalize_title(left).split()), set(normalize_title(right).split())
    return len(a & b) / len(a | b) if a and b else 0.0


def cluster_documents(documents: list[SourceDocument]) -> list[list[SourceDocument]]:
    exact_seen: set[tuple[str, str]] = set()
    unique: list[SourceDocument] = []
    for document in sorted(documents, key=lambda item: item.published_at, reverse=True):
        key = (canonicalize_url(document.canonical_url), document.content_hash)
        if key not in exact_seen:
            exact_seen.add(key)
            unique.append(document)

    clusters: list[list[SourceDocument]] = []
    for document in unique:
        for cluster in clusters:
            anchor = cluster[0]
            hours = abs((document.published_at - anchor.published_at).total_seconds()) / 3600
            if hours <= 72 and title_similarity(document.title, anchor.title) >= 0.68:
                cluster.append(document)
                break
        else:
            clusters.append([document])
    return clusters


def classify_event(text: str) -> EventType:
    lowered = text.casefold()
    for event_type, keywords in EVENT_RULES:
        if any(keyword in lowered for keyword in keywords):
            return event_type
    return EventType.OTHER


def extract_entities(text: str, entity_config: list[dict[str, Any]]) -> list[str]:
    lowered = text.casefold()
    result = []
    for entity in entity_config:
        matches = (
            re.search(rf"\b{re.escape(alias.casefold())}\b", lowered)
            for alias in entity["aliases"]
        )
        if any(matches):
            result.append(str(entity["id"]))
    return sorted(set(result))


def extract_events(
    clusters: list[list[SourceDocument]], entity_config: list[dict[str, Any]], now: datetime
) -> tuple[list[Event], list[Claim], list[Evidence]]:
    events: list[Event] = []
    claims: list[Claim] = []
    evidence_items: list[Evidence] = []
    for cluster in clusters:
        anchor = sorted(cluster, key=lambda item: (item.source_tier.value, item.published_at))[0]
        combined = " ".join(f"{item.title} {item.content}" for item in cluster)
        event_type = classify_event(combined)
        entities = extract_entities(combined, entity_config)
        evidence_ids: list[str] = []
        for document in cluster:
            excerpt = (document.content or document.title)[:500]
            evidence_id = stable_id("evidence", f"{document.id}|{content_hash(excerpt)}")
            evidence_items.append(
                Evidence(
                    id=evidence_id,
                    source_kind=SourceKind.DOCUMENT,
                    source_id=document.id,
                    source_document_id=document.id,
                    locator={"type": "rss_or_provider_record", "title": document.title},
                    excerpt=excerpt,
                    checksum=content_hash(excerpt),
                    quality_score={
                        SourceTier.A: 1.0,
                        SourceTier.B: 0.8,
                        SourceTier.C: 0.6,
                        SourceTier.D: 0.3,
                    }[document.source_tier],
                )
            )
            evidence_ids.append(evidence_id)
        event_key = content_hash(normalize_title(anchor.title))[:24]
        claim_id = stable_id("claim", event_key)
        claims.append(
            Claim(
                id=claim_id,
                claim_type=ClaimType.FACT,
                text=anchor.title,
                evidence_ids=evidence_ids,
                confidence=min(0.98, 0.68 + 0.1 * len({d.publisher for d in cluster})),
                as_of=anchor.published_at,
            )
        )
        age_hours = max(0.0, (now.astimezone(UTC) - anchor.published_at).total_seconds() / 3600)
        components = {
            "source_quality": TIER_POINTS[min(d.source_tier for d in cluster)],
            "materiality": TYPE_POINTS[event_type],
            "corroboration": min(14.0, 7.0 * (len({d.publisher for d in cluster}) - 1)),
            "watchlist_relevance": min(18.0, 9.0 * len(entities)),
            "freshness": max(0.0, 14.0 - age_hours / 4),
        }
        importance = round(min(100.0, sum(components.values())), 1)
        events.append(
            Event(
                id=stable_id("event", event_key),
                event_key=event_key,
                event_type=event_type,
                title=anchor.title,
                occurred_at=anchor.published_at,
                first_seen_at=min(d.retrieved_at for d in cluster),
                last_seen_at=max(d.retrieved_at for d in cluster),
                entity_ids=entities,
                claim_ids=[claim_id],
                evidence_ids=evidence_ids,
                source_document_ids=[d.id for d in cluster],
                importance_score=importance,
                importance_components={key: round(value, 1) for key, value in components.items()},
                confidence=min(0.98, 0.65 + 0.1 * len({d.publisher for d in cluster})),
                is_demo=any(d.is_demo for d in cluster),
            )
        )
    ranked = sorted(events, key=lambda item: item.importance_score, reverse=True)
    return ranked, claims, evidence_items


def extract_market_events(
    markets: list[MarketObservation], asset_config: list[dict[str, Any]], now: datetime
) -> tuple[list[Event], list[Claim], list[Evidence]]:
    """Create policy-threshold market events from already collected observations."""
    assets = {str(asset["symbol"]): asset for asset in asset_config}
    events: list[Event] = []
    claims: list[Claim] = []
    evidence_items: list[Evidence] = []
    for observation in markets:
        asset = assets.get(observation.symbol)
        if asset is None:
            logger.warning(
                "Skipping market event without universe asset_class symbol=%s",
                observation.symbol,
            )
            continue
        if observation.symbol == "^VIX":
            if observation.change_pct < 5.0:
                continue
            event_type = EventType.RISK
            high = observation.change_pct >= 10.0
        else:
            raw_asset_class = asset.get("asset_class")
            try:
                asset_class = AssetClass(str(raw_asset_class))
            except ValueError as exc:
                raise ValueError(
                    f"Invalid asset_class for market symbol {observation.symbol}: "
                    f"{raw_asset_class!r}"
                ) from exc
            notable_threshold, high_threshold = MARKET_THRESHOLDS[asset_class]
            if abs(observation.change_pct) < notable_threshold:
                continue
            event_type = EventType.MARKET_MOVE
            high = abs(observation.change_pct) >= high_threshold

        observed_at = observation.observed_at.astimezone(UTC)
        event_key = f"market:{observation.symbol}:{observed_at.isoformat()}"
        excerpt = (
            f"{observation.name} ({observation.symbol}) moved "
            f"{observation.change_pct:+.2f}% from {observation.previous_close:g} to "
            f"{observation.close:g} at {observed_at.isoformat()} "
            f"according to {observation.provider}."
        )
        evidence_id = stable_id("evidence", f"{observation.id}|{content_hash(excerpt)}")
        claim_id = stable_id("claim", event_key)
        direction = "rose" if observation.change_pct >= 0 else "fell"
        title = f"{observation.name} {direction} {abs(observation.change_pct):.2f}%"
        evidence_items.append(
            Evidence(
                id=evidence_id,
                source_kind=SourceKind.MARKET_OBSERVATION,
                source_id=observation.id,
                locator={
                    "type": "market_bar",
                    "asset": observation.name,
                    "symbol": observation.symbol,
                    "previous_close": observation.previous_close,
                    "latest_close": observation.close,
                    "change_pct": observation.change_pct,
                    "observed_at": observed_at.isoformat(),
                    "provider": observation.provider,
                },
                excerpt=excerpt,
                checksum=content_hash(excerpt),
                quality_score=0.75,
            )
        )
        claims.append(
            Claim(
                id=claim_id,
                claim_type=ClaimType.DERIVED,
                text=excerpt,
                evidence_ids=[evidence_id],
                confidence=0.99,
                as_of=observed_at,
            )
        )
        importance_score = (
            HIGH_MARKET_IMPORTANCE if high else NOTABLE_MARKET_IMPORTANCE
        )
        events.append(
            Event(
                id=stable_id("event", event_key),
                event_key=event_key,
                event_type=event_type,
                title=title,
                occurred_at=observed_at,
                first_seen_at=now.astimezone(UTC),
                last_seen_at=now.astimezone(UTC),
                entity_ids=[observation.symbol],
                claim_ids=[claim_id],
                evidence_ids=[evidence_id],
                source_document_ids=[],
                importance_score=importance_score,
                importance_components={
                    "materiality": TYPE_POINTS[event_type],
                    "threshold_severity": importance_score - TYPE_POINTS[event_type],
                },
                confidence=0.99,
                is_demo=observation.is_demo,
            )
        )
    ranked = sorted(events, key=lambda item: item.importance_score, reverse=True)
    return ranked, claims, evidence_items


def build_macro_event(
    observation: MacroObservation, now: datetime
) -> tuple[Event, Claim, Evidence]:
    """Build a factual event for a period first observed by Radar."""
    event_key = f"macro:{observation.series_id}:{observation.period}"
    observed_at = observation.observed_at.astimezone(UTC)
    value_text = (
        f"{observation.value:g}%"
        if observation.unit == "%"
        else f"{observation.value:g} {observation.unit}"
    )
    change_text = ""
    change_unit: str | None = None
    if observation.change is not None:
        if observation.unit == "%":
            change_unit = "percentage points"
        elif observation.unit == "index":
            change_unit = "index points"
        else:
            change_unit = observation.unit
        label = observation.change_label or "vs previous observation"
        change_text = f"; change {observation.change:+g} {change_unit} {label}"
    excerpt = (
        f"{observation.name} ({observation.series_id}) period {observation.period}: "
        f"{value_text}{change_text}; first observed by Radar at {observed_at.isoformat()} "
        f"from {observation.provider}."
    )
    evidence_id = stable_id("evidence", f"{observation.id}|{content_hash(excerpt)}")
    claim_id = stable_id("claim", event_key)
    evidence = Evidence(
        id=evidence_id,
        source_kind=SourceKind.MACRO_OBSERVATION,
        source_id=observation.id,
        locator={
            "type": "macro_period",
            "series_id": observation.series_id,
            "series": observation.name,
            "period": observation.period,
            "value": observation.value,
            "unit": observation.unit,
            "previous_value": observation.previous_value,
            "change": observation.change,
            "change_unit": change_unit,
            "change_label": observation.change_label,
            "radar_observed_at": observed_at.isoformat(),
            "provider": observation.provider,
        },
        excerpt=excerpt,
        checksum=content_hash(excerpt),
        quality_score=1.0 if observation.provider == "bls-public-api" else 0.8,
    )
    claim = Claim(
        id=claim_id,
        claim_type=ClaimType.DERIVED,
        text=excerpt,
        evidence_ids=[evidence_id],
        confidence=0.99,
        as_of=observed_at,
    )
    event = Event(
        id=stable_id("event", event_key),
        event_key=event_key,
        event_type=EventType.MACRO,
        title=f"New period first observed by Radar: {observation.name} {observation.period}",
        occurred_at=observed_at,
        first_seen_at=now.astimezone(UTC),
        last_seen_at=now.astimezone(UTC),
        entity_ids=[observation.series_id],
        claim_ids=[claim_id],
        evidence_ids=[evidence_id],
        source_document_ids=[],
        importance_score=58.0,
        importance_components={
            "source_quality": 30.0,
            "materiality": TYPE_POINTS[EventType.MACRO],
            "first_observed": 8.0,
        },
        confidence=0.99,
        is_demo=observation.is_demo,
    )
    return event, claim, evidence


def build_deterministic_summary(
    markets: list[MarketObservation], macro: list[MacroObservation], events: list[Event]
) -> DeterministicSummary:
    if markets:
        advancing = sum(item.change_pct > 0 for item in markets)
        declining = sum(item.change_pct < 0 for item in markets)
        headline = (
            f"{advancing} tracked assets advanced and {declining} declined "
            "in the latest session."
        )
        leaders = sorted(markets, key=lambda item: abs(item.change_pct), reverse=True)[:6]
    else:
        headline = "Market observations were unavailable for this run."
        leaders = []

    macro_headlines = []
    for macro_item in macro:
        change = ""
        if macro_item.change is not None:
            sign = "+" if macro_item.change > 0 else ""
            change = f" ({sign}{macro_item.change:g} {macro_item.change_label})"
        macro_headlines.append(
            f"{macro_item.name}: {macro_item.value:g}{macro_item.unit}{change}"
        )

    flags: list[str] = []
    for market_item in markets:
        if abs(market_item.change_pct) >= 2:
            flags.append(
                f"Large move: {market_item.name} changed {market_item.change_pct:+.2f}%."
            )
        if market_item.symbol == "^VIX" and market_item.change_pct >= 5:
            flags.append(f"Volatility warning: VIX rose {market_item.change_pct:+.2f}%.")
    counts: dict[EventType, int] = defaultdict(int)
    for event in events:
        counts[event.event_type] += 1
    if counts[EventType.LEGAL] or counts[EventType.RISK]:
        flags.append("The event set contains legal, enforcement, or systemic-risk items.")
    if not flags:
        flags.append("No deterministic high-severity risk rule was triggered.")
    return DeterministicSummary(
        market_headline=headline,
        market_leaders=leaders,
        macro_headlines=macro_headlines,
        risk_flags=flags,
    )


def assess_report_quality(
    *,
    market_count: int,
    expected_market_count: int,
    macro_count: int,
    document_count: int,
    official_event_count: int,
    provider_statuses: list[ProviderRunStatus],
) -> ReportQualityAssessment:
    if expected_market_count > 0:
        market_coverage = min(1.0, market_count / expected_market_count)
    else:
        market_coverage = 1.0 if market_count else 0.0

    has_support = macro_count > 0 or official_event_count > 0
    severely_missing = (
        market_coverage < 0.25
        and macro_count == 0
        and document_count == 0
        and official_event_count == 0
    )
    if severely_missing:
        return ReportQualityAssessment(
            status=ReportQualityStatus.INSUFFICIENT,
            market_coverage=market_coverage,
            reasons=["Market, macro, and official event data are all severely incomplete."],
        )

    unhealthy_providers = [
        status.provider
        for status in provider_statuses
        if status.status in {ProviderStatus.DEGRADED, ProviderStatus.FAILED}
    ]
    if market_coverage >= 0.7 and has_support and not unhealthy_providers:
        return ReportQualityAssessment(
            status=ReportQualityStatus.HEALTHY,
            market_coverage=market_coverage,
        )

    reasons: list[str] = []
    if market_coverage < 0.7:
        reasons.append(f"Core market coverage is {market_coverage:.2%}.")
    if not has_support:
        reasons.append("Neither macro observations nor official events are available.")
    if unhealthy_providers:
        reasons.append(f"Provider issues: {', '.join(unhealthy_providers)}.")
    return ReportQualityAssessment(
        status=ReportQualityStatus.DEGRADED,
        market_coverage=market_coverage,
        reasons=reasons,
    )
