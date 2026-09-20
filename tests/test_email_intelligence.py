from __future__ import annotations

from datetime import UTC, datetime

from test_briefing_composition import _compose
from test_c8_storage import _record
from test_web_intelligence import _market

from radar.calendar import ScheduledCatalyst
from radar.china_market import (
    CapabilityState,
    ChinaBreadth,
    ChinaCapabilitySummary,
    ChinaIndexSnapshot,
    ChinaLeadership,
    ChinaMarketIntelligence,
)
from radar.email_intelligence import build_email_intelligence
from radar.market_discovery import discover_market_anomalies


def test_quiet_or_stale_day_does_not_fill_focus_or_future_catalysts() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    stale_stories = tuple(
        story.model_copy(update={"occurred_at": datetime(2026, 9, 13, 12, tzinfo=UTC)})
        for story in briefing.primary_stories
    )
    stale_ids = {story.event_id for story in stale_stories}
    briefing = briefing.model_copy(update={"primary_stories": stale_stories})

    projection = build_email_intelligence(briefing)

    assert projection.focus == ()
    assert projection.next_catalysts == ()
    assert set(projection.diagnostics.stale_or_demoted_event_ids) == stale_ids


def test_volatility_complex_is_one_cluster_not_two_focus_items() -> None:
    briefing, _ = _compose(report_date="2026-09-17")
    projection = build_email_intelligence(
        briefing.model_copy(update={"primary_stories": (), "watch_today": ()}),
        markets=(
            _market("^VIX", "VIX", -4.08),
            _market("^VXN", "VXN", -3.36),
        ),
    )

    assert len(projection.market_signals) == 1
    signal = projection.market_signals[0]
    assert signal.cluster_key == "market:volatility"
    assert signal.symbols == ("^VIX", "^VXN")
    assert "-4.08%" in signal.observed
    assert "-3.36%" in signal.observed
    assert "DRIVER_UNRESOLVED" not in signal.unknown_zh
    assert "尚无足够证据" in signal.unknown_zh


def test_focus_explains_decision_value_without_internal_enums() -> None:
    briefing, _ = _compose()

    projection = build_email_intelligence(briefing)

    assert 0 < len(projection.focus) <= 5
    first = projection.focus[0]
    assert first.what_happened
    assert first.why_it_matters
    assert first.affected
    assert first.unknown_zh
    rendered = " ".join(
        filter(
            None,
            (
                first.what_happened,
                first.why_it_matters,
                first.affected,
                first.market_reaction_en,
                first.unknown_en,
            ),
        )
    )
    for internal in (
        "MUST_READ",
        "IMPORTANT",
        "STRUCTURED_AI",
        "DETERMINISTIC_SEMANTICS",
        "DRIVER_UNRESOLVED",
    ):
        assert internal not in rendered


def test_projection_does_not_mutate_canonical_briefing_or_ranking_order() -> None:
    briefing, _ = _compose()
    original = briefing.model_dump(mode="json")
    original_ids = tuple(story.event_id for story in briefing.primary_stories)

    projection = build_email_intelligence(briefing)

    assert briefing.model_dump(mode="json") == original
    projected_ids = tuple(item.event_id for item in projection.focus)
    assert projected_ids == tuple(item for item in original_ids if item in projected_ids)


def test_future_catalysts_require_specific_action_and_current_story() -> None:
    briefing, _ = _compose()
    generic = briefing.watch_today[0].model_copy(
        update={"text": "Monitor whether further official updates clarify this item."}
    )
    precise = briefing.watch_today[1].model_copy(
        update={
            "text": (
                "Watch the scheduled policy decision at 14:00 UTC and the published rate path."
            )
        }
    )
    briefing = briefing.model_copy(update={"watch_today": (generic, precise)})

    projection = build_email_intelligence(briefing)

    assert tuple(item.event_id for item in projection.next_catalysts) == (precise.event_id,)
    assert "14:00 UTC" in projection.next_catalysts[0].trigger


def test_focus_and_market_signals_have_disjoint_cluster_identities() -> None:
    briefing, _ = _compose()
    projection = build_email_intelligence(
        briefing,
        markets=(_market("^VIX", "VIX", 4.2), _market("^VXN", "VXN", 3.8)),
    )

    focus_clusters = {item.cluster_key for item in projection.focus}
    signal_clusters = {item.cluster_key for item in projection.market_signals}
    assert focus_clusters.isdisjoint(signal_clusters)


def test_us_index_divergence_is_one_reader_facing_signal() -> None:
    briefing, _ = _compose()
    projection = build_email_intelligence(
        briefing.model_copy(update={"primary_stories": (), "watch_today": ()}),
        markets=(
            _market("^NDX", "Nasdaq 100", 0.02),
            _market("^DJI", "Dow Jones", -1.21),
        ),
    )

    assert len(projection.market_signals) == 1
    signal = projection.market_signals[0]
    assert signal.cluster_key == "market:us-equity-divergence"
    assert signal.symbols == ("^NDX", "^DJI")
    assert "Nasdaq 100 +0.02%" in signal.observed
    assert "Dow Jones -1.21%" in signal.observed


def test_market_brief_is_concrete_bounded_and_uses_only_projection_facts() -> None:
    briefing, _ = _compose()
    projection = build_email_intelligence(
        briefing,
        markets=(
            _market("^NDX", "Nasdaq 100", 0.02),
            _market("^DJI", "Dow Jones", -1.21),
            _market("CL=F", "WTI Crude Futures", -4.06),
        ),
    )

    assert 1 <= len(projection.market_brief_zh) <= 4
    brief = " ".join(projection.market_brief_zh)
    assert "WTI Crude Futures -4.06%" in brief
    assert "3 个市场观察" in brief
    assert "确定性" not in brief
    assert "DRIVER_UNRESOLVED" not in brief


def test_abnormal_move_uses_structured_reaction_without_claiming_causality() -> None:
    base, _ = _compose()
    briefing, _ = _compose(
        market_impacts=(_record(event_id=base.primary_stories[0].event_id, symbol="CL=F"),)
    )
    reaction = briefing.primary_stories[0].market_reactions[0]
    briefing = briefing.model_copy(
        update={
            "primary_stories": (
                briefing.primary_stories[0].model_copy(
                    update={
                        "market_reactions": (reaction.model_copy(update={"target_symbol": "CL=F"}),)
                    }
                ),
                *briefing.primary_stories[1:],
            )
        }
    )

    projection = build_email_intelligence(
        briefing,
        markets=(_market("CL=F", "WTI Crude Futures", -4.06),),
    )

    signal = projection.market_signals[0]
    assert "结构化市场反应证据" in signal.interpretation_zh
    assert "主要驱动" in signal.unknown_zh
    assert "causality" in signal.unknown_en


def test_validated_calendar_replaces_news_derived_future_watch() -> None:
    briefing, _ = _compose()
    scheduled = ScheduledCatalyst(
        id="calendar-us-cpi",
        provider_id="bls-calendar",
        provider_name="U.S. Bureau of Labor Statistics",
        external_id="cpi-2026-09",
        title="Consumer Price Index",
        scheduled_at=datetime(2026, 9, 18, 12, 30, tzinfo=UTC),
        source_timezone="America/New_York",
        region="US",
        category="macro_release",
        importance="high",
        source_url="https://www.bls.gov/schedule/",
    )

    projection = build_email_intelligence(briefing, catalysts=(scheduled,))

    assert len(projection.next_catalysts) == 1
    assert projection.next_catalysts[0].event_id == "calendar-us-cpi"
    assert projection.next_catalysts[0].source_url == "https://www.bls.gov/schedule/"
    assert "2026-09-18 12:30 UTC" in projection.next_catalysts[0].trigger


def test_market_discovery_projection_uses_deterministic_signal_without_enum_leakage() -> None:
    briefing, _ = _compose()
    markets = (
        _market("^NDX", "Nasdaq 100", 0.02),
        _market("^DJI", "Dow Jones", -1.21),
        _market("CL=F", "WTI Crude Futures", -4.06),
    )
    discovery = discover_market_anomalies(markets, (), as_of=datetime(2026, 9, 17, tzinfo=UTC))

    projection = build_email_intelligence(
        briefing.model_copy(update={"primary_stories": (), "watch_today": ()}),
        markets,
        market_discovery=discovery,
    )

    rendered = " ".join(
        signal.title_zh + signal.interpretation_zh + signal.unknown_zh
        for signal in projection.market_signals
    )
    assert "美国科技股相对韧性" in rendered
    assert "WTI Crude Futures 异常波动" in rendered
    assert "US_TECH_RELATIVE_STRENGTH" not in rendered
    assert "absolute_1d" not in rendered
    assert "尚无足够证据确认主要驱动" in rendered


def test_china_capability_summary_is_specific_and_localized() -> None:
    briefing, _ = _compose()
    observed_at = datetime(2026, 9, 17, 7, tzinfo=UTC)
    china = ChinaMarketIntelligence(
        provider="eastmoney",
        indices=(
            ChinaIndexSnapshot(
                code="000001",
                name="SSE Composite",
                close=3300.0,
                change_pct=0.4,
                turnover=400_000_000_000.0,
                observed_at=observed_at,
                provider="eastmoney",
            ),
        ),
        breadth=ChinaBreadth(
            advancers=2800,
            decliners=1900,
            unchanged=200,
            total=4900,
            observed_at=observed_at,
            provider="eastmoney",
        ),
        leadership=ChinaLeadership(
            signals=("GROWTH_LEADERSHIP",),
            observed_at=observed_at,
            provider="eastmoney",
        ),
        capabilities=ChinaCapabilitySummary(
            indices=CapabilityState.AVAILABLE,
            sectors=CapabilityState.UNAVAILABLE,
            breadth=CapabilityState.AVAILABLE,
            turnover=CapabilityState.PARTIAL,
            style=CapabilityState.AVAILABLE,
        ),
        warnings=("Recognized sector taxonomy is unavailable.",),
    )

    projection = build_email_intelligence(briefing, china=china)

    assert projection.china is not None
    assert projection.china.indices[0].name_zh == "上证综指"
    assert projection.china.breadth_zh == "上涨 2800 / 下跌 1900 / 平盘 200"
    assert projection.china.leadership_zh == ("成长风格相对占优",)
    assert projection.china.sectors_available is False
    assert "GROWTH_LEADERSHIP" not in projection.model_dump_json()
