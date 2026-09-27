from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from radar.domain import Event, EventType, MarketObservation
from radar.impact import (
    C8_RULE_VERSION,
    ImpactReason,
    MarketImpactRecord,
    ReactionWindow,
    evaluate_market_impacts,
    market_impact_id,
)
from radar.relevance import (
    RELATIONSHIP_RULE_VERSION,
    EventTargetDecision,
    EventTargetRelationship,
    RelationshipReason,
    TransmissionChannel,
)
from radar.storage import C8_MARKET_REACTIONS_MIGRATION, RadarStore, c8_market_impacts_table

PREVIOUS = datetime(2026, 9, 10, 13, 30, tzinfo=UTC)
LATEST = datetime(2026, 9, 11, 13, 30, tzinfo=UTC)
EVALUATED = datetime(2026, 9, 11, 22, tzinfo=UTC)


def _record(*, event_id: str = "event", symbol: str = "^GSPC", rule: str = C8_RULE_VERSION):
    event = Event(
        id=event_id,
        event_key=f"story:{event_id}",
        event_type=EventType.POLICY,
        title="Policy decision",
        occurred_at=datetime(2026, 9, 11, 15, tzinfo=UTC),
        first_seen_at=datetime(2026, 9, 11, 16, tzinfo=UTC),
        last_seen_at=datetime(2026, 9, 11, 16, tzinfo=UTC),
        entity_ids=["federal-reserve"],
        claim_ids=[],
        evidence_ids=["evidence"],
        source_document_ids=["document"],
        importance_score=80,
        importance_components={},
        confidence=0.9,
    )
    target = Event(
        id=f"target-{symbol}",
        event_key=f"market:{symbol}",
        event_type=EventType.MARKET_MOVE,
        title="Market moved",
        occurred_at=LATEST,
        first_seen_at=EVALUATED,
        last_seen_at=EVALUATED,
        entity_ids=[symbol],
        claim_ids=[],
        evidence_ids=[],
        source_document_ids=[],
        importance_score=50,
        importance_components={},
        confidence=0.99,
    )
    market = MarketObservation(
        id=f"market-{symbol}",
        symbol=symbol,
        name=symbol,
        region="US",
        previous_observed_at=PREVIOUS,
        observed_at=LATEST,
        close=101.0,
        previous_close=100.0,
        change_pct=1.0,
        provider="test",
    )
    relationship = EventTargetRelationship(
        target_event_id=target.id,
        candidate_event_id=event.id,
        decision=EventTargetDecision.ALLOW,
        transmission_channel=TransmissionChannel.CENTRAL_BANK_POLICY,
        reason_codes=(RelationshipReason.CENTRAL_BANK_REGION_MATCH,),
        rule_version=RELATIONSHIP_RULE_VERSION,
    )
    return evaluate_market_impacts(
        candidate_events=[event],
        market_events=[target],
        markets=[market],
        relationships=[relationship],
        stories=[],
        assets=[
            {
                "symbol": symbol,
                "name": symbol,
                "region": "US",
                "asset_class": "EQUITY_INDEX",
                "session_kind": "EXCHANGE",
                "timezone": "America/New_York",
                "close_time": "16:00",
            }
        ],
        evaluated_at=EVALUATED,
        rule_version=rule,
    ).records[0]


def test_migration_005_is_additive_repeatable_and_validated(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c8.db').as_posix()}")

    store.initialize()
    store.initialize()

    with store.engine.connect() as connection:
        versions = set(
            connection.exec_driver_sql("SELECT version FROM schema_migrations").scalars()
        )
        legacy_tables = {
            "events",
            "driver_candidates",
            "stories",
            "story_documents",
            "c7_rankings",
        }
        names = set(connection.exec_driver_sql("SELECT name FROM sqlite_master").scalars())
    assert C8_MARKET_REACTIONS_MIGRATION in versions
    assert legacy_tables <= names
    assert "c8_market_impacts" in names


def test_migration_005_rejects_incomplete_existing_schema_without_marker(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c8-invalid.db').as_posix()}")
    with store.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE c8_market_impacts (id VARCHAR(36) PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="C8 migration is incomplete"):
        store.initialize()

    with store.engine.connect() as connection:
        marker = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (C8_MARKET_REACTIONS_MIGRATION,),
        ).first()
    assert marker is None


def test_exact_evaluation_is_idempotently_refreshed_and_round_trips(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c8-refresh.db').as_posix()}")
    store.initialize()
    record = _record()

    store.save_market_impacts("run-1", [record])
    refreshed = record.model_copy(update={"evaluated_at": EVALUATED.replace(minute=1)})
    store.save_market_impacts("run-1", [refreshed])

    assert store.table_count(c8_market_impacts_table) == 1
    assert store.load_market_impacts("run-1", C8_RULE_VERSION) == [refreshed]


def test_different_targets_windows_rules_and_runs_preserve_history(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c8-history.db').as_posix()}")
    store.initialize()
    base = _record()
    other_target = _record(symbol="^NDX")
    other_rule = _record(rule="c8-daily-close-v2")
    next_window = MarketImpactRecord.model_validate(
        {
            **base.model_dump(),
            "id": market_impact_id(
                story_id=base.story_id,
                event_id=base.event_id,
                symbol=base.target.symbol,
                reaction_window=ReactionWindow.NEXT_SESSION,
                rule_version=base.rule_version,
            ),
            "event_at": datetime(2026, 9, 10, 21, tzinfo=UTC),
            "reaction_window": ReactionWindow.NEXT_SESSION,
            "reason_codes": tuple(
                sorted(
                    {
                        reason
                        for reason in base.reason_codes
                        if reason.value not in {"SAME_SESSION", "STRONG_TEMPORAL_ALIGNMENT"}
                    }
                    | {
                        ImpactReason.NEXT_SESSION,
                        ImpactReason.WEAK_TEMPORAL_ALIGNMENT,
                    },
                    key=lambda reason: reason.value,
                )
            ),
        }
    )

    transaction_count = 0

    def count_transaction(_connection) -> None:
        nonlocal transaction_count
        transaction_count += 1

    event.listen(store.engine, "begin", count_transaction)
    store.save_market_impacts("run-1", [base, other_target, other_rule, next_window])
    assert transaction_count == 1
    store.save_market_impacts("run-2", [base])

    assert store.table_count(c8_market_impacts_table) == 5
    assert len(store.load_market_impacts("run-1", C8_RULE_VERSION)) == 3
    assert store.load_market_impacts("run-1", "c8-daily-close-v2") == [other_rule]
