from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from radar.cli import app
from radar.config import Settings
from radar.domain import CollectionBatch, ProviderStatus, ReportQualityStatus
from radar.pipeline import run_daily
from radar.providers import DemoProvider
from radar.storage import RadarStore, events_table, reports_table, runs, source_documents


class StaticProvider:
    def __init__(self, batch: CollectionBatch) -> None:
        self.name = batch.provider
        self.batch = batch

    def collect(self, now: datetime) -> CollectionBatch:
        _ = now
        return self.batch


def track_run_transitions(monkeypatch) -> list[str]:
    transitions: list[str] = []
    original_complete = RadarStore.complete_run
    original_fail = RadarStore.fail_run

    def complete_run(
        store: RadarStore, run_id: str, completed_at: datetime, details: dict[str, object]
    ) -> None:
        transitions.append("completed")
        original_complete(store, run_id, completed_at, details)

    def fail_run(
        store: RadarStore, run_id: str, completed_at: datetime, details: dict[str, object]
    ) -> None:
        transitions.append("failed")
        original_fail(store, run_id, completed_at, details)

    monkeypatch.setattr(RadarStore, "complete_run", complete_run)
    monkeypatch.setattr(RadarStore, "fail_run", fail_run)
    return transitions


def test_no_ai_demo_pipeline_generates_html(tmp_path, monkeypatch) -> None:
    transitions = track_run_transitions(monkeypatch)
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
    assert result.quality_status is ReportQualityStatus.HEALTHY
    html = report.read_text(encoding="utf-8")
    assert "DEMO DATA" in html
    assert "AI analysis is disabled" in html
    assert "Top Global Events" in html
    assert "Data Coverage" in html
    assert "Missing Data" in html
    assert "Freshness" in html
    assert transitions == ["completed"]


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
    transitions = track_run_transitions(monkeypatch)

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
    assert transitions == ["failed"]


def test_provider_failure_remains_isolated(tmp_path, monkeypatch) -> None:
    transitions = track_run_transitions(monkeypatch)

    class FailingProvider:
        name = "failing-provider"

        def collect(self, now: datetime) -> CollectionBatch:
            _ = now
            raise OSError("simulated provider outage")

    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [FailingProvider(), DemoProvider()],
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'partial.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert result.events == 4
    assert result.quality_status is ReportQualityStatus.DEGRADED
    assert result.provider_statuses[0].status is ProviderStatus.FAILED
    assert transitions == ["completed"]


def test_fatal_pipeline_exception_marks_started_run_failed(tmp_path, monkeypatch) -> None:
    transitions = track_run_transitions(monkeypatch)
    database = tmp_path / "fatal.db"
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{database.as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )
    monkeypatch.setattr(
        "radar.pipeline.HtmlReportRenderer.render",
        lambda self, context: (_ for _ in ()).throw(RuntimeError("render failed")),
    )

    with pytest.raises(RuntimeError, match="render failed"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        status = connection.execute(select(runs.c.status)).scalar_one()
    assert status == "failed"
    assert transitions == ["failed"]


def test_no_official_items_does_not_make_supported_report_insufficient(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    demo = DemoProvider().collect(now).model_copy(update={"documents": []})
    rss = CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA)
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(demo), StaticProvider(rss)],
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'no-news.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )

    result = run_daily(settings, now=now)

    assert result.quality_status is ReportQualityStatus.HEALTHY


def test_insufficient_data_generates_diagnostic_report_and_exits_nonzero(
    tmp_path, monkeypatch
) -> None:
    transitions = track_run_transitions(monkeypatch)
    database = tmp_path / "insufficient.db"
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [
            StaticProvider(
                CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA)
            )
        ],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{database.as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    with pytest.raises(RuntimeError, match="Insufficient data"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    report = tmp_path / "reports" / "global-investment-radar-2026-09-07.html"
    assert report.exists()
    assert "INSUFFICIENT DATA" in report.read_text(encoding="utf-8")
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        status = connection.execute(select(runs.c.status)).scalar_one()
    assert status == "failed"
    assert transitions == ["failed"]


def test_insufficient_data_uses_diagnostic_email_subject(tmp_path, monkeypatch) -> None:
    subjects: list[str] = []

    class CapturingSender:
        def send(self, *, subject: str, html: str) -> str:
            _ = html
            subjects.append(subject)
            return "captured"

    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [
            StaticProvider(
                CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA)
            )
        ],
    )
    monkeypatch.setattr(
        "radar.pipeline.build_email_sender", lambda settings: CapturingSender()
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'diagnostic-email.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="file",
    )

    with pytest.raises(RuntimeError, match="Insufficient data"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert len(subjects) == 1
    assert "INSUFFICIENT DATA" in subjects[0]


def test_insufficient_data_returns_nonzero_from_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [
            StaticProvider(
                CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA)
            )
        ],
    )
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("EMAIL_MODE", "disabled")

    result = CliRunner().invoke(app, ["daily", "--data-mode", "live", "--no-ai"])

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert "Insufficient data" in str(result.exception)


def test_start_run_failure_does_not_attempt_fail_run(tmp_path, monkeypatch) -> None:
    transitions = track_run_transitions(monkeypatch)
    monkeypatch.setattr(
        RadarStore,
        "start_run",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("start failed")),
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'start.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )

    with pytest.raises(RuntimeError, match="start failed"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert transitions == []


def test_result_construction_failure_does_not_complete_before_failure(
    tmp_path, monkeypatch
) -> None:
    transitions = track_run_transitions(monkeypatch)
    monkeypatch.setattr(
        "radar.pipeline.PipelineResult",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("result failed")),
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'result.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )

    with pytest.raises(RuntimeError, match="result failed"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert transitions == ["failed"]


def test_completion_log_failure_does_not_create_two_terminal_transitions(
    tmp_path, monkeypatch
) -> None:
    transitions = track_run_transitions(monkeypatch)
    original_info = __import__("radar.pipeline", fromlist=["logger"]).logger.info

    def fail_on_completion_log(message: str, *args: object) -> None:
        if message.startswith("Pipeline completed"):
            raise RuntimeError("completion log failed")
        original_info(message, *args)

    monkeypatch.setattr("radar.pipeline.logger.info", fail_on_completion_log)
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'completion-log.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )

    with pytest.raises(RuntimeError, match="completion log failed"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert transitions == ["failed"]
