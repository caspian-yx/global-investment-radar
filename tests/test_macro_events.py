from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from radar.deterministic import build_macro_event
from radar.domain import (
    ClaimType,
    DriverTemporalRelation,
    Event,
    EventType,
    Evidence,
    MacroObservation,
    MarketObservation,
    SourceCategory,
    SourceKind,
    SourceRole,
    SourceTier,
    content_hash,
    stable_id,
)
from radar.explanation import (
    build_driver_candidates,
    build_explanation_bundles,
    validate_driver_candidate_bundle,
)
from radar.macro import build_macro_source_document
from radar.relevance import EventTargetDecision, build_event_target_relationships
from radar.storage import (
    RadarStore,
    claims_table,
    events_table,
    evidence_table,
    macro_table,
)

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


def macro_observation(
    period: str,
    *,
    series_id: str = "CUUR0000SA0",
    name: str = "U.S. CPI All Urban Consumers",
    value: float = 324.5,
    previous_value: float | None = 324.0,
    change: float | None = 0.5,
    unit: str = "index",
    observed_at: datetime = NOW,
    release_at: datetime | None = None,
    linked_source: bool = False,
) -> MacroObservation:
    observation = MacroObservation(
        id=stable_id("macro", f"BLS|{series_id}|{period}"),
        series_id=series_id,
        name=name,
        period=period,
        observed_at=observed_at,
        value=value,
        previous_value=previous_value,
        change=change,
        change_label="vs previous month" if change is not None else None,
        unit=unit,
        provider="bls-public-api",
        release_at=release_at,
    )
    if not linked_source:
        return observation
    # This branch models a release-aware upstream fixture; the live BLS adapter
    # intentionally leaves release_at unknown.
    source = build_macro_source_document(observation)
    assert source is not None
    return observation.model_copy(update={"source_document_id": source.id})


def test_macro_source_document_is_official_stable_and_not_fabricated_news() -> None:
    release_at = NOW - timedelta(days=1)
    observation = macro_observation(
        "2026-08", release_at=release_at, linked_source=True
    )

    first = build_macro_source_document(observation)
    second = build_macro_source_document(observation.model_copy(deep=True))
    revised = build_macro_source_document(observation.model_copy(update={"value": 324.6}))

    assert first == second
    assert first is not None
    assert revised is not None
    assert first.id == observation.source_document_id
    assert revised.id == first.id
    assert revised.content_hash != first.content_hash
    assert first.provider_native_id == "CUUR0000SA0:2026-08"
    assert first.publisher == "U.S. Bureau of Labor Statistics"
    assert first.source_category is SourceCategory.OFFICIAL
    assert first.source_role is SourceRole.PRIMARY
    assert first.source_tier is SourceTier.A
    assert first.canonical_url == "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    assert first.published_at == release_at
    assert first.collected_at == observation.observed_at
    assert "first observed by Radar" in first.content
    assert "news" not in first.content.casefold()


def test_macro_source_document_requires_trusted_release_time() -> None:
    observation = macro_observation("2026-08")

    assert build_macro_source_document(observation) is None


def test_macro_event_requires_trusted_release_time() -> None:
    assert build_macro_event(macro_observation("2026-08"), NOW) is None


def test_legacy_macro_payload_defaults_release_time_to_unknown() -> None:
    payload = macro_observation("2026-08").model_dump()
    payload.pop("release_at")

    restored = MacroObservation.model_validate(payload)

    assert restored.release_at is None


def test_trusted_macro_event_uses_release_time_and_document_evidence() -> None:
    first_observed = datetime(2026, 9, 9, 12, 30, tzinfo=UTC)
    release_at = first_observed - timedelta(days=1)
    observation = macro_observation(
        "2026-08",
        observed_at=first_observed,
        release_at=release_at,
        linked_source=True,
    )

    event, claim, evidence = build_macro_event(observation, NOW + timedelta(days=5))

    assert event.occurred_at == release_at
    assert event.occurred_at != observation.observed_at
    assert event.source_document_ids == [observation.source_document_id]
    assert evidence.source_kind is SourceKind.DOCUMENT
    assert evidence.source_id == observation.source_document_id
    assert evidence.source_document_id == observation.source_document_id
    assert claim.evidence_ids == [evidence.id]
    assert "2026-08" in evidence.excerpt


def test_macro_bootstrap_saves_observation_without_event(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'bootstrap.db').as_posix()}")
    store.initialize()

    events, claims, evidence = store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-08")], NOW
    )

    assert events == claims == evidence == []
    assert store.table_count(macro_table) == 1
    assert store.table_count(events_table) == 0


def test_trusted_new_macro_period_creates_exactly_one_stable_derived_event(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'new-period.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-07")], NOW
    )
    current = macro_observation(
        "2026-08", release_at=NOW - timedelta(days=1), linked_source=True
    )

    first = store.save_macro_with_first_observed_events("run-2", [current], NOW)
    repeated = store.save_macro_with_first_observed_events("run-3", [current], NOW)

    events, claims, evidence = first
    assert len(events) == len(claims) == len(evidence) == 1
    assert repeated == ([], [], [])
    assert store.table_count(events_table) == 1
    assert events[0].event_key == "macro:CUUR0000SA0:2026-08"
    assert claims[0].claim_type is ClaimType.DERIVED
    assert claims[0].claim_type is not ClaimType.INTERPRETATION
    assert evidence[0].source_kind is SourceKind.DOCUMENT
    assert evidence[0].source_id == current.source_document_id
    assert evidence[0].source_document_id == current.source_document_id
    assert "first observed by Radar" in claims[0].text
    assert "official release" not in claims[0].text.casefold()

    other_store = RadarStore(f"sqlite:///{(tmp_path / 'stable.db').as_posix()}")
    other_store.initialize()
    other_store.save_macro_with_first_observed_events(
        "run-a", [macro_observation("2026-07")], NOW
    )
    other_event = other_store.save_macro_with_first_observed_events(
        "run-b", [current], NOW
    )[0][0]
    assert other_event.id == events[0].id


def test_unknown_macro_release_persists_without_event(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'unknown-release.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-07")], NOW
    )

    events, claims, evidence = store.save_macro_with_first_observed_events(
        "run-2", [macro_observation("2026-08")], NOW
    )

    assert events == claims == evidence == []
    assert store.table_count(macro_table) == 2
    assert store.table_count(events_table) == 0


def test_macro_change_units_are_semantically_correct(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'units.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1",
        [
            macro_observation("2026-07"),
            macro_observation(
                "2026-07",
                series_id="LNS14000000",
                name="U.S. Unemployment Rate",
                value=4.1,
                previous_value=4.0,
                change=0.1,
                unit="%",
            ),
        ],
        NOW,
    )

    _, _, cpi_evidence = store.save_macro_with_first_observed_events(
        "run-2",
        [
            macro_observation(
                "2026-08", release_at=NOW - timedelta(days=1), linked_source=True
            )
        ],
        NOW,
    )
    _, _, unemployment_evidence = store.save_macro_with_first_observed_events(
        "run-2",
        [
            macro_observation(
                "2026-08",
                series_id="LNS14000000",
                name="U.S. Unemployment Rate",
                value=4.2,
                previous_value=4.1,
                change=0.1,
                unit="%",
                release_at=NOW - timedelta(days=1),
                linked_source=True,
            )
        ],
        NOW,
    )

    assert "index points" in cpi_evidence[0].excerpt
    assert "+0.5%" not in cpi_evidence[0].excerpt
    assert "percentage points" in unemployment_evidence[0].excerpt


def test_linked_macro_source_reaches_c6_c2_and_explanation_bundle() -> None:
    observation = macro_observation(
        "2026-08",
        observed_at=datetime(2026, 9, 9, 13, 30, tzinfo=UTC),
        release_at=datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
        linked_source=True,
    )
    source = build_macro_source_document(observation)
    assert source is not None
    macro_event, _, macro_evidence = build_macro_event(observation, NOW)
    target_at = datetime(2026, 9, 10, 20, tzinfo=UTC)
    market = MarketObservation(
        id="market-sp500",
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=target_at,
        close=102,
        previous_close=100,
        change_pct=2,
        currency="USD",
        provider="test-market",
    )
    target_evidence = Evidence(
        id="evidence-sp500",
        source_kind=SourceKind.MARKET_OBSERVATION,
        source_id=market.id,
        locator={"type": "market_bar", "symbol": market.symbol},
        excerpt="S&P 500 moved 2%.",
        checksum=content_hash("S&P 500 moved 2%."),
        quality_score=0.75,
    )
    target = Event(
        id="target-sp500",
        event_key="market:^GSPC:2026-09-10",
        event_type=EventType.MARKET_MOVE,
        title="S&P 500 moved materially",
        occurred_at=target_at,
        first_seen_at=target_at,
        last_seen_at=target_at,
        entity_ids=["^GSPC"],
        claim_ids=[],
        evidence_ids=[target_evidence.id],
        source_document_ids=[],
        importance_score=65,
        importance_components={"materiality": 65},
        confidence=0.99,
    )
    asset = {
        "symbol": "^GSPC",
        "name": "S&P 500",
        "region": "US",
        "asset_class": "EQUITY_INDEX",
        "session_kind": "EXCHANGE",
        "timezone": "America/New_York",
        "close_time": "16:00",
        "topics": ["RATES", "INFLATION", "EMPLOYMENT", "US"],
    }

    relationship = build_event_target_relationships(
        target_events=[target],
        candidate_events=[macro_event],
        documents=[source],
        assets=[asset],
    )[0]
    assert relationship.decision is EventTargetDecision.ALLOW
    candidates, driver_claims = build_driver_candidates(
        target_events=[target],
        candidate_events=[macro_event],
        evidence=[target_evidence, macro_evidence],
        documents=[source],
        assets=[asset],
        as_of=target_at,
    )
    repeated, _ = build_driver_candidates(
        target_events=[target],
        candidate_events=[macro_event],
        evidence=[macro_evidence, target_evidence],
        documents=[source],
        assets=[asset],
        as_of=target_at,
    )

    assert len(candidates) == 1
    assert candidates[0].id == repeated[0].id
    assert candidates[0].candidate_event_id == macro_event.id
    assert candidates[0].temporal_relation is DriverTemporalRelation.PRIOR
    validate_driver_candidate_bundle(
        candidates[0],
        events=[target, macro_event],
        claims=driver_claims,
        evidence=[target_evidence, macro_evidence],
        markets=[market],
        documents=[source],
        assets=[asset],
    )
    bundles = build_explanation_bundles(
        target_events=[target],
        driver_candidates=candidates,
        claims=driver_claims,
        evidence=[target_evidence, macro_evidence],
    )
    assert bundles[0].driver_candidates == candidates
    assert bundles[0].evidence == sorted(
        [target_evidence, macro_evidence], key=lambda item: item.id
    )


def test_macro_event_failure_does_not_consume_first_observed_state(
    tmp_path, monkeypatch
) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-07")], NOW
    )
    current = macro_observation(
        "2026-08", release_at=NOW - timedelta(days=1), linked_source=True
    )
    original = store._save_macro_event_bundle

    def fail_bundle(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        raise RuntimeError("simulated event persistence failure")

    monkeypatch.setattr(store, "_save_macro_event_bundle", fail_bundle)
    with pytest.raises(RuntimeError, match="simulated event persistence failure"):
        store.save_macro_with_first_observed_events("run-2", [current], NOW)

    assert store.table_count(macro_table) == 1
    monkeypatch.setattr(store, "_save_macro_event_bundle", original)
    events, _, _ = store.save_macro_with_first_observed_events("run-2", [current], NOW)
    assert len(events) == 1
    assert store.table_count(macro_table) == 2
    assert store.table_count(evidence_table) == 1
    assert store.table_count(claims_table) == 1
    assert store.table_count(events_table) == 1
