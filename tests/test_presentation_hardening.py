from __future__ import annotations

import json

from test_briefing_composition import _compose, _market
from test_localization import _translated_payload

from radar.audience import DeliveryMode
from radar.localization import build_localization_payload, parse_localization_response
from radar.presentation import presentation_label
from radar.report import (
    EmailHtmlReportRenderer,
    RecipientComposition,
    WebHtmlReportRenderer,
    report_context,
)


def _localized_context() -> dict[str, object]:
    briefing, _ = _compose(markets=(_market("^GSPC", "S&P 500", "US", 1.2),))
    localized = parse_localization_response(
        json.dumps(_translated_payload(briefing), ensure_ascii=False),
        build_localization_payload(briefing),
        briefing,
    )
    return report_context(
        briefing=briefing,
        data_mode="live",
        is_demo=False,
        ai_test_data=False,
        localized=localized,
    )


def test_web_presentation_maps_internal_codes_and_localizes_system_copy() -> None:
    html = WebHtmlReportRenderer().render(_localized_context())
    chinese_body = html.split('id="english-reference"', 1)[0]

    for code in (
        "DETERMINISTIC_SIGNAL",
        "DEMOTE_SECONDARY",
        "UNEXPLAINED_MAJOR_MOVE",
        "BALANCED · NO DOMINANT REGIME",
        "NO_DATA",
        "UPTREND",
        "DOWNTREND",
    ):
        assert code not in html
    for untranslated in (
        "latest observation",
        "No current story cleared the headline threshold.",
        "Sector ranking appears only",
        "Deterministic market context:",
        "Visual budget",
        "Source:",
        "No watch item was inferred.",
    ):
        assert untranslated not in chinese_body
    assert "市场信号均衡" in chinese_body
    assert "^GSPC" in chinese_body
    assert "S&amp;P 500" in chinese_body


def test_email_presentation_localizes_system_status_without_changing_source_names() -> None:
    html = EmailHtmlReportRenderer().render(_localized_context())
    chinese_body = html.split("English reference / 英文原文", 1)[0]

    for code in ("DETERMINISTIC_SIGNAL", "DEMOTE_SECONDARY", "NO_DATA"):
        assert code not in html
    assert "BALANCED · NO DOMINANT REGIME" not in chinese_body
    assert "DEGRADED DATA" not in chinese_body
    assert "No completed market observation is available." not in chinese_body
    assert "市场信号均衡" in chinese_body
    assert "^GSPC" in chinese_body
    assert "S&amp;P 500" in chinese_body


def test_english_presentations_map_internal_statuses_without_rewriting_source_titles() -> None:
    context = _localized_context()
    context = report_context(
        briefing=context["briefing"],
        data_mode="live",
        is_demo=False,
        ai_test_data=False,
        composition=RecipientComposition(delivery_mode=DeliveryMode.ENGLISH),
    )
    for html in (
        WebHtmlReportRenderer().render(context),
        EmailHtmlReportRenderer().render(context),
    ):
        assert "DETERMINISTIC_SIGNAL" not in html
        assert "DEMOTE_SECONDARY" not in html
        assert "BALANCED · NO DOMINANT REGIME" not in html
        assert "S&amp;P 500" in html


def test_english_presentation_does_not_leak_chinese_system_copy() -> None:
    context = _localized_context()
    context = report_context(
        briefing=context["briefing"],
        data_mode="live",
        is_demo=False,
        ai_test_data=False,
        composition=RecipientComposition(delivery_mode=DeliveryMode.ENGLISH),
    )

    html = WebHtmlReportRenderer().render(context)

    for untranslated in ("已验证", "今日有", "异常波动", "跨资产观察"):
        assert untranslated not in html
    assert "Market context:" in html


def test_english_email_maps_ai_status_and_reference_regime() -> None:
    context = _localized_context()
    briefing = context["briefing"].model_copy(update={"ai_status": "failed"})
    context = report_context(
        briefing=briefing,
        data_mode="live",
        is_demo=False,
        ai_test_data=False,
        localized=context["localized"],
        composition=RecipientComposition(delivery_mode=DeliveryMode.ENGLISH),
    )

    html = EmailHtmlReportRenderer().render(context)

    assert "AI enrichment failed" not in html
    assert "Market Regime" in html
    assert "Balanced signals; no dominant market theme" in html


def test_unmapped_future_reason_code_fails_closed_at_presentation_boundary() -> None:
    assert presentation_label("MAJOR_POLICY_DECISION", zh=True) == "状态待确认"
    assert presentation_label("MAJOR_POLICY_DECISION", zh=False) == "Status unavailable"
