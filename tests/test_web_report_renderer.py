from __future__ import annotations

from datetime import UTC, datetime

from test_briefing_composition import _compose
from test_web_intelligence import _market

from radar.config import Settings
from radar.pipeline import run_daily
from radar.report import (
    EmailHtmlReportRenderer,
    WebHtmlReportRenderer,
    report_context,
)
from radar.web_intelligence import ChinaSectorObservation


def _context() -> dict[str, object]:
    briefing, _ = _compose()
    return report_context(
        briefing=briefing,
        is_demo=False,
        ai_test_data=False,
        data_mode="live",
        markets=(_market("^VIX", "VIX", 4.0, history=(1, 2, 3, 4, 5, 6, 7, 8)),),
    )


def test_web_and_email_renderers_have_separate_presentation_contracts() -> None:
    context = _context()
    web = WebHtmlReportRenderer().render(context)
    email = EmailHtmlReportRenderer().render(context)

    assert "Market Regime" in web
    assert "Volatility &amp; Rates" in web
    assert "<svg" in web
    assert "backdrop-filter" in web
    assert "display:grid" in web
    assert "display:grid" not in email
    assert "backdrop-filter" not in email
    assert "<script" not in email.casefold()


def test_email_renderer_carries_the_c7_intelligence_product_in_email_safe_markup() -> None:
    briefing, _ = _compose()
    markets = (
        _market("^GSPC", "S&P 500", 0.7),
        _market("^NDX", "Nasdaq 100", 1.4),
        _market("^DJI", "Dow Jones", -0.2),
        _market("^VIX", "VIX", -4.1, history=(21, 20, 19, 18, 17, 16, 15, 14)),
        _market("^VXN", "VXN", -3.2, history=(28, 27, 26, 25, 24, 23, 22, 21)),
        _market("^TNX", "US 10Y Treasury", 1.1, history=(4, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7)),
        _market("CL=F", "WTI Crude", -3.6, history=(79, 78, 77, 76, 75, 74, 73, 72)),
        _market("000001.SS", "SSE Composite", 0.8),
    )
    sectors = (
        ChinaSectorObservation(sector="通信", change_1d=4.57),
        ChinaSectorObservation(sector="煤炭", change_1d=-2.31),
    )

    email = EmailHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
            markets=markets,
            china_sectors=sectors,
        )
    )

    for section in (
        "Market Regime",
        "Global Market Strip",
        "Today’s Focus",
        "China / A-Shares",
        "Volatility &amp; Rates",
        "Market Signals",
        "What Matters Next",
        "Secondary Developments",
        "Sources &amp; Data Quality",
    ):
        assert section in email
    assert "CAUTIOUS" in email
    assert "VIX" in email
    assert "VXN" in email
    assert "US 10Y Treasury" in email
    assert "WTI Crude" in email
    assert "Evidence is insufficient to confirm" in email
    assert "5D" in email
    assert "7D" in email
    assert "通信" in email
    assert "煤炭" in email
    assert "Sector ranking" in email
    assert "What happened" in email
    assert "<script" not in email.casefold()
    assert "<svg" not in email.casefold()
    assert "backdrop-filter" not in email.casefold()
    assert "display:grid" not in email.casefold()
    assert "display:flex" not in email.casefold()


def test_web_interaction_contract_has_real_ranges_tooltips_and_fail_closed_hooks() -> None:
    briefing, _ = _compose()
    market = _market("^VIX", "VIX", 4.0, history=(1, 2, 3, 4, 5, 6, 7, 8))
    html = WebHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
            markets=(market,),
        )
    )

    assert 'data-range="1D"' in html
    assert 'data-range="1W"' in html
    assert 'data-range="1M"' in html
    assert 'data-points=' in html
    assert 'data-sparkline="' in html
    assert "pointermove" in html
    assert "pointerleave" in html
    assert "prefers-reduced-motion:reduce" in html
    assert "<noscript>" in html
    assert "scroll-snap-type:x mandatory" in html


def test_china_indices_render_even_when_sector_coverage_is_unavailable() -> None:
    briefing, _ = _compose()
    html = WebHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
            markets=(_market("^HSI", "Hang Seng", 1.2),),
        )
    )

    china = html.split('id="china-a-shares"', 1)[1].split('id="cross-asset"', 1)[0]
    assert "Hang Seng" in china
    assert "Sector ranking appears only when structured sector observations are available." in china


def test_web_renderer_honors_major_visual_budget_for_risk_and_china_visuals() -> None:
    briefing, _ = _compose()
    sectors = (ChinaSectorObservation(sector="通信", change_1d=4.57),)
    markets = (_market("^VIX", "VIX", 4.0, history=tuple(range(1, 9))),)
    html = WebHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
            markets=markets,
            china_sectors=sectors,
        )
    )

    assert "Visual budget 2 / 2" in html
    assert html.count("<svg") <= 1


def test_web_renderer_preserves_real_briefing_content() -> None:
    briefing, _ = _compose()
    html = WebHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
        )
    )

    assert briefing.primary_stories[0].headline in html
    assert briefing.primary_stories[0].why_it_matters in html
    assert "C9" not in html


def test_pipeline_saves_web_html_but_sends_email_safe_html(tmp_path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class Sender:
        name = "capture"

        def send(self, *, subject: str, html: str) -> str:
            captured["subject"] = subject
            captured["html"] = html
            return "captured"

    monkeypatch.setattr("radar.pipeline.build_email_sender", lambda settings: Sender())
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'report.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        email_mode="disabled",
    )

    result = run_daily(settings, now=datetime(2026, 9, 17, 9, tzinfo=UTC))
    saved = (tmp_path / "reports" / "global-investment-radar-2026-09-17.html").read_text(
        encoding="utf-8"
    )

    assert result.email_status == "captured"
    assert "display:grid" in saved
    assert "backdrop-filter" in saved
    assert "display:grid" not in captured["html"]
    assert "backdrop-filter" not in captured["html"]
    assert "<script" not in captured["html"].casefold()
