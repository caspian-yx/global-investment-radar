from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_briefing_composition import _compose

from radar.domain import MarketHistoryPoint, MarketObservation
from radar.web_intelligence import (
    AbnormalMoveConfig,
    ChinaSectorObservation,
    FreshnessBand,
    SignalState,
    build_web_intelligence,
)


def _market(
    symbol: str, name: str, change: float, history: tuple[float, ...] = ()
) -> MarketObservation:
    observed = datetime(2026, 9, 17, 20, tzinfo=UTC)
    history_items = tuple(
        MarketHistoryPoint(
            observed_at=datetime(2026, 9, 17 - index, 20, tzinfo=UTC),
            close=value,
        )
        for index, value in enumerate(reversed(history))
    )
    return MarketObservation(
        id=f"market-{symbol}",
        symbol=symbol,
        name=name,
        region=(
            "HK"
            if symbol == "^HSI"
            else "CN"
            if symbol in {"000001.SS", "510300.SS"}
            else "US"
            if symbol.startswith("^") or symbol in {"US2Y", "US10Y"}
            else "Commodity"
        ),
        observed_at=observed,
        close=100 + change,
        previous_close=100,
        change_pct=change,
        currency="USD",
        provider="test",
        history=history_items,
    )


def test_market_observation_preserves_canonical_history_order() -> None:
    market = _market("^VIX", "VIX", 4.0, history=(18, 19, 20, 21, 22, 23, 24))

    assert [item.close for item in market.history] == [24, 23, 22, 21, 20, 19, 18]
    assert market.history[0].observed_at > market.history[-1].observed_at


def test_web_intelligence_marks_stale_stories_ineligible_for_headline() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    stale = briefing.primary_stories[0].model_copy(
        update={"occurred_at": datetime(2026, 9, 13, 12, tzinfo=UTC)}
    )
    briefing = briefing.model_copy(update={"primary_stories": (stale,)})

    intelligence = build_web_intelligence(briefing, report_date="2026-09-17")

    assessment = intelligence.freshness[stale.event_id]
    assert assessment.band is FreshnessBand.STALE
    assert assessment.headline_eligible is False


def test_story_lifecycle_is_generic_and_material_follow_up_reactivates() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    stories = list(briefing.primary_stories[:3])
    stories[0] = stories[0].model_copy(
        update={"occurred_at": datetime(2026, 9, 17, 18, tzinfo=UTC)}
    )
    stories[1] = stories[1].model_copy(
        update={"occurred_at": datetime(2026, 9, 16, 12, tzinfo=UTC), "supporting_source_count": 0}
    )
    stories[2] = stories[2].model_copy(
        update={"occurred_at": datetime(2026, 9, 13, 12, tzinfo=UTC)}
    )
    intelligence = build_web_intelligence(
        briefing.model_copy(update={"primary_stories": tuple(stories)}),
        report_date="2026-09-17",
    )

    day0 = intelligence.freshness[stories[0].event_id]
    day1 = intelligence.freshness[stories[1].event_id]
    repeated = intelligence.freshness[stories[2].event_id]
    follow_up_story = stories[1].model_copy(
        update={"occurred_at": datetime(2026, 9, 17, 19, tzinfo=UTC)}
    )
    follow_up_intelligence = build_web_intelligence(
        briefing.model_copy(update={"primary_stories": (follow_up_story,)}),
        report_date="2026-09-17",
    )
    follow_up = follow_up_intelligence.freshness[follow_up_story.event_id]
    assert day0.headline_eligible is True
    assert day0.information_gain is True
    assert day1.headline_eligible is False
    assert day1.decision == "DEMOTE_SECONDARY"
    assert "Day+1" in day1.decay_reason
    assert repeated.section == "Secondary / Decayed"
    assert follow_up.headline_eligible is True
    assert follow_up.information_gain is True
    assert follow_up.new_rank == 1


def test_web_intelligence_detects_abnormal_move_and_unresolved_driver() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    markets = (_market("CL=F", "WTI Crude Futures", -4.06),)

    intelligence = build_web_intelligence(
        briefing,
        markets=markets,
        report_date="2026-09-17",
        abnormal_config=AbnormalMoveConfig(absolute_pct=3.0),
    )

    move = intelligence.abnormal_moves[0]
    assert move.symbol == "CL=F"
    assert move.state is SignalState.DRIVER_UNRESOLVED
    assert "UNEXPLAINED_MAJOR_MOVE" in {gap.code for gap in intelligence.coverage_gaps}


def test_web_intelligence_detects_cross_asset_signals_and_regime() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    markets = (
        _market("^NDX", "Nasdaq 100", 0.02),
        _market("^DJI", "Dow Jones", -1.21),
        _market("^VIX", "VIX", 5.0),
        _market("^VXN", "VXN", 1.0),
        _market("US10Y", "US 10Y Treasury Yield", 0.8),
        _market("CL=F", "WTI Crude Futures", -4.06),
    )

    intelligence = build_web_intelligence(briefing, markets=markets, report_date="2026-09-17")
    signal_codes = {item.code for item in intelligence.cross_asset_signals}

    assert "US_TECH_RELATIVE_STRENGTH" in signal_codes
    assert "TECH_RISK_CONCENTRATION_CANDIDATE" in signal_codes
    assert intelligence.regime.label == "CAUTIOUS · RATES-LED · NOT PANIC"


def test_regime_is_not_rates_led_without_a_material_yield_move() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    markets = (
        _market("^VIX", "VIX", 5.0),
        _market("US10Y", "US 10Y Treasury Yield", 0.0),
    )

    intelligence = build_web_intelligence(briefing, markets=markets, report_date="2026-09-17")

    assert intelligence.regime.label == "CAUTIOUS · MIXED DRIVERS · NOT PANIC"


def test_coverage_gaps_identify_missing_required_risk_series() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    intelligence = build_web_intelligence(
        briefing,
        markets=(_market("^VIX", "VIX", 1.0),),
        report_date="2026-09-17",
    )

    codes = {item.code for item in intelligence.coverage_gaps}
    assert {"MISSING_VXN", "MISSING_US2Y", "MISSING_US10Y", "MISSING_WTI"} <= codes


def test_china_sectors_are_ranked_and_leadership_is_deterministic() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    sectors = (
        ChinaSectorObservation(sector="通信", change_1d=4.57, style="Technology / Growth"),
        ChinaSectorObservation(sector="电子", change_1d=3.73, style="Technology / Growth"),
        ChinaSectorObservation(sector="银行", change_1d=-0.68, style="Large-cap / Value"),
    )

    intelligence = build_web_intelligence(briefing, china_sectors=sectors)

    assert [item.sector for item in intelligence.china.sectors] == ["通信", "电子", "银行"]
    assert intelligence.china.breadth == (2, 1)
    assert intelligence.china.leadership == "Technology / Growth"
    assert intelligence.china.available is True


def test_china_sector_observations_require_provider_provenance() -> None:
    observation = ChinaSectorObservation(
        sector="通信",
        change_1d=4.57,
        provider="validated-sector-feed",
        source_document_ids=("source-1",),
    )

    assert observation.provider == "validated-sector-feed"
    assert observation.source_document_ids == ("source-1",)


def test_china_sector_observations_reject_blank_provider() -> None:
    with pytest.raises(ValueError, match="provider"):
        ChinaSectorObservation(sector="通信", change_1d=4.57, provider=" ")


def test_multi_day_context_uses_real_market_history() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    market = _market("^GSPC", "S&P 500", 2.0, history=(93, 94, 95, 96, 97, 98, 99, 100))

    intelligence = build_web_intelligence(briefing, markets=(market,))

    context = intelligence.multi_day[0]
    assert context.change_1d == 2.0
    assert context.change_5d == round((102 / 95 - 1) * 100, 2)
    assert context.change_7d == round((102 / 93 - 1) * 100, 2)


def test_next_horizons_are_relevance_gated_and_do_not_invent_calendar_events() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    intelligence = build_web_intelligence(briefing)

    assert len(intelligence.next_horizons.next_24_hours) == len(briefing.watch_today)
    assert intelligence.next_horizons.next_7_days == ()
