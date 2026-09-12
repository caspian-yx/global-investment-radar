from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.deterministic import (
    assess_report_quality,
    build_deterministic_summary,
    canonicalize_url,
    cluster_documents,
    extract_events,
    title_similarity,
)
from radar.domain import (
    ClaimType,
    MacroObservation,
    ProviderRunStatus,
    ProviderStatus,
    ReportQualityStatus,
    SourceCategory,
    SourceKind,
    SourceRole,
    SourceTier,
)
from radar.providers import make_document


def test_canonicalize_url_removes_tracking() -> None:
    assert canonicalize_url("https://EXAMPLE.com/a?utm_source=x&id=7#top") == "https://example.com/a?id=7"


def test_macro_summary_separates_value_from_unit() -> None:
    observation = MacroObservation(
        id="macro-cpi",
        series_id="CPIAUCSL",
        name="CPI",
        period="2026-08",
        observed_at=datetime(2026, 9, 10, tzinfo=UTC),
        value=333.918,
        previous_value=None,
        change=None,
        change_label=None,
        unit="index",
        provider="fixture",
    )

    summary = build_deterministic_summary([], [observation], [])

    assert summary.macro_headlines == ["CPI: 333.918 index"]


def test_near_duplicate_titles_cluster() -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    one = make_document(
        provider="test",
        tier=SourceTier.A,
        publisher="A",
        url="https://example.com/a",
        title="Central bank keeps policy rate unchanged and retains data-dependent guidance",
        content="one",
        published_at=now,
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )
    two = make_document(
        provider="test",
        tier=SourceTier.B,
        publisher="B",
        url="https://example.com/b",
        title="Central bank keeps policy rate unchanged, retains data dependent guidance",
        content="two",
        published_at=now,
        source_category=SourceCategory.PROFESSIONAL_NEWS,
        source_role=SourceRole.SECONDARY,
    )
    assert title_similarity(one.title, two.title) > 0.68
    assert len(cluster_documents([one, two])) == 1


def test_official_document_event_keeps_fact_claim_and_document_provenance() -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    document = make_document(
        provider="official-rss",
        tier=SourceTier.A,
        publisher="Federal Reserve",
        url="https://example.com/policy",
        title="Federal Reserve keeps policy rate unchanged",
        content="The committee kept its policy rate unchanged.",
        published_at=now,
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
    )

    events, claims, evidence = extract_events([[document]], [], now)

    assert len(events) == len(claims) == len(evidence) == 1
    assert claims[0].claim_type is ClaimType.FACT
    assert evidence[0].source_kind is SourceKind.DOCUMENT
    assert evidence[0].source_id == document.id
    assert evidence[0].source_document_id == document.id


def test_quality_is_healthy_with_sufficient_markets_and_macro_support() -> None:
    quality = assess_report_quality(
        market_count=8,
        expected_market_count=10,
        macro_count=2,
        document_count=0,
        official_event_count=0,
        provider_statuses=[
            ProviderRunStatus(provider="rss", status=ProviderStatus.NO_DATA, item_count=0)
        ],
    )

    assert quality.status is ReportQualityStatus.HEALTHY
    assert quality.market_coverage == 0.8


def test_quality_is_degraded_when_usable_coverage_is_incomplete() -> None:
    quality = assess_report_quality(
        market_count=5,
        expected_market_count=10,
        macro_count=1,
        document_count=0,
        official_event_count=0,
        provider_statuses=[],
    )

    assert quality.status is ReportQualityStatus.DEGRADED


def test_quality_is_insufficient_when_all_categories_are_severely_missing() -> None:
    quality = assess_report_quality(
        market_count=1,
        expected_market_count=10,
        macro_count=0,
        document_count=0,
        official_event_count=0,
        provider_statuses=[],
    )

    assert quality.status is ReportQualityStatus.INSUFFICIENT


@pytest.mark.parametrize(
    ("market_count", "macro_count", "event_count", "provider_status", "expected"),
    [
        (7000, 1, 0, None, ReportQualityStatus.HEALTHY),
        (6999, 1, 0, None, ReportQualityStatus.DEGRADED),
        (2500, 0, 0, None, ReportQualityStatus.DEGRADED),
        (2499, 0, 0, None, ReportQualityStatus.INSUFFICIENT),
        (7000, 0, 1, None, ReportQualityStatus.HEALTHY),
        (7000, 1, 0, ProviderStatus.NO_DATA, ReportQualityStatus.HEALTHY),
        (7000, 1, 0, ProviderStatus.DEGRADED, ReportQualityStatus.DEGRADED),
        (7000, 1, 0, ProviderStatus.FAILED, ReportQualityStatus.DEGRADED),
    ],
)
def test_quality_gate_exact_boundaries_and_provider_policy(
    market_count: int,
    macro_count: int,
    event_count: int,
    provider_status: ProviderStatus | None,
    expected: ReportQualityStatus,
) -> None:
    statuses = (
        [ProviderRunStatus(provider="test", status=provider_status, item_count=0)]
        if provider_status is not None
        else []
    )

    quality = assess_report_quality(
        market_count=market_count,
        expected_market_count=10_000,
        macro_count=macro_count,
        document_count=0,
        official_event_count=event_count,
        provider_statuses=statuses,
    )

    assert quality.status is expected


def test_quality_uses_unrounded_market_coverage_for_boundary_decisions() -> None:
    quality = assess_report_quality(
        market_count=6999,
        expected_market_count=10_000,
        macro_count=1,
        document_count=0,
        official_event_count=0,
        provider_statuses=[],
    )

    assert quality.market_coverage == 0.6999
    assert quality.status is ReportQualityStatus.DEGRADED


def test_market_events_do_not_count_as_official_quality_support() -> None:
    quality = assess_report_quality(
        market_count=7000,
        expected_market_count=10_000,
        macro_count=0,
        document_count=0,
        official_event_count=0,
        provider_statuses=[],
    )

    assert quality.status is ReportQualityStatus.DEGRADED
    assert "official events" in quality.reasons[0]
