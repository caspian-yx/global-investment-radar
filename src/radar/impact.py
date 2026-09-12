from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from radar.domain import (
    AssetClass,
    Event,
    EventType,
    MarketObservation,
    SessionKind,
    Story,
    stable_id,
)
from radar.relevance import (
    EventTargetDecision,
    EventTargetRelationship,
    RelationshipReason,
    TransmissionChannel,
)

C8_RULE_VERSION = "c8-daily-close-v1"


class ReactionWindow(StrEnum):
    SAME_SESSION = "SAME_SESSION"
    NEXT_SESSION = "NEXT_SESSION"


class ReactionDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class ImpactMagnitude(StrEnum):
    EXTREME = "EXTREME"
    LARGE = "LARGE"
    MODERATE = "MODERATE"
    SMALL = "SMALL"
    FLAT = "FLAT"


class EpistemicStatus(StrEnum):
    OBSERVED = "OBSERVED"
    ASSOCIATED = "ASSOCIATED"
    CAUSALLY_SUPPORTED = "CAUSALLY_SUPPORTED"


class AssociationConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class ReactionProfile(StrEnum):
    EQUITY_INDEX = "EQUITY_INDEX"
    EQUITY_ETF_PROXY = "EQUITY_ETF_PROXY"
    FX = "FX"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"
    VOLATILITY_INDEX = "VOLATILITY_INDEX"


class BarDateBasis(StrEnum):
    UTC_PROVIDER_TIMESTAMP_DATE = "UTC_PROVIDER_TIMESTAMP_DATE"


class ImpactTargetKind(StrEnum):
    DIRECT_TARGET_REACTION = "DIRECT_TARGET_REACTION"
    SECTOR_REACTION = "SECTOR_REACTION"
    REGIONAL_MARKET_REACTION = "REGIONAL_MARKET_REACTION"
    MACRO_MARKET_REACTION = "MACRO_MARKET_REACTION"
    RISK_REACTION = "RISK_REACTION"


class ImpactReason(StrEnum):
    C6_AUTHORIZED = "C6_AUTHORIZED"
    C6_REJECTED = "C6_REJECTED"
    DIRECT_ENTITY_TARGET = "DIRECT_ENTITY_TARGET"
    SECTOR_THEME_TARGET = "SECTOR_THEME_TARGET"
    REGIONAL_BROAD_MARKET = "REGIONAL_BROAD_MARKET"
    MACRO_REGION_TARGET = "MACRO_REGION_TARGET"
    RISK_TARGET = "RISK_TARGET"
    SAME_SESSION = "SAME_SESSION"
    NEXT_SESSION = "NEXT_SESSION"
    STRONG_TEMPORAL_ALIGNMENT = "STRONG_TEMPORAL_ALIGNMENT"
    WEAK_TEMPORAL_ALIGNMENT = "WEAK_TEMPORAL_ALIGNMENT"
    POST_EVENT_REACTION = "POST_EVENT_REACTION"
    MATERIAL_MOVE = "MATERIAL_MOVE"
    LIMITED_MOVE = "LIMITED_MOVE"
    NO_MEANINGFUL_REACTION = "NO_MEANINGFUL_REACTION"
    MULTIPLE_PLAUSIBLE_EVENTS = "MULTIPLE_PLAUSIBLE_EVENTS"
    PRE_EVENT_MOVE = "PRE_EVENT_MOVE"
    EVENT_OUTSIDE_REACTION_WINDOW = "EVENT_OUTSIDE_REACTION_WINDOW"
    UNKNOWN_EVENT_TIME = "UNKNOWN_EVENT_TIME"
    INSUFFICIENT_MARKET_INTERVAL = "INSUFFICIENT_MARKET_INTERVAL"
    INCOMPLETE_MARKET_INTERVAL = "INCOMPLETE_MARKET_INTERVAL"
    INVALID_MARKET_OBSERVATION = "INVALID_MARKET_OBSERVATION"
    TARGET_PROFILE_INVALID = "TARGET_PROFILE_INVALID"
    MARKET_EVENT_SOURCE_REJECTED = "MARKET_EVENT_SOURCE_REJECTED"
    TARGET_EVENT_MISSING = "TARGET_EVENT_MISSING"
    MARKET_OBSERVATION_MISSING = "MARKET_OBSERVATION_MISSING"


class MarketTargetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    symbol: str = Field(min_length=1, pattern=r".*\S.*")
    name: str = Field(min_length=1, pattern=r".*\S.*")
    asset_class: AssetClass
    region: str = Field(min_length=1, pattern=r".*\S.*")
    session_kind: SessionKind
    timezone: str = Field(min_length=1, pattern=r".*\S.*")
    close_time: time
    bar_date_basis: BarDateBasis
    reaction_profile: ReactionProfile

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("market target requires a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_reaction_profile(self) -> MarketTargetSnapshot:
        if (
            self.reaction_profile is not ReactionProfile.VOLATILITY_INDEX
            and self.reaction_profile.value != self.asset_class.value
        ):
            raise ValueError("reaction profile must match asset class unless explicitly volatility")
        return self


class TransmissionBasisSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    target_kind: ImpactTargetKind
    source_channel: TransmissionChannel
    source_reason_codes: tuple[RelationshipReason, ...] = Field(min_length=1)
    source_rule_version: str = Field(min_length=1, pattern=r".*\S.*")

    @model_validator(mode="after")
    def validate_canonical_reasons(self) -> TransmissionBasisSnapshot:
        if self.source_reason_codes != tuple(
            sorted(set(self.source_reason_codes), key=lambda item: item.value)
        ):
            raise ValueError("transmission reason codes must be sorted and unique")
        return self


class ReactionMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    market_observation_id: str = Field(min_length=1, pattern=r".*\S.*")
    provider: str = Field(min_length=1, pattern=r".*\S.*")
    raw_reference_at: datetime
    raw_observed_at: datetime
    reference_at: datetime
    observed_at: datetime
    currency: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    reference_value: float
    observed_value: float
    absolute_move: float
    percentage_move: float

    @field_validator("raw_reference_at", "raw_observed_at", "reference_at", "observed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reaction measurement timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_measurement(self) -> ReactionMeasurement:
        values = (
            self.reference_value,
            self.observed_value,
            self.absolute_move,
            self.percentage_move,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("reaction measurement values must be finite")
        if self.reference_value <= 0 or self.observed_value <= 0:
            raise ValueError("reaction measurement price values must be positive")
        if self.reference_at >= self.observed_at:
            raise ValueError("reaction measurement interval must move forward")
        if self.raw_reference_at >= self.raw_observed_at:
            raise ValueError("raw market interval must move forward")
        expected_absolute = self.observed_value - self.reference_value
        expected_percentage = expected_absolute / self.reference_value * 100
        if not math.isclose(self.absolute_move, expected_absolute, abs_tol=1e-9):
            raise ValueError("reaction absolute move is inconsistent")
        if not math.isclose(self.percentage_move, expected_percentage, abs_tol=0.011):
            raise ValueError("reaction percentage move is inconsistent")
        return self


class MarketImpactRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str = Field(min_length=1, pattern=r".*\S.*")
    story_id: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    event_id: str = Field(min_length=1, pattern=r".*\S.*")
    event_at: datetime
    target: MarketTargetSnapshot
    reaction_window: ReactionWindow
    measurement: ReactionMeasurement
    direction: ReactionDirection
    magnitude: ImpactMagnitude
    epistemic_status: EpistemicStatus
    association_confidence: AssociationConfidence
    transmission_basis: TransmissionBasisSnapshot | None
    reason_codes: tuple[ImpactReason, ...] = Field(min_length=1)
    event_evidence_ids: tuple[str, ...]
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    evaluated_at: datetime

    @field_validator("event_at", "evaluated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market impact timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_impact_contract(self) -> MarketImpactRecord:
        expected_id = market_impact_id(
            story_id=self.story_id,
            event_id=self.event_id,
            symbol=self.target.symbol,
            reaction_window=self.reaction_window,
            rule_version=self.rule_version,
        )
        if self.id != expected_id:
            raise ValueError("market impact identity is not deterministic")
        if not self.measurement.reference_at < self.event_at <= self.measurement.observed_at:
            raise ValueError("market impact Event must fall inside its daily interval")
        if self.evaluated_at < self.measurement.observed_at:
            raise ValueError("market impact cannot evaluate an incomplete daily interval")
        timezone = ZoneInfo(self.target.timezone)
        expected_window = (
            ReactionWindow.SAME_SESSION
            if self.event_at.astimezone(timezone).date()
            == self.measurement.observed_at.astimezone(timezone).date()
            else ReactionWindow.NEXT_SESSION
        )
        if self.reaction_window is not expected_window:
            raise ValueError("market impact reaction window is inconsistent with timestamps")
        if self.epistemic_status is EpistemicStatus.CAUSALLY_SUPPORTED:
            raise ValueError("C8 v1 cannot emit causally supported impact")
        if self.epistemic_status is EpistemicStatus.ASSOCIATED:
            if self.association_confidence is AssociationConfidence.NONE:
                raise ValueError("associated impact requires non-NONE confidence")
            if self.transmission_basis is None:
                raise ValueError("associated impact requires a transmission basis")
        elif (
            self.association_confidence is not AssociationConfidence.NONE
            or self.transmission_basis is not None
        ):
            raise ValueError("observed-only impact cannot claim association evidence")
        canonical_reasons = tuple(sorted(set(self.reason_codes), key=lambda item: item.value))
        if self.reason_codes != canonical_reasons:
            raise ValueError("market impact reason codes must be sorted and unique")
        if tuple(sorted(set(self.event_evidence_ids))) != self.event_evidence_ids:
            raise ValueError("market impact evidence IDs must be sorted and unique")
        expected_reasons = {
            ImpactReason.POST_EVENT_REACTION,
            ImpactReason.SAME_SESSION
            if self.reaction_window is ReactionWindow.SAME_SESSION
            else ImpactReason.NEXT_SESSION,
            ImpactReason.STRONG_TEMPORAL_ALIGNMENT
            if self.reaction_window is ReactionWindow.SAME_SESSION
            else ImpactReason.WEAK_TEMPORAL_ALIGNMENT,
            ImpactReason.NO_MEANINGFUL_REACTION
            if self.magnitude is ImpactMagnitude.FLAT
            else ImpactReason.LIMITED_MOVE
            if self.magnitude is ImpactMagnitude.SMALL
            else ImpactReason.MATERIAL_MOVE,
        }
        if self.epistemic_status is EpistemicStatus.ASSOCIATED:
            expected_reasons.add(ImpactReason.C6_AUTHORIZED)
            assert self.transmission_basis is not None
            target_kind = self.transmission_basis.target_kind
            if self.target.reaction_profile is ReactionProfile.VOLATILITY_INDEX:
                if target_kind is not ImpactTargetKind.RISK_REACTION:
                    raise ValueError("volatility target requires risk reaction semantics")
            elif target_kind is not _TARGET_KIND_BY_CHANNEL[
                self.transmission_basis.source_channel
            ]:
                raise ValueError("transmission channel and target kind are inconsistent")
            expected_reasons.add(_TARGET_REASON_BY_KIND[target_kind])
        if not expected_reasons <= set(self.reason_codes):
            raise ValueError("market impact reason/component closure is incomplete")
        if (self.direction is ReactionDirection.FLAT) != (
            self.magnitude is ImpactMagnitude.FLAT
        ):
            raise ValueError("FLAT direction and magnitude must agree")
        return self


class C8Diagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    events_evaluated: int = Field(ge=0)
    candidate_pairs: int = Field(ge=0)
    eligible_pairs: int = Field(ge=0)
    rejected_pairs: int = Field(ge=0)
    reaction_records: int = Field(ge=0)
    direction_counts: dict[str, int]
    magnitude_counts: dict[str, int]
    association_counts: dict[str, int]
    rejection_counts: dict[str, int]
    no_meaningful_reaction_count: int = Field(ge=0)
    ambiguous_record_count: int = Field(ge=0)
    targets_with_multiple_plausible_stories: int = Field(ge=0)
    rule_version: str


class C8EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    records: tuple[MarketImpactRecord, ...]
    diagnostics: C8Diagnostics


_MAGNITUDE_THRESHOLDS: dict[ReactionProfile, tuple[float, float, float, float]] = {
    ReactionProfile.EQUITY_INDEX: (0.10, 0.75, 1.50, 3.00),
    ReactionProfile.EQUITY_ETF_PROXY: (0.10, 0.75, 1.50, 3.00),
    ReactionProfile.FX: (0.05, 0.30, 0.75, 1.50),
    ReactionProfile.COMMODITY: (0.10, 0.75, 1.50, 3.00),
    ReactionProfile.CRYPTO: (0.25, 1.50, 3.00, 6.00),
    ReactionProfile.VOLATILITY_INDEX: (1.00, 5.00, 10.00, 20.00),
}

_TARGET_KIND_BY_CHANNEL = {
    TransmissionChannel.DIRECT_ENTITY: ImpactTargetKind.DIRECT_TARGET_REACTION,
    TransmissionChannel.SECTOR_THEME: ImpactTargetKind.SECTOR_REACTION,
    TransmissionChannel.CENTRAL_BANK_POLICY: ImpactTargetKind.REGIONAL_MARKET_REACTION,
    TransmissionChannel.MACROECONOMIC_RELEASE: ImpactTargetKind.MACRO_MARKET_REACTION,
}

_TARGET_REASON_BY_KIND = {
    ImpactTargetKind.DIRECT_TARGET_REACTION: ImpactReason.DIRECT_ENTITY_TARGET,
    ImpactTargetKind.SECTOR_REACTION: ImpactReason.SECTOR_THEME_TARGET,
    ImpactTargetKind.REGIONAL_MARKET_REACTION: ImpactReason.REGIONAL_BROAD_MARKET,
    ImpactTargetKind.MACRO_MARKET_REACTION: ImpactReason.MACRO_REGION_TARGET,
    ImpactTargetKind.RISK_REACTION: ImpactReason.RISK_TARGET,
}


def market_impact_id(
    *,
    story_id: str | None,
    event_id: str,
    symbol: str,
    reaction_window: ReactionWindow,
    rule_version: str,
) -> str:
    identity = f"{story_id or '-'}|{event_id}|{symbol}|{reaction_window.value}|{rule_version}"
    return stable_id("market-impact", identity)


def build_c8_market_target_events(
    markets: Iterable[MarketObservation], evaluated_at: datetime
) -> list[Event]:
    """Project every observation into a runtime-only C6 target, including flat moves."""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("C8 target projection time must be timezone-aware")
    result = []
    for observation in _markets_by_symbol(markets).values():
        event_type = EventType.RISK if observation.symbol == "^VIX" else EventType.MARKET_MOVE
        event_key = f"c8-market-target:{observation.symbol}:{observation.observed_at.isoformat()}"
        result.append(
            Event(
                id=stable_id("c8-market-target", event_key),
                event_key=event_key,
                event_type=event_type,
                title=f"{observation.name} daily close interval",
                occurred_at=observation.observed_at,
                first_seen_at=evaluated_at.astimezone(UTC),
                last_seen_at=evaluated_at.astimezone(UTC),
                entity_ids=[observation.symbol],
                claim_ids=[],
                evidence_ids=[],
                source_document_ids=[],
                importance_score=0.0,
                importance_components={},
                confidence=0.99,
                is_demo=observation.is_demo,
            )
        )
    return result


def _parse_close_time(value: object) -> time:
    if not isinstance(value, str) or re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", value) is None:
        raise ValueError("asset close_time must be HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    if hour > 23:
        raise ValueError("asset close_time must be HH:MM")
    return time(hour, minute)


def _target_snapshot(asset: Mapping[str, Any]) -> MarketTargetSnapshot:
    try:
        timezone = str(asset.get("reaction_timezone", asset["timezone"]))
        ZoneInfo(timezone)
    except (KeyError, ZoneInfoNotFoundError) as exc:
        raise ValueError("C8 asset requires a valid IANA timezone") from exc
    asset_class = AssetClass(str(asset["asset_class"]))
    profile = ReactionProfile(str(asset.get("reaction_profile", asset_class.value)))
    return MarketTargetSnapshot(
        symbol=str(asset["symbol"]),
        name=str(asset["name"]),
        asset_class=asset_class,
        region=str(asset["region"]),
        session_kind=SessionKind(str(asset["session_kind"])),
        timezone=timezone,
        close_time=_parse_close_time(asset.get("reaction_close_time", asset.get("close_time"))),
        bar_date_basis=BarDateBasis.UTC_PROVIDER_TIMESTAMP_DATE,
        reaction_profile=profile,
    )


def _events_by_id(items: Iterable[Event]) -> dict[str, Event]:
    result: dict[str, Event] = {}
    for item in sorted(items, key=lambda event: event.id):
        existing = result.get(item.id)
        if existing is not None and existing != item:
            raise ValueError(f"Conflicting Events for ID {item.id}")
        result.setdefault(item.id, item)
    return result


def _markets_by_symbol(
    items: Iterable[MarketObservation],
) -> dict[str, MarketObservation]:
    result: dict[str, MarketObservation] = {}
    for item in sorted(items, key=lambda market: (market.symbol, market.observed_at, market.id)):
        existing = result.get(item.symbol)
        if existing is not None and existing.observed_at == item.observed_at:
            if existing != item:
                raise ValueError(
                    f"Conflicting market observations for {item.symbol} at {item.observed_at}"
                )
            continue
        result[item.symbol] = item
    return dict(sorted(result.items()))


def _assets_by_symbol(
    items: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in sorted(items, key=lambda asset: str(asset["symbol"])):
        symbol = str(item["symbol"])
        existing = result.get(symbol)
        if existing is not None and dict(existing) != dict(item):
            raise ValueError(f"Conflicting asset profiles for {symbol}")
        result.setdefault(symbol, item)
    return result


def _unique_relationships(
    items: Iterable[EventTargetRelationship],
) -> list[EventTargetRelationship]:
    result: dict[tuple[str, str, str], EventTargetRelationship] = {}
    for item in items:
        key = (item.target_event_id, item.candidate_event_id, item.rule_version)
        existing = result.get(key)
        if existing is not None and existing != item:
            raise ValueError("Conflicting C6 relationships for one C8 pair")
        result.setdefault(key, item)
    return sorted(
        result.values(),
        key=lambda item: (item.candidate_event_id, item.target_event_id, item.rule_version),
    )


def _interval(
    observation: MarketObservation,
    target: MarketTargetSnapshot,
    evaluated_at: datetime,
) -> tuple[datetime, datetime] | ImpactReason:
    previous_at = observation.previous_observed_at
    latest_at = observation.observed_at
    if previous_at is None:
        return ImpactReason.INSUFFICIENT_MARKET_INTERVAL
    if (
        previous_at.tzinfo is None
        or previous_at.utcoffset() is None
        or latest_at.tzinfo is None
        or latest_at.utcoffset() is None
    ):
        return ImpactReason.INSUFFICIENT_MARKET_INTERVAL
    timezone = ZoneInfo(target.timezone)
    reference_at = datetime.combine(
        previous_at.astimezone(UTC).date(), target.close_time, timezone
    ).astimezone(UTC)
    observed_at = datetime.combine(
        latest_at.astimezone(UTC).date(), target.close_time, timezone
    ).astimezone(UTC)
    if reference_at >= observed_at:
        return ImpactReason.INSUFFICIENT_MARKET_INTERVAL
    if evaluated_at.astimezone(UTC) < observed_at:
        return ImpactReason.INCOMPLETE_MARKET_INTERVAL
    return reference_at, observed_at


def _direction_and_magnitude(
    percentage_move: float, profile: ReactionProfile
) -> tuple[ReactionDirection, ImpactMagnitude]:
    absolute = abs(percentage_move)
    flat, moderate, large, extreme = _MAGNITUDE_THRESHOLDS[profile]
    if absolute < flat:
        return ReactionDirection.FLAT, ImpactMagnitude.FLAT
    direction = ReactionDirection.UP if percentage_move > 0 else ReactionDirection.DOWN
    if absolute < moderate:
        magnitude = ImpactMagnitude.SMALL
    elif absolute < large:
        magnitude = ImpactMagnitude.MODERATE
    elif absolute < extreme:
        magnitude = ImpactMagnitude.LARGE
    else:
        magnitude = ImpactMagnitude.EXTREME
    return direction, magnitude


def _target_kind(
    relationship: EventTargetRelationship, target: MarketTargetSnapshot
) -> ImpactTargetKind:
    channel = relationship.transmission_channel
    if channel is None:
        raise ValueError("eligible C8 relationship requires C6 transmission channel")
    if target.reaction_profile is ReactionProfile.VOLATILITY_INDEX:
        return ImpactTargetKind.RISK_REACTION
    return _TARGET_KIND_BY_CHANNEL[channel]


def _association_confidence(
    channel: TransmissionChannel,
    window: ReactionWindow,
) -> AssociationConfidence:
    if channel is TransmissionChannel.DIRECT_ENTITY:
        return (
            AssociationConfidence.HIGH
            if window is ReactionWindow.SAME_SESSION
            else AssociationConfidence.MEDIUM
        )
    return (
        AssociationConfidence.MEDIUM
        if window is ReactionWindow.SAME_SESSION
        else AssociationConfidence.LOW
    )


def _reason_codes(
    *,
    target_kind: ImpactTargetKind,
    window: ReactionWindow,
    magnitude: ImpactMagnitude,
) -> tuple[ImpactReason, ...]:
    reasons = {
        ImpactReason.C6_AUTHORIZED,
        ImpactReason.POST_EVENT_REACTION,
        ImpactReason.SAME_SESSION
        if window is ReactionWindow.SAME_SESSION
        else ImpactReason.NEXT_SESSION,
        ImpactReason.STRONG_TEMPORAL_ALIGNMENT
        if window is ReactionWindow.SAME_SESSION
        else ImpactReason.WEAK_TEMPORAL_ALIGNMENT,
    }
    reasons.add(
        _TARGET_REASON_BY_KIND[target_kind]
    )
    if magnitude is ImpactMagnitude.FLAT:
        reasons.add(ImpactReason.NO_MEANINGFUL_REACTION)
    elif magnitude is ImpactMagnitude.SMALL:
        reasons.add(ImpactReason.LIMITED_MOVE)
    else:
        reasons.add(ImpactReason.MATERIAL_MOVE)
    return tuple(sorted(reasons, key=lambda item: item.value))


def _story_id_by_event(stories: Iterable[Story]) -> dict[str, str]:
    return {stable_id("event", story.id): story.id for story in stories}


def _build_record(
    *,
    event: Event,
    story_id: str | None,
    observation: MarketObservation,
    target: MarketTargetSnapshot,
    relationship: EventTargetRelationship,
    evaluated_at: datetime,
    rule_version: str,
) -> MarketImpactRecord | ImpactReason:
    if event.event_type in {EventType.MARKET_MOVE, EventType.RISK}:
        return ImpactReason.MARKET_EVENT_SOURCE_REJECTED
    if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() is None:
        return ImpactReason.UNKNOWN_EVENT_TIME
    market_interval = _interval(observation, target, evaluated_at)
    if isinstance(market_interval, ImpactReason):
        return market_interval
    raw_reference_at = observation.previous_observed_at
    if raw_reference_at is None:
        return ImpactReason.INSUFFICIENT_MARKET_INTERVAL
    reference_at, observed_at = market_interval
    event_at = event.occurred_at.astimezone(UTC)
    if event_at > observed_at:
        return ImpactReason.PRE_EVENT_MOVE
    if event_at <= reference_at:
        return ImpactReason.EVENT_OUTSIDE_REACTION_WINDOW
    try:
        absolute_move = observation.close - observation.previous_close
        percentage_move = observation.change_pct
        measurement = ReactionMeasurement(
            market_observation_id=observation.id,
            provider=observation.provider,
            raw_reference_at=raw_reference_at,
            raw_observed_at=observation.observed_at,
            reference_at=reference_at,
            observed_at=observed_at,
            currency=observation.currency,
            reference_value=observation.previous_close,
            observed_value=observation.close,
            absolute_move=absolute_move,
            percentage_move=percentage_move,
        )
    except (ArithmeticError, ValidationError, ValueError):
        return ImpactReason.INVALID_MARKET_OBSERVATION
    timezone = ZoneInfo(target.timezone)
    window = (
        ReactionWindow.SAME_SESSION
        if event_at.astimezone(timezone).date() == observed_at.astimezone(timezone).date()
        else ReactionWindow.NEXT_SESSION
    )
    direction, magnitude = _direction_and_magnitude(percentage_move, target.reaction_profile)
    channel = relationship.transmission_channel
    if channel is None:
        raise ValueError("allowed C6 relationship is missing channel")
    target_kind = _target_kind(relationship, target)
    return MarketImpactRecord(
        id=market_impact_id(
            story_id=story_id,
            event_id=event.id,
            symbol=target.symbol,
            reaction_window=window,
            rule_version=rule_version,
        ),
        story_id=story_id,
        event_id=event.id,
        event_at=event_at,
        target=target,
        reaction_window=window,
        measurement=measurement,
        direction=direction,
        magnitude=magnitude,
        epistemic_status=EpistemicStatus.ASSOCIATED,
        association_confidence=_association_confidence(channel, window),
        transmission_basis=TransmissionBasisSnapshot(
            target_kind=target_kind,
            source_channel=channel,
            source_reason_codes=tuple(
                sorted(set(relationship.reason_codes), key=lambda reason: reason.value)
            ),
            source_rule_version=relationship.rule_version,
        ),
        reason_codes=_reason_codes(
            target_kind=target_kind,
            window=window,
            magnitude=magnitude,
        ),
        event_evidence_ids=tuple(sorted(set(event.evidence_ids))),
        rule_version=rule_version,
        evaluated_at=evaluated_at.astimezone(UTC),
    )


def _apply_ambiguity(records: list[MarketImpactRecord]) -> list[MarketImpactRecord]:
    grouped: dict[tuple[str, str, ReactionWindow], list[MarketImpactRecord]] = {}
    for record in records:
        key = (
            record.target.symbol,
            record.measurement.market_observation_id,
            record.reaction_window,
        )
        grouped.setdefault(key, []).append(record)
    result: list[MarketImpactRecord] = []
    for group in grouped.values():
        distinct_sources = {(item.story_id, item.event_id) for item in group}
        if len(distinct_sources) < 2:
            result.extend(group)
            continue
        for item in group:
            reasons = tuple(
                sorted(
                    {*item.reason_codes, ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS},
                    key=lambda reason: reason.value,
                )
            )
            result.append(
                item.model_copy(
                    update={
                        "association_confidence": AssociationConfidence.LOW,
                        "reason_codes": reasons,
                    }
                )
            )
    return sorted(result, key=lambda item: (item.event_id, item.target.symbol, item.id))


def evaluate_market_impacts(
    *,
    candidate_events: Iterable[Event],
    market_events: Iterable[Event],
    markets: Iterable[MarketObservation],
    relationships: Iterable[EventTargetRelationship],
    stories: Iterable[Story],
    assets: Iterable[Mapping[str, Any]],
    evaluated_at: datetime,
    rule_version: str = C8_RULE_VERSION,
) -> C8EvaluationResult:
    """Evaluate C6-authorized pairs against one complete daily close interval."""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("C8 evaluation time must be timezone-aware")
    events_by_id = _events_by_id(candidate_events)
    targets_by_id = _events_by_id(market_events)
    markets_by_symbol = _markets_by_symbol(markets)
    assets_by_symbol = _assets_by_symbol(assets)
    story_ids = _story_id_by_event(stories)
    rejection_counts: Counter[str] = Counter()
    records: list[MarketImpactRecord] = []
    relationships_list = _unique_relationships(relationships)
    eligible = 0
    for relationship in relationships_list:
        if relationship.decision is not EventTargetDecision.ALLOW:
            rejection_counts[ImpactReason.C6_REJECTED.value] += 1
            continue
        eligible += 1
        event = events_by_id.get(relationship.candidate_event_id)
        target_event = targets_by_id.get(relationship.target_event_id)
        if event is None or target_event is None:
            rejection_counts[ImpactReason.TARGET_EVENT_MISSING.value] += 1
            continue
        symbol = next(
            (entity_id for entity_id in target_event.entity_ids if entity_id in assets_by_symbol),
            None,
        )
        if symbol is None or symbol not in markets_by_symbol:
            rejection_counts[ImpactReason.MARKET_OBSERVATION_MISSING.value] += 1
            continue
        try:
            target = _target_snapshot(assets_by_symbol[symbol])
        except (KeyError, ValueError, ValidationError):
            rejection_counts[ImpactReason.TARGET_PROFILE_INVALID.value] += 1
            continue
        built = _build_record(
            event=event,
            story_id=story_ids.get(event.id),
            observation=markets_by_symbol[symbol],
            target=target,
            relationship=relationship,
            evaluated_at=evaluated_at,
            rule_version=rule_version,
        )
        if isinstance(built, ImpactReason):
            rejection_counts[built.value] += 1
        else:
            records.append(built)
    records = _apply_ambiguity(records)
    ambiguous_targets = {
        (item.target.symbol, item.measurement.market_observation_id, item.reaction_window)
        for item in records
        if ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS in item.reason_codes
    }
    diagnostics = C8Diagnostics(
        events_evaluated=len(events_by_id),
        candidate_pairs=len(relationships_list),
        eligible_pairs=eligible,
        rejected_pairs=sum(rejection_counts.values()),
        reaction_records=len(records),
        direction_counts=dict(sorted(Counter(item.direction.value for item in records).items())),
        magnitude_counts=dict(sorted(Counter(item.magnitude.value for item in records).items())),
        association_counts=dict(
            sorted(Counter(item.association_confidence.value for item in records).items())
        ),
        rejection_counts=dict(sorted(rejection_counts.items())),
        no_meaningful_reaction_count=sum(
            ImpactReason.NO_MEANINGFUL_REACTION in item.reason_codes for item in records
        ),
        ambiguous_record_count=sum(
            ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS in item.reason_codes for item in records
        ),
        targets_with_multiple_plausible_stories=len(ambiguous_targets),
        rule_version=rule_version,
    )
    return C8EvaluationResult(records=tuple(records), diagnostics=diagnostics)
