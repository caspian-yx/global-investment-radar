from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from radar.cli import app
from radar.config import Settings
from radar.domain import (
    CollectionBatch,
    DriverConfidence,
    EvidencePack,
    MacroObservation,
    MarketObservation,
    ProviderStatus,
    ReportQualityStatus,
    SourceTier,
    StructuredAnalysis,
    stable_id,
)
from radar.llm import DisabledLLMProvider
from radar.pipeline import run_daily
from radar.providers import DemoProvider, make_document
from radar.report import HtmlReportRenderer
from radar.storage import (
    RadarStore,
    driver_candidates_table,
    events_table,
    evidence_table,
    reports_table,
    runs,
    source_documents,
)


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
    assert result.events == 5
    assert result.official_events == 4
    assert result.market_events == 1
    assert result.macro_events == 0
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
    assert "Radar macro backdrop: U.S. 10Y Treasury Yield" not in html
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
    assert store.table_count(events_table) == 5
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

    assert result.events == 5
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
    assert result.market_events == 1
    assert result.official_events == 0
    assert result.macro_events == 0
    assert result.driver_candidates == 0
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert "No evidence-backed driver identified." in html
    assert "Macro backdrop" in html
    assert "Cross-asset context" not in html


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


def test_pipeline_combines_official_market_and_macro_events_before_optional_llm(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 9, 8, tzinfo=UTC)
    database_url = f"sqlite:///{(tmp_path / 'combined.db').as_posix()}"
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=database_url,
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )
    store = RadarStore(database_url)
    store.initialize()
    store.save_macro_with_first_observed_events(
        "bootstrap",
        [
            MacroObservation(
                id=stable_id("macro", "BLS|CUUR0000SA0|2026-07"),
                series_id="CUUR0000SA0",
                name="U.S. CPI All Urban Consumers",
                period="2026-07",
                observed_at=now,
                value=324.0,
                previous_value=323.5,
                change=0.5,
                change_label="vs previous month",
                unit="index",
                provider="bls-public-api",
            )
        ],
        now,
    )
    document = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.com/policy",
        title="Federal Reserve <script>alert(1)</script> keeps policy rate unchanged",
        content="The committee kept its policy rate unchanged.",
        published_at=now,
    )
    market = MarketObservation(
        id=stable_id("market", "test|^GSPC|2026-09-09"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=now,
        close=102.0,
        previous_close=100.0,
        change_pct=2.0,
        currency="USD",
        provider="test-market",
    )
    macro = MacroObservation(
        id=stable_id("macro", "BLS|CUUR0000SA0|2026-08"),
        series_id="CUUR0000SA0",
        name="U.S. CPI All Urban Consumers",
        period="2026-08",
        observed_at=now,
        value=324.5,
        previous_value=324.0,
        change=0.5,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    batch = CollectionBatch(
        provider="combined-fixture",
        documents=[document],
        markets=[market],
        macro=[macro],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )

    result = run_daily(settings, now=now)

    assert result.official_events == 1
    assert result.market_events == 1
    assert result.macro_events == 1
    assert result.events == 3
    assert result.ai_status == "disabled"
    assert RadarStore(database_url).table_count(events_table) == 3
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-09.html").read_text(
        encoding="utf-8"
    )
    assert 'href="https://example.com/policy"' in html
    assert "S&amp;P 500 (^GSPC)" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "Previous 100" in html
    assert "Latest 102" in html
    assert "+2.00%" in html
    assert "test-market" in html
    assert "CUUR0000SA0" in html
    assert "2026-08" in html
    assert "index points" in html
    assert "first observed by Radar" in html
    assert 'href="test-market"' not in html
    assert 'href="bls-public-api"' not in html


def test_quiet_day_with_zero_events_is_a_valid_report(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 9, 8, tzinfo=UTC)
    market = MarketObservation(
        id=stable_id("market", "quiet|^GSPC|2026-09-09"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=now,
        close=100.1,
        previous_close=100.0,
        change_pct=0.1,
        currency="USD",
        provider="test-market",
    )
    macro = MacroObservation(
        id=stable_id("macro", "BLS|CUUR0000SA0|2026-08"),
        series_id="CUUR0000SA0",
        name="U.S. CPI All Urban Consumers",
        period="2026-08",
        observed_at=now,
        value=324.5,
        previous_value=324.0,
        change=0.5,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    batch = CollectionBatch(
        provider="quiet-fixture",
        markets=[market],
        macro=[macro],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'quiet.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.events == 0
    assert result.quality_status is ReportQualityStatus.HEALTHY
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-09.html").read_text(
        encoding="utf-8"
    )
    assert "No event passed the deterministic extraction stage." in html


def test_market_event_alone_does_not_supply_official_quality_support(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 9, 8, tzinfo=UTC)
    market = MarketObservation(
        id=stable_id("market", "unsupported|^GSPC|2026-09-09"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=now,
        close=102.0,
        previous_close=100.0,
        change_pct=2.0,
        currency="USD",
        provider="test-market",
    )
    batch = CollectionBatch(
        provider="market-only-fixture",
        markets=[market],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'market-only.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.market_events == 1
    assert result.official_events == 0
    assert result.macro_events == 0
    assert result.quality_status is ReportQualityStatus.DEGRADED


def test_llm_failure_cannot_change_deterministic_events(tmp_path, monkeypatch) -> None:
    class FailingLLM:
        name = "failing-llm"

        def analyze(self, evidence_pack: object) -> object:
            _ = evidence_pack
            raise RuntimeError("simulated LLM failure")

    def build_provider(settings: Settings) -> object:
        if settings.ai_enabled:
            return FailingLLM()
        return DisabledLLMProvider()

    monkeypatch.setattr("radar.pipeline.build_llm_provider", build_provider)
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'llm-failure.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=6,
        llm_provider="failing",
        email_mode="disabled",
    )

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert result.events == 5
    assert result.official_events == 4
    assert result.market_events == 1
    assert result.macro_events == 0
    assert RadarStore(settings.database_url).table_count(events_table) == 5
    assert any(status.provider == "llm:failing-llm" for status in result.provider_statuses)


def test_deterministic_events_are_available_before_optional_llm(
    tmp_path, monkeypatch
) -> None:
    seen_event_ids: list[str] = []

    class CapturingLLM:
        name = "capturing-llm"

        def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis:
            seen_event_ids.append(evidence_pack.event.id)
            return StructuredAnalysis(
                event_id=evidence_pack.event.id,
                status="captured",
                provider=self.name,
            )

    monkeypatch.setattr("radar.pipeline.build_llm_provider", lambda settings: CapturingLLM())
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'llm-order.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=6,
        llm_provider="capturing",
        email_mode="disabled",
    )

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert len(seen_event_ids) == result.events == 5
    assert len(set(seen_event_ids)) == result.events


def test_llm_receives_isolated_copies_of_deterministic_artifacts(
    tmp_path, monkeypatch
) -> None:
    class MutatingLLM:
        name = "mutating-llm"

        def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis:
            evidence_pack.event.id = "mutated-event"
            evidence_pack.event.event_key = "mutated-key"
            for item in evidence_pack.evidence:
                item.source_id = "mutated-source"
                item.excerpt = "mutated evidence"
            return StructuredAnalysis(
                event_id=evidence_pack.event.id,
                status="mutated-copy",
                provider=self.name,
            )

    monkeypatch.setattr("radar.pipeline.build_llm_provider", lambda settings: MutatingLLM())
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'llm-isolation.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=6,
        llm_provider="mutating",
        email_mode="disabled",
    )

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        event_rows = connection.execute(
            select(events_table.c.id, events_table.c.event_key, events_table.c.payload_json)
        ).all()
        evidence_source_ids = connection.execute(select(evidence_table.c.source_id)).scalars()

    assert result.events == 5
    assert len(event_rows) == 5
    assert all(row.id != "mutated-event" and row.event_key != "mutated-key" for row in event_rows)
    assert all(json.loads(row.payload_json)["id"] == row.id for row in event_rows)
    assert "mutated-source" not in set(evidence_source_ids)


def test_pipeline_persists_and_renders_evidence_backed_driver(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 9, 20, tzinfo=UTC)
    source = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.com/rate-decision",
        title="Federal Reserve announces interest rate decision",
        content="The Federal Reserve announced a federal funds rate decision.",
        published_at=datetime(2026, 9, 8, 20, tzinfo=UTC),
    )
    market = MarketObservation(
        id=stable_id("market", "driver|^GSPC|2026-09-09"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=now,
        close=98,
        previous_close=100,
        change_pct=-2,
        currency="USD",
        provider="test-market",
    )
    macro = MacroObservation(
        id=stable_id("macro", "driver|CPI|2026-08"),
        series_id="CUUR0000SA0",
        name="CPI",
        period="2026-08",
        observed_at=now,
        value=318.2,
        previous_value=317.5,
        change=0.7,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    batch = CollectionBatch(
        provider="driver-fixture",
        documents=[source],
        markets=[market],
        macro=[macro],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'driver.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.driver_candidates == 1
    store = RadarStore(settings.database_url)
    assert store.table_count(driver_candidates_table) == 1
    with store.engine.connect() as connection:
        target_event_id = connection.execute(
            select(events_table.c.id).where(events_table.c.event_type == "market_move")
        ).scalar_one()
    loaded = store.load_driver_candidates_for_target(target_event_id)
    assert len(loaded) == 1
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-10.html").read_text(
        encoding="utf-8"
    )
    assert "Possible drivers" in html
    assert "plausible candidate" in html
    assert "MEDIUM" in html
    assert "Prior" in html
    assert "Strong Topic" in html
    assert 'href="https://example.com/rate-decision"' in html
    assert 'href="test-market"' not in html
    assert "Why it happened" not in html


def test_c2_1_failure_is_fatal_and_marks_run_failed(tmp_path, monkeypatch) -> None:
    transitions = track_run_transitions(monkeypatch)
    monkeypatch.setattr(
        "radar.pipeline.build_driver_candidates",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("C2.1 failed")),
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c2-failure.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
    )

    with pytest.raises(RuntimeError, match="C2.1 failed"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert transitions == ["failed"]


def test_report_renders_multiple_high_and_ambiguous_drivers(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 9, 20, tzinfo=UTC)
    prior_a = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.com/fed/rate",
        title="Federal Reserve announces interest rate decision for S&P 500 context",
        content="The Federal Reserve announced a federal funds rate decision.",
        published_at=now.replace(day=8),
    )
    prior_b = make_document(
        provider="official-rss",
        tier=SourceTier.B,
        publisher="Independent Newswire",
        url="https://example.net/wire/rate",
        title="Federal Reserve announces interest rate decision for S&P 500 context",
        content="An independent report covered the federal funds rate decision.",
        published_at=now.replace(day=8, hour=21),
    )
    ambiguous = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Statistics Office",
        url="https://example.org/cpi",
        title="Consumer price index shows inflation pressures & context",
        content="The consumer price index showed inflation pressures.",
        published_at=now.replace(hour=18),
    )
    market = MarketObservation(
        id=stable_id("market", "multi-driver|^GSPC|2026-09-09"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=now,
        close=98,
        previous_close=100,
        change_pct=-2,
        currency="USD",
        provider="test-market",
    )
    macro = MacroObservation(
        id=stable_id("macro", "multi-driver|CPI|2026-08"),
        series_id="CUUR0000SA0",
        name="CPI",
        period="2026-08",
        observed_at=now,
        value=318.2,
        previous_value=317.5,
        change=0.7,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    batch = CollectionBatch(
        provider="multi-driver-fixture",
        documents=[prior_a, prior_b, ambiguous],
        markets=[market],
        macro=[macro],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'multi-driver.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.driver_candidates == 2
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-10.html").read_text(
        encoding="utf-8"
    )
    assert html.count("plausible candidate") == 2
    assert "Confidence HIGH" in html
    assert "Confidence LOW" in html
    assert "Same Window Ambiguous" in html
    assert "Macro backdrop" in html
    assert "inflation pressures &amp; context" in html
    assert "inflation pressures & context" not in html


def test_pipeline_renders_shared_semantic_candidate_order(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 9, 20, tzinfo=UTC)
    exact = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.com/exact/s-and-p-500",
        title="S&P 500 responds to Federal Reserve policy announcement",
        content="The official statement explicitly named the S&P 500.",
        published_at=now.replace(day=8),
    )
    broad_a = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Statistics Office",
        url="https://example.com/topic/inflation",
        title="Consumer price index shows inflation pressures",
        content="The consumer price index showed inflation pressures.",
        published_at=now.replace(day=8, hour=18),
    )
    broad_b = make_document(
        provider="official-rss",
        tier=SourceTier.B,
        publisher="Independent Newswire",
        url="https://example.net/topic/inflation",
        title="Consumer price index shows inflation pressures",
        content="An independent report covered the consumer price index.",
        published_at=now.replace(day=8, hour=19),
    )
    market = MarketObservation(
        id=stable_id("market", "ranked-pipeline|^GSPC|2026-09-09"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=now,
        close=98,
        previous_close=100,
        change_pct=-2,
        currency="USD",
        provider="test-market",
    )
    batch = CollectionBatch(
        provider="ranked-driver-fixture",
        documents=[broad_a, exact, broad_b],
        markets=[market],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'ranked-pipeline.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )
    rendered_context: dict[str, object] = {}
    original_render = HtmlReportRenderer.render

    def capture_render(renderer: HtmlReportRenderer, context: dict[str, object]) -> str:
        rendered_context.update(context)
        return original_render(renderer, context)

    monkeypatch.setattr(HtmlReportRenderer, "render", capture_render)

    result = run_daily(settings, now=now)

    assert result.driver_candidates == 2
    assert "driver_candidates" not in rendered_context
    bundles_by_target = rendered_context["explanation_bundles"]
    assert isinstance(bundles_by_target, dict)
    assert len(bundles_by_target) == 1
    bundle = next(iter(bundles_by_target.values()))
    assert [item.confidence for item in bundle.driver_candidates] == [
        DriverConfidence.MEDIUM,
        DriverConfidence.HIGH,
    ]
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-10.html").read_text(
        encoding="utf-8"
    )
    assert html.index("Confidence MEDIUM") < html.index("Confidence HIGH")


def test_report_renders_context_only_for_vix_and_equity_weakness(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 9, 20, tzinfo=UTC)
    specifications = [
        ("^GSPC", "S&P 500", -2.0),
        ("^NDX", "Nasdaq 100", -2.5),
        ("^DJI", "Dow Jones", -2.0),
        ("^RUT", "Russell 2000", -2.5),
        ("^VIX", "VIX", 8.0),
    ]
    markets = [
        MarketObservation(
            id=stable_id("market", f"context|{symbol}|2026-09-09"),
            symbol=symbol,
            name=name,
            region="US",
            observed_at=now,
            close=100 + change_pct,
            previous_close=100,
            change_pct=change_pct,
            currency="USD",
            provider="test-market",
        )
        for symbol, name, change_pct in specifications
    ]
    batch = CollectionBatch(
        provider="context-fixture",
        markets=markets,
        diagnostics={"market_items_expected": 5, "market_items_collected": 5},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'context.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.driver_candidates == 0
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-10.html").read_text(
        encoding="utf-8"
    )
    assert "Cross-asset context" in html
    assert "US equity breadth context" in html
    assert "US equity weakness and a rising VIX" in html
    assert "Macro backdrop" not in html
    assert "No evidence-backed driver identified." in html
