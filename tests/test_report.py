from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radar.domain import (
    AssetClass,
    CandidateExplanation,
    DeterministicSummary,
    Event,
    EventType,
    GenerationMetadata,
    ReportQualityAssessment,
    ReportQualityStatus,
    SessionKind,
    StructuredExplanation,
)
from radar.impact import (
    C8_RULE_VERSION,
    AssociationConfidence,
    BarDateBasis,
    EpistemicStatus,
    ImpactMagnitude,
    ImpactReason,
    ImpactTargetKind,
    MarketImpactRecord,
    MarketTargetSnapshot,
    ReactionDirection,
    ReactionMeasurement,
    ReactionProfile,
    ReactionWindow,
    TransmissionBasisSnapshot,
    market_impact_id,
)
from radar.relevance import (
    RELATIONSHIP_RULE_VERSION,
    RelationshipReason,
    TransmissionChannel,
)
from radar.report import HtmlReportRenderer, report_context

AS_OF = datetime(2026, 9, 10, 8, tzinfo=UTC)


def _target() -> Event:
    return Event(
        id="target-event",
        event_key="market:^GSPC:2026-09-10",
        event_type=EventType.MARKET_MOVE,
        title="S&P 500 declined",
        occurred_at=AS_OF,
        first_seen_at=AS_OF,
        last_seen_at=AS_OF,
        entity_ids=["^GSPC"],
        claim_ids=[],
        evidence_ids=[],
        source_document_ids=[],
        importance_score=80,
        importance_components={"magnitude": 80},
        confidence=0.9,
    )


def _context(
    structured_explanations: dict[str, StructuredExplanation] | None = None,
) -> dict[str, object]:
    target = _target()
    return report_context(
        report_date="2026-09-10",
        run_id="run-test",
        data_mode="test",
        is_demo=False,
        ai_status="disabled",
        ai_test_data=False,
        quality=ReportQualityAssessment(
            status=ReportQualityStatus.HEALTHY,
            market_coverage=1.0,
        ),
        provider_statuses=[],
        markets=[],
        macro=[],
        events=[target],
        claims={},
        evidence={},
        sources={},
        analyses={},
        explanation_bundles={},
        cross_asset_context={},
        macro_backdrop={},
        summary=DeterministicSummary(
            market_headline="No market observations.",
            market_leaders=[],
            macro_headlines=[],
            risk_flags=[],
        ),
        coverage={
            "markets": 0,
            "macro": 0,
            "documents": 0,
            "events": 1,
        },
        freshness={
            "latest_market_at": None,
            "latest_source_at": None,
            "latest_macro_period": None,
        },
        missing_data=[],
        structured_explanations=structured_explanations,
    )


def _explanation(*, uncertainty: str | None = None) -> StructuredExplanation:
    return StructuredExplanation(
        target_event_id="target-event",
        headline="A generated headline & <script>alert(1)</script>",
        summary="A concise generated summary.",
        candidate_explanations=(
            CandidateExplanation(
                candidate_id="candidate-first",
                explanation="First explanation & <b>escaped</b>.",
            ),
            CandidateExplanation(
                candidate_id="candidate-second",
                explanation="Second explanation.",
            ),
        ),
        uncertainty=uncertainty,
        generation=GenerationMetadata(
            provider="deepseek",
            requested_model="deepseek-v4-flash",
            returned_model="deepseek-flash",
            input_tokens=12,
            output_tokens=34,
            total_tokens=46,
            latency_seconds=1.25,
        ),
    )


def _impact(
    *,
    move: float = -1.2,
    confidence: AssociationConfidence = AssociationConfidence.MEDIUM,
    ambiguous: bool = False,
) -> MarketImpactRecord:
    magnitude = ImpactMagnitude.FLAT if abs(move) < 0.1 else ImpactMagnitude.MODERATE
    direction = (
        ReactionDirection.FLAT
        if magnitude is ImpactMagnitude.FLAT
        else ReactionDirection.UP
        if move > 0
        else ReactionDirection.DOWN
    )
    reasons = {
        ImpactReason.C6_AUTHORIZED,
        ImpactReason.DIRECT_ENTITY_TARGET,
        ImpactReason.POST_EVENT_REACTION,
        ImpactReason.SAME_SESSION,
        ImpactReason.STRONG_TEMPORAL_ALIGNMENT,
        ImpactReason.NO_MEANINGFUL_REACTION
        if magnitude is ImpactMagnitude.FLAT
        else ImpactReason.MATERIAL_MOVE,
    }
    if ambiguous:
        reasons.add(ImpactReason.MULTIPLE_PLAUSIBLE_EVENTS)
    return MarketImpactRecord(
        id=market_impact_id(
            story_id=None,
            event_id="target-event",
            symbol="TEST",
            reaction_window=ReactionWindow.SAME_SESSION,
            rule_version=C8_RULE_VERSION,
        ),
        story_id=None,
        event_id="target-event",
        event_at=AS_OF,
        target=MarketTargetSnapshot(
            symbol="TEST",
            name="Test <script>secret()</script> Market",
            asset_class=AssetClass.EQUITY_INDEX,
            region="US",
            session_kind=SessionKind.EXCHANGE,
            timezone="America/New_York",
            close_time=datetime.strptime("16:00", "%H:%M").time(),
            bar_date_basis=BarDateBasis.UTC_PROVIDER_TIMESTAMP_DATE,
            reaction_profile=ReactionProfile.EQUITY_INDEX,
        ),
        reaction_window=ReactionWindow.SAME_SESSION,
        measurement=ReactionMeasurement(
            market_observation_id="market-observation",
            provider="test",
            raw_reference_at=AS_OF - timedelta(hours=2),
            raw_observed_at=AS_OF,
            reference_at=AS_OF - timedelta(hours=1),
            observed_at=AS_OF + timedelta(hours=1),
            currency="USD",
            reference_value=100.0,
            observed_value=100.0 + move,
            absolute_move=move,
            percentage_move=move,
        ),
        direction=direction,
        magnitude=magnitude,
        epistemic_status=EpistemicStatus.ASSOCIATED,
        association_confidence=confidence,
        transmission_basis=TransmissionBasisSnapshot(
            target_kind=ImpactTargetKind.DIRECT_TARGET_REACTION,
            source_channel=TransmissionChannel.DIRECT_ENTITY,
            source_reason_codes=(RelationshipReason.DIRECT_ENTITY_MATCH,),
            source_rule_version=RELATIONSHIP_RULE_VERSION,
        ),
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        event_evidence_ids=(),
        rule_version=C8_RULE_VERSION,
        evaluated_at=AS_OF + timedelta(hours=2),
    )


def test_report_without_structured_explanation_keeps_deterministic_path() -> None:
    html = HtmlReportRenderer().render(_context())

    assert "<html" in html
    assert "Evidence-first · Optional AI enrichment" in html
    assert "No-AI capable pipeline" not in html
    assert "Possible drivers" in html
    assert "AI explanation" not in html


def test_report_renders_structured_explanation_in_candidate_order() -> None:
    html = HtmlReportRenderer().render(
        _context({"target-event": _explanation()})
    )

    assert "A generated headline" in html
    assert "A concise generated summary." in html
    assert html.index("First explanation") < html.index("Second explanation")
    assert "candidate-first" not in html
    assert "candidate-second" not in html
    assert "Uncertainty" not in html


def test_report_renders_uncertainty_metadata_and_escapes_generated_text() -> None:
    html = HtmlReportRenderer().render(
        _context(
            {
                "target-event": _explanation(
                    uncertainty="Support is limited; causality remains uncertain."
                )
            }
        )
    )

    assert "Support is limited; causality remains uncertain." in html
    assert "deepseek" in html
    assert "deepseek-v4-flash" in html
    assert "deepseek-flash" in html
    assert "12 input tokens" in html
    assert "34 output tokens" in html
    assert "46 total tokens" in html
    assert "1.25s" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;b&gt;escaped&lt;/b&gt;" in html


def test_report_does_not_render_prompt_or_reasoning_content() -> None:
    html = HtmlReportRenderer().render(
        _context({"target-event": _explanation()})
    )

    assert "INPUT_JSON" not in html
    assert "reasoning_content" not in html


def test_report_renders_observed_reaction_without_causal_upgrade_or_internal_json() -> None:
    context = _context()
    context["market_impacts"] = {"target-event": [_impact()]}

    html = HtmlReportRenderer().render(context)

    assert "Market reaction" in html
    assert "-1.20%" in html
    assert "Magnitude MODERATE" in html
    assert "Association MEDIUM" in html
    assert "does not establish causality" in html
    assert "caused by" not in html.casefold()
    assert "source_reason_codes" not in html
    assert "&lt;script&gt;secret()&lt;/script&gt;" in html
    assert "<script>secret()</script>" not in html


def test_report_distinguishes_no_reaction_low_confidence_and_competing_events() -> None:
    context = _context()
    context["market_impacts"] = {
        "target-event": [
            _impact(move=0.01),
            _impact(confidence=AssociationConfidence.LOW, ambiguous=True),
        ]
    }

    html = HtmlReportRenderer().render(context)

    assert "limited / no meaningful immediate reaction" in html
    assert "evidence is insufficient to attribute it to this Story" in html
    assert "Multiple plausible Events share this target and window" in html
    assert "no single cause is assigned" in html
