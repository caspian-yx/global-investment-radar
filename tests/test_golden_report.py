from __future__ import annotations

from datetime import UTC, datetime

from test_briefing_composition import _compose
from test_web_intelligence import _market

from radar.report import WebHtmlReportRenderer, report_context
from radar.web_intelligence import ChinaSectorObservation, build_web_intelligence


def test_2026_09_17_golden_market_intelligence_web_report() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    fresh_time = datetime(2026, 9, 17, 12, tzinfo=UTC)
    briefing = briefing.model_copy(
        update={
            "primary_stories": tuple(
                item.model_copy(update={"occurred_at": fresh_time})
                for item in briefing.primary_stories
            )
        }
    )
    markets = (
        _market("^NDX", "Nasdaq 100", 0.02, history=(100, 101, 102, 103, 104, 105, 106, 107)),
        _market("^DJI", "Dow Jones", -1.21, history=(100, 101, 102, 103, 104, 105, 106, 107)),
        _market("^VIX", "VIX", 5.0, history=(18, 19, 20, 21, 22, 23, 24, 25)),
        _market("^VXN", "VXN", 1.0, history=(20, 21, 22, 23, 24, 25, 26, 27)),
        _market("US2Y", "US 2Y Treasury Yield", 0.4, history=(4, 5, 6, 7, 8, 9, 10, 11)),
        _market("US10Y", "US 10Y Treasury Yield", 0.8, history=(4, 5, 6, 7, 8, 9, 10, 11)),
        _market("CL=F", "WTI Crude Futures", -4.06, history=(70, 71, 72, 73, 74, 75, 76, 77)),
    )
    sectors = (
        ChinaSectorObservation("通信", 4.57, style="Technology / Growth"),
        ChinaSectorObservation("电子", 3.73, style="Technology / Growth"),
        ChinaSectorObservation("银行", -0.68, style="Large-cap / Value"),
    )
    intelligence = build_web_intelligence(briefing, markets=markets, china_sectors=sectors)
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

    assert briefing.primary_stories[0].event_type.value == "policy"
    assert "WTI Crude Futures" in html
    assert "UNEXPLAINED_MAJOR_MOVE" not in html
    assert "尚无已验证的原因" in html
    assert "VIX" in html and "VXN" in html
    assert "美国2年期国债" in html and "美国10年期国债" in html
    assert "US_TECH_RELATIVE_STRENGTH" not in html
    assert "TECH_RISK_CONCENTRATION_CANDIDATE" not in html
    assert "科技股相对韧性" in html
    assert "通信" in html and "电子" in html and "Technology / Growth" in html
    assert "主要驱动暂未确认" in html
    assert len(intelligence.major_visuals) <= 2
    assert "全球投资雷达 · 2026-09-17" in html


def test_stale_story_is_demoted_to_secondary_without_mutating_briefing() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    fresh_time = datetime(2026, 9, 17, 12, tzinfo=UTC)
    fresh_stories = tuple(
        item.model_copy(update={"occurred_at": fresh_time}) for item in briefing.primary_stories
    )
    stale = fresh_stories[-1].model_copy(
        update={"occurred_at": datetime(2026, 9, 13, 12, tzinfo=UTC)}
    )
    original_order = tuple(item.event_id for item in briefing.primary_stories)
    updated = briefing.model_copy(
        update={
            "primary_stories": (*fresh_stories[:-1], stale),
            "overnight": (
                *briefing.overnight[:-1],
                briefing.overnight[-1].model_copy(update={"event_id": stale.event_id}),
            ),
        }
    )

    intelligence = build_web_intelligence(updated, report_date="2026-09-17")

    assert tuple(item.event_id for item in intelligence.primary_stories) == original_order[:-1]
    assert intelligence.decayed_stories[0].event_id == stale.event_id
    assert tuple(item.event_id for item in updated.primary_stories) == original_order


def test_golden_report_exposes_generic_freshness_decision_and_rank_change() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    current = datetime(2026, 9, 17, 12, tzinfo=UTC)
    stale = briefing.primary_stories[-1].model_copy(
        update={"occurred_at": datetime(2026, 9, 16, 12, tzinfo=UTC)}
    )
    updated = briefing.model_copy(
        update={
            "primary_stories": (
                *tuple(
                    item.model_copy(update={"occurred_at": current})
                    for item in briefing.primary_stories[:-1]
                ),
                stale,
            ),
            "overnight": (
                *briefing.overnight[:-1],
                briefing.overnight[-1].model_copy(update={"event_id": stale.event_id}),
            ),
        }
    )
    markets = (
        _market("CL=F", "WTI Crude Futures", -4.06, history=(70, 71, 72, 73, 74, 75, 76, 77)),
    )
    html = WebHtmlReportRenderer().render(
        report_context(
            briefing=updated,
            is_demo=False,
            ai_test_data=False,
            data_mode="live",
            markets=markets,
        )
    )

    assert "DEMOTE_SECONDARY" not in html
    assert "隔日观察窗口" in html
    assert "次要进展" in html
    assert "此前排名 #" in html
    assert "WTI Crude Futures" in html
    assert tuple(item.event_id for item in updated.primary_stories)[-1] == stale.event_id
