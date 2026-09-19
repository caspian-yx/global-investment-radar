from __future__ import annotations

import json

from test_briefing_composition import _compose, _market
from test_localization import _translated_payload

from radar.localization import build_localization_payload, parse_localization_response
from radar.report import EmailHtmlReportRenderer, WebHtmlReportRenderer, report_context


def test_localized_report_is_chinese_first_and_keeps_english_reference() -> None:
    briefing, _ = _compose()
    localized = parse_localization_response(
        json.dumps(_translated_payload(briefing), ensure_ascii=False),
        build_localization_payload(briefing),
        briefing,
    )

    html = EmailHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            data_mode="demo",
            is_demo=True,
            ai_test_data=False,
            localized=localized,
        )
    )

    assert '<html lang="zh-CN">' in html
    assert "中文本地化简报" in html
    assert "中文标题：" in html
    assert "English reference / 英文原文" in html
    assert "Things That Matter" in html
    assert "Evidence-linked intelligence, not investment advice." in html
    assert "<script" not in html.casefold()
    assert "briefing_id" not in html
    body = html.split("<body", 1)[1]
    assert body.index("全球投资雷达") < body.index("Global Investment Radar")
    assert body.count("全球投资雷达") == 1
    assert body.count("Global Investment Radar") == 1
    assert body.index("English reference / 英文原文") < body.index("Overnight in 30 Seconds")
    assert "全球投资晨报" in body.split("<table", 1)[0]


def test_english_report_shape_remains_the_default_without_localization() -> None:
    briefing, _ = _compose()
    html = EmailHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            data_mode="demo",
            is_demo=True,
            ai_test_data=False,
        )
    )

    assert '<html lang="en">' in html
    assert "中文本地化简报" not in html
    assert "Things That Matter" in html


def test_bilingual_snapshot_keeps_canonical_market_symbol_value_and_source_url() -> None:
    briefing, _ = _compose(markets=(_market("^GSPC", "S&P 500", "US", 1.2),))
    localized = parse_localization_response(
        json.dumps(_translated_payload(briefing), ensure_ascii=False),
        build_localization_payload(briefing),
        briefing,
    )

    html = EmailHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            data_mode="demo",
            is_demo=False,
            ai_test_data=False,
            localized=localized,
        )
    )

    chinese = html.split("English reference / 英文原文", 1)[0]
    assert "^GSPC" in chinese
    assert "+1.20%" in chinese
    first_url = briefing.primary_stories[0].sources[0].url
    assert f'href="{first_url}"' in chinese
    assert html.count(f'href="{first_url}"') == 2


def test_localized_web_report_is_chinese_first_but_keeps_canonical_values_and_sources() -> None:
    briefing, _ = _compose(markets=(_market("^GSPC", "S&P 500", "US", 1.2),))
    localized = parse_localization_response(
        json.dumps(_translated_payload(briefing), ensure_ascii=False),
        build_localization_payload(briefing),
        briefing,
    )

    html = WebHtmlReportRenderer().render(
        report_context(
            briefing=briefing,
            data_mode="demo",
            is_demo=False,
            ai_test_data=False,
            localized=localized,
        )
    )

    assert '<html lang="zh-CN">' in html
    assert "中文标题：" in html
    assert "中文关注：" in html
    assert "+1.20%" in html
    assert f'href="{briefing.primary_stories[0].sources[0].url}"' in html
    assert html.index("全球投资雷达") < html.index("中文标题：")
