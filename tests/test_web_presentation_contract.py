from __future__ import annotations

from test_briefing_composition import _compose
from test_web_intelligence import _market

from radar.web_intelligence import (
    build_sparkline_points,
    build_web_intelligence,
    is_low_information_explanation,
    select_major_visuals,
)


def test_low_information_explanation_is_rejected() -> None:
    assert (
        is_low_information_explanation(
            "It may change regulatory, financial, or operating constraints."
        )
        is True
    )
    assert (
        is_low_information_explanation(
            "The filing changes issuer compliance workflow over the next reporting cycle."
        )
        is False
    )


def test_chart_eligibility_is_deterministic_and_respects_visual_budget() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    intelligence = build_web_intelligence(briefing)

    visuals = select_major_visuals(intelligence, max_visuals=2)

    assert len(visuals) <= 2
    assert intelligence.major_visuals == visuals
    assert [item.kind for item in visuals] == sorted(item.kind for item in visuals)
    assert all(item.eligible for item in visuals)


def test_sparkline_points_are_real_svg_coordinates_without_unicode_blocks() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    intelligence = build_web_intelligence(briefing)
    history = intelligence.market_observations

    assert history == ()
    assert build_sparkline_points(()) == ""


def test_sparkline_points_use_the_latest_seven_day_window() -> None:
    market = _market("^VIX", "VIX", 4.0, history=tuple(range(1, 12)))

    points = build_sparkline_points(market.history)

    assert len(points.split()) == 8
