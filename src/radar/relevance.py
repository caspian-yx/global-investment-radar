from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.domain import AssetClass, Event, EventType, SourceDocument, Topic
from radar.explanation import extract_topics

RELATIONSHIP_RULE_VERSION = "c6.1-event-target-v1"


class EventTargetDecision(StrEnum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"


class TransmissionChannel(StrEnum):
    DIRECT_ENTITY = "DIRECT_ENTITY"
    CENTRAL_BANK_POLICY = "CENTRAL_BANK_POLICY"
    MACROECONOMIC_RELEASE = "MACROECONOMIC_RELEASE"
    SECTOR_THEME = "SECTOR_THEME"


class EventScope(StrEnum):
    ENTITY = "ENTITY"
    LOCAL = "LOCAL"
    REGIONAL = "REGIONAL"
    MACRO = "MACRO"


class MacroDomain(StrEnum):
    INFLATION = "INFLATION"
    EMPLOYMENT = "EMPLOYMENT"


class PolicyAuthority(StrEnum):
    FEDERAL_RESERVE = "federal-reserve"
    EUROPEAN_CENTRAL_BANK = "european-central-bank"


class TargetRiskRole(StrEnum):
    NONE = "NONE"
    BROAD_RISK = "BROAD_RISK"


class RelationshipReason(StrEnum):
    DIRECT_ENTITY_MATCH = "DIRECT_ENTITY_MATCH"
    CENTRAL_BANK_REGION_MATCH = "CENTRAL_BANK_REGION_MATCH"
    CENTRAL_BANK_BROAD_RISK = "CENTRAL_BANK_BROAD_RISK"
    MACRO_REGION_TOPIC_MATCH = "MACRO_REGION_TOPIC_MATCH"
    LEGAL_TOPIC_ONLY = "LEGAL_TOPIC_ONLY"
    UNSUPPORTED_CROSS_REGION = "UNSUPPORTED_CROSS_REGION"
    NO_SUPPORTED_TRANSMISSION = "NO_SUPPORTED_TRANSMISSION"
    TARGET_PROFILE_MISSING = "TARGET_PROFILE_MISSING"
    SECTOR_THEME_MATCH = "SECTOR_THEME_MATCH"


_REASON_ORDER = {reason: position for position, reason in enumerate(RelationshipReason)}
_CENTRAL_BANK_REGIONS = {
    "federal-reserve": "US",
    "european-central-bank": "EU",
}
_CENTRAL_BANK_ALIASES = {
    "federal-reserve": (
        "board of governors of the federal reserve system",
        "federal reserve board",
        "federal reserve",
        "fomc",
    ),
    "european-central-bank": (
        "european central bank",
        "ecb",
        "governing council",
    ),
}
_MACRO_TOPICS = {Topic.INFLATION, Topic.EMPLOYMENT}
_SECTOR_EVENT_TYPES = {
    EventType.EARNINGS,
    EventType.GUIDANCE,
    EventType.PRODUCT,
    EventType.SUPPLY_CHAIN,
}


class EventTargetRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    candidate_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    decision: EventTargetDecision
    transmission_channel: TransmissionChannel | None
    reason_codes: tuple[RelationshipReason, ...] = Field(min_length=1)
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")

    @model_validator(mode="after")
    def validate_decision_shape(self) -> EventTargetRelationship:
        if self.target_event_id == self.candidate_event_id:
            raise ValueError("relationship target and candidate Events must differ")
        if self.decision is EventTargetDecision.ALLOW:
            if self.transmission_channel is None:
                raise ValueError("allowed relationship requires a transmission channel")
        elif self.transmission_channel is not None:
            raise ValueError("rejected relationship cannot carry a transmission channel")
        canonical_reasons = tuple(
            sorted(set(self.reason_codes), key=_REASON_ORDER.__getitem__)
        )
        if self.reason_codes != canonical_reasons:
            raise ValueError("relationship reason codes must be unique and canonically ordered")
        return self


class EventSemanticProfile(BaseModel):
    """Immutable, runtime-only canonical semantics for an Event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    event_type: EventType
    entity_ids: tuple[str, ...]
    regions: tuple[str, ...]
    topics: tuple[Topic, ...]
    event_scope: EventScope
    policy_authority: PolicyAuthority | None = None
    macro_domain: MacroDomain | None = None
    sector_themes: tuple[str, ...]


class TargetSemanticProfile(BaseModel):
    """Immutable, runtime-only canonical semantics for a market target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    symbol: str = Field(min_length=1, pattern=r".*\S.*")
    entity_ids: tuple[str, ...]
    asset_class: AssetClass
    region: str = Field(min_length=1, pattern=r".*\S.*")
    topics: tuple[Topic, ...]
    sector_themes: tuple[str, ...]
    risk_role: TargetRiskRole
    rates_sensitive: bool


class _HasId(Protocol):
    id: str


def _unique_by_id[T: _HasId](items: list[T]) -> list[T]:
    by_id: dict[str, T] = {}
    for item in items:
        existing = by_id.get(item.id)
        if existing is not None and existing != item:
            raise ValueError(f"Conflicting duplicate payload for ID {item.id}")
        by_id.setdefault(item.id, item)
    return [by_id[item_id] for item_id in sorted(by_id)]


def _normalized_words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalized_words(phrase)
    if not normalized_phrase:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])",
            text,
        )
    )


def _canonical_topics(values: set[Topic]) -> tuple[Topic, ...]:
    return tuple(sorted(values, key=lambda item: item.value))


def _canonical_strings(values: set[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))


def _policy_authority(
    event: Event, documents: list[SourceDocument], document_text: str
) -> PolicyAuthority | None:
    matches: set[PolicyAuthority] = set()
    publisher_text = _normalized_words(" ".join(item.publisher for item in documents))
    for identity, aliases in _CENTRAL_BANK_ALIASES.items():
        authority = PolicyAuthority(identity)
        if identity in event.entity_ids or any(
            _contains_phrase(publisher_text, alias) or _contains_phrase(document_text, alias)
            for alias in aliases
        ):
            matches.add(authority)
    return next(iter(matches)) if len(matches) == 1 else None


def _macro_domain(topics: set[Topic]) -> MacroDomain | None:
    if Topic.INFLATION in topics:
        return MacroDomain.INFLATION
    if Topic.EMPLOYMENT in topics:
        return MacroDomain.EMPLOYMENT
    return None


def build_event_semantic_profile(
    event: Event,
    documents: list[SourceDocument],
    entity_config: list[dict[str, Any]] | None = None,
) -> EventSemanticProfile:
    """Derive deterministic semantics without reading provider metadata."""
    ordered_documents = _unique_by_id(documents)
    text = _document_text(ordered_documents)
    regions = {item.region for item in ordered_documents if item.region}
    topics = extract_topics(text)
    themes: set[str] = set()
    for entity in entity_config or []:
        if str(entity.get("id", "")) in event.entity_ids:
            themes.update(str(item) for item in entity.get("sector_themes", []))
    if event.event_type is EventType.MACRO:
        scope = EventScope.MACRO
    elif event.event_type is EventType.POLICY:
        scope = EventScope.REGIONAL if regions else EventScope.LOCAL
    elif event.entity_ids:
        scope = EventScope.ENTITY
    elif regions:
        scope = EventScope.REGIONAL
    else:
        scope = EventScope.LOCAL
    return EventSemanticProfile(
        event_id=event.id,
        event_type=event.event_type,
        entity_ids=_canonical_strings(set(event.entity_ids)),
        regions=_canonical_strings({str(item) for item in regions}),
        topics=_canonical_topics(topics),
        event_scope=scope,
        policy_authority=_policy_authority(event, ordered_documents, text),
        macro_domain=_macro_domain(topics) if event.event_type is EventType.MACRO else None,
        sector_themes=_canonical_strings(themes),
    )


def build_target_semantic_profile(
    target: Event, asset: dict[str, Any]
) -> TargetSemanticProfile:
    raw_topics = {
        Topic(str(item))
        for item in asset.get("topics", [])
        if str(item) in Topic._value2member_map_
    }
    asset_class = AssetClass(str(asset.get("asset_class")))
    symbol = str(asset.get("symbol", ""))
    return TargetSemanticProfile(
        target_event_id=target.id,
        symbol=symbol,
        entity_ids=_canonical_strings(set(target.entity_ids)),
        asset_class=asset_class,
        region=str(asset.get("region", "")),
        topics=_canonical_topics(raw_topics),
        sector_themes=_canonical_strings({str(item) for item in asset.get("sector_themes", [])}),
        risk_role=TargetRiskRole.BROAD_RISK if symbol == "^VIX" else TargetRiskRole.NONE,
        rates_sensitive=(
            asset_class in {AssetClass.EQUITY_INDEX, AssetClass.EQUITY_ETF_PROXY}
            and Topic.RATES in raw_topics
        )
        or str(asset.get("target_role", "")) in {"RATES", "RATES_SENSITIVE"},
    )


def summarize_relationships(
    relationships: list[EventTargetRelationship],
) -> dict[str, Any]:
    """Return stable aggregate diagnostics for relationship decisions."""
    rejected: dict[str, int] = {}
    allowed: dict[str, int] = {}
    for relationship in relationships:
        if relationship.decision is EventTargetDecision.REJECT:
            for reason in relationship.reason_codes:
                rejected[reason.value] = rejected.get(reason.value, 0) + 1
        elif relationship.transmission_channel is not None:
            key = relationship.transmission_channel.value
            allowed[key] = allowed.get(key, 0) + 1
    return {
        "evaluated": len(relationships),
        "allowed": sum(allowed.values()),
        "rejected": sum(1 for item in relationships if item.decision is EventTargetDecision.REJECT),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "allowed_by_channel": dict(sorted(allowed.items())),
    }


def _linked_documents(
    candidate: Event,
    documents_by_id: dict[str, SourceDocument],
) -> list[SourceDocument]:
    return [
        documents_by_id[document_id]
        for document_id in sorted(set(candidate.source_document_ids))
        if document_id in documents_by_id
    ]


def _document_text(documents: list[SourceDocument]) -> str:
    return _normalized_words(
        " ".join(f"{item.title} {item.content}" for item in documents)
    )


def _target_asset(
    target: Event,
    assets_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            assets_by_symbol[entity_id]
            for entity_id in target.entity_ids
            if entity_id in assets_by_symbol
        ),
        None,
    )


def _direct_entity_match(
    target: Event,
    candidate: Event,
    asset: dict[str, Any],
    document_text: str,
) -> bool:
    if set(target.entity_ids) & set(candidate.entity_ids):
        return True
    aliases = [str(asset.get("symbol", "")), str(asset.get("name", ""))]
    aliases.extend(str(alias) for alias in asset.get("entity_aliases", []))
    return any(_contains_phrase(document_text, alias) for alias in aliases)


def _candidate_regions(documents: list[SourceDocument]) -> set[str]:
    return {item.region for item in documents if item.region is not None}


def _reject_reasons(
    candidate: Event,
    documents: list[SourceDocument],
    target_region: str,
) -> tuple[RelationshipReason, ...]:
    reasons: list[RelationshipReason] = []
    if candidate.event_type is EventType.LEGAL:
        reasons.append(RelationshipReason.LEGAL_TOPIC_ONLY)
    regions = _candidate_regions(documents)
    if regions and target_region not in regions:
        reasons.append(RelationshipReason.UNSUPPORTED_CROSS_REGION)
    if candidate.event_type is not EventType.LEGAL:
        reasons.append(RelationshipReason.NO_SUPPORTED_TRANSMISSION)
    return tuple(sorted(set(reasons), key=_REASON_ORDER.__getitem__))


def _classify_relationship(
    target: Event,
    candidate: Event,
    documents: list[SourceDocument],
    asset: dict[str, Any] | None,
    entity_config: list[dict[str, Any]] | None = None,
) -> EventTargetRelationship:
    if asset is None:
        return EventTargetRelationship(
            target_event_id=target.id,
            candidate_event_id=candidate.id,
            decision=EventTargetDecision.REJECT,
            transmission_channel=None,
            reason_codes=(RelationshipReason.TARGET_PROFILE_MISSING,),
            rule_version=RELATIONSHIP_RULE_VERSION,
        )

    text = _document_text(documents)
    event_profile = build_event_semantic_profile(candidate, documents, entity_config)
    target_profile = build_target_semantic_profile(target, asset)
    if _direct_entity_match(target, candidate, asset, text):
        return EventTargetRelationship(
            target_event_id=target.id,
            candidate_event_id=candidate.id,
            decision=EventTargetDecision.ALLOW,
            transmission_channel=TransmissionChannel.DIRECT_ENTITY,
            reason_codes=(RelationshipReason.DIRECT_ENTITY_MATCH,),
            rule_version=RELATIONSHIP_RULE_VERSION,
        )

    target_region = target_profile.region
    candidate_topics = set(event_profile.topics)
    target_topics = set(target_profile.topics)
    authority = event_profile.policy_authority
    if (
        candidate.event_type is EventType.POLICY
        and Topic.RATES in candidate_topics
        and authority is not None
    ):
        authority_region = _CENTRAL_BANK_REGIONS[authority]
        if (
            authority == "federal-reserve"
            and target_profile.risk_role is TargetRiskRole.BROAD_RISK
            and target_profile.region == "US"
        ):
            return EventTargetRelationship(
                target_event_id=target.id,
                candidate_event_id=candidate.id,
                decision=EventTargetDecision.ALLOW,
                transmission_channel=TransmissionChannel.CENTRAL_BANK_POLICY,
                reason_codes=(RelationshipReason.CENTRAL_BANK_BROAD_RISK,),
                rule_version=RELATIONSHIP_RULE_VERSION,
            )
        broad_rates_target = (
            target_profile.rates_sensitive
        )
        if target_region == authority_region and broad_rates_target:
            return EventTargetRelationship(
                target_event_id=target.id,
                candidate_event_id=candidate.id,
                decision=EventTargetDecision.ALLOW,
                transmission_channel=TransmissionChannel.CENTRAL_BANK_POLICY,
                reason_codes=(RelationshipReason.CENTRAL_BANK_REGION_MATCH,),
                rule_version=RELATIONSHIP_RULE_VERSION,
            )

    if candidate.event_type is EventType.MACRO:
        regions = set(event_profile.regions)
        if (
            regions == {target_region}
            and event_profile.macro_domain is not None
            and bool(target_topics & candidate_topics & _MACRO_TOPICS)
            and (
                target_profile.rates_sensitive
            )
        ):
            return EventTargetRelationship(
                target_event_id=target.id,
                candidate_event_id=candidate.id,
                decision=EventTargetDecision.ALLOW,
                transmission_channel=TransmissionChannel.MACROECONOMIC_RELEASE,
                reason_codes=(RelationshipReason.MACRO_REGION_TOPIC_MATCH,),
                rule_version=RELATIONSHIP_RULE_VERSION,
            )

    if (
        candidate.event_type in _SECTOR_EVENT_TYPES
        and event_profile.sector_themes
        and set(event_profile.sector_themes) & set(target_profile.sector_themes)
    ):
        return EventTargetRelationship(
            target_event_id=target.id,
            candidate_event_id=candidate.id,
            decision=EventTargetDecision.ALLOW,
            transmission_channel=TransmissionChannel.SECTOR_THEME,
            reason_codes=(RelationshipReason.SECTOR_THEME_MATCH,),
            rule_version=RELATIONSHIP_RULE_VERSION,
        )

    return EventTargetRelationship(
        target_event_id=target.id,
        candidate_event_id=candidate.id,
        decision=EventTargetDecision.REJECT,
        transmission_channel=None,
        reason_codes=_reject_reasons(candidate, documents, target_region),
        rule_version=RELATIONSHIP_RULE_VERSION,
    )


def build_event_target_relationships(
    *,
    target_events: list[Event],
    candidate_events: list[Event],
    documents: list[SourceDocument],
    assets: list[dict[str, Any]],
    entity_config: list[dict[str, Any]] | None = None,
) -> list[EventTargetRelationship]:
    """Authorize plausible event-target pairs before unchanged C2 processing."""
    targets = _unique_by_id(target_events)
    candidates = _unique_by_id(candidate_events)
    source_documents = _unique_by_id(documents)
    documents_by_id = {item.id: item for item in source_documents}
    assets_by_symbol = {str(item["symbol"]): item for item in assets}

    relationships: list[EventTargetRelationship] = []
    for target in targets:
        if target.event_type not in {EventType.MARKET_MOVE, EventType.RISK}:
            continue
        asset = _target_asset(target, assets_by_symbol)
        for candidate in candidates:
            if candidate.id == target.id:
                continue
            relationships.append(
                _classify_relationship(
                    target,
                    candidate,
                    _linked_documents(candidate, documents_by_id),
                    asset,
                    entity_config,
                )
            )
    return relationships
