from __future__ import annotations

from datetime import UTC, datetime

from radar.domain import (
    CandidateExplanation,
    DeterministicSummary,
    Event,
    EventType,
    GenerationMetadata,
    ReportQualityAssessment,
    ReportQualityStatus,
    StructuredExplanation,
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


def test_report_without_structured_explanation_keeps_deterministic_path() -> None:
    html = HtmlReportRenderer().render(_context())

    assert "<html" in html
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
