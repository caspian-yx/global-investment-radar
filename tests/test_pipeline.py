from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from radar.briefing import C9_RULE_VERSION, DailyIntelligenceBrief, ExplanationBasis
from radar.cli import app
from radar.config import Settings
from radar.domain import (
    AIEnrichmentResult,
    AIRunUsage,
    CollectionBatch,
    DriverConfidence,
    EvidencePack,
    MacroObservation,
    MarketObservation,
    ProviderStatus,
    ReportQualityStatus,
    SourceCategory,
    SourceRole,
    SourceTier,
    StructuredAnalysis,
    stable_id,
)
from radar.llm import DisabledLLMProvider, LLMConfigurationError, LLMResponse
from radar.macro import build_macro_source_document
from radar.pipeline import _resolve_ai_status, run_daily
from radar.providers import DemoProvider, make_document
from radar.report import HtmlReportRenderer
from radar.storage import (
    RadarStore,
    claims_table,
    driver_candidates_table,
    events_table,
    evidence_table,
    reports_table,
    runs,
    source_documents,
    stories_table,
    story_documents_table,
)


class StaticProvider:
    def __init__(self, batch: CollectionBatch) -> None:
        self.name = batch.provider
        self.batch = batch

    def collect(self, now: datetime) -> CollectionBatch:
        _ = now
        return self.batch


def c6_authorized_fed_batch(now: datetime) -> CollectionBatch:
    document = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.test/fed-policy",
        title="Federal Reserve announces interest rate decision",
        content="The Federal Reserve announced a federal funds rate decision.",
        published_at=now - timedelta(hours=24),
        region="US",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    market = MarketObservation(
        id=stable_id("market", f"c6-ai|^GSPC|{now.isoformat()}"),
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
    return CollectionBatch(
        provider="c6-authorized-fixture",
        documents=[document],
        markets=[market],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )


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

    report = tmp_path / "reports" / "global-investment-radar-2026-09-07.html"
    assert result.report_path == str(report)
    assert result.ai_status == "disabled"
    assert result.documents == 5
    assert result.events == 8
    assert result.official_events == 4
    assert result.market_events == 4
    assert result.macro_events == 0
    assert result.markets == 11
    assert result.macro == 3
    assert result.quality_status is ReportQualityStatus.HEALTHY
    assert result.recall_funnel == {
        "provider_items": 19,
        "documents_before_dedup": 5,
        "documents_after_dedup": 5,
        "stories": 4,
        "events": 8,
    }
    assert result.email_intelligence["focus_selected"] <= 5
    assert result.email_intelligence["rule_version"] == "c9-email-v1"
    assert result.c9_discovery["discovery"]["clusters"] == 4
    assert result.c9_discovery["market_discovery"]["markets_evaluated"] == 11
    assert result.c9_discovery["coverage_audit"]["measurable"] is True
    html = report.read_text(encoding="utf-8")
    assert "DEMO DATA" in html
    assert "AI is off" in html
    assert "Things That Matter" in html
    assert "Sources &amp; Data Quality" in html
    assert "Radar macro backdrop: U.S. 10Y Treasury Yield" not in html
    assert transitions == ["completed"]


def test_pipeline_uses_one_unified_provider_collection_seam(tmp_path, monkeypatch) -> None:
    from radar.ingestion import collect_intelligence_providers as collect_real

    seam_calls = 0

    def collect_once(providers, collected_at):
        nonlocal seam_calls
        seam_calls += 1
        return collect_real(providers, collected_at)

    monkeypatch.setattr(
        "radar.pipeline.collect_intelligence_providers",
        collect_once,
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'ingestion-seam.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert seam_calls == 1
    assert result.documents == 5
    assert result.markets == 11
    assert result.macro == 3


@pytest.mark.parametrize("reverse_provider_order", [False, True])
def test_pipeline_deduplicates_exact_canonical_url_before_story_clustering(
    tmp_path,
    monkeypatch,
    reverse_provider_order: bool,
) -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    official = make_document(
        provider="zeta-official",
        tier=SourceTier.A,
        publisher="Example Authority",
        url="https://EXAMPLE.test/releases/policy?id=7&utm_source=rss#top",
        title="Example Authority announces policy decision",
        content="The authority published the direct policy decision.",
        published_at=now - timedelta(hours=2),
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    aggregator = make_document(
        provider="alpha-aggregator",
        tier=SourceTier.C,
        publisher="Example Aggregator",
        url="https://example.test/releases/policy?id=7",
        title="Example Authority announces policy decision",
        content="An aggregator copied the same decision.",
        published_at=now - timedelta(hours=1),
        source_category=SourceCategory.AGGREGATOR,
        source_role=SourceRole.SECONDARY,
    )
    batches = [
        CollectionBatch(provider="zeta-official", documents=[official]),
        CollectionBatch(provider="alpha-aggregator", documents=[aggregator]),
    ]
    if reverse_provider_order:
        batches.reverse()
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch) for batch in batches],
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=(f"sqlite:///{(tmp_path / f'dedupe-{reverse_provider_order}.db').as_posix()}"),
        report_output_dir=tmp_path / f"reports-{reverse_provider_order}",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.documents == 1
    assert result.official_events == 1
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        provider = connection.execute(select(source_documents.c.provider)).scalar_one()
        event_payload = json.loads(
            connection.execute(select(events_table.c.payload_json)).scalar_one()
        )
    assert provider == "zeta-official"
    assert event_payload["source_document_ids"] == [official.id]


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
    assert store.table_count(events_table) == 8
    assert store.table_count(reports_table) == 1


def test_pipeline_persists_story_identity_and_attaches_later_source(tmp_path, monkeypatch) -> None:
    first_now = datetime(2026, 9, 12, 9, tzinfo=UTC)
    second_now = first_now + timedelta(hours=3)
    secondary = make_document(
        provider="news-secondary",
        tier=SourceTier.B,
        publisher="Independent News",
        url="https://news.example/nvidia-blackwell",
        title="NVIDIA launches Blackwell platform for enterprise AI",
        content="NVIDIA launched the Blackwell product for enterprise data centers.",
        published_at=first_now - timedelta(hours=2),
        collected_at=first_now,
        region="US",
        source_category=SourceCategory.PROFESSIONAL_NEWS,
        source_role=SourceRole.SECONDARY,
    )
    primary = make_document(
        provider="nvidia-primary",
        tier=SourceTier.A,
        publisher="NVIDIA",
        url="https://nvidia.example/blackwell-enterprise",
        title="NVIDIA launches Blackwell enterprise AI platform",
        content="NVIDIA launched the Blackwell product for enterprise data centers.",
        published_at=first_now - timedelta(hours=3),
        collected_at=second_now,
        region="US",
        source_category=SourceCategory.COMPANY_PRIMARY,
        source_role=SourceRole.PRIMARY,
    )
    demo = DemoProvider().collect(first_now)
    batches = iter(
        [
            CollectionBatch(
                provider="story-first",
                documents=[secondary],
                markets=demo.markets,
                macro=demo.macro,
                diagnostics={"market_items_expected": 8, "market_items_collected": 8},
            ),
            CollectionBatch(
                provider="story-second",
                documents=[primary],
                markets=demo.markets,
                macro=demo.macro,
                diagnostics={"market_items_expected": 8, "market_items_collected": 8},
            ),
        ]
    )

    def providers(settings, universe):
        _ = settings, universe
        return [StaticProvider(next(batches))]

    monkeypatch.setattr("radar.pipeline.build_providers", providers)
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'persistent-story.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    first_result = run_daily(settings, now=first_now)
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        first_story = json.loads(
            connection.execute(select(stories_table.c.payload_json)).scalar_one()
        )
        first_event = json.loads(
            connection.execute(
                select(events_table.c.payload_json).where(events_table.c.event_key.like("story:%"))
            ).scalar_one()
        )

    second_result = run_daily(settings, now=second_now)
    with store.engine.connect() as connection:
        second_story = json.loads(
            connection.execute(select(stories_table.c.payload_json)).scalar_one()
        )
        second_event = json.loads(
            connection.execute(
                select(events_table.c.payload_json).where(events_table.c.event_key.like("story:%"))
            ).scalar_one()
        )
        details = json.loads(connection.execute(select(runs.c.details_json)).scalar_one())

    assert first_result.official_events == second_result.official_events == 1
    assert second_result.documents == 2
    assert store.table_count(stories_table) == 1
    assert store.table_count(story_documents_table) == 2
    assert second_story["id"] == first_story["id"]
    assert second_story["identity_seed_document_id"] == secondary.id
    assert second_story["presentation_document_id"] == primary.id
    assert second_event["id"] == first_event["id"]
    assert second_event["title"] == primary.title
    assert second_event["source_document_ids"] == sorted([primary.id, secondary.id])
    assert details["story_consolidation"]["documents_attached_to_existing_stories"] == 1
    assert details["story_consolidation"]["rule_version"] == "story-v1"


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
    assert "Any AI-assisted prose is synthetic" in html


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


def test_ai_status_resolver_distinguishes_disabled_success_partial_and_failure() -> None:
    disabled = Settings(ai_enabled=False, daily_llm_budget=0)
    enabled = Settings(ai_enabled=True, daily_llm_budget=1, llm_provider="deepseek")

    def enrichment(
        *,
        attempted: int,
        succeeded: int,
        failed: int,
        skipped: int = 0,
        exhausted: bool = False,
    ) -> AIEnrichmentResult:
        return AIEnrichmentResult(
            explanations={},
            usage=AIRunUsage(
                requests_attempted=attempted,
                requests_succeeded=succeeded,
                requests_failed=failed,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                targets_skipped_due_to_budget=skipped,
                budget_exhausted=exhausted,
            ),
        )

    assert (
        _resolve_ai_status(
            disabled,
            enrichment(attempted=0, succeeded=0, failed=0),
            [],
            has_bundles=False,
            provider_available=False,
        )
        == "disabled"
    )
    assert (
        _resolve_ai_status(
            enabled,
            enrichment(attempted=1, succeeded=1, failed=0),
            [],
            has_bundles=True,
            provider_available=True,
        )
        == "enabled"
    )
    assert (
        _resolve_ai_status(
            enabled,
            enrichment(attempted=2, succeeded=1, failed=1),
            [],
            has_bundles=True,
            provider_available=True,
        )
        == "partial"
    )
    assert (
        _resolve_ai_status(
            enabled,
            enrichment(attempted=1, succeeded=0, failed=1),
            [],
            has_bundles=True,
            provider_available=True,
        )
        == "failed"
    )


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

    assert result.events == 8
    assert result.quality_status is ReportQualityStatus.DEGRADED
    assert result.provider_statuses[0].status is ProviderStatus.FAILED
    assert result.provider_statuses[0].error_type == "OSError"
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
    assert result.market_events == 4
    assert result.official_events == 0
    assert result.macro_events == 0
    assert result.driver_candidates == 0
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert "No strong Story met today’s primary threshold" in html
    assert "Market Snapshot" in html
    assert "No validated linked reaction is available" in html


def test_insufficient_data_generates_diagnostic_report_and_exits_nonzero(
    tmp_path, monkeypatch
) -> None:
    transitions = track_run_transitions(monkeypatch)
    database = tmp_path / "insufficient.db"
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [
            StaticProvider(CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA))
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
            StaticProvider(CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA))
        ],
    )
    monkeypatch.setattr("radar.pipeline.build_email_sender", lambda settings: CapturingSender())
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
            StaticProvider(CollectionBatch(provider="official-rss", status=ProviderStatus.NO_DATA))
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
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
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
        release_at=now - timedelta(hours=12),
        value=324.5,
        previous_value=324.0,
        change=0.5,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    macro_source = build_macro_source_document(macro)
    assert macro_source is not None
    macro = macro.model_copy(update={"source_document_id": macro_source.id})
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
    assert "S&amp;P 500" in html
    assert "+2.00%" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "Things That Matter" in html
    assert "test-market" not in html
    assert 'href="test-market"' not in html
    assert 'href="bls-public-api"' not in html


def test_pipeline_routes_linked_macro_event_through_c6_and_unchanged_c2(
    tmp_path, monkeypatch, caplog
) -> None:
    now = datetime(2026, 9, 10, 21, tzinfo=UTC)
    database_url = f"sqlite:///{(tmp_path / 'macro-driver.db').as_posix()}"
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
                observed_at=now - timedelta(days=40),
                value=324,
                previous_value=323.5,
                change=0.5,
                change_label="vs previous month",
                unit="index",
                provider="bls-public-api",
            )
        ],
        now - timedelta(days=40),
    )
    macro = MacroObservation(
        id=stable_id("macro", "BLS|CUUR0000SA0|2026-08"),
        series_id="CUUR0000SA0",
        name="U.S. CPI All Urban Consumers",
        period="2026-08",
        observed_at=datetime(2026, 9, 9, 12, 30, tzinfo=UTC),
        release_at=datetime(2026, 9, 9, 11, 30, tzinfo=UTC),
        value=324.5,
        previous_value=324,
        change=0.5,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    macro_source = build_macro_source_document(macro)
    assert macro_source is not None
    macro = macro.model_copy(update={"source_document_id": macro_source.id})
    market = MarketObservation(
        id=stable_id("market", "macro-driver|^GSPC|2026-09-10"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=datetime(2026, 9, 10, 20, tzinfo=UTC),
        close=102,
        previous_close=100,
        change_pct=2,
        currency="USD",
        provider="test-market",
    )
    batch = CollectionBatch(
        provider="macro-driver-fixture",
        markets=[market],
        macro=[macro],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers", lambda settings, universe: [StaticProvider(batch)]
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=database_url,
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )
    caplog.set_level("INFO", logger="radar.pipeline")

    result = run_daily(settings, now=now)

    assert result.documents == 1
    assert result.official_events == 0
    assert result.macro_events == 1
    assert result.driver_candidates == 1
    assert RadarStore(database_url).table_count(source_documents) == 1
    driver = RadarStore(database_url).load_driver_candidates_for_target(
        stable_id("event", f"market:^GSPC:{market.observed_at.isoformat()}")
    )[0]
    assert driver.candidate_event_id == stable_id("event", "macro:CUUR0000SA0:2026-08")
    assert (
        "C6 relationships evaluated=1 allowed=1 rejected=0 "
        "rejected_by_reason={} allowed_by_channel={'MACROECONOMIC_RELEASE': 1}" in caplog.text
    )


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
    assert "No strong Story met today’s primary threshold" in html


def test_market_event_alone_does_not_supply_official_quality_support(tmp_path, monkeypatch) -> None:
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

    assert result.events == 8
    assert result.official_events == 4
    assert result.market_events == 4
    assert result.macro_events == 0
    assert RadarStore(settings.database_url).table_count(events_table) == 8
    assert any(status.provider == "llm:failing-llm" for status in result.provider_statuses)


def test_structured_ai_configuration_failure_keeps_deterministic_report(
    tmp_path, monkeypatch
) -> None:
    def fail_generation_provider(settings: Settings) -> object:
        _ = settings
        raise LLMConfigurationError("missing test credentials")

    monkeypatch.setattr("radar.pipeline.build_generation_provider", fail_generation_provider)
    now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(c6_authorized_fed_batch(now))],
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'structured-config-failure.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        llm_provider="deepseek",
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.report_path.endswith("global-investment-radar-2026-09-07.html")
    assert result.ai_status == "failed"
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert "Things That Matter" in html
    assert "AI explanation" not in html


def test_zero_ai_request_budget_uses_single_guarded_seam_and_keeps_report(
    tmp_path, monkeypatch
) -> None:
    from radar.enrichment import run_structured_explanation_enrichment as run_guarded

    seam_calls: list[tuple[int, object | None]] = []

    def capture_guarded(bundles, provider, **limits):
        materialized = list(bundles)
        seam_calls.append((len(materialized), provider))
        return run_guarded(materialized, provider, **limits)

    def unexpected_provider_construction(settings: Settings) -> object:
        _ = settings
        raise AssertionError("zero request budget must prevent provider construction")

    monkeypatch.setattr(
        "radar.pipeline.run_structured_explanation_enrichment",
        capture_guarded,
    )
    monkeypatch.setattr(
        "radar.pipeline.build_generation_provider",
        unexpected_provider_construction,
    )
    now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(c6_authorized_fed_batch(now))],
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'zero-ai-budget.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        ai_max_requests_per_run=0,
        llm_provider="deepseek",
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert seam_calls == [(1, None)]
    assert result.events == 2
    assert result.market_events == 1
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert "Things That Matter" in html
    assert "AI explanation" not in html


def test_c6_rejects_sec_ponzi_before_c2_and_empty_bundle_uses_zero_ai_requests(
    tmp_path, monkeypatch
) -> None:
    from radar.enrichment import run_structured_explanation_enrichment as run_guarded
    from radar.explanation import build_driver_candidates as build_c2

    now = datetime(2026, 9, 10, 20, tzinfo=UTC)
    sec_ponzi = make_document(
        provider="sec-newsroom-rss",
        tier=SourceTier.A,
        publisher="U.S. SEC",
        url="https://www.sec.gov/newsroom/press-releases/example",
        title=(
            "SEC Charges Founder and His Two New Jersey-Based Companies "
            "in Alleged $16 Million Ponzi Scheme"
        ),
        content="The Securities and Exchange Commission announced an enforcement action.",
        published_at=now - timedelta(hours=24),
        region="US",
        source_category=SourceCategory.REGULATORY,
        source_role=SourceRole.PRIMARY,
    )
    market = MarketObservation(
        id=stable_id("market", "c6-hard-negative|3033.HK|2026-09-10"),
        symbol="3033.HK",
        name="Hang Seng TECH ETF proxy",
        region="HK",
        observed_at=now,
        close=97.88,
        previous_close=100,
        change_pct=-2.12,
        currency="HKD",
        provider="test-market",
    )
    batch = CollectionBatch(
        provider="c6-hard-negative",
        documents=[sec_ponzi],
        markets=[market],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(batch)],
    )
    c2_candidate_event_ids: list[list[str]] = []

    def capture_c2(**kwargs):
        c2_candidate_event_ids.append([item.id for item in kwargs["candidate_events"]])
        return build_c2(**kwargs)

    seam_results: list[AIEnrichmentResult] = []
    seam_bundle_counts: list[int] = []

    def capture_guarded(bundles, provider, **limits):
        materialized = list(bundles)
        seam_bundle_counts.append(len(materialized))
        result = run_guarded(materialized, provider, **limits)
        seam_results.append(result)
        return result

    provider_constructions = 0
    provider_generate_calls = 0

    class UnexpectedProvider:
        name = "unexpected"

        def generate(self, request):
            nonlocal provider_generate_calls
            provider_generate_calls += 1
            raise AssertionError("empty ExplanationBundle must not consume an LLM request")

    def build_unexpected_provider(settings):
        nonlocal provider_constructions
        _ = settings
        provider_constructions += 1
        return UnexpectedProvider()

    rendered_context: dict[str, object] = {}
    original_render = HtmlReportRenderer.render

    def capture_render(renderer: HtmlReportRenderer, context: dict[str, object]) -> str:
        rendered_context.update(context)
        return original_render(renderer, context)

    monkeypatch.setattr("radar.pipeline.build_driver_candidates", capture_c2)
    monkeypatch.setattr(
        "radar.pipeline.run_structured_explanation_enrichment",
        capture_guarded,
    )
    monkeypatch.setattr(
        "radar.pipeline.build_generation_provider",
        build_unexpected_provider,
    )
    monkeypatch.setattr(HtmlReportRenderer, "render", capture_render)
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'c6-hard-negative.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        ai_max_requests_per_run=2,
        llm_provider="deepseek",
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert c2_candidate_event_ids == [[]]
    assert result.driver_candidates == 0
    assert seam_bundle_counts == [0]
    assert len(seam_results) == 1
    assert seam_results[0].usage.requests_attempted == 0
    assert provider_constructions == 0
    assert provider_generate_calls == 0
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        interpretation_claim_ids = (
            connection.execute(
                select(claims_table.c.id).where(claims_table.c.claim_type == "INTERPRETATION")
            )
            .scalars()
            .all()
        )
    assert interpretation_claim_ids == []
    brief = rendered_context["briefing"]
    assert isinstance(brief, DailyIntelligenceBrief)
    assert "explanation_bundles" not in rendered_context
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-11.html").read_text(
        encoding="utf-8"
    )
    assert "Things That Matter" in html


def test_successful_structured_ai_enrichment_reaches_report_by_target_id(
    tmp_path, monkeypatch
) -> None:
    class StructuredProvider:
        name = "fake-structured"

        def __init__(self) -> None:
            self.requests = []

        def generate(self, request):
            self.requests.append(request.model_copy(deep=True))
            payload = json.loads(request.prompt.split("INPUT_JSON:\n", maxsplit=1)[1])
            target_id = payload["target"]["target_event_id"]
            candidate_ids = [item["candidate_id"] for item in payload["candidates"]]
            return LLMResponse(
                content=json.dumps(
                    {
                        "target_event_id": target_id,
                        "headline": f"Generated headline for {target_id}",
                        "summary": "Generated deterministic fixture summary.",
                        "candidate_explanations": [
                            {
                                "candidate_id": candidate_id,
                                "explanation": f"Generated explanation for {candidate_id}.",
                            }
                            for candidate_id in candidate_ids
                        ],
                        "uncertainty": None,
                    }
                ),
                provider=self.name,
                requested_model="fixture-model",
                returned_model="fixture-model",
                latency_seconds=0,
            )

    provider = StructuredProvider()
    rendered_context: dict[str, object] = {}
    original_render = HtmlReportRenderer.render

    def capture_render(renderer: HtmlReportRenderer, context: dict[str, object]) -> str:
        rendered_context.update(context)
        return original_render(renderer, context)

    monkeypatch.setattr("radar.pipeline.build_generation_provider", lambda settings: provider)
    monkeypatch.setattr(HtmlReportRenderer, "render", capture_render)
    now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [StaticProvider(c6_authorized_fed_batch(now))],
    )
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'structured-success.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        llm_provider="deepseek",
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.ai_status == "enabled"
    assert len(provider.requests) == 1
    brief = rendered_context["briefing"]
    assert isinstance(brief, DailyIntelligenceBrief)
    assert "structured_explanations" not in rendered_context
    assert any(
        item.explanation_basis is ExplanationBasis.STRUCTURED_AI for item in brief.primary_stories
    )
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert "Generated explanation for" in html
    assert "AI is off" not in html


def test_deterministic_events_are_available_before_optional_llm(tmp_path, monkeypatch) -> None:
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

    assert len(seen_event_ids) == 6
    assert result.events == 8
    assert len(set(seen_event_ids)) == len(seen_event_ids)


def test_llm_receives_isolated_copies_of_deterministic_artifacts(tmp_path, monkeypatch) -> None:
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

    assert result.events == 8
    assert len(event_rows) == 8
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
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
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
    assert "plausible candidate" in html
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
        region="US",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    prior_b = make_document(
        provider="official-rss",
        tier=SourceTier.B,
        publisher="Independent Newswire",
        url="https://example.net/wire/rate",
        title="Federal Reserve announces interest rate decision for S&P 500 context",
        content="An independent report covered the federal funds rate decision.",
        published_at=now.replace(day=8, hour=21),
        region="US",
        source_category=SourceCategory.PROFESSIONAL_NEWS,
        source_role=SourceRole.SECONDARY,
    )
    ambiguous = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Statistics Office",
        url="https://example.org/cpi",
        title="Consumer price index shows inflation pressures & context",
        content="The consumer price index showed inflation pressures.",
        published_at=now.replace(hour=18),
        region="US",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
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
    brief = RadarStore(settings.database_url).load_daily_briefing(
        result.run_id, "global", C9_RULE_VERSION
    )
    assert brief is not None
    assert (
        sum(
            item.explanation_basis is ExplanationBasis.C2_EVIDENCE for item in brief.primary_stories
        )
        == 2
    )
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
        region="US",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    broad_a = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Statistics Office",
        url="https://example.com/topic/inflation",
        title="Consumer price index shows inflation pressures",
        content="The consumer price index showed inflation pressures.",
        published_at=now.replace(day=8, hour=18),
        region="US",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    broad_b = make_document(
        provider="official-rss",
        tier=SourceTier.B,
        publisher="Independent Newswire",
        url="https://example.net/topic/inflation",
        title="Consumer price index shows inflation pressures",
        content="An independent report covered the consumer price index.",
        published_at=now.replace(day=8, hour=19),
        region="US",
        source_category=SourceCategory.PROFESSIONAL_NEWS,
        source_role=SourceRole.SECONDARY,
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
    with RadarStore(settings.database_url).engine.connect() as connection:
        target_event_id = connection.execute(
            select(events_table.c.id).where(events_table.c.event_type == "market_move")
        ).scalar_one()
    loaded = RadarStore(settings.database_url).load_driver_candidates_for_target(target_event_id)
    assert [item.confidence for item in loaded] == [
        DriverConfidence.MEDIUM,
        DriverConfidence.HIGH,
    ]
    assert isinstance(rendered_context["briefing"], DailyIntelligenceBrief)


def test_report_renders_context_only_for_vix_and_equity_weakness(tmp_path, monkeypatch) -> None:
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
    assert "Market Snapshot" in html
    assert "VIX" in html
    assert "No strong Story met today’s primary threshold" in html
    assert "No watch item was inferred" in html
