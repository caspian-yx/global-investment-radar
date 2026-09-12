from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from radar.domain import Event, EventType, MarketObservation
from radar.impact import (
    C8_RULE_VERSION,
    AssociationConfidence,
    EpistemicStatus,
    ImpactMagnitude,
    ImpactReason,
    MarketImpactRecord,
    ReactionDirection,
    ReactionProfile,
    ReactionWindow,
    evaluate_market_impacts,
)
from radar.relevance import (
    RELATIONSHIP_RULE_VERSION,
    EventTargetDecision,
    EventTargetRelationship,
    RelationshipReason,
    TransmissionChannel,
)

PREVIOUS_BAR = datetime(2026, 9, 10, 13, 30, tzinfo=UTC)
LATEST_BAR = datetime(2026, 9, 11, 13, 30, tzinfo=UTC)
EVALUATED_AT = datetime(2026, 9, 11, 22, tzinfo=UTC)


def _asset(
    symbol: str = "^GSPC",
    *,
    asset_class: str = "EQUITY_INDEX",
    region: str = "US",
    timezone: str = "America/New_York",
    close_time: str | None = "16:00",
    reaction_profile: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "symbol": symbol,
        "name": symbol,
        "region": region,
        "asset_class": asset_class,
        "session_kind": "EXCHANGE" if close_time else "NEAR_24_HOUR",
        "timezone": timezone,
        "topics": ["RATES"],
    }
    if close_time is not None:
        result["close_time"] = close_time
    if reaction_profile is not None:
        result["reaction_profile"] = reaction_profile
    return result


def _observation(
    symbol: str = "^GSPC",
    *,
    change_pct: float = 1.0,
    previous_observed_at: datetime | None = PREVIOUS_BAR,
    observed_at: datetime = LATEST_BAR,
) -> MarketObservation:
    previous = 100.0
    return MarketObservation(
        id=f"observation-{symbol}",
        symbol=symbol,
        name=symbol,
        region="US",
        previous_observed_at=previous_observed_at,
        observed_at=observed_at,
        close=previous * (1 + change_pct / 100),
        previous_close=previous,
        change_pct=change_pct,
        currency="USD",
        provider="test-daily-bars",
    )


def _event(
    event_id: str = "fed-event",
    *,
    occurred_at: datetime = datetime(2026, 9, 11, 15, tzinfo=UTC),
    event_type: EventType = EventType.POLICY,
    entity_ids: list[str] | None = None,
    importance_score: float = 80.0,
) -> Event:
    return Event(
        id=event_id,
        event_key=f"story:{event_id}",
        event_type=event_type,
        title=event_id,
        occurred_at=occurred_at,
        first_seen_at=occurred_at + timedelta(minutes=5),
        last_seen_at=occurred_at + timedelta(minutes=5),
        entity_ids=entity_ids or ["federal-reserve"],
        claim_ids=[],
        evidence_ids=[],
        source_document_ids=[f"document-{event_id}"],
        importance_score=importance_score,
        importance_components={},
        confidence=0.9,
    )


def _market_event(symbol: str = "^GSPC") -> Event:
    return Event(
        id=f"market-event-{symbol}",
        event_key=f"market:{symbol}:{LATEST_BAR.isoformat()}",
        event_type=EventType.MARKET_MOVE,
        title=f"{symbol} moved",
        occurred_at=LATEST_BAR,
        first_seen_at=EVALUATED_AT,
        last_seen_at=EVALUATED_AT,
        entity_ids=[symbol],
        claim_ids=[],
        evidence_ids=[],
        source_document_ids=[],
        importance_score=50,
        importance_components={},
        confidence=0.99,
    )


def _relationship(
    candidate_id: str = "fed-event",
    *,
    target_id: str = "market-event-^GSPC",
    channel: TransmissionChannel = TransmissionChannel.CENTRAL_BANK_POLICY,
    decision: EventTargetDecision = EventTargetDecision.ALLOW,
) -> EventTargetRelationship:
    reason = {
        TransmissionChannel.CENTRAL_BANK_POLICY: RelationshipReason.CENTRAL_BANK_REGION_MATCH,
        TransmissionChannel.MACROECONOMIC_RELEASE: RelationshipReason.MACRO_REGION_TOPIC_MATCH,
        TransmissionChannel.DIRECT_ENTITY: RelationshipReason.DIRECT_ENTITY_MATCH,
        TransmissionChannel.SECTOR_THEME: RelationshipReason.SECTOR_THEME_MATCH,
    }[channel]
    return EventTargetRelationship(
        target_event_id=target_id,
        candidate_event_id=candidate_id,
        decision=decision,
        transmission_channel=channel if decision is EventTargetDecision.ALLOW else None,
        reason_codes=(
            reason
            if decision is EventTargetDecision.ALLOW
            else RelationshipReason.NO_SUPPORTED_TRANSMISSION,
        ),
        rule_version=RELATIONSHIP_RULE_VERSION,
    )


def _evaluate(
    *,
    event: Event | None = None,
    observation: MarketObservation | None = None,
    asset: dict[str, object] | None = None,
    relationship: EventTargetRelationship | None = None,
):
    source = event or _event()
    market = observation or _observation()
    return evaluate_market_impacts(
        candidate_events=[source],
        market_events=[_market_event(market.symbol)],
        markets=[market],
        relationships=[
            relationship
            or _relationship(source.id, target_id=f"market-event-{market.symbol}")
        ],
        stories=[],
        assets=[asset or _asset(market.symbol)],
        evaluated_at=EVALUATED_AT,
    )


@pytest.mark.parametrize(
    ("move", "direction"),
    [
        (0.11, ReactionDirection.UP),
        (-0.11, ReactionDirection.DOWN),
        (0.099, ReactionDirection.FLAT),
    ],
)
def test_direction_is_objective_and_flat_threshold_is_explicit(
    move: float, direction: ReactionDirection
) -> None:
    record = _evaluate(observation=_observation(change_pct=move)).records[0]

    assert record.direction is direction


@pytest.mark.parametrize(
    ("move", "magnitude"),
    [
        (0.099, ImpactMagnitude.FLAT),
        (0.10, ImpactMagnitude.SMALL),
        (0.75, ImpactMagnitude.MODERATE),
        (1.50, ImpactMagnitude.LARGE),
        (3.00, ImpactMagnitude.EXTREME),
    ],
)
def test_equity_index_magnitude_boundaries(move: float, magnitude: ImpactMagnitude) -> None:
    assert _evaluate(observation=_observation(change_pct=move)).records[0].magnitude is magnitude


def test_magnitude_is_asset_profile_specific() -> None:
    equity = _evaluate(observation=_observation(change_pct=2.0)).records[0]
    volatility = _evaluate(
        observation=_observation(symbol="^VIX", change_pct=2.0),
        asset=_asset("^VIX", reaction_profile="VOLATILITY_INDEX"),
        relationship=_relationship(target_id="market-event-^VIX"),
    ).records[0]

    assert equity.magnitude is ImpactMagnitude.LARGE
    assert volatility.target.reaction_profile is ReactionProfile.VOLATILITY_INDEX
    assert volatility.magnitude is ImpactMagnitude.SMALL


@pytest.mark.parametrize(
    ("symbol", "asset_class", "profile", "thresholds"),
    [
        ("ETF", "EQUITY_ETF_PROXY", None, (0.10, 0.75, 1.50, 3.00)),
        ("FX", "FX", None, (0.05, 0.30, 0.75, 1.50)),
        ("OIL", "COMMODITY", None, (0.10, 0.75, 1.50, 3.00)),
        ("BTC", "CRYPTO", None, (0.25, 1.50, 3.00, 6.00)),
        ("VOL", "EQUITY_INDEX", "VOLATILITY_INDEX", (1.00, 5.00, 10.00, 20.00)),
    ],
)
def test_every_reaction_profile_has_inclusive_versioned_boundaries(
    symbol: str,
    asset_class: str,
    profile: str | None,
    thresholds: tuple[float, float, float, float],
) -> None:
    flat, moderate, large, extreme = thresholds
    asset = _asset(symbol, asset_class=asset_class, reaction_profile=profile)
    relationship = _relationship(target_id=f"market-event-{symbol}")

    results = [
        _evaluate(
            observation=_observation(symbol=symbol, change_pct=move),
            asset=asset,
            relationship=relationship,
        ).records[0].magnitude
        for move in (flat - 0.001, flat, moderate, large, extreme)
    ]

    assert results == [
        ImpactMagnitude.FLAT,
        ImpactMagnitude.SMALL,
        ImpactMagnitude.MODERATE,
        ImpactMagnitude.LARGE,
        ImpactMagnitude.EXTREME,
    ]


def test_same_session_and_next_session_use_daily_close_interval() -> None:
    same = _evaluate(event=_event(occurred_at=datetime(2026, 9, 11, 15, tzinfo=UTC)))
    next_session = _evaluate(event=_event(occurred_at=datetime(2026, 9, 10, 21, tzinfo=UTC)))

    assert same.records[0].reaction_window is ReactionWindow.SAME_SESSION
    assert next_session.records[0].reaction_window is ReactionWindow.NEXT_SESSION
    assert same.records[0].measurement.reference_at == datetime(2026, 9, 10, 20, tzinfo=UTC)
    assert same.records[0].measurement.observed_at == datetime(2026, 9, 11, 20, tzinfo=UTC)


def test_utc_daily_bar_label_does_not_shift_fx_session_to_prior_date() -> None:
    observation = _observation(
        symbol="EURUSD=X",
        previous_observed_at=datetime(2026, 9, 10, tzinfo=UTC),
        observed_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    asset = _asset(
        "EURUSD=X",
        asset_class="FX",
        region="FX",
        timezone="America/New_York",
        close_time="17:00",
    )
    result = _evaluate(
        observation=observation,
        asset=asset,
        relationship=_relationship(target_id="market-event-EURUSD=X"),
    )

    assert result.records[0].measurement.reference_at == datetime(
        2026, 9, 10, 21, tzinfo=UTC
    )
    assert result.records[0].measurement.observed_at == datetime(
        2026, 9, 11, 21, tzinfo=UTC
    )


def test_event_before_daily_interval_is_rejected_as_outside_reaction_window() -> None:
    result = _evaluate(event=_event(occurred_at=datetime(2026, 9, 10, 19, tzinfo=UTC)))

    assert result.records == ()
    assert result.diagnostics.rejection_counts[
        ImpactReason.EVENT_OUTSIDE_REACTION_WINDOW.value
    ] == 1


def test_market_move_completed_before_event_is_never_called_reaction() -> None:
    result = _evaluate(event=_event(occurred_at=datetime(2026, 9, 11, 21, tzinfo=UTC)))

    assert result.records == ()
    assert result.diagnostics.rejected_pairs == 1
    assert result.diagnostics.rejection_counts[ImpactReason.PRE_EVENT_MOVE.value] == 1


def test_unknown_previous_bar_time_is_rejected_conservatively() -> None:
    result = _evaluate(observation=_observation(previous_observed_at=None))

    assert result.records == ()
    assert result.diagnostics.rejection_counts[ImpactReason.INSUFFICIENT_MARKET_INTERVAL.value] == 1


def test_invalid_target_session_profile_is_rejected_conservatively() -> None:
    invalid_asset = _asset()
    invalid_asset["timezone"] = "Not/A_Zone"
    result = _evaluate(asset=invalid_asset)

    assert result.records == ()
    assert result.diagnostics.rejection_counts[ImpactReason.TARGET_PROFILE_INVALID.value] == 1


def test_incomplete_latest_daily_bar_is_rejected() -> None:
    result = evaluate_market_impacts(
        candidate_events=[_event()],
        market_events=[_market_event()],
        markets=[_observation()],
        relationships=[_relationship()],
        stories=[],
        assets=[_asset()],
        evaluated_at=datetime(2026, 9, 11, 19, tzinfo=UTC),
    )

    assert result.records == ()
    assert result.diagnostics.rejection_counts[ImpactReason.INCOMPLETE_MARKET_INTERVAL.value] == 1


def test_unknown_event_timestamp_is_rejected_conservatively() -> None:
    event = _event().model_copy(update={"occurred_at": datetime(2026, 9, 11, 15)})
    result = _evaluate(event=event)

    assert result.records == ()
    assert result.diagnostics.rejection_counts[ImpactReason.UNKNOWN_EVENT_TIME.value] == 1


def test_collection_time_never_substitutes_for_event_time() -> None:
    event = _event(occurred_at=datetime(2026, 9, 10, 19, tzinfo=UTC)).model_copy(
        update={"first_seen_at": datetime(2026, 9, 11, 15, tzinfo=UTC)}
    )

    assert _evaluate(event=event).records == ()


def test_c6_allow_relationship_produces_associated_record_with_snapshot() -> None:
    record = _evaluate().records[0]

    assert record.epistemic_status is EpistemicStatus.ASSOCIATED
    assert record.association_confidence is AssociationConfidence.MEDIUM
    assert record.transmission_basis.source_rule_version == RELATIONSHIP_RULE_VERSION
    assert record.transmission_basis.source_reason_codes == (
        RelationshipReason.CENTRAL_BANK_REGION_MATCH,
    )
    assert ImpactReason.C6_AUTHORIZED in record.reason_codes
    assert record.rule_version == C8_RULE_VERSION


def test_direct_entity_same_session_can_be_high_confidence_even_for_small_move() -> None:
    nvidia = _event(
        "nvidia-event",
        event_type=EventType.GUIDANCE,
        entity_ids=["nvidia"],
        importance_score=20,
    )
    relationship = _relationship(
        "nvidia-event",
        target_id="market-event-NVDA",
        channel=TransmissionChannel.DIRECT_ENTITY,
    )
    record = _evaluate(
        event=nvidia,
        observation=_observation(symbol="NVDA", change_pct=0.2),
        asset=_asset("NVDA"),
        relationship=relationship,
    ).records[0]

    assert record.association_confidence is AssociationConfidence.HIGH
    assert record.magnitude is ImpactMagnitude.SMALL


def test_large_move_can_remain_low_association_for_routine_next_session_event() -> None:
    event = _event(
        importance_score=5.0,
        occurred_at=datetime(2026, 9, 10, 21, tzinfo=UTC),
    )
    record = _evaluate(event=event, observation=_observation(change_pct=12.0)).records[0]

    assert record.magnitude is ImpactMagnitude.EXTREME
    assert record.association_confidence is AssociationConfidence.LOW
    assert record.epistemic_status is EpistemicStatus.ASSOCIATED


def test_rejected_or_unrelated_pair_never_associates_even_for_huge_move() -> None:
    rejected = _relationship(decision=EventTargetDecision.REJECT)
    result = _evaluate(observation=_observation(change_pct=12.0), relationship=rejected)

    assert result.records == ()
    assert result.diagnostics.eligible_pairs == 0
    assert result.diagnostics.rejected_pairs == 1
    assert result.diagnostics.rejection_counts[ImpactReason.C6_REJECTED.value] == 1
    assert result.diagnostics.candidate_pairs == (
        result.diagnostics.reaction_records + result.diagnostics.rejected_pairs
    )


def test_market_move_event_cannot_be_an_impact_source_or_explain_itself() -> None:
    source = _event("source", event_type=EventType.MARKET_MOVE, entity_ids=["^GSPC"])
    result = _evaluate(
        event=source,
        relationship=_relationship(
            "source", target_id="market-event-^GSPC", channel=TransmissionChannel.DIRECT_ENTITY
        ),
    )

    assert result.records == ()
    assert result.diagnostics.rejection_counts[ImpactReason.MARKET_EVENT_SOURCE_REJECTED.value] == 1


def test_flat_move_is_persistable_associated_intelligence() -> None:
    record = _evaluate(observation=_observation(change_pct=0.01)).records[0]

    assert record.direction is ReactionDirection.FLAT
    assert record.magnitude is ImpactMagnitude.FLAT
    assert ImpactReason.NO_MEANINGFUL_REACTION in record.reason_codes


def test_c7_importance_and_relevance_cannot_change_c8_output() -> None:
    low_event = _event(importance_score=1.0)
    critical_event = _event(importance_score=100.0).model_copy(update={"title": "New title"})
    low = _evaluate(event=low_event, observation=_observation(change_pct=0.01)).records[0]
    critical = _evaluate(
        event=critical_event, observation=_observation(change_pct=0.01)
    ).records[0]

    assert low == critical
    assert critical.magnitude is ImpactMagnitude.FLAT
    assert ImpactReason.NO_MEANINGFUL_REACTION in critical.reason_codes


def test_competing_events_are_all_preserved_and_confidence_is_capped() -> None:
    first = _event("a-event")
    second = _event("b-event", occurred_at=datetime(2026, 9, 11, 16, tzinfo=UTC))
    result = evaluate_market_impacts(
        candidate_events=[second, first],
        market_events=[_market_event()],
        markets=[_observation(change_pct=2.0)],
        relationships=[_relationship("b-event"), _relationship("a-event")],
        stories=[],
        assets=[_asset()],
        evaluated_at=EVALUATED_AT,
    )

    assert [item.event_id for item in result.records] == ["a-event", "b-event"]
    assert all(item.association_confidence is AssociationConfidence.LOW for item in result.records)
    assert all(
        ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS in item.reason_codes for item in result.records
    )
    assert result.diagnostics.targets_with_multiple_plausible_stories == 1


def test_evaluation_is_permutation_invariant_with_stable_ids() -> None:
    events = [_event("b-event"), _event("a-event")]
    relationships = [_relationship("a-event"), _relationship("b-event")]
    kwargs = {
        "market_events": [_market_event()],
        "markets": [_observation()],
        "stories": [],
        "assets": [_asset()],
        "evaluated_at": EVALUATED_AT,
    }

    first = evaluate_market_impacts(
        candidate_events=events, relationships=relationships, **kwargs
    )
    second = evaluate_market_impacts(
        candidate_events=list(reversed(events)),
        relationships=list(reversed(relationships)),
        **kwargs,
    )

    assert first == second
    assert len({item.id for item in first.records}) == 2


def test_market_impact_model_is_frozen_strict_and_rejects_extra_fields() -> None:
    record = _evaluate().records[0]

    with pytest.raises(ValidationError):
        MarketImpactRecord.model_validate({**record.model_dump(), "reasoning": "because"})
    with pytest.raises(ValidationError):
        record.event_id = "changed"  # type: ignore[misc]
    causal_payload = record.model_dump()
    causal_payload["epistemic_status"] = EpistemicStatus.CAUSALLY_SUPPORTED
    with pytest.raises(ValidationError, match="cannot emit causally supported"):
        MarketImpactRecord.model_validate(causal_payload)


def test_canonical_model_can_represent_observed_without_claiming_association() -> None:
    associated = _evaluate().records[0]
    observational_reasons = tuple(
        reason
        for reason in associated.reason_codes
        if reason
        not in {
            ImpactReason.C6_AUTHORIZED,
            ImpactReason.REGIONAL_BROAD_MARKET,
        }
    )
    observed = MarketImpactRecord.model_validate(
        {
            **associated.model_dump(),
            "epistemic_status": EpistemicStatus.OBSERVED,
            "association_confidence": AssociationConfidence.NONE,
            "transmission_basis": None,
            "reason_codes": observational_reasons,
        }
    )

    assert observed.epistemic_status is EpistemicStatus.OBSERVED
    assert observed.association_confidence is AssociationConfidence.NONE


def test_reason_component_closure_is_enforced() -> None:
    record = _evaluate().records[0]
    payload = record.model_dump()
    payload["reason_codes"] = (ImpactReason.C6_AUTHORIZED,)

    with pytest.raises(ValidationError):
        MarketImpactRecord.model_validate(payload)


def test_invalid_non_finite_or_nonpositive_market_values_produce_no_record() -> None:
    invalid = _observation().model_copy(update={"close": float("nan")})
    zero_reference = _observation().model_copy(update={"previous_close": 0.0})

    assert _evaluate(observation=invalid).records == ()
    assert _evaluate(observation=zero_reference).records == ()


def test_measurement_preserves_raw_provider_timestamps_and_currency() -> None:
    record = _evaluate().records[0]

    assert record.measurement.raw_reference_at == PREVIOUS_BAR
    assert record.measurement.raw_observed_at == LATEST_BAR
    assert record.measurement.currency == "USD"


def test_exact_duplicate_inputs_are_idempotent_and_conflicts_fail_closed() -> None:
    event = _event()
    target = _market_event()
    market = _observation()
    relationship = _relationship()
    kwargs = {
        "candidate_events": [event, event],
        "market_events": [target, target],
        "relationships": [relationship, relationship],
        "stories": [],
        "assets": [_asset(), _asset()],
        "evaluated_at": EVALUATED_AT,
    }

    result = evaluate_market_impacts(markets=[market, market], **kwargs)

    assert len(result.records) == 1
    conflicting = market.model_copy(update={"close": 103.0, "change_pct": 3.0})
    with pytest.raises(ValueError, match="Conflicting market observations"):
        evaluate_market_impacts(markets=[market, conflicting], **kwargs)
