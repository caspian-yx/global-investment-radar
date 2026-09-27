from __future__ import annotations

from datetime import UTC, datetime

from radar.config import Settings
from radar.importance import C7_RULE_VERSION
from radar.pipeline import run_daily
from radar.storage import RadarStore


def test_pipeline_persists_all_view_scores_and_renders_selected_view(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c7-pipeline.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        radar_view_id="ai-semiconductors",
        ai_enabled=False,
        email_mode="disabled",
    )
    result = run_daily(settings, now=datetime(2026, 9, 12, 12, tzinfo=UTC))
    store = RadarStore(settings.database_url)
    selected = store.load_c7_scores(result.run_id, "ai-semiconductors", C7_RULE_VERSION)
    global_scores = store.load_c7_scores(result.run_id, "global", C7_RULE_VERSION)
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-12.html").read_text(
        encoding="utf-8"
    )

    assert len(selected) == result.events
    assert len(global_scores) == result.events
    assert [item.priority_rank for item in selected] == list(range(1, result.events + 1))
    assert "今日焦点" in html
    assert "AI / 半导体" in html


def test_c7_scores_do_not_replace_legacy_event_importance(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c7-boundary.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )
    result = run_daily(settings, now=datetime(2026, 9, 12, 12, tzinfo=UTC))
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        payloads = connection.exec_driver_sql(
            "SELECT payload_json FROM events ORDER BY id"
        ).scalars()
    assert len(list(payloads)) == result.events
