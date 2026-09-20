from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.briefing import C9_RULE_VERSION
from radar.calendar import CalendarBatch, ScheduledCatalyst
from radar.china_market import (
    CapabilityState,
    ChinaCapabilitySummary,
    ChinaIndexSnapshot,
    ChinaMarketIntelligence,
)
from radar.config import Settings
from radar.domain import CollectionBatch, EventType, ProviderStatus
from radar.pipeline import run_daily
from radar.providers import DemoProvider
from radar.report import HtmlReportRenderer
from radar.storage import RadarStore, c7_rankings_table, c8_market_impacts_table


def test_no_ai_pipeline_persists_and_renders_one_c9_snapshot(tmp_path, monkeypatch) -> None:
    rendered: dict[str, object] = {}

    def capture_render(_renderer, context):
        rendered.update(context)
        return "<html><body>C9</body></html>"

    monkeypatch.setattr("radar.report.HtmlReportRenderer.render", capture_render)
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c9-pipeline.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        daily_llm_budget=0,
        email_mode="disabled",
    )

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    store = RadarStore(settings.database_url)
    brief = store.load_daily_briefing(result.run_id, "global", C9_RULE_VERSION)

    assert brief is not None
    assert rendered["briefing"] == brief
    assert brief.ai_status == "disabled"
    assert all(
        item.event_type not in {EventType.MARKET_MOVE, EventType.RISK}
        for item in brief.primary_stories
    )
    assert store.table_count(c7_rankings_table) > 0
    assert store.table_count(c8_market_impacts_table) >= 0
    assert brief.diagnostics.rule_version == C9_RULE_VERSION


def test_retry_reuses_persisted_snapshot_as_html_source_of_truth(tmp_path, monkeypatch) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c9-retry.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )
    first = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    store = RadarStore(settings.database_url)
    stored = store.load_daily_briefing(first.run_id, "global", C9_RULE_VERSION)
    assert stored is not None

    class ChangedProvider:
        name = "changed-upstream"

        def collect(self, now: datetime) -> CollectionBatch:
            _ = now
            return CollectionBatch(provider=self.name, status=ProviderStatus.NO_DATA)

    captured: dict[str, object] = {}
    original_render = HtmlReportRenderer.render

    def capture_render(renderer, context):
        captured["briefing"] = context["briefing"]
        return original_render(renderer, context)

    def fail_compose(**kwargs):
        _ = kwargs
        raise AssertionError("retry must not compose a new snapshot")

    monkeypatch.setattr(
        "radar.pipeline.build_providers",
        lambda settings, universe: [ChangedProvider()],
    )
    monkeypatch.setattr("radar.pipeline.compose_daily_brief", fail_compose)
    monkeypatch.setattr("radar.pipeline.HtmlReportRenderer.render", capture_render)

    with pytest.raises(RuntimeError, match="Insufficient data"):
        run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert captured["briefing"] == stored
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-07.html").read_text(
        encoding="utf-8"
    )
    assert stored.primary_stories[0].headline in html


def test_c9_pipeline_exposes_calendar_china_and_source_coverage(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 20, 8, tzinfo=UTC)
    email_context: dict[str, object] = {}

    class CalendarProvider:
        name = "test-official-calendar"

        def collect(self, collected_at: datetime) -> CalendarBatch:
            return CalendarBatch(
                provider_id=self.name,
                status=ProviderStatus.OK,
                events=(
                    ScheduledCatalyst(
                        id="calendar-cpi",
                        provider_id=self.name,
                        provider_name="Official calendar",
                        external_id="cpi",
                        title="Consumer Price Index",
                        scheduled_at=collected_at.replace(day=21),
                        source_timezone="UTC",
                        region="US",
                        category="macro_release",
                        importance="high",
                        source_url="https://example.test/calendar",
                    ),
                ),
                diagnostics={"events_accepted": 1},
            )

    class ChinaProvider:
        name = "test-china-market"

        def collect(self, collected_at: datetime) -> ChinaMarketIntelligence:
            return ChinaMarketIntelligence(
                provider=self.name,
                indices=(
                    ChinaIndexSnapshot(
                        code="000001",
                        name="SSE Composite",
                        close=3300.0,
                        change_pct=0.5,
                        turnover=None,
                        observed_at=collected_at,
                        provider=self.name,
                    ),
                ),
                capabilities=ChinaCapabilitySummary(
                    indices=CapabilityState.AVAILABLE,
                    sectors=CapabilityState.UNAVAILABLE,
                    breadth=CapabilityState.UNAVAILABLE,
                    turnover=CapabilityState.UNAVAILABLE,
                    style=CapabilityState.UNAVAILABLE,
                ),
            )

    def capture_email(_renderer, context):
        email_context.update(context)
        return "<html>C9 email</html>"

    monkeypatch.setattr(
        "radar.pipeline.build_providers", lambda settings, universe: [DemoProvider()]
    )
    monkeypatch.setattr(
        "radar.pipeline.build_calendar_providers",
        lambda settings, universe: [CalendarProvider()],
    )
    monkeypatch.setattr(
        "radar.pipeline.build_china_market_provider", lambda settings: ChinaProvider()
    )
    monkeypatch.setattr("radar.pipeline.EmailHtmlReportRenderer.render", capture_email)
    settings = Settings(
        app_env="development",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'c9-integration.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)

    assert result.c9_discovery["calendar_events"] == 1
    assert result.c9_discovery["future_catalysts"] == 1
    assert result.c9_discovery["china"]["indices"] == "AVAILABLE"
    coverage = result.c9_discovery["source_coverage"]
    assert {item["provider_id"] for item in coverage} >= {
        "demo-fixtures",
        "test-official-calendar",
        "test-china-market",
    }
    email_intelligence = email_context["email_intelligence"]
    assert email_intelligence.next_catalysts[0].event_id == "calendar-cpi"
    assert email_intelligence.china.indices[0].name_zh == "上证综指"
