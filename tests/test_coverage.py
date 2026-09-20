from __future__ import annotations

from datetime import UTC, datetime

from radar.coverage import BenchmarkStory, CoverageMissReason, audit_coverage
from radar.discovery import DiscoveryCluster, ImportanceComponents

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


def _cluster(
    cluster_id: str,
    title: str,
    *,
    entities: tuple[str, ...] = (),
    evidence_resolved: bool = True,
) -> DiscoveryCluster:
    return DiscoveryCluster(
        cluster_id=cluster_id,
        story_id=cluster_id,
        title=title,
        event_type="policy",
        entity_ids=entities,
        topics=("RATES",),
        region="US",
        source_document_ids=(f"doc-{cluster_id}",),
        source_publishers=("Federal Reserve",),
        source_categories=("official",),
        primary_source_ids=(f"doc-{cluster_id}",) if evidence_resolved else (),
        latest_published_at=NOW,
        evidence_resolved=evidence_resolved,
        focus_eligible=evidence_resolved,
        importance=ImportanceComponents(
            authority=1.0,
            source_diversity=0.2,
            evidence_strength=1.0 if evidence_resolved else 0.2,
            freshness=1.0,
            systemic_relevance=1.0,
            total=0.8 if evidence_resolved else 0.4,
        ),
        rejection_reasons=() if evidence_resolved else ("evidence_unresolved",),
    )


def _benchmark(
    benchmark_id: str,
    title: str,
    provider: str,
    *,
    entities: tuple[str, ...] = (),
) -> BenchmarkStory:
    return BenchmarkStory(
        benchmark_id=benchmark_id,
        title=title,
        provider_id=provider,
        source_url=f"https://{provider}.example/{benchmark_id}",
        published_at=NOW,
        entity_ids=entities,
        topics=("RATES",),
        region="US",
        asset_classes=("RATE", "EQUITY_INDEX"),
        quality_sufficient=True,
    )


def test_coverage_audit_deduplicates_benchmark_and_calculates_literal_recall() -> None:
    benchmark = [
        _benchmark(
            "cnbc-fed",
            "Federal Reserve lowers policy rate by 25 basis points",
            "cnbc",
            entities=("federal-reserve",),
        ),
        _benchmark(
            "wsj-fed",
            "Fed lowers policy rate by 25 basis points",
            "wsj",
            entities=("federal-reserve",),
        ),
        _benchmark("oil-supply", "Oil falls after supply forecast", "marketwatch"),
    ]
    clusters = [
        _cluster(
            "fed-cluster",
            "Federal Reserve lowers policy rate 25 basis points",
            entities=("federal-reserve",),
        )
    ]

    audit = audit_coverage(
        benchmark,
        clusters,
        evidence_resolved_ids={"fed-cluster"},
        focus_ids={"fed-cluster"},
    )

    assert audit.measurable is True
    assert audit.benchmark_count == 2
    assert audit.discovered_count == 1
    assert audit.evidence_resolved_count == 1
    assert audit.focus_count == 1
    assert audit.major_story_recall == 0.5
    assert audit.focus_recall == 0.5
    assert len(audit.misses) == 1
    assert audit.misses[0].title == "Oil falls after supply forecast"
    assert audit.misses[0].reason is CoverageMissReason.NOT_INGESTED


def test_discovered_but_unresolved_story_reports_evidence_failure() -> None:
    benchmark = [_benchmark("oil", "Oil falls after supply forecast", "marketwatch")]
    cluster = _cluster(
        "oil-cluster",
        "Oil falls after supply forecast",
        evidence_resolved=False,
    )

    audit = audit_coverage(benchmark, [cluster], evidence_resolved_ids=set(), focus_ids=set())

    assert audit.discovered_count == 1
    assert audit.misses[0].reason is CoverageMissReason.EVIDENCE_FAILURE


def test_unreliable_benchmark_reports_not_measurable_instead_of_fake_recall() -> None:
    story = _benchmark("rumor", "Unverified market rumor", "unknown").model_copy(
        update={"quality_sufficient": False}
    )

    audit = audit_coverage([story], [], evidence_resolved_ids=set(), focus_ids=set())

    assert audit.measurable is False
    assert audit.major_story_recall is None
    assert audit.measurement_reason == "benchmark_quality_insufficient"
