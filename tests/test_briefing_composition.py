from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_briefing_selection import NOW, _candidate, _select
from test_c8_storage import _record
from test_explanation_bundles import (
    claim_for,
    driver_candidate,
    evidence_for,
    target_event,
)

from radar.briefing import (
    ExplanationBasis,
    SelectionReason,
    compose_daily_brief,
)
from radar.domain import (
    CandidateExplanation,
    EventType,
    ExplanationBundle,
    GenerationMetadata,
    MarketObservation,
    ProviderRunStatus,
    ProviderStatus,
    ReportQualityAssessment,
    ReportQualityStatus,
    StructuredExplanation,
)
from radar.impact import (
    AssociationConfidence,
    EpistemicStatus,
    ImpactMagnitude,
    ImpactReason,
    ImpactTargetKind,
)
from radar.importance import ImportanceBand, PriorityBand, RelevanceBand


def _items():
    return [
        _candidate(
            "policy",
            event_type=EventType.POLICY,
            region="US",
            entity="federal-reserve",
            importance=90,
            importance_band=ImportanceBand.CRITICAL,
            relevance=10,
            relevance_band=RelevanceBand.LOW,
            priority=PriorityBand.MUST_READ,
        ),
        _candidate(
            "semiconductor",
            event_type=EventType.SUPPLY_CHAIN,
            region="KR",
            entity="semiconductors",
            importance=70,
            importance_band=ImportanceBand.MATERIAL,
            relevance=70,
            relevance_band=RelevanceBand.HIGH,
            priority=PriorityBand.MUST_READ,
        ),
        _candidate(
            "legal",
            event_type=EventType.LEGAL,
            region="EU",
            entity="nvidia",
            importance=62,
            importance_band=ImportanceBand.MATERIAL,
            relevance=45,
            relevance_band=RelevanceBand.MEDIUM,
            priority=PriorityBand.IMPORTANT,
        ),
        _candidate(
            "guidance",
            event_type=EventType.GUIDANCE,
            region="US",
            entity="apple",
            importance=55,
            importance_band=ImportanceBand.ROUTINE,
            relevance=70,
            relevance_band=RelevanceBand.HIGH,
            priority=PriorityBand.WATCH,
        ),
    ]


def _quality() -> ReportQualityAssessment:
    return ReportQualityAssessment(
        status=ReportQualityStatus.HEALTHY,
        market_coverage=0.8,
    )


def _providers() -> list[ProviderRunStatus]:
    return [
        ProviderRunStatus(provider="official", status=ProviderStatus.OK, item_count=3),
        ProviderRunStatus(provider="market", status=ProviderStatus.DEGRADED, item_count=1),
    ]


def _market(symbol: str, name: str, region: str, change: float) -> MarketObservation:
    return MarketObservation(
        id=f"market-{symbol}",
        symbol=symbol,
        name=name,
        region=region,
        previous_observed_at=datetime(2026, 9, 10, 13, 30, tzinfo=UTC),
        observed_at=datetime(2026, 9, 11, 13, 30, tzinfo=UTC),
        close=100 + change,
        previous_close=100,
        change_pct=change,
        provider="test",
    )


def _compose(**overrides):
    items = _items()
    selection = _select(items)
    defaults = {
        "selection": selection,
        "run_id": "run-c9",
        "report_date": "2026-09-13",
        "view_name": "Global",
        "composed_at": NOW,
        "ai_status": "disabled",
        "quality": _quality(),
        "document_count": 4,
        "event_count": 7,
        "provider_statuses": _providers(),
        "markets": (),
        "assets": (),
        "market_impacts": (),
        "explanation_bundles": (),
        "structured_explanations": {},
    }
    defaults.update(overrides)
    return compose_daily_brief(**defaults), items


def _bundle_for(event_id: str) -> tuple[ExplanationBundle, str]:
    target = target_event("market-target")
    candidate = driver_candidate("driver", target_id=target.id)
    candidate = candidate.model_copy(update={"candidate_event_id": event_id})
    bundle = ExplanationBundle(
        target_event=target,
        driver_candidates=[candidate],
        claims=[claim_for(candidate)],
        evidence=evidence_for(candidate),
    )
    return bundle, candidate.id


def test_explanation_hierarchy_prefers_ai_then_c2_then_deterministic() -> None:
    event_id = _items()[0][2].id
    bundle, candidate_id = _bundle_for(event_id)
    structured = StructuredExplanation(
        target_event_id=bundle.target_event.id,
        headline="Observed market context",
        summary="The broader response remained measured.",
        candidate_explanations=(
            CandidateExplanation(
                candidate_id=candidate_id,
                explanation="Policy expectations were repriced cautiously.",
            ),
        ),
        generation=GenerationMetadata(
            provider="test",
            requested_model="test",
            latency_seconds=0,
        ),
    )

    ai_brief, _ = _compose(
        explanation_bundles=(bundle,),
        structured_explanations={bundle.target_event.id: structured},
        ai_status="success",
    )
    c2_brief, _ = _compose(explanation_bundles=(bundle,))
    deterministic_brief, _ = _compose()

    assert ai_brief.primary_stories[0].explanation_basis is ExplanationBasis.STRUCTURED_AI
    assert ai_brief.primary_stories[0].why_it_matters == (
        "Policy expectations were repriced cautiously."
    )
    assert {item.id for item in bundle.evidence} <= set(
        ai_brief.primary_stories[0].explanation_reference_ids
    )
    assert c2_brief.primary_stories[0].explanation_basis is ExplanationBasis.C2_EVIDENCE
    assert c2_brief.primary_stories[0].why_it_matters == bundle.claims[0].text
    assert {item.id for item in bundle.evidence} <= set(
        c2_brief.primary_stories[0].explanation_reference_ids
    )
    assert deterministic_brief.primary_stories[0].explanation_basis is (
        ExplanationBasis.DETERMINISTIC_SEMANTICS
    )


def test_ai_state_and_prose_never_change_selected_identity_or_order() -> None:
    event_id = _items()[0][2].id
    bundle, candidate_id = _bundle_for(event_id)
    structured = StructuredExplanation(
        target_event_id=bundle.target_event.id,
        headline="Context",
        summary="Summary",
        candidate_explanations=(
            CandidateExplanation(candidate_id=candidate_id, explanation="AI prose"),
        ),
        generation=GenerationMetadata(provider="test", requested_model="test", latency_seconds=0),
    )
    disabled, _ = _compose(explanation_bundles=(bundle,), ai_status="disabled")
    enabled, _ = _compose(
        explanation_bundles=(bundle,),
        structured_explanations={bundle.target_event.id: structured},
        ai_status="success",
    )

    assert [item.event_id for item in disabled.primary_stories] == [
        item.event_id for item in enabled.primary_stories
    ]
    assert disabled.overnight[0].event_id == enabled.overnight[0].event_id


@pytest.mark.parametrize("ai_status", ["disabled", "failed", "partial"])
def test_ai_unavailable_states_keep_complete_deterministic_product(ai_status: str) -> None:
    brief, _ = _compose(ai_status=ai_status)

    assert brief.primary_stories
    assert all(item.why_it_matters for item in brief.primary_stories)
    assert len(brief.overnight) == len(brief.primary_stories)


def test_watch_items_are_monitoring_actions_not_headline_repetition() -> None:
    brief, _ = _compose()

    assert brief.watch_today
    for watch in brief.watch_today:
        assert watch.text.casefold() != watch.headline.casefold()
        assert any(
            term in watch.text.casefold() for term in ("monitor", "observe", "follow", "clarify")
        )
        assert "2026-09-14" not in watch.text
        assert "earnings date" not in watch.text.casefold()


def test_watch_item_rejects_repetition_only_and_does_not_fill_quota() -> None:
    from pydantic import ValidationError

    from radar.briefing import WatchItem

    with pytest.raises(ValidationError, match="monitoring action"):
        WatchItem(
            event_id="event",
            headline="Existing policy development",
            text="Existing policy development",
            reason=SelectionReason.WATCH_UNRESOLVED,
            target_symbols=(),
        )

    weak = _candidate(
        "watch-weak",
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
        run_id="run-watch-empty",
        report_date="2026-09-13",
        view_name="Global",
        composed_at=NOW,
        ai_status="disabled",
        quality=_quality(),
        document_count=1,
        event_count=1,
    )
    assert brief.watch_today == ()


def test_c8_reactions_are_bounded_observational_and_uncertainty_is_explicit() -> None:
    event_id = _items()[0][2].id
    direct_base = _record(event_id=event_id, symbol="^GSPC")
    assert direct_base.transmission_basis is not None
    direct = direct_base.model_copy(
        update={
            "transmission_basis": direct_base.transmission_basis.model_copy(
                update={"target_kind": ImpactTargetKind.DIRECT_TARGET_REACTION}
            )
        }
    )
    small = _record(event_id=event_id, symbol="^NDX").model_copy(
        update={
            "magnitude": ImpactMagnitude.SMALL,
            "association_confidence": AssociationConfidence.LOW,
            "reason_codes": tuple(
                sorted(
                    set(_record(event_id=event_id, symbol="^NDX").reason_codes)
                    | {ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS},
                    key=lambda item: item.value,
                )
            ),
        }
    )
    third = _record(event_id=event_id, symbol="^DJI").model_copy(
        update={
            "magnitude": ImpactMagnitude.FLAT,
            "association_confidence": AssociationConfidence.NONE,
            "epistemic_status": EpistemicStatus.OBSERVED,
            "transmission_basis": None,
        }
    )

    brief, _ = _compose(market_impacts=(third, small, direct))
    reactions = brief.primary_stories[0].market_reactions

    assert len(reactions) == 2
    assert reactions[0].target_symbol == "^GSPC"
    assert "associated" in reactions[0].summary.lower()
    assert "caused" not in " ".join(item.summary for item in reactions).lower()
    assert "limited" in reactions[1].summary.lower()
    assert reactions[1].uncertainty is not None


def test_secondary_sections_are_bounded_derived_and_grouped() -> None:
    markets = (
        _market("^GSPC", "S&P 500", "US", 1.2),
        _market("^N225", "Nikkei 225", "JP", -0.4),
        _market("EURUSD=X", "EUR/USD", "FX", 0.2),
        _market("GC=F", "Gold", "Commodity", 0.6),
        _market("BTC-USD", "Bitcoin", "Crypto", -1.1),
        _market("EWZ", "Brazil ETF", "BR", 0.3),
    )
    assets = ({"symbol": market.symbol, "region": market.region} for market in markets)
    brief, items = _compose(markets=markets, assets=assets)

    assert len(brief.overnight) == len(brief.primary_stories)
    assert len(brief.under_radar) <= 3
    assert brief.under_radar[0].event_id == items[3][2].id
    assert SelectionReason.UNDER_RADAR_RELEVANT in brief.under_radar[0].reasons
    assert all(item.event_id for item in brief.watch_today)
    assert len(brief.watch_today) <= 3
    assert [group.name for group in brief.market_snapshot] == [
        "US",
        "Asia",
        "FX",
        "Commodities",
        "Crypto",
    ]
    assert all(item.symbol != "EWZ" for group in brief.market_snapshot for item in group.items)
    assert brief.data_quality.sources_healthy == 1
    assert brief.data_quality.sources_total == 2


def test_low_activity_brief_is_complete_without_invented_items() -> None:
    weak = _candidate(
        "weak",
        event_type=EventType.OTHER,
        region="US",
        entity="other",
        importance=5,
        importance_band=ImportanceBand.LOW,
        relevance=0,
        relevance_band=RelevanceBand.NONE,
        priority=PriorityBand.IGNORE,
    )
    brief = compose_daily_brief(
        selection=_select([weak]),
        run_id="run-low",
        report_date="2026-09-13",
        view_name="Global",
        composed_at=NOW,
        ai_status="failed",
        quality=_quality(),
        document_count=1,
        event_count=1,
    )

    assert brief.primary_stories == ()
    assert brief.overnight == ()
    assert brief.market_reactions == ()
    assert brief.under_radar == ()
    assert brief.watch_today == ()
    assert brief.market_snapshot == ()
    assert brief.data_quality.ai_status == "failed"
