from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from radar.config import Settings
from radar.pipeline import run_daily
from radar.storage import RadarStore, events_table, reports_table, runs, source_documents


def test_no_ai_demo_pipeline_generates_html(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'radar.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        daily_llm_budget=0,
        email_mode="disabled",
    )
    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    report = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html")
    assert result.report_path == str(report)
    assert result.ai_status == "disabled"
    assert result.documents == 5
    assert result.events == 4
    assert result.markets == 8
    assert result.macro == 3
    html = report.read_text(encoding="utf-8")
    assert "DEMO DATA" in html
    assert "AI analysis is disabled" in html
    assert "Top Global Events" in html
    assert "Data Coverage" in html
    assert "Missing Data" in html
    assert "Freshness" in html


def test_pipeline_is_idempotent_for_same_business_day(tmp_path) -> None:
    database = tmp_path / "radar.db"
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{database.as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )
    now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    first = run_daily(settings, now=now)
    second = run_daily(settings, now=now)
    assert first.run_id == second.run_id
    assert first.report_path == second.report_path
    store = RadarStore(settings.database_url)
    assert store.table_count(runs) == 1
    assert store.table_count(source_documents) == 5
    assert store.table_count(events_table) == 4
    assert store.table_count(reports_table) == 1


def test_fake_llm_is_visibly_marked(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'fake.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        llm_provider="fake",
    )
    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert result.ai_status == "test-data"
    assert "TEST AI OUTPUT" in html
    assert "FakeLLMProvider" in html


def test_fake_llm_is_blocked_in_production(tmp_path) -> None:
    settings = Settings(
        app_env="production",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'prod.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        llm_provider="fake",
    )
    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert result.ai_status == "disabled"
    assert "TEST AI OUTPUT" not in html


def test_business_timezone_controls_report_date(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'timezone.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        business_timezone="Asia/Shanghai",
        ai_enabled=False,
    )
    result = run_daily(settings, now=datetime(2026, 9, 6, 22, 17, tzinfo=UTC))
    assert result.report_date == "2026-09-07"


def test_email_failure_marks_run_failed_and_preserves_report(tmp_path, monkeypatch) -> None:
    class FailingSender:
        def send(self, *, subject: str, html: str) -> str:
            _ = subject, html
            raise OSError("simulated SMTP outage")

    monkeypatch.setattr("radar.pipeline.build_email_sender", lambda settings: FailingSender())
    database = tmp_path / "failure.db"
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{database.as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )
    with pytest.raises(RuntimeError, match="Email delivery failed"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    report = tmp_path / "reports" / "global-investment-radar-2026-09-07.html"
    assert report.exists()
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        status = connection.execute(select(runs.c.status)).scalar_one()
    assert status == "failed"
