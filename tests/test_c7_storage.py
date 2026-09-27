from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import event

from radar.importance import (
    C7_RULE_VERSION,
    C7Score,
    ImportanceBand,
    ImportanceReason,
    PriorityBand,
    RelevanceBand,
    ViewProfile,
)
from radar.storage import (
    C7_IMPORTANCE_RELEVANCE_MIGRATION,
    RadarStore,
    c7_rankings_table,
)


def _score(
    *, event_id: str = "event-a", view_id: str = "global", rule_version: str = C7_RULE_VERSION
) -> C7Score:
    return C7Score(
        event_id=event_id,
        story_id="story-a",
        view_id=view_id,
        rule_version=rule_version,
        scored_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        importance_score=80,
        importance_band=ImportanceBand.CRITICAL,
        importance_components=(),
        importance_reason_codes=(ImportanceReason.MAJOR_POLICY_DECISION,),
        relevance_score=55,
        relevance_band=RelevanceBand.HIGH,
        relevance_components=(),
        relevance_reason_codes=(),
        priority_band=PriorityBand.MUST_READ,
        priority_rank=1,
        view_snapshot=ViewProfile(id=view_id, name=view_id),
    )


def test_c7_migration_is_additive_repeatable_and_round_trips_multiple_views(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c7.db').as_posix()}")
    store.initialize()
    transaction_count = 0

    def count_transaction(_connection) -> None:
        nonlocal transaction_count
        transaction_count += 1

    event.listen(store.engine, "begin", count_transaction)
    store.initialize()
    transaction_count = 0
    store.save_c7_scores("run-1", [_score(), _score(view_id="us")])
    assert transaction_count == 1

    transaction_count = 0
    store.save_c7_scores("run-1", [_score(), _score(view_id="us")])
    assert transaction_count == 1

    assert store.load_c7_scores("run-1", "global", C7_RULE_VERSION) == [_score()]
    assert store.load_c7_scores("run-1", "us", C7_RULE_VERSION) == [_score(view_id="us")]
    assert store.table_count(c7_rankings_table) == 2
    with store.engine.connect() as connection:
        versions = set(
            connection.exec_driver_sql("SELECT version FROM schema_migrations").scalars()
        )
    assert C7_IMPORTANCE_RELEVANCE_MIGRATION in versions


def test_c7_old_rule_version_rows_are_not_overwritten(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c7-history.db').as_posix()}")
    store.initialize()
    old = _score(rule_version="c7-v0")
    current = _score(rule_version=C7_RULE_VERSION)
    store.save_c7_scores("run-1", [old, current])
    assert store.load_c7_scores("run-1", "global", "c7-v0") == [old]
    assert store.load_c7_scores("run-1", "global", C7_RULE_VERSION) == [current]
    assert store.table_count(c7_rankings_table) == 2


def test_c7_same_run_evaluation_can_refresh_without_crossing_identity_boundary(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c7-refresh.db').as_posix()}")
    store.initialize()
    original = _score()
    refreshed = original.model_copy(update={"relevance_score": 25})
    store.save_c7_scores("run-1", [original])
    store.save_c7_scores("run-1", [refreshed])
    assert store.load_c7_scores("run-1", "global", C7_RULE_VERSION) == [refreshed]
    assert store.table_count(c7_rankings_table) == 1
