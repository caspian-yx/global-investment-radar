from __future__ import annotations

from datetime import UTC, datetime

from radar.deterministic import canonicalize_url, cluster_documents, title_similarity
from radar.domain import SourceTier
from radar.providers import make_document


def test_canonicalize_url_removes_tracking() -> None:
    assert canonicalize_url("https://EXAMPLE.com/a?utm_source=x&id=7#top") == "https://example.com/a?id=7"


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
    )
    two = make_document(
        provider="test",
        tier=SourceTier.B,
        publisher="B",
        url="https://example.com/b",
        title="Central bank keeps policy rate unchanged, retains data dependent guidance",
        content="two",
        published_at=now,
    )
    assert title_similarity(one.title, two.title) > 0.68
    assert len(cluster_documents([one, two])) == 1
