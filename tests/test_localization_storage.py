from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from test_briefing_composition import _compose
from test_localization import _translated_payload

from radar.llm import FakeLLMProvider, LLMResponse, LLMUsage
from radar.localization import (
    C91_LOCALE,
    C91_RULE_VERSION,
    build_localization_snapshot,
    localize_briefing,
    validate_localization_snapshot,
)
from radar.storage import C9_LOCALIZATIONS_MIGRATION, RadarStore, c9_localizations_table


def _snapshot(brief, model: str = "deepseek-v4-flash"):
    response = LLMResponse(
        content=json.dumps(_translated_payload(brief), ensure_ascii=False),
        provider="fake",
        requested_model=model,
        returned_model="deepseek-flash",
        usage=LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        latency_seconds=0.25,
    )
    result = localize_briefing(
        brief,
        FakeLLMProvider(response=response),
        enabled=True,
        requested_model=model,
    )
    return build_localization_snapshot(
        brief,
        result,
        created_at=datetime(2026, 9, 13, 1, tzinfo=UTC),
    )


def test_migration_007_is_additive_repeatable_and_validated(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'localization.db').as_posix()}")

    store.initialize()
    store.initialize()

    inspector = inspect(store.engine)
    columns = {item["name"] for item in inspector.get_columns("c9_localizations")}
    indexes = inspector.get_indexes("c9_localizations")
    with store.engine.connect() as connection:
        versions = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())
        legacy_tables = set(connection.exec_driver_sql("SELECT name FROM sqlite_master").scalars())

    assert C9_LOCALIZATIONS_MIGRATION in versions
    assert {
        "id",
        "briefing_id",
        "run_id",
        "view_id",
        "briefing_rule_version",
        "locale",
        "localization_rule_version",
        "requested_model",
        "returned_model",
        "provider",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_seconds",
        "created_at",
        "payload_hash",
        "payload_json",
    } <= columns
    assert any(index["unique"] for index in indexes)
    assert {"c9_daily_briefings", "c8_market_impacts", "c7_rankings"} <= legacy_tables


def test_identical_localization_snapshot_is_idempotent_and_round_trips(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'roundtrip.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()
    snapshot = _snapshot(brief)

    store.save_localization_snapshot(snapshot)
    store.save_localization_snapshot(snapshot)

    assert store.table_count(c9_localizations_table) == 1
    loaded = store.load_localization_snapshot(
        brief.id,
        C91_LOCALE,
        C91_RULE_VERSION,
        snapshot.requested_model,
    )
    assert loaded == snapshot


def test_conflicting_localization_snapshot_fails_closed(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'conflict.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()
    snapshot = _snapshot(brief)
    conflict = snapshot.model_copy(
        update={"content": snapshot.content.model_copy(update={"briefing_id": brief.id})}
    )
    conflict = conflict.model_copy(
        update={
            "content": snapshot.content.model_copy(
                update={
                    "primary_stories": tuple(
                        item.model_copy(update={"headline": "changed"})
                        for item in snapshot.content.primary_stories
                    )
                }
            )
        }
    )

    store.save_localization_snapshot(snapshot)
    with pytest.raises(ValueError, match="immutable C9.1 localization"):
        store.save_localization_snapshot(conflict)


def test_localization_checksum_tamper_fails_closed(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'tamper.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()
    snapshot = _snapshot(brief)
    store.save_localization_snapshot(snapshot)

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE c9_localizations SET payload_json = :payload WHERE id = :id"),
            {"payload": "{}", "id": snapshot.id},
        )

    with pytest.raises(RuntimeError, match="checksum"):
        store.load_localization_snapshot(
            brief.id,
            C91_LOCALE,
            C91_RULE_VERSION,
            snapshot.requested_model,
        )


def test_different_requested_models_preserve_separate_localization_history(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'history.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()
    first = _snapshot(brief, "model-one")
    second = _snapshot(brief, "model-two")

    store.save_localization_snapshot(first)
    store.save_localization_snapshot(second)

    assert store.table_count(c9_localizations_table) == 2
    assert store.load_localization_snapshot(
        brief.id, C91_LOCALE, C91_RULE_VERSION, "model-one"
    ) == first
    assert store.load_localization_snapshot(
        brief.id, C91_LOCALE, C91_RULE_VERSION, "model-two"
    ) == second


def test_snapshot_must_match_the_canonical_briefing_identity() -> None:
    brief, _ = _compose()
    snapshot = _snapshot(brief)
    mismatched = snapshot.model_copy(update={"run_id": "different-run"})

    with pytest.raises(ValueError, match="identity"):
        validate_localization_snapshot(mismatched, brief)
