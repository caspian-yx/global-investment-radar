from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from radar.domain import (
    Claim,
    ClaimType,
    DriverCandidate,
    DriverConfidence,
    DriverRelevance,
    DriverTemporalRelation,
    Event,
    EventType,
    Evidence,
    ExplanationBundle,
    MacroObservation,
    MarketObservation,
    SessionKind,
    SourceDocument,
    SourceKind,
    SourceTier,
    Topic,
    content_hash,
    stable_id,
)
from radar.ranking import rank_driver_candidates

DRIVER_RULE_VERSION = "c2.1-driver-v1"
CONTEXT_RULE_VERSION = "c2.1-context-v1"
MACRO_BACKDROP_RULE_VERSION = "c2.1-macro-backdrop-v1"
SUPPORTED_MACRO_BACKDROP_SERIES = {"CUUR0000SA0", "LNS14000000", "UNRATE"}
NORMAL_DRIVER_WINDOW = timedelta(hours=48)
WEEKEND_DRIVER_WINDOW = timedelta(hours=96)

# Only phrases with enough semantic content to support STRONG_TOPIC are listed.
# Generic single words such as "rate", "AI", "US", and "energy" are intentionally absent.
TOPIC_ALIASES: dict[Topic, tuple[str, ...]] = {
    Topic.RATES: (
        "interest rate",
        "federal funds rate",
        "rate decision",
        "rate hike",
        "rate cut",
        "fomc",
        "monetary policy",
    ),
    Topic.INFLATION: (
        "consumer price index",
        "producer price index",
        "inflation rate",
        "inflation pressures",
        "cpi",
        "pce inflation",
    ),
    Topic.EMPLOYMENT: (
        "nonfarm payroll",
        "nonfarm payrolls",
        "non-farm payroll",
        "unemployment rate",
        "labor market",
        "jobless claims",
        "employment report",
    ),
    Topic.REGULATION: (
        "securities and exchange commission",
        "sec enforcement",
        "regulatory action",
        "antitrust action",
        "financial regulation",
    ),
    Topic.TECHNOLOGY: (
        "artificial intelligence",
        "ai infrastructure",
        "semiconductor",
        "data center",
        "technology sector",
    ),
    Topic.ENERGY: (
        "crude oil",
        "natural gas",
        "oil market",
        "energy prices",
        "opec",
    ),
    Topic.CHINA: (
        "people's bank of china",
        "peoples bank of china",
        "chinese economy",
        "china policy",
        "beijing policy",
    ),
    Topic.US: (
        "united states",
        "u.s. economy",
        "u.s. market",
        "federal reserve",
        "wall street",
    ),
}
WEAK_TOPIC_ALIASES: dict[Topic, tuple[str, ...]] = {
    Topic.RATES: ("rate", "rates"),
    Topic.INFLATION: ("inflation",),
    Topic.EMPLOYMENT: ("employment", "jobs"),
    Topic.REGULATION: ("regulation", "regulatory"),
    Topic.TECHNOLOGY: ("ai", "technology", "tech"),
    Topic.ENERGY: ("energy",),
    Topic.CHINA: ("china", "chinese"),
    Topic.US: ("us", "u.s."),
}
BOILERPLATE_MARKERS = ("navigation", "cookie", "privacy policy", "site menu", "footer")
PUBLISHER_ALIASES = {
    "board of governors of the federal reserve system": "federal-reserve",
    "federal reserve": "federal-reserve",
    "federal reserve board": "federal-reserve",
    "securities and exchange commission": "us-sec",
    "u s sec": "us-sec",
    "us sec": "us-sec",
}
CONFIDENCE_NUMERIC = {
    DriverConfidence.LOW: 0.35,
    DriverConfidence.MEDIUM: 0.65,
    DriverConfidence.HIGH: 0.85,
}


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(phrase.casefold())}(?![a-z0-9])")


def _normalize_topic_text(text: str) -> str:
    segments = re.split(
        r"[|\r\n]+|(?<![a-z]\.[a-z]\.)(?<=[.!?])\s+", text.casefold()
    )
    retained = [
        segment
        for segment in segments
        if not any(_phrase_pattern(marker).search(segment) for marker in BOILERPLATE_MARKERS)
    ]
    return " | ".join(" ".join(segment.split()) for segment in retained)


def extract_topics(text: str) -> set[Topic]:
    normalized = _normalize_topic_text(text)
    return {
        topic
        for topic, aliases in TOPIC_ALIASES.items()
        if any(_phrase_pattern(alias).search(normalized) for alias in aliases)
    }


def _extract_weak_topics(text: str) -> set[Topic]:
    normalized = _normalize_topic_text(text)
    return {
        topic
        for topic, aliases in WEAK_TOPIC_ALIASES.items()
        if any(_phrase_pattern(alias).search(normalized) for alias in aliases)
    }


def build_market_observation_evidence(observation: MarketObservation) -> Evidence:
    observed_at = observation.observed_at.astimezone(UTC)
    excerpt = (
        f"{observation.name} ({observation.symbol}) moved "
        f"{observation.change_pct:+.2f}% from {observation.previous_close:g} to "
        f"{observation.close:g} at {observed_at.isoformat()} "
        f"according to {observation.provider}."
    )
    return Evidence(
        id=stable_id("evidence", f"{observation.id}|{content_hash(excerpt)}"),
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


def _macro_change_unit(observation: MacroObservation) -> str | None:
    if observation.change is None:
        return None
    if observation.unit == "%":
        return "percentage points"
    if observation.unit == "index":
        return "index points"
    return observation.unit


def build_macro_observation_evidence(observation: MacroObservation) -> Evidence:
    observed_at = observation.observed_at.astimezone(UTC)
    value_text = (
        f"{observation.value:g}%"
        if observation.unit == "%"
        else f"{observation.value:g} {observation.unit}"
    )
    change_unit = _macro_change_unit(observation)
    change_text = ""
    if observation.change is not None:
        label = observation.change_label or "vs previous observation"
        change_text = f"; change {observation.change:+g} {change_unit} {label}"
    excerpt = (
        f"{observation.name} ({observation.series_id}) period {observation.period}: "
        f"{value_text}{change_text}; first observed by Radar at {observed_at.isoformat()} "
        f"from {observation.provider}."
    )
    return Evidence(
        id=stable_id("evidence", f"{observation.id}|{content_hash(excerpt)}"),
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


def build_observation_evidence(
    markets: list[MarketObservation], macro: list[MacroObservation]
) -> list[Evidence]:
    items = [build_market_observation_evidence(item) for item in markets]
    items.extend(build_macro_observation_evidence(item) for item in macro)
    return unique_by_id(items)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def evidence_window_allows(age: timedelta, *, weekend_extension: bool) -> bool:
    if age < timedelta(0):
        return False
    limit = WEEKEND_DRIVER_WINDOW if weekend_extension else NORMAL_DRIVER_WINDOW
    return age <= limit


def classify_temporal_relation(
    target: Event, candidate: Event, asset: dict[str, Any]
) -> DriverTemporalRelation:
    if not _aware(target.occurred_at) or not _aware(candidate.occurred_at):
        return DriverTemporalRelation.UNKNOWN
    try:
        session_kind = SessionKind(str(asset.get("session_kind", "")))
    except ValueError:
        return DriverTemporalRelation.UNKNOWN

    target_at = target.occurred_at.astimezone(UTC)
    candidate_at = candidate.occurred_at.astimezone(UTC)
    if session_kind is SessionKind.EXCHANGE:
        timezone_name = str(asset.get("timezone", ""))
        close_value = str(asset.get("close_time", ""))
        try:
            timezone = ZoneInfo(timezone_name)
            close = time.fromisoformat(close_value)
        except (ValueError, ZoneInfoNotFoundError):
            return DriverTemporalRelation.UNKNOWN
        target_local = target_at.astimezone(timezone)
        candidate_local = candidate_at.astimezone(timezone)
        target_cutoff = datetime.combine(target_local.date(), close, timezone)
        if candidate_local > target_cutoff:
            return DriverTemporalRelation.POST_EVENT
        if candidate_local.date() == target_local.date():
            return DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
        age = target_cutoff - candidate_local
        if evidence_window_allows(age, weekend_extension=False):
            return DriverTemporalRelation.PRIOR
        weekend_eligible = (
            target_local.weekday() == 0
            and candidate_local.weekday() in {4, 5, 6}
            and evidence_window_allows(age, weekend_extension=True)
        )
        return (
            DriverTemporalRelation.PRIOR
            if weekend_eligible
            else DriverTemporalRelation.STALE
        )

    if candidate_at.date() == target_at.date():
        return DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
    if candidate_at > target_at:
        return DriverTemporalRelation.POST_EVENT
    return (
        DriverTemporalRelation.PRIOR
        if evidence_window_allows(target_at - candidate_at, weekend_extension=False)
        else DriverTemporalRelation.STALE
    )


def _candidate_documents(
    candidate: Event, documents: list[SourceDocument]
) -> list[SourceDocument]:
    document_ids = set(candidate.source_document_ids)
    return [item for item in documents if item.id in document_ids]


def _eligible_candidate_documents(
    target: Event,
    candidate: Event,
    documents: list[SourceDocument],
    asset: dict[str, Any],
) -> tuple[list[SourceDocument], DriverTemporalRelation | None]:
    eligible: list[tuple[SourceDocument, DriverTemporalRelation]] = []
    for document in _candidate_documents(candidate, documents):
        document_event = candidate.model_copy(
            update={"occurred_at": document.published_at}
        )
        relation = classify_temporal_relation(target, document_event, asset)
        if relation in {
            DriverTemporalRelation.PRIOR,
            DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
        }:
            eligible.append((document, relation))
    if not eligible:
        return [], None
    relation = (
        DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
        if any(
            item[1] is DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS
            for item in eligible
        )
        else DriverTemporalRelation.PRIOR
    )
    return [item[0] for item in eligible], relation


def _matches_exact_asset(text: str, asset: dict[str, Any]) -> bool:
    aliases = [str(asset.get("symbol", "")), str(asset.get("name", ""))]
    aliases.extend(str(alias) for alias in asset.get("entity_aliases", []))
    return any(alias and _phrase_pattern(alias).search(text.casefold()) for alias in aliases)


def classify_relevance(
    target: Event,
    candidate: Event,
    documents: list[SourceDocument],
    asset: dict[str, Any],
) -> DriverRelevance:
    candidate_documents = _candidate_documents(candidate, documents)
    if not candidate_documents:
        return DriverRelevance.NONE
    combined = " ".join(f"{item.title} {item.content}" for item in candidate_documents)
    if _matches_exact_asset(combined, asset):
        return DriverRelevance.EXACT_ENTITY
    if set(target.entity_ids) & set(candidate.entity_ids):
        return DriverRelevance.EXACT_ENTITY
    try:
        target_topics = {Topic(str(item)) for item in asset.get("topics", [])}
    except ValueError:
        return DriverRelevance.NONE
    candidate_topics = extract_topics(combined)
    if target_topics & candidate_topics:
        return DriverRelevance.STRONG_TOPIC
    if target_topics & _extract_weak_topics(combined):
        return DriverRelevance.WEAK
    return DriverRelevance.NONE


def driver_candidate_id(
    target_event_id: str,
    candidate_event_id: str,
    candidate_evidence_ids: list[str],
    rule_version: str,
) -> str:
    relationship = "|".join(
        [
            target_event_id,
            candidate_event_id,
            ",".join(sorted(set(candidate_evidence_ids))),
            rule_version,
        ]
    )
    return stable_id("driver-candidate", relationship)


def categorize_driver_confidence(
    *,
    temporal: DriverTemporalRelation,
    relevance: DriverRelevance,
    source_tiers: list[SourceTier],
    publishers: list[str],
    conflict: bool,
) -> tuple[DriverConfidence, dict[str, str], list[str]]:
    known_quality = bool(source_tiers) and all(
        tier in {SourceTier.A, SourceTier.B} for tier in source_tiers
    )
    publisher_count = len({item.casefold().strip() for item in publishers if item.strip()})
    components = {
        "temporal": temporal.value,
        "relevance": relevance.value,
        "source_quality": "A_B" if known_quality else "LOW_OR_UNKNOWN",
        "independent_publishers": str(publisher_count),
        "conflict": "YES" if conflict else "NO",
    }
    reasons: list[str] = []
    if temporal is DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS:
        reasons.append("Daily-bar ordering is ambiguous, so confidence is capped at LOW.")
    elif temporal is not DriverTemporalRelation.PRIOR:
        reasons.append("Prior temporal ordering is not defensible.")
    if not known_quality:
        reasons.append("Source quality is below Tier B or unknown.")
    if conflict:
        reasons.append("A credible contradictory candidate caps confidence at LOW.")
    if publisher_count < 2:
        reasons.append("A single independent publisher caps confidence at MEDIUM.")

    hard_low = (
        temporal is not DriverTemporalRelation.PRIOR or not known_quality or conflict
    )
    if hard_low:
        confidence = DriverConfidence.LOW
    elif (
        relevance in {DriverRelevance.EXACT_ENTITY, DriverRelevance.STRONG_TOPIC}
        and publisher_count >= 2
    ):
        confidence = DriverConfidence.HIGH
        reasons.append("Prior, relevant Tier A/B evidence has independent corroboration.")
    else:
        confidence = DriverConfidence.MEDIUM
        reasons.append("Relevant prior evidence lacks independent corroboration.")
    return confidence, components, reasons


def _stances(text: str) -> set[str]:
    lowered = text.casefold()
    up = ("rate hike", "rate increase", "raises interest rates", "higher interest rates")
    down = ("rate cut", "rate decrease", "lowers interest rates", "lower interest rates")
    result: set[str] = set()
    if any(item in lowered for item in up):
        result.add("RATE_UP")
    if any(item in lowered for item in down):
        result.add("RATE_DOWN")
    return result


def _conflicting_candidate_ids(
    candidate_documents: dict[str, list[SourceDocument]],
    candidates: dict[str, Event],
) -> set[str]:
    stances_by_candidate: dict[str, set[str]] = defaultdict(set)
    internally_conflicted: set[str] = set()
    for candidate_id, documents in candidate_documents.items():
        for document in documents:
            stances_by_candidate[candidate_id].update(
                _stances(f"{document.title} {document.content}")
            )
        if {"RATE_UP", "RATE_DOWN"} <= stances_by_candidate[candidate_id]:
            internally_conflicted.add(candidate_id)
    result = set(internally_conflicted)
    candidate_ids = sorted(stances_by_candidate)
    for position, left_id in enumerate(candidate_ids):
        for right_id in candidate_ids[position + 1 :]:
            if not set(candidates[left_id].entity_ids) & set(candidates[right_id].entity_ids):
                continue
            left = stances_by_candidate[left_id]
            right = stances_by_candidate[right_id]
            if ("RATE_UP" in left and "RATE_DOWN" in right) or (
                "RATE_DOWN" in left and "RATE_UP" in right
            ):
                result.update({left_id, right_id})
    return result


def _independent_publishers(documents: list[SourceDocument]) -> list[str]:
    by_url: dict[str, str] = {}
    for item in documents:
        parts = urlsplit(item.canonical_url)
        normalized_url = (
            f"{parts.scheme.casefold()}://{parts.netloc.casefold()}"
            f"{parts.path.rstrip('/')}"
        )
        normalized_publisher = re.sub(
            r"[^a-z0-9]+", " ", item.publisher.casefold()
        ).strip()
        publisher_key = PUBLISHER_ALIASES.get(
            normalized_publisher, normalized_publisher
        )
        by_url.setdefault(normalized_url, publisher_key)
    return sorted(set(by_url.values()))


def build_driver_candidates(
    *,
    target_events: list[Event],
    candidate_events: list[Event],
    evidence: list[Evidence],
    documents: list[SourceDocument],
    assets: list[dict[str, Any]],
    as_of: datetime,
) -> tuple[list[DriverCandidate], list[Claim]]:
    evidence_map = {item.id: item for item in evidence}
    assets_by_symbol = {str(item["symbol"]): item for item in assets}
    drivers: list[DriverCandidate] = []
    claims: list[Claim] = []
    for target in sorted(target_events, key=lambda item: item.id):
        if target.event_type not in {EventType.MARKET_MOVE, EventType.RISK}:
            continue
        symbol = next((item for item in target.entity_ids if item in assets_by_symbol), None)
        if symbol is None:
            continue
        asset = assets_by_symbol[symbol]
        eligible_documents: dict[str, list[SourceDocument]] = {}
        temporal_relations: dict[str, DriverTemporalRelation] = {}
        relevance_relations: dict[str, DriverRelevance] = {}
        for item in candidate_events:
            if item.id == target.id or not item.source_document_ids:
                continue
            evidenced_document_ids = {
                evidence_map[evidence_id].source_id
                for evidence_id in item.evidence_ids
                if evidence_id in evidence_map
                and evidence_map[evidence_id].source_kind is SourceKind.DOCUMENT
            }
            evidenced_documents = [
                document
                for document in documents
                if document.id in evidenced_document_ids
            ]
            source_items, temporal = _eligible_candidate_documents(
                target, item, evidenced_documents, asset
            )
            if temporal is None:
                continue
            relevance = classify_relevance(target, item, source_items, asset)
            if relevance not in {
                DriverRelevance.EXACT_ENTITY,
                DriverRelevance.STRONG_TOPIC,
            }:
                continue
            eligible_documents[item.id] = source_items
            temporal_relations[item.id] = temporal
            relevance_relations[item.id] = relevance
        candidates_by_id = {item.id: item for item in candidate_events}
        conflict_ids = _conflicting_candidate_ids(
            eligible_documents, candidates_by_id
        )
        target_evidence_ids = sorted(
            evidence_id
            for evidence_id in target.evidence_ids
            if evidence_id in evidence_map
            and evidence_map[evidence_id].source_kind is SourceKind.MARKET_OBSERVATION
        )
        if not target_evidence_ids:
            continue
        for candidate in sorted(candidate_events, key=lambda item: item.id):
            if candidate.id not in eligible_documents:
                continue
            source_items = eligible_documents[candidate.id]
            eligible_document_ids = {item.id for item in source_items}
            candidate_evidence_ids = sorted(
                evidence_id
                for evidence_id in candidate.evidence_ids
                if evidence_id in evidence_map
                and evidence_map[evidence_id].source_kind is SourceKind.DOCUMENT
                and evidence_map[evidence_id].source_id in eligible_document_ids
            )
            if not candidate_evidence_ids:
                continue
            temporal = temporal_relations[candidate.id]
            relevance = relevance_relations[candidate.id]
            confidence, components, reasons = categorize_driver_confidence(
                temporal=temporal,
                relevance=relevance,
                source_tiers=[item.source_tier for item in source_items],
                publishers=_independent_publishers(source_items),
                conflict=candidate.id in conflict_ids,
            )
            driver_id = driver_candidate_id(
                target.id,
                candidate.id,
                candidate_evidence_ids,
                DRIVER_RULE_VERSION,
            )
            claim_id = stable_id("claim", f"driver:{driver_id}")
            if temporal is DriverTemporalRelation.PRIOR:
                temporal_text = "published in the relevant prior evidence window"
            else:
                temporal_text = (
                    "published in the same-date evidence window, where ordering remains ambiguous"
                )
            claim_text = (
                "An evidence-backed official event is a plausible candidate that is "
                f"temporally consistent with the target market observation ({temporal_text}) "
                "and topically relevant to it. It may help explain related context but does "
                "not establish causality."
            )
            supporting_ids = sorted(set([*target_evidence_ids, *candidate_evidence_ids]))
            driver = DriverCandidate(
                id=driver_id,
                target_event_id=target.id,
                candidate_event_id=candidate.id,
                interpretation_claim_id=claim_id,
                evidence_ids=supporting_ids,
                target_evidence_ids=target_evidence_ids,
                candidate_evidence_ids=candidate_evidence_ids,
                temporal_relation=temporal,
                relevance_relation=relevance,
                confidence=confidence,
                confidence_components=components,
                confidence_reasons=reasons,
                generator="deterministic-driver-candidate",
                rule_version=DRIVER_RULE_VERSION,
                as_of=as_of.astimezone(UTC),
            )
            claims.append(
                Claim(
                    id=claim_id,
                    claim_type=ClaimType.INTERPRETATION,
                    text=claim_text,
                    evidence_ids=supporting_ids,
                    confidence=CONFIDENCE_NUMERIC[confidence],
                    created_by="deterministic-driver-candidate",
                    as_of=as_of.astimezone(UTC),
                )
            )
            drivers.append(driver)
    return unique_by_id(drivers), unique_by_id(claims)


class _HasId(Protocol):
    id: str


def unique_by_id[T: _HasId](items: list[T]) -> list[T]:
    result: dict[str, T] = {}
    for item in items:
        existing = result.get(item.id)
        if existing is not None and existing != item:
            raise ValueError(f"Conflicting duplicate payload for ID {item.id}")
        result.setdefault(item.id, item)
    return [result[item_id] for item_id in sorted(result)]


def build_explanation_bundles(
    *,
    target_events: Iterable[Event],
    driver_candidates: Iterable[DriverCandidate],
    claims: Iterable[Claim],
    evidence: Iterable[Evidence],
) -> list[ExplanationBundle]:
    """Package validated candidates and their exact linked objects by target."""
    targets = unique_by_id(list(target_events))
    targets_by_id = {item.id: item for item in targets}
    for target in targets:
        if target.event_type not in {EventType.MARKET_MOVE, EventType.RISK}:
            raise ValueError("ExplanationBundle has an unknown or ineligible target Event")

    claims_by_id = {item.id: item for item in unique_by_id(list(claims))}
    evidence_by_id = {item.id: item for item in unique_by_id(list(evidence))}
    candidates_by_target: dict[str, list[DriverCandidate]] = defaultdict(list)
    candidate_ids: set[str] = set()
    for candidate in driver_candidates:
        if candidate.id in candidate_ids:
            raise ValueError(f"Duplicate DriverCandidate ID {candidate.id}")
        candidate_ids.add(candidate.id)
        if candidate.target_event_id not in targets_by_id:
            raise ValueError("DriverCandidate has an unknown or ineligible target Event")
        candidates_by_target[candidate.target_event_id].append(candidate)

    bundles: list[ExplanationBundle] = []
    for target in targets:
        ranked_candidates = rank_driver_candidates(candidates_by_target[target.id])
        claim_ids = sorted(
            {item.interpretation_claim_id for item in ranked_candidates}
        )
        missing_claim_ids = [item_id for item_id in claim_ids if item_id not in claims_by_id]
        if missing_claim_ids:
            raise ValueError(
                "DriverCandidate is missing interpretation Claim: "
                + ", ".join(missing_claim_ids)
            )
        evidence_ids = sorted(
            {
                evidence_id
                for candidate in ranked_candidates
                for evidence_id in candidate.evidence_ids
            }
        )
        missing_evidence_ids = [
            item_id for item_id in evidence_ids if item_id not in evidence_by_id
        ]
        if missing_evidence_ids:
            raise ValueError(
                "DriverCandidate is missing Evidence: "
                + ", ".join(missing_evidence_ids)
            )
        bundles.append(
            ExplanationBundle(
                target_event=target,
                driver_candidates=ranked_candidates,
                claims=[claims_by_id[item_id] for item_id in claim_ids],
                evidence=[evidence_by_id[item_id] for item_id in evidence_ids],
            )
        )
    return bundles


def build_cross_asset_context(
    markets: list[MarketObservation],
    target_events: list[Event],
    assets: list[dict[str, Any]],
    as_of: datetime,
) -> tuple[list[Claim], list[Evidence], dict[str, list[str]]]:
    assets_by_symbol = {str(item["symbol"]): item for item in assets}
    by_symbol: dict[str, MarketObservation] = {}
    for item in sorted(markets, key=lambda value: (value.observed_at, value.id)):
        by_symbol[item.symbol] = item
    observations = sorted(by_symbol.values(), key=lambda item: item.symbol)
    evidence = build_observation_evidence(observations, [])
    evidence_by_source = {item.source_id: item for item in evidence}
    claims: list[Claim] = []
    claim_scope: dict[str, tuple[set[str], date]] = {}

    equities = [
        item for item in observations if item.symbol in {"^GSPC", "^NDX", "^DJI", "^RUT"}
    ]
    equity_dates = {_session_date(item, assets_by_symbol) for item in equities}
    if len(equities) >= 3 and len(equity_dates) == 1:
        advancing = [item for item in equities if item.change_pct > 0]
        declining = [item for item in equities if item.change_pct < 0]
        aligned = advancing if len(advancing) >= 3 else declining if len(declining) >= 3 else []
        if aligned:
            direction = "advanced" if aligned is advancing else "declined"
            ids = sorted(evidence_by_source[item.id].id for item in equities)
            text = (
                f"US equity breadth context: {len(aligned)} of {len(equities)} tracked US "
                f"equity benchmarks {direction} on comparable session dates; global sessions "
                "are asynchronous and this is related context, not causality."
            )
            claim = _derived_context_claim("us-equity-breadth", ids, text, as_of)
            claims.append(claim)
            claim_scope[claim.id] = (
                {"^GSPC", "^NDX", "^DJI", "^RUT"},
                next(iter(equity_dates)),
            )

    vix = next((item for item in observations if item.symbol == "^VIX"), None)
    weak_equities = [item for item in equities if item.change_pct < 0]
    if vix and weak_equities:
        same_date = all(
            _session_date(item, assets_by_symbol)
            == _session_date(vix, assets_by_symbol)
            for item in weak_equities
        )
        if same_date and vix.change_pct > 0:
            cited = [*weak_equities, vix]
            ids = sorted(evidence_by_source[item.id].id for item in cited)
            text = (
                "US equity weakness and a rising VIX appeared on comparable session dates. "
                "This is cross-asset context and does not establish simultaneity or causality."
            )
            claim = _derived_context_claim("us-equity-vix", ids, text, as_of)
            claims.append(claim)
            claim_scope[claim.id] = (
                {"^GSPC", "^NDX", "^DJI", "^RUT", "^VIX"},
                _session_date(vix, assets_by_symbol),
            )

    eurusd = next((item for item in observations if item.symbol == "EURUSD=X"), None)
    usdjpy = next((item for item in observations if item.symbol == "JPY=X"), None)
    if eurusd and usdjpy:
        same_date = _session_date(eurusd, assets_by_symbol) == _session_date(
            usdjpy, assets_by_symbol
        )
        coherent = (eurusd.change_pct < 0 < usdjpy.change_pct) or (
            eurusd.change_pct > 0 > usdjpy.change_pct
        )
        if same_date and coherent:
            direction = "firmer" if eurusd.change_pct < 0 else "softer"
            ids = sorted(
                [evidence_by_source[eurusd.id].id, evidence_by_source[usdjpy.id].id]
            )
            text = (
                f"Cautious FX proxy context: EUR/USD and USD/JPY were directionally "
                f"consistent with a {direction} US dollar proxy on comparable dates. "
                "This is related context, not a broad dollar-index measurement or causal claim."
            )
            claim = _derived_context_claim("fx-proxy", ids, text, as_of)
            claims.append(claim)
            claim_scope[claim.id] = (
                {"^GSPC", "^NDX", "^DJI", "^RUT", "^VIX", "EURUSD=X", "JPY=X"},
                _session_date(eurusd, assets_by_symbol),
            )

    by_target: dict[str, list[str]] = {}
    for target in target_events:
        if target.event_type not in {EventType.MARKET_MOVE, EventType.RISK}:
            continue
        symbol = next(
            (item for item in target.entity_ids if item in assets_by_symbol), None
        )
        if symbol is None:
            by_target[target.id] = []
            continue
        target_date = target.occurred_at.astimezone(
            ZoneInfo(str(assets_by_symbol[symbol].get("timezone", "UTC")))
        ).date()
        by_target[target.id] = [
            claim.id
            for claim in claims
            if symbol in claim_scope[claim.id][0]
            and target_date == claim_scope[claim.id][1]
        ]
    return claims, evidence, by_target


def _derived_context_claim(
    kind: str, evidence_ids: list[str], text: str, as_of: datetime
) -> Claim:
    identity = f"{kind}|{','.join(evidence_ids)}|{CONTEXT_RULE_VERSION}"
    return Claim(
        id=stable_id("claim", identity),
        claim_type=ClaimType.DERIVED,
        text=text,
        evidence_ids=evidence_ids,
        confidence=0.9,
        created_by="deterministic-cross-asset-context",
        as_of=as_of.astimezone(UTC),
    )


def _session_date(
    observation: MarketObservation, assets_by_symbol: dict[str, dict[str, Any]]
) -> date:
    asset = assets_by_symbol.get(observation.symbol, {})
    timezone_name = str(asset.get("timezone", "UTC"))
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return observation.observed_at.astimezone(timezone).date()


def build_macro_backdrop(
    macro: list[MacroObservation], target_events: list[Event], as_of: datetime
) -> tuple[list[Claim], list[Evidence], dict[str, list[str]]]:
    by_period: dict[tuple[str, str], MacroObservation] = {}
    for item in sorted(macro, key=lambda value: (value.observed_at, value.id)):
        if item.series_id not in SUPPORTED_MACRO_BACKDROP_SERIES:
            continue
        by_period.setdefault((item.series_id, item.period), item)
    observations = [by_period[key] for key in sorted(by_period)]
    evidence = build_observation_evidence([], observations)
    evidence_by_source = {item.source_id: item for item in evidence}
    claims: list[Claim] = []
    for item in observations:
        cited = evidence_by_source[item.id]
        value_text = f"{item.value:g}%" if item.unit == "%" else f"{item.value:g} {item.unit}"
        change_text = ""
        change_unit = _macro_change_unit(item)
        if item.change is not None:
            label = item.change_label or "vs previous observation"
            change_text = f"; change {item.change:+g} {change_unit} {label}"
        text = (
            f"Radar macro backdrop: {item.name} ({item.series_id}) for {item.period} was "
            f"observed at {value_text}{change_text}. This is observation context only."
        )
        identity = (
            f"{item.series_id}|{item.period}|{cited.id}|{MACRO_BACKDROP_RULE_VERSION}"
        )
        claims.append(
            Claim(
                id=stable_id("claim", identity),
                claim_type=ClaimType.DERIVED,
                text=text,
                evidence_ids=[cited.id],
                confidence=0.99,
                created_by="deterministic-macro-backdrop",
                as_of=as_of.astimezone(UTC),
            )
        )
    by_target = {
        target.id: [claim.id for claim in claims]
        for target in target_events
        if target.event_type in {EventType.MARKET_MOVE, EventType.RISK}
    }
    return claims, evidence, by_target


def validate_driver_candidate_bundle(
    candidate: DriverCandidate,
    *,
    events: list[Event],
    claims: list[Claim],
    evidence: list[Evidence],
    markets: list[MarketObservation],
    documents: list[SourceDocument],
    assets: list[dict[str, Any]],
) -> None:
    event_map = {item.id: item for item in events}
    claim_map = {item.id: item for item in claims}
    evidence_map = {item.id: item for item in evidence}
    market_ids = {item.id for item in markets}
    document_ids = {item.id for item in documents}
    target = event_map.get(candidate.target_event_id)
    if target is None:
        raise ValueError("DriverCandidate target Event does not exist")
    if target.event_type not in {EventType.MARKET_MOVE, EventType.RISK}:
        raise ValueError("DriverCandidate target must be MARKET_MOVE/RISK")
    assets_by_symbol = {str(item["symbol"]): item for item in assets}
    target_symbol = next(
        (item for item in target.entity_ids if item in assets_by_symbol), None
    )
    if target_symbol is None:
        raise ValueError("DriverCandidate target Event has no configured asset")
    asset = assets_by_symbol[target_symbol]
    if candidate.temporal_relation not in {
        DriverTemporalRelation.PRIOR,
        DriverTemporalRelation.SAME_WINDOW_AMBIGUOUS,
    }:
        raise ValueError("DriverCandidate must have an eligible temporal relation")
    if candidate.relevance_relation not in {
        DriverRelevance.EXACT_ENTITY,
        DriverRelevance.STRONG_TOPIC,
    }:
        raise ValueError("DriverCandidate must have an eligible relevance relation")
    if (
        candidate.generator != "deterministic-driver-candidate"
        or candidate.rule_version != DRIVER_RULE_VERSION
    ):
        raise ValueError("DriverCandidate generator or rule version is invalid")
    source_event = event_map.get(candidate.candidate_event_id)
    if source_event is None:
        raise ValueError("DriverCandidate candidate Event does not exist")
    interpretation = claim_map.get(candidate.interpretation_claim_id)
    if interpretation is None or interpretation.claim_type is not ClaimType.INTERPRETATION:
        raise ValueError("DriverCandidate Claim must exist and be INTERPRETATION")
    if interpretation.evidence_ids != candidate.evidence_ids:
        raise ValueError("DriverCandidate Claim evidence must match its canonical union")
    if (
        interpretation.created_by != candidate.generator
        or interpretation.confidence != CONFIDENCE_NUMERIC[candidate.confidence]
        or interpretation.id != stable_id("claim", f"driver:{candidate.id}")
    ):
        raise ValueError("DriverCandidate Claim confidence or metadata is inconsistent")
    for evidence_id in candidate.candidate_evidence_ids:
        item = evidence_map.get(evidence_id)
        if (
            item is None
            or item.source_kind is not SourceKind.DOCUMENT
            or item.source_id not in document_ids
            or item.source_id not in source_event.source_document_ids
            or evidence_id not in source_event.evidence_ids
        ):
            raise ValueError("DriverCandidate candidate evidence is not owned by its source Event")
    for evidence_id in candidate.target_evidence_ids:
        item = evidence_map.get(evidence_id)
        if (
            item is None
            or item.source_kind is not SourceKind.MARKET_OBSERVATION
            or item.source_id not in market_ids
            or evidence_id not in target.evidence_ids
        ):
            raise ValueError("DriverCandidate target evidence is not owned by its observation")
        market = next(value for value in markets if value.id == item.source_id)
        if market.symbol not in target.entity_ids:
            raise ValueError(
                "DriverCandidate target observation symbol is not owned by target Event"
            )
    candidate_document_ids = {
        evidence_map[evidence_id].source_id
        for evidence_id in candidate.candidate_evidence_ids
    }
    source_documents = [
        item for item in documents if item.id in candidate_document_ids
    ]
    eligible_documents, expected_temporal = _eligible_candidate_documents(
        target, source_event, source_documents, asset
    )
    if (
        expected_temporal is None
        or expected_temporal is not candidate.temporal_relation
        or {item.id for item in eligible_documents} != candidate_document_ids
    ):
        raise ValueError("DriverCandidate document timing is inconsistent")
    expected_relevance = classify_relevance(
        target, source_event, source_documents, asset
    )
    if expected_relevance is not candidate.relevance_relation:
        raise ValueError("DriverCandidate document relevance is inconsistent")
    conflict_value = candidate.confidence_components.get("conflict")
    if conflict_value not in {"YES", "NO"}:
        raise ValueError("DriverCandidate confidence conflict component is invalid")
    expected_confidence, expected_components, expected_reasons = (
        categorize_driver_confidence(
            temporal=candidate.temporal_relation,
            relevance=candidate.relevance_relation,
            source_tiers=[item.source_tier for item in source_documents],
            publishers=_independent_publishers(source_documents),
            conflict=conflict_value == "YES",
        )
    )
    if (
        candidate.confidence is not expected_confidence
        or candidate.confidence_components != expected_components
        or candidate.confidence_reasons != expected_reasons
    ):
        raise ValueError("DriverCandidate confidence is inconsistent with its components")
    expected_id = driver_candidate_id(
        candidate.target_event_id,
        candidate.candidate_event_id,
        candidate.candidate_evidence_ids,
        candidate.rule_version,
    )
    if candidate.id != expected_id:
        raise ValueError("DriverCandidate stable identity does not match its relationship")
