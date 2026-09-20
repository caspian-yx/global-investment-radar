from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radar.discovery import build_discovery_clusters
from radar.domain import SourceCategory, SourceRole, SourceTier
from radar.providers import make_document
from radar.story import consolidate_stories

NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)
ENTITIES = [
    {
        "id": "federal-reserve",
        "name": "Federal Reserve",
        "aliases": ["federal reserve", "fed", "fomc"],
        "importance_points": 10,
    }
]


def _document(
    *,
    provider: str,
    publisher: str,
    url: str,
    title: str = "Federal Reserve lowers policy rate target by 25 basis points",
    role: SourceRole = SourceRole.SECONDARY,
    category: SourceCategory = SourceCategory.PROFESSIONAL_NEWS,
    tier: SourceTier = SourceTier.B,
    published_at: datetime = NOW - timedelta(hours=2),
):
    return make_document(
        provider=provider,
        tier=tier,
        publisher=publisher,
        url=url,
        title=title,
        content=title,
        published_at=published_at,
        collected_at=NOW,
        source_category=category,
        source_role=role,
        region="US",
    )


def _build(documents):
    consolidated = consolidate_stories(documents, ENTITIES, now=NOW)
    return build_discovery_clusters(consolidated.stories, documents, now=NOW)


def test_same_event_preserves_source_diversity_and_primary_evidence() -> None:
    news = _document(
        provider="cnbc-top-news-rss",
        publisher="CNBC",
        url="https://example.test/cnbc-fed-cut",
    )
    official = _document(
        provider="federal-reserve-monetary-rss",
        publisher="Federal Reserve",
        url="https://example.test/fed-statement",
        title="Federal Reserve lowers policy rate target 25 basis points",
        role=SourceRole.PRIMARY,
        category=SourceCategory.OFFICIAL,
        tier=SourceTier.A,
    )

    result = _build([news, official])

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.source_publishers == ("CNBC", "Federal Reserve")
    assert cluster.source_categories == ("official", "professional_news")
    assert cluster.evidence_resolved is True
    assert cluster.focus_eligible is True
    assert cluster.rejection_reasons == ()


def test_news_discovery_without_primary_or_independent_support_is_not_focus_eligible() -> None:
    news = _document(
        provider="marketwatch-top-stories-rss",
        publisher="MarketWatch",
        url="https://example.test/unconfirmed-driver",
        title="Traders discuss a possible policy shift",
    )

    result = _build([news])

    cluster = result.clusters[0]
    assert cluster.evidence_resolved is False
    assert cluster.focus_eligible is False
    assert cluster.rejection_reasons == ("evidence_unresolved",)


def test_repeated_items_from_one_publisher_do_not_inflate_source_diversity() -> None:
    first = _document(
        provider="cnbc-top-news-rss",
        publisher="CNBC",
        url="https://example.test/cnbc-fed-cut-1",
    )
    repeated = _document(
        provider="cnbc-world-news-rss",
        publisher="CNBC",
        url="https://example.test/cnbc-fed-cut-2",
        title="Federal Reserve lowers policy rate target by 25 basis points today",
    )

    cluster = _build([first, repeated]).clusters[0]

    assert cluster.source_publishers == ("CNBC",)
    assert cluster.importance.source_diversity == 0.2


def test_cluster_projection_is_permutation_invariant() -> None:
    fed = _document(
        provider="federal-reserve-monetary-rss",
        publisher="Federal Reserve",
        url="https://example.test/fed-statement",
        role=SourceRole.PRIMARY,
        category=SourceCategory.OFFICIAL,
        tier=SourceTier.A,
    )
    unrelated = _document(
        provider="wsj-markets-rss",
        publisher="The Wall Street Journal",
        url="https://example.test/oil",
        title="Oil prices fall after inventory report",
    )

    forward = _build([fed, unrelated])
    reverse = _build([unrelated, fed])

    assert forward == reverse
    assert len(forward.clusters) == 2
