from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

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
    Story,
    content_hash,
    stable_id,
)
from radar.explanation import (
    build_macro_observation_evidence,
    build_market_observation_evidence,
)

logger = logging.getLogger(__name__)

EVENT_RULES: list[tuple[EventType, tuple[str, ...]]] = [
    (EventType.EARNINGS, ("earnings", "revenue", "eps", "margin", "profit")),
    (EventType.GUIDANCE, ("guidance", "outlook", "forecast", "raises", "cuts")),
    (EventType.M_AND_A, ("acquire", "acquisition", "merger", "takeover")),
    (
        EventType.POLICY,
        (
            "interest rate",
            "policy rate",
            "benchmark rate",
            "rate hike",
            "rate cut",
            "rates unchanged",
            "rate decision",
            "monetary policy",
            "central bank",
            "fomc",
            "policy",
            "regulation",
            "regulations",
        ),
    ),
    (
        EventType.MACRO,
        (
            "inflation",
            "consumer price index",
            "cpi",
            "employment",
            "gdp",
            "pce",
            "payroll",
        ),
    ),
    (
        EventType.SUPPLY_CHAIN,
        ("supply", "shortage", "inventory", "inventories", "capacity"),
    ),
    (
        EventType.PRODUCT,
        ("launch", "launched", "launches", "product", "approval", "trial result"),
    ),
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


def classify_event(text: str) -> EventType:
    lowered = text.casefold()
    for event_type, keywords in EVENT_RULES:
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", lowered)
            for keyword in keywords
        ):
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


def extract_story_events(
    stories: Iterable[Story],
    documents: Iterable[SourceDocument],
    now: datetime,
) -> tuple[list[Event], list[Claim], list[Evidence]]:
    """Project persistent Stories into Events with document-scoped provenance."""
    documents_by_id: dict[str, SourceDocument] = {}
    for document in documents:
        existing = documents_by_id.get(document.id)
        if existing is not None and existing != document:
            raise ValueError(f"Conflicting SourceDocument payload for {document.id}")
        documents_by_id.setdefault(document.id, document)

    events: list[Event] = []
    claims_by_id: dict[str, Claim] = {}
    evidence_by_id: dict[str, Evidence] = {}
    for story in sorted(stories, key=lambda item: item.id):
        missing = [
            document_id
            for document_id in story.source_document_ids
            if document_id not in documents_by_id
        ]
        if missing:
            raise ValueError(
                "Story Event projection is missing SourceDocument: "
                + ", ".join(missing)
            )
        members = [documents_by_id[document_id] for document_id in story.source_document_ids]
        presentation = documents_by_id[story.presentation_document_id]
        event_claim_ids: list[str] = []
        event_evidence_ids: list[str] = []
        for document in members:
            excerpt = (document.content or document.title)[:500]
            evidence_id = stable_id("evidence", f"{document.id}|{content_hash(excerpt)}")
            evidence = Evidence(
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
            existing_evidence = evidence_by_id.get(evidence_id)
            if existing_evidence is not None and existing_evidence != evidence:
                raise ValueError(f"Conflicting Evidence payload for {evidence_id}")
            evidence_by_id.setdefault(evidence_id, evidence)
            claim_id = stable_id(
                "claim", f"story-v1|document|{document.id}"
            )
            claim = Claim(
                id=claim_id,
                claim_type=ClaimType.FACT,
                text=document.title,
                evidence_ids=[evidence_id],
                confidence=0.78,
                as_of=document.published_at,
            )
            existing_claim = claims_by_id.get(claim_id)
            if existing_claim is not None and existing_claim != claim:
                raise ValueError(f"Conflicting Claim payload for {claim_id}")
            claims_by_id.setdefault(claim_id, claim)
            event_claim_ids.append(claim_id)
            event_evidence_ids.append(evidence_id)

        age_hours = max(
            0.0,
            (
                now.astimezone(UTC) - story.earliest_trusted_published_at
            ).total_seconds()
            / 3600,
        )
        components = {
            "source_quality": TIER_POINTS[min(item.source_tier for item in members)],
            "materiality": TYPE_POINTS[story.event_type],
            "corroboration": min(
                14.0, 7.0 * (len({item.publisher for item in members}) - 1)
            ),
            "watchlist_relevance": min(18.0, 9.0 * len(story.canonical_entity_ids)),
            "freshness": max(0.0, 14.0 - age_hours / 4),
        }
        events.append(
            Event(
                id=stable_id("event", story.id),
                event_key=f"story:{story.id}",
                event_type=story.event_type,
                title=presentation.title,
                occurred_at=story.earliest_trusted_published_at,
                first_seen_at=story.first_seen_at,
                last_seen_at=story.last_seen_at,
                entity_ids=list(story.canonical_entity_ids),
                claim_ids=sorted(set(event_claim_ids)),
                evidence_ids=sorted(set(event_evidence_ids)),
                source_document_ids=list(story.source_document_ids),
                importance_score=round(min(100.0, sum(components.values())), 1),
                importance_components={
                    key: round(value, 1) for key, value in components.items()
                },
                confidence=min(
                    0.98, 0.65 + 0.1 * len({item.publisher for item in members})
                ),
                is_demo=any(item.is_demo for item in members),
            )
        )
    ranked = sorted(
        events,
        key=lambda item: (-item.importance_score, item.id),
    )
    claims = [claims_by_id[item_id] for item_id in sorted(claims_by_id)]
    evidence_items = [evidence_by_id[item_id] for item_id in sorted(evidence_by_id)]
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
        market_evidence = build_market_observation_evidence(observation)
        evidence_id = market_evidence.id
        excerpt = market_evidence.excerpt
        claim_id = stable_id("claim", event_key)
        direction = "rose" if observation.change_pct >= 0 else "fell"
        title = f"{observation.name} {direction} {abs(observation.change_pct):.2f}%"
        evidence_items.append(market_evidence)
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
) -> tuple[Event, Claim, Evidence] | None:
    """Build a factual event backed by a trusted release and source document."""
    release_at = observation.release_at
    if (
        release_at is None
        or release_at.tzinfo is None
        or release_at.utcoffset() is None
        or observation.source_document_id is None
    ):
        return None
    event_key = f"macro:{observation.series_id}:{observation.period}"
    occurred_at = release_at.astimezone(UTC)
    observation_evidence = build_macro_observation_evidence(observation)
    evidence = Evidence(
        id=stable_id(
            "evidence",
            f"{observation.source_document_id}|{content_hash(observation_evidence.excerpt)}",
        ),
        source_kind=SourceKind.DOCUMENT,
        source_id=observation.source_document_id,
        source_document_id=observation.source_document_id,
        locator={**observation_evidence.locator, "type": "macro_source_document"},
        excerpt=observation_evidence.excerpt,
        checksum=observation_evidence.checksum,
        quality_score=observation_evidence.quality_score,
    )
    evidence_id = evidence.id
    excerpt = evidence.excerpt
    claim_id = stable_id("claim", event_key)
    claim = Claim(
        id=claim_id,
        claim_type=ClaimType.DERIVED,
        text=excerpt,
        evidence_ids=[evidence_id],
        confidence=0.99,
        as_of=occurred_at,
    )
    event = Event(
        id=stable_id("event", event_key),
        event_key=event_key,
        event_type=EventType.MACRO,
        title=f"New period first observed by Radar: {observation.name} {observation.period}",
        occurred_at=occurred_at,
        first_seen_at=now.astimezone(UTC),
        last_seen_at=now.astimezone(UTC),
        entity_ids=[observation.series_id],
        claim_ids=[claim_id],
        evidence_ids=[evidence_id],
        source_document_ids=[observation.source_document_id],
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
            f"{macro_item.name}: {macro_item.value:g} {macro_item.unit}{change}"
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
