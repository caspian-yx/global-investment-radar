from __future__ import annotations

from test_briefing_composition import _compose

from radar.report import EmailHtmlReportRenderer, WebHtmlReportRenderer, report_context


def _render(*, briefing_override=None, **context_overrides) -> str:
    brief = briefing_override or _compose()[0]
    context = report_context(
        briefing=brief,
        is_demo=False,
        ai_test_data=False,
        data_mode="live",
        **context_overrides,
    )
    return EmailHtmlReportRenderer().render(context)


def _render_web(*, briefing_override=None, **context_overrides) -> str:
    brief = briefing_override or _compose()[0]
    context = report_context(
        briefing=brief,
        is_demo=False,
        ai_test_data=False,
        data_mode="live",
        **context_overrides,
    )
    return WebHtmlReportRenderer().render(context)


def test_c9_report_renders_complete_editorial_product() -> None:
    html = _render()

    for heading in (
        "Overnight in 30 Seconds",
        "Things That Matter",
        "Market Reaction",
        "Under the Radar",
        "Watch Today",
        "Market Snapshot",
        "Sources &amp; Data Quality",
    ):
        assert heading in html
    assert "Global Investment Radar" in html
    assert "AI is off" in html
    assert "policy material development" in html
    assert "It may reset policy expectations" in html


def test_report_is_mobile_safe_email_html_without_dashboard_dependencies() -> None:
    html = _render()
    lowered = html.casefold()

    assert 'name="viewport"' in lowered
    assert "max-width:680px" in lowered
    assert "width:100%" in lowered
    assert "<script" not in lowered
    assert "<link" not in lowered
    assert "position:fixed" not in lowered
    assert "display:grid" not in lowered
    assert "display:flex" not in lowered
    assert "linear-gradient" not in lowered
    assert "source_reason_codes" not in lowered
    assert "priority_reason" not in lowered
    assert "input_tokens" not in lowered
    assert "provider-status" not in lowered
    assert lowered.count("<table") == lowered.count("</table>")
    assert lowered.count("<tr") == lowered.count("</tr>")
    assert lowered.count("<td") == lowered.count("</td>")


def test_report_escapes_content_and_does_not_render_internal_ids() -> None:
    brief, _ = _compose()
    first = brief.primary_stories[0]
    hostile = first.model_copy(
        update={"headline": "Policy <script>alert('secret')</script> & update"}
    )
    brief = brief.model_copy(
        update={
            "primary_stories": (hostile, *brief.primary_stories[1:]),
            "overnight": (
                brief.overnight[0].model_copy(
                    update={"text": "Policy <script>alert('secret')</script> & update"}
                ),
                *brief.overnight[1:],
            ),
        }
    )
    html = EmailHtmlReportRenderer().render(
        report_context(
            briefing=brief,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
        )
    )

    assert "&lt;script&gt;alert" in html
    assert "<script>alert" not in html
    assert first.event_id not in html
    assert first.story_id not in html
    assert "claim-" not in html
    assert "evidence-" not in html


def test_report_keeps_c8_observational_and_bounded() -> None:
    from test_briefing_composition import _items
    from test_c8_storage import _record

    event_id = _items()[0][2].id
    brief, _ = _compose(
        market_impacts=(
            _record(event_id=event_id, symbol="^GSPC"),
            _record(event_id=event_id, symbol="^NDX"),
            _record(event_id=event_id, symbol="^DJI"),
        )
    )
    html = _render(briefing_override=brief)

    assert "associated, not causal" in html
    assert html.count("completed daily bar") <= 4
    assert "caused by" not in html.casefold()
    assert "triggered by" not in html.casefold()
    assert "investors sold because" not in html.casefold()
    assert "payload_json" not in html
    assert "reason_codes" not in html
    assert "api_key" not in html.casefold()
    assert "BUY" not in html
    assert "SELL" not in html


def test_low_activity_and_demo_states_are_explicit_and_complete() -> None:
    from test_briefing_composition import _quality
    from test_briefing_selection import NOW, _candidate, _select

    from radar.briefing import compose_daily_brief
    from radar.domain import EventType
    from radar.importance import ImportanceBand, PriorityBand, RelevanceBand

    weak = _candidate(
        "weak-report",
        event_type=EventType.OTHER,
        region="US",
        entity="other",
        importance=1,
        importance_band=ImportanceBand.LOW,
        relevance=0,
        relevance_band=RelevanceBand.NONE,
        priority=PriorityBand.IGNORE,
    )
    brief = compose_daily_brief(
        selection=_select([weak]),
        run_id="run-low-report",
        report_date="2026-09-13",
        view_name="Global",
        composed_at=NOW,
        ai_status="disabled",
        quality=_quality(),
        document_count=1,
        event_count=1,
    )
    html = EmailHtmlReportRenderer().render(
        report_context(
            briefing=brief,
            is_demo=True,
            ai_test_data=False,
            data_mode="demo",
        )
    )

    assert "Low-activity brief" in html
    assert "No strong Story met today’s primary threshold" in html
    assert "No validated linked reaction is available" in html
    assert "No watch item was inferred" in html
    assert "DEMO DATA" in html
