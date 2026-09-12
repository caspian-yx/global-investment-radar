from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from test_briefing_composition import _compose, _items, _quality
from test_briefing_selection import NOW, _select

from radar.briefing import C9_RULE_VERSION, compose_daily_brief
from radar.storage import (
    C9_DAILY_BRIEFINGS_MIGRATION,
    RadarStore,
    c9_daily_briefings_table,
)


def test_migration_006_is_additive_repeatable_and_validated(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c9.db').as_posix()}")

    store.initialize()
    store.initialize()

    inspector = inspect(store.engine)
    columns = {item["name"] for item in inspector.get_columns("c9_daily_briefings")}
    indexes = inspector.get_indexes("c9_daily_briefings")
    with store.engine.connect() as connection:
        versions = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())
        legacy_tables = set(connection.exec_driver_sql("SELECT name FROM sqlite_master").scalars())
    assert C9_DAILY_BRIEFINGS_MIGRATION in versions
    assert {
        "id",
        "run_id",
        "report_date",
        "view_id",
        "rule_version",
        "composed_at",
        "primary_count",
        "payload_hash",
        "payload_json",
    } <= columns
    assert any(
        index["column_names"] == ["run_id", "view_id", "rule_version"] and index["unique"]
        for index in indexes
    )
    assert {"events", "c7_rankings", "c8_market_impacts"} <= legacy_tables


def test_migration_006_rejects_incomplete_existing_schema(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c9-invalid.db').as_posix()}")
    with store.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE c9_daily_briefings (id VARCHAR(36) PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="C9 migration is incomplete"):
        store.initialize()

    with store.engine.connect() as connection:
        marker = connection.execute(
            text("SELECT version FROM schema_migrations WHERE version = :version"),
            {"version": C9_DAILY_BRIEFINGS_MIGRATION},
        ).first()
    assert marker is None


def test_identical_snapshot_is_idempotent_and_round_trips(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c9-roundtrip.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()

    store.save_daily_briefing(brief)
    store.save_daily_briefing(brief)

    assert store.table_count(c9_daily_briefings_table) == 1
    assert store.load_daily_briefing("run-c9", "global", C9_RULE_VERSION) == brief


def test_snapshot_checksum_rejects_tampered_payload(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c9-tamper.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()
    store.save_daily_briefing(brief)

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE c9_daily_briefings SET payload_json = :payload WHERE run_id = :run_id"),
            {"payload": "{}", "run_id": brief.run_id},
        )

    with pytest.raises(RuntimeError, match="checksum"):
        store.load_daily_briefing(brief.run_id, brief.view_id, brief.rule_version)


def test_conflicting_same_run_view_rule_snapshot_fails_closed(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c9-conflict.db').as_posix()}")
    store.initialize()
    brief, _ = _compose()
    conflict = brief.model_copy(update={"view_name": "Changed after composition"})

    store.save_daily_briefing(brief)
    with pytest.raises(ValueError, match="immutable C9 briefing"):
        store.save_daily_briefing(conflict)


def test_different_runs_views_and_rule_versions_preserve_history(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'c9-history.db').as_posix()}")
    store.initialize()
    base, _ = _compose()
    selection = _select(_items())
    other_run = compose_daily_brief(
        selection=selection,
        run_id="run-other",
        report_date="2026-09-14",
        view_name="Global",
        composed_at=NOW,
        ai_status="disabled",
        quality=_quality(),
        document_count=4,
        event_count=7,
    )
    other_view = compose_daily_brief(
        selection=selection.model_copy(update={"view_id": "macro"}),
        run_id="run-c9",
        report_date="2026-09-13",
        view_name="Macro",
        composed_at=NOW,
        ai_status="disabled",
        quality=_quality(),
        document_count=4,
        event_count=7,
    )
    other_rule = compose_daily_brief(
        selection=selection.model_copy(update={"rule_version": "c9-v2"}),
        run_id="run-c9",
        report_date="2026-09-13",
        view_name="Global",
        composed_at=NOW,
        ai_status="disabled",
        quality=_quality(),
        document_count=4,
        event_count=7,
    )

    for brief in (base, other_run, other_view, other_rule):
        store.save_daily_briefing(brief)

    assert store.table_count(c9_daily_briefings_table) == 4
    assert store.load_daily_briefing("run-c9", "global", "c9-v2") == other_rule
