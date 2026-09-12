from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.briefing import C9_RULE_VERSION
from radar.config import Settings
from radar.domain import CollectionBatch, EventType, ProviderStatus
from radar.pipeline import run_daily
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
