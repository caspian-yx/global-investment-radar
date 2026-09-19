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


def _context() -> dict[str, object]:
    briefing, _ = _compose()
    return report_context(
        briefing=briefing,
        is_demo=False,
        ai_test_data=False,
        data_mode="live",
        markets=(_market("^VIX", "VIX", 4.0, history=(1, 2, 3, 4, 5, 6, 7)),),
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
