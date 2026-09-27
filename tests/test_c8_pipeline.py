from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime

from radar.config import Settings
from radar.domain import CollectionBatch, MarketObservation, SourceCategory, SourceRole, SourceTier
from radar.impact import C8_RULE_VERSION, EpistemicStatus
from radar.pipeline import run_daily
from radar.providers import make_document
from radar.storage import RadarStore


class StaticProvider:
    name = "c8-static"

    def __init__(self, batch: CollectionBatch) -> None:
        self.batch = batch

    def collect(self, now: datetime) -> CollectionBatch:
        _ = now
        return self.batch


def test_story_to_c7_to_c8_to_report_preserves_c6_c2_path(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 11, 22, tzinfo=UTC)
    document = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.test/fed-c8",
        title="Federal Reserve announces interest rate decision",
        content="The FOMC announced its policy rate decision.",
        published_at=datetime(2026, 9, 11, 15, tzinfo=UTC),
        collected_at=datetime(2026, 9, 11, 16, tzinfo=UTC),
        region="US",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    market = MarketObservation(
        id="c8-market-observation",
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        previous_observed_at=datetime(2026, 9, 10, 13, 30, tzinfo=UTC),
        observed_at=datetime(2026, 9, 11, 13, 30, tzinfo=UTC),
        close=98.0,
        previous_close=100.0,
        change_pct=-2.0,
        currency="USD",
        provider="test-daily-market",
    )
    batch = CollectionBatch(
        provider="c8-static",
        documents=[document],
        markets=[market],
        diagnostics={"market_items_expected": 1, "market_items_collected": 1},
        collected_at=now,
    )
    monkeypatch.setattr(
        "radar.pipeline.build_providers", lambda settings, universe: [StaticProvider(batch)]
    )
    settings = Settings(
        app_env="test",
        data_mode="live",
        database_url=f"sqlite:///{(tmp_path / 'pipeline.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=now)
    impacts = RadarStore(settings.database_url).load_market_impacts(result.run_id, C8_RULE_VERSION)
    html = (tmp_path / "reports" / "global-investment-radar-2026-09-12.html").read_text(
        encoding="utf-8"
    )

    assert len(impacts) == 1
    assert impacts[0].epistemic_status is EpistemicStatus.ASSOCIATED
    assert result.driver_candidates == 1
    assert "跨资产情报" in html
    assert "S&amp;P 500" in html
    assert "-2.00%" in html
    assert "caused by" not in html.casefold()
    assert document.collected_at == datetime(2026, 9, 11, 16, tzinfo=UTC)
    assert impacts[0].event_at == document.published_at


def test_c8_adds_no_network_or_ai_work(monkeypatch, tmp_path) -> None:
    calls = {"providers": 0, "analysis": 0}
    now = datetime(2026, 9, 11, 22, tzinfo=UTC)
    batch = CollectionBatch(provider="empty", collected_at=now)

    def providers(settings, universe):
        calls["providers"] += 1
        return [StaticProvider(batch)]

    monkeypatch.setattr("radar.pipeline.build_providers", providers)
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'no-cost.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    with suppress(RuntimeError):
        run_daily(settings, now=now)

    assert calls == {"providers": 1, "analysis": 0}
