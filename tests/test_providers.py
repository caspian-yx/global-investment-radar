from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from radar.config import Settings, load_universe
from radar.deterministic import extract_story_events
from radar.domain import (
    CollectionBatch,
    EventType,
    MarketObservation,
    ProviderStatus,
    SourceCategory,
    SourceRole,
    SourceTier,
    stable_id,
)
from radar.ingestion import deduplicate_source_documents
from radar.providers import (
    BLSMacroProvider,
    DemoProvider,
    FredYieldProvider,
    OfficialRSSProvider,
    RSSFeedConfig,
    YahooChartProvider,
    build_providers,
)
from radar.story import consolidate_stories

FIXTURES = Path(__file__).parent / "fixtures"
APPLE = {"cik": "0000320193", "name": "Apple Inc."}
FED_FEED = {
    "url": "https://www.federalreserve.gov/feeds/press_all.xml",
    "publisher": "Federal Reserve",
    "source_tier": "A",
    "source_category": "official",
    "source_role": "primary",
    "native_ids_immutable": True,
    "region": "US",
}
SEC_FEED = {
    "url": "https://www.sec.gov/news/pressreleases.rss",
    "publisher": "U.S. SEC",
    "source_tier": "A",
    "source_category": "regulatory",
    "source_role": "primary",
    "native_ids_immutable": True,
    "region": "US",
}

APPROVED_FEED_FIXTURES = [
    (
        "boj_whatsnew.xml",
        {
            "provider_name": "bank-of-japan-whats-new-rss",
            "url": "https://www.boj.or.jp/en/rss/whatsnew.xml",
            "publisher": "Bank of Japan",
            "source_tier": "A",
            "source_category": "official",
            "source_role": "primary",
            "region": "JP",
            "language": "en",
        },
        datetime(2026, 9, 11, 1, tzinfo=UTC),
        SourceCategory.OFFICIAL,
    ),
    (
        "bok_monetary_policy.xml",
        {
            "provider_name": "bank-of-korea-monetary-policy-rss",
            "url": "https://www.bok.or.kr/eng/bbs/E0000627/news.rss?menuNo=400022",
            "publisher": "Bank of Korea",
            "source_tier": "A",
            "source_category": "official",
            "source_role": "primary",
            "region": "KR",
            "language": "en",
        },
        datetime(2026, 9, 10, 0, tzinfo=UTC),
        SourceCategory.OFFICIAL,
    ),
    (
        "nbs_china_releases.xml",
        {
            "provider_name": "china-nbs-releases-rss",
            "url": "https://www.stats.gov.cn/english/PressRelease/rss.xml",
            "publisher": "National Bureau of Statistics of China",
            "source_tier": "A",
            "source_category": "official",
            "source_role": "primary",
            "region": "CN",
            "language": "en",
            "publication_timezone": "Asia/Shanghai",
        },
        datetime(2026, 9, 10, 1, 30, 1, tzinfo=UTC),
        SourceCategory.OFFICIAL,
    ),
    (
        "eia_today_in_energy.xml",
        {
            "provider_name": "us-eia-today-in-energy-rss",
            "url": "https://www.eia.gov/rss/todayinenergy.xml",
            "publisher": "U.S. Energy Information Administration",
            "source_tier": "A",
            "source_category": "official",
            "source_role": "primary",
            "region": "US",
            "language": "en",
        },
        datetime(2026, 9, 10, 13, tzinfo=UTC),
        SourceCategory.OFFICIAL,
    ),
    (
        "apple_newsroom.xml",
        {
            "provider_name": "apple-newsroom-rss",
            "url": "https://www.apple.com/newsroom/rss-feed.rss",
            "publisher": "Apple Newsroom",
            "source_tier": "A",
            "source_category": "company_primary",
            "source_role": "primary",
            "region": "US",
            "language": "en",
        },
        datetime(2026, 9, 10, 14, tzinfo=UTC),
        SourceCategory.COMPANY_PRIMARY,
    ),
    (
        "nvidia_press_releases.xml",
        {
            "provider_name": "nvidia-press-releases-rss",
            "url": "https://nvidianews.nvidia.com/cats/press_release.xml",
            "publisher": "NVIDIA Newsroom",
            "source_tier": "A",
            "source_category": "company_primary",
            "source_role": "primary",
            "region": "US",
            "language": "en",
        },
        datetime(2026, 9, 10, 8, tzinfo=UTC),
        SourceCategory.COMPANY_PRIMARY,
    ),
]


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "ftp://example.test/feed.xml"},
        {"publication_timezone": "Mars/Olympus"},
        {"max_items": 101},
        {"max_response_bytes": 10_000_001},
        {"source_role": "secondary"},
        {"api_key": "must-not-enter-feed-config"},
        {"url": "https://user:password@example.test/feed.xml"},
        {"url": "https://example.test/feed.xml?api_key=must-not-persist"},
        {"url": "https://example.test/feed.xml?access_token=must-not-persist"},
        {"publisher": "   "},
        {"language": "   "},
        {"provider_name": "   "},
    ],
)
def test_rss_feed_configuration_fails_closed_for_invalid_policy_or_bounds(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        RSSFeedConfig.model_validate({**FED_FEED, **overrides})


def test_warning_does_not_automatically_degrade_provider_status() -> None:
    batch = CollectionBatch(provider="experimental", warnings=["No project-controlled SLA."])

    assert batch.status is ProviderStatus.OK


def test_no_data_is_distinct_from_failed() -> None:
    no_data = CollectionBatch(provider="official", status=ProviderStatus.NO_DATA)
    failed = CollectionBatch(provider="official", status=ProviderStatus.FAILED)

    assert no_data.status is ProviderStatus.NO_DATA
    assert failed.status is ProviderStatus.FAILED


def test_demo_provider_reports_canonical_collection_time() -> None:
    collected_at = datetime(2026, 9, 8, 16, tzinfo=UTC)

    batch = DemoProvider().collect(collected_at)

    assert batch.collected_at == collected_at
    assert all(document.collected_at == collected_at for document in batch.documents)


def test_fed_rss_fixture_parses_with_rfc2822_date() -> None:
    provider = OfficialRSSProvider([], lookback_hours=168)
    result = provider._parse(
        (FIXTURES / "fed_press_feed.xml").read_text(encoding="utf-8"),
        FED_FEED,
        datetime(2026, 9, 8, 0, tzinfo=UTC),
        timedelta(hours=168),
    )

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.published_at == datetime(2026, 9, 4, 15, tzinfo=UTC)
    assert document.collected_at == datetime(2026, 9, 8, 0, tzinfo=UTC)
    assert document.provider_native_id == (
        "https://www.federalreserve.gov/newsevents/pressreleases/enforcement20260904a.htm"
    )
    assert document.source_category is SourceCategory.OFFICIAL
    assert document.source_role is SourceRole.PRIMARY
    assert document.region == "US"
    assert document.provider_metadata == {
        "feed_url": "https://www.federalreserve.gov/feeds/press_all.xml"
    }
    assert result.diagnostics == {
        "total_entries": 1,
        "parsed_entries": 1,
        "filtered_by_date": 0,
        "invalid_date": 0,
        "invalid_link": 0,
        "duplicate_items": 0,
        "content_truncated": 0,
        "items_limited": 0,
    }


def test_public_news_feed_remains_secondary_discovery_not_primary_evidence() -> None:
    raw = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Stocks fall as yields rise</title>
      <link>https://news.example.test/markets/stocks-fall</link>
      <pubDate>Sat, 19 Sep 2026 18:00:00 GMT</pubDate>
      <description>Major indexes retreated while Treasury yields moved higher.</description>
      </item></channel></rss>"""
    feed = {
        "provider_name": "public-market-news-rss",
        "url": "https://news.example.test/rss/markets.xml",
        "publisher": "Public Market News",
        "source_tier": "B",
        "source_category": "professional_news",
        "source_role": "secondary",
        "region": "US",
        "language": "en",
    }

    result = OfficialRSSProvider([], lookback_hours=48)._parse(
        raw,
        feed,
        datetime(2026, 9, 20, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.source_category is SourceCategory.PROFESSIONAL_NEWS
    assert document.source_role is SourceRole.SECONDARY
    assert document.source_tier is SourceTier.B
    assert document.provider_metadata == {
        "feed_url": "https://news.example.test/rss/markets.xml"
    }


def test_sec_rss_fixture_parses_timezone_offset() -> None:
    provider = OfficialRSSProvider([], lookback_hours=168)
    result = provider._parse(
        (FIXTURES / "sec_press_feed.xml").read_text(encoding="utf-8"),
        SEC_FEED,
        datetime(2026, 9, 8, 0, tzinfo=UTC),
        timedelta(hours=168),
    )

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.published_at == datetime(2026, 9, 3, 20, 30, tzinfo=UTC)
    assert document.provider_native_id == "06cec215-1a77-4ef5-826d-4a0a23404513"
    assert document.source_category is SourceCategory.REGULATORY
    assert document.source_role is SourceRole.PRIMARY


def test_rss_native_id_is_used_only_when_feed_declares_it_immutable() -> None:
    raw = (FIXTURES / "fed_press_feed.xml").read_text(encoding="utf-8")
    provider = OfficialRSSProvider([], lookback_hours=168)
    untrusted_feed = {**FED_FEED, "native_ids_immutable": False}

    trusted = provider._parse(
        raw,
        FED_FEED,
        datetime(2026, 9, 8, tzinfo=UTC),
        timedelta(hours=168),
    ).documents[0]
    untrusted = provider._parse(
        raw,
        untrusted_feed,
        datetime(2026, 9, 8, tzinfo=UTC),
        timedelta(hours=168),
    ).documents[0]

    assert trusted.provider_native_id is not None
    assert untrusted.provider_native_id is None
    assert trusted.id != untrusted.id


def test_namespaced_atom_entry_parses_link_summary_and_updated_date() -> None:
    raw = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Policy update</title>
        <link href="https://example.test/policy" />
        <id>tag:example.test,2026:policy</id>
        <updated>2026-09-07T20:00:00Z</updated>
        <summary>Policy remained unchanged.</summary>
      </entry>
    </feed>
    """
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(raw, FED_FEED, datetime(2026, 9, 8, tzinfo=UTC), timedelta(hours=48))

    assert len(result.documents) == 1
    assert result.documents[0].canonical_url == "https://example.test/policy"
    assert result.documents[0].content == "Policy remained unchanged."
    assert result.documents[0].published_at == datetime(2026, 9, 7, 20, tzinfo=UTC)


@pytest.mark.parametrize("reverse_links", [False, True])
def test_atom_parser_uses_alternate_page_link_regardless_of_link_order(
    reverse_links: bool,
) -> None:
    links = [
        '<link rel="self" href="https://example.test/feed/entry-7" />',
        '<link rel="alternate" type="text/html" href="https://example.test/policy" />',
    ]
    if reverse_links:
        links.reverse()
    raw = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Policy update</title>{"".join(links)}
      <updated>2026-09-07T20:00:00Z</updated>
    </entry></feed>"""
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(
        raw,
        FED_FEED,
        datetime(2026, 9, 8, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert result.documents[0].canonical_url == "https://example.test/policy"


def test_namespaced_rss_and_malformed_items_are_counted_without_aborting() -> None:
    raw = """<rss xmlns="urn:example:rss"><channel>
      <item>
        <title>Current item</title><link>https://example.test/current</link>
        <pubDate>Mon, 07 Sep 2026 20:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Old item</title><link>https://example.test/old</link>
        <pubDate>Mon, 31 Aug 2026 20:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Bad date</title><link>https://example.test/bad-date</link>
        <pubDate>not-a-date</pubDate>
      </item>
      <item>
        <title>Bad link</title><link>javascript:alert(1)</link>
        <pubDate>Mon, 07 Sep 2026 20:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Duplicate</title><link>https://example.test/current</link>
        <pubDate>Mon, 07 Sep 2026 21:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(raw, FED_FEED, datetime(2026, 9, 8, tzinfo=UTC), timedelta(hours=48))

    assert [document.title for document in result.documents] == ["Duplicate"]
    assert result.diagnostics == {
        "total_entries": 5,
        "parsed_entries": 3,
        "filtered_by_date": 1,
        "invalid_date": 1,
        "invalid_link": 1,
        "duplicate_items": 1,
        "content_truncated": 0,
        "items_limited": 0,
    }


def test_naive_iso_date_uses_utc_and_future_item_is_rejected() -> None:
    raw = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Naive current item</title><link href="https://example.test/current" />
        <updated>2026-09-07T20:00:00</updated>
      </entry>
      <entry>
        <title>Near future item</title><link href="https://example.test/near-future" />
        <updated>2026-09-08T00:01:00Z</updated>
      </entry>
      <entry>
        <title>Future item</title><link href="https://example.test/future" />
        <updated>2026-09-10T20:00:00Z</updated>
      </entry>
    </feed>"""
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(raw, FED_FEED, datetime(2026, 9, 8, tzinfo=UTC), timedelta(hours=48))

    assert [document.title for document in result.documents] == ["Naive current item"]
    assert result.documents[0].published_at == datetime(2026, 9, 7, 20, tzinfo=UTC)
    assert result.diagnostics["invalid_date"] == 2


@pytest.mark.parametrize(
    ("fixture_name", "feed", "expected_published_at", "expected_category"),
    APPROVED_FEED_FIXTURES,
)
def test_approved_feed_fixtures_preserve_provenance_and_publication_time(
    fixture_name: str,
    feed: dict[str, object],
    expected_published_at: datetime,
    expected_category: SourceCategory,
) -> None:
    provider = OfficialRSSProvider([], lookback_hours=168)
    universe = load_universe(Settings(_env_file=None))
    registered_feeds = {item["provider_name"]: item for item in universe["rss_feeds"]}
    registered_feed = registered_feeds[str(feed["provider_name"])]

    result = provider._parse(
        (FIXTURES / fixture_name).read_text(encoding="utf-8"),
        registered_feed,
        datetime(2026, 9, 11, 12, tzinfo=UTC),
        timedelta(hours=168),
    )

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.published_at == expected_published_at
    assert document.source_category is expected_category
    assert document.source_role is SourceRole.PRIMARY
    assert document.source_tier is SourceTier.A
    assert document.region == registered_feed["region"]
    assert document.language == "en"
    assert document.provider_native_id is None
    assert document.provider_metadata["feed_url"] == registered_feed["url"]
    if fixture_name == "nbs_china_releases.xml":
        assert document.title == "Consumer Prices in August 2026"
        assert document.canonical_url == (
            "https://www.stats.gov.cn/english/PressRelease/202609/t20260910_000001.html"
        )
        assert document.content == "The consumer price index increased year on year."
        assert (
            document.provider_metadata["publication_timezone_inferred"]
            == registered_feed["publication_timezone"]
        )


def test_approved_source_families_enter_provider_neutral_event_extraction() -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    universe = load_universe(Settings(_env_file=None))
    registered_feeds = {item["provider_name"]: item for item in universe["rss_feeds"]}
    provider = OfficialRSSProvider([], lookback_hours=168)
    documents = []
    for fixture_name, feed, _, _ in APPROVED_FEED_FIXTURES:
        documents.extend(
            provider._parse(
                (FIXTURES / fixture_name).read_text(encoding="utf-8"),
                registered_feeds[str(feed["provider_name"])],
                now,
                timedelta(hours=168),
            ).documents
        )

    normalized = deduplicate_source_documents(documents).documents
    consolidated = consolidate_stories(normalized, universe["entities"], now=now)
    events, _, _ = extract_story_events(
        consolidated.stories,
        normalized,
        now,
    )
    event_types = {event.title: event.event_type for event in events}

    assert len(normalized) == len(events) == 6
    assert event_types == {
        "Statement on Monetary Policy": EventType.POLICY,
        "Monetary Policy Decision": EventType.POLICY,
        "Consumer Prices in August 2026": EventType.MACRO,
        "Global oil inventories increased in August": EventType.SUPPLY_CHAIN,
        "Apple introduces a new generation of chips": EventType.PRODUCT,
        "NVIDIA launches accelerated computing platform": EventType.PRODUCT,
    }


def test_rss_parser_canonicalizes_duplicates_before_accepting_items() -> None:
    raw = """<rss><channel>
      <item><title>First</title>
        <link>https://example.test/release?id=7&amp;utm_source=feed</link>
        <pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate></item>
      <item><title>Duplicate</title>
        <link>https://EXAMPLE.test/release?id=7#top</link>
        <pubDate>Thu, 10 Sep 2026 13:00:00 GMT</pubDate></item>
    </channel></rss>"""
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(
        raw,
        FED_FEED,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert [document.title for document in result.documents] == ["Duplicate"]
    assert result.documents[0].canonical_url == "https://example.test/release?id=7"
    assert result.diagnostics["duplicate_items"] == 1


def test_rss_meta_normalization_does_not_modify_cdata() -> None:
    provider = OfficialRSSProvider([], lookback_hours=48)
    raw = (
        '<rss><channel><meta name="feed" content="metadata">'
        "<item><title>Item</title><link>https://example.test/item</link>"
        "<pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate>"
        '<description><![CDATA[<meta name="content"/>Body]]></description>'
        "</item></channel></rss>"
    )

    normalized = provider._normalize_xml_empty_meta(raw)

    assert '<meta name="feed" content="metadata"/>' in normalized
    assert '<![CDATA[<meta name="content"/>Body]]>' in normalized


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://user:password@example.test/release",
        "https://example.test/release?token=must-not-persist",
        "https://example.test/release#access_token=must-not-persist",
    ],
)
def test_rss_parser_rejects_item_urls_with_embedded_secrets(unsafe_url: str) -> None:
    raw = f"""<rss><channel><item><title>Unsafe link</title>
      <link>{unsafe_url.replace("&", "&amp;")}</link>
      <pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(
        raw,
        FED_FEED,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert result.documents == []
    assert result.diagnostics["invalid_link"] == 1


def test_rss_provider_local_dedupe_has_stable_native_id_tie_breaker() -> None:
    items = [
        """<item><title>Same release</title>
          <link>https://example.test/release?utm_source=one</link><guid>native-b</guid>
          <pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate>
          <description>Same body.</description></item>""",
        """<item><title>Same release</title>
          <link>https://EXAMPLE.test/release#top</link><guid>native-a</guid>
          <pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate>
          <description>Same body.</description></item>""",
    ]
    feed = {**FED_FEED, "native_ids_immutable": True}
    provider = OfficialRSSProvider([], lookback_hours=48)

    forward = provider._parse(
        f"<rss><channel>{''.join(items)}</channel></rss>",
        feed,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )
    reverse = provider._parse(
        f"<rss><channel>{''.join(reversed(items))}</channel></rss>",
        feed,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert forward.documents == reverse.documents
    assert forward.documents[0].provider_native_id == "native-a"


def test_rss_provider_local_dedupe_orders_timezone_provenance_metadata() -> None:
    items = [
        """<item><title>Same release</title><link>https://example.test/release</link>
          <pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate>
          <description>Same body.</description></item>""",
        """<item><title>Same release</title><link>https://example.test/release</link>
          <pubDate>2026-09-10T12:00:00</pubDate>
          <description>Same body.</description></item>""",
    ]
    provider = OfficialRSSProvider([], lookback_hours=48)

    forward = provider._parse(
        f"<rss><channel>{''.join(items)}</channel></rss>",
        FED_FEED,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )
    reverse = provider._parse(
        f"<rss><channel>{''.join(reversed(items))}</channel></rss>",
        FED_FEED,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert forward.documents == reverse.documents


def test_rss_parser_sorts_then_applies_item_and_content_limits() -> None:
    raw = """<rss><channel>
      <item><title>Oldest</title><link>https://example.test/oldest</link>
        <pubDate>Thu, 10 Sep 2026 10:00:00 GMT</pubDate>
        <description>Short body.</description></item>
      <item><title>Newest</title><link>https://example.test/newest</link>
        <pubDate>Thu, 10 Sep 2026 12:00:00 GMT</pubDate>
        <description>&lt;script&gt;steal()&lt;/script&gt;
          &lt;p&gt;A deliberately long clean body for truncation.&lt;/p&gt;
        </description></item>
      <item><title>Middle</title><link>https://example.test/middle</link>
        <pubDate>Thu, 10 Sep 2026 11:00:00 GMT</pubDate>
        <description>Middle body.</description></item>
    </channel></rss>"""
    feed = {**FED_FEED, "max_items": 2, "max_content_chars": 25}
    provider = OfficialRSSProvider([], lookback_hours=48)

    result = provider._parse(
        raw,
        feed,
        datetime(2026, 9, 11, tzinfo=UTC),
        timedelta(hours=48),
    )

    assert [document.title for document in result.documents] == ["Newest", "Middle"]
    assert result.documents[0].content == "A deliberately long clean"
    assert "steal" not in result.documents[0].content
    assert result.diagnostics["content_truncated"] == 1
    assert result.diagnostics["items_limited"] == 1


def test_rss_collection_fails_cleanly_when_response_exceeds_configured_limit(
    monkeypatch,
) -> None:
    raw = (FIXTURES / "fed_press_feed.xml").read_text(encoding="utf-8")
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw.encode(), request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    feed = {**FED_FEED, "max_response_bytes": 64}

    batch = OfficialRSSProvider([feed], lookback_hours=168).collect(
        datetime(2026, 9, 8, tzinfo=UTC)
    )

    assert batch.status is ProviderStatus.FAILED
    assert batch.documents == []
    assert batch.diagnostics["oversize_responses"] == 1
    assert all("fed_press_feed" not in warning for warning in batch.warnings)


def test_rss_feed_lookback_override_remains_strict_on_monday(monkeypatch) -> None:
    raw = """<rss><channel><item><title>Two hours old</title>
      <link>https://example.test/two-hours-old</link>
      <pubDate>Mon, 14 Sep 2026 10:00:00 GMT</pubDate>
    </item></channel></rss>"""
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=raw, request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    feed = {**FED_FEED, "lookback_hours": 1}

    batch = OfficialRSSProvider([feed], lookback_hours=168).collect(
        datetime(2026, 9, 14, 12, tzinfo=UTC)
    )

    assert batch.status is ProviderStatus.NO_DATA
    assert batch.documents == []
    assert batch.diagnostics["filtered_by_date"] == 1


def test_rss_http_200_non_feed_document_is_a_provider_failure(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body>Proxy login page</body></html>",
            request=request,
        )

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    batch = OfficialRSSProvider([FED_FEED], lookback_hours=48).collect(
        datetime(2026, 9, 14, 12, tzinfo=UTC)
    )

    assert batch.status is ProviderStatus.FAILED
    assert batch.diagnostics["feeds_succeeded"] == 0
    assert batch.diagnostics["feeds_failed"] == 1


def test_rss_no_new_items_is_no_data_with_parse_diagnostics(monkeypatch) -> None:
    raw = (FIXTURES / "fed_press_feed.xml").read_text(encoding="utf-8")
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=raw, request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = OfficialRSSProvider([FED_FEED], lookback_hours=48)

    batch = provider.collect(datetime(2026, 9, 8, 12, tzinfo=UTC))

    assert batch.status is ProviderStatus.NO_DATA
    assert batch.documents == []
    assert batch.diagnostics["total_entries"] == 1
    assert batch.diagnostics["parsed_entries"] == 1
    assert batch.diagnostics["filtered_by_date"] == 1
    assert batch.diagnostics["feeds_succeeded"] == 1
    assert batch.diagnostics["feeds_failed"] == 0


def test_rss_partial_feed_failure_is_degraded_and_keeps_documents(monkeypatch) -> None:
    raw = (FIXTURES / "fed_press_feed.xml").read_text(encoding="utf-8")
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if "federalreserve" in str(request.url):
            return httpx.Response(200, text=raw, request=request)
        return httpx.Response(503, request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = OfficialRSSProvider([FED_FEED, SEC_FEED], lookback_hours=168)

    batch = provider.collect(datetime(2026, 9, 8, tzinfo=UTC))

    assert batch.status is ProviderStatus.DEGRADED
    assert len(batch.documents) == 1
    assert batch.diagnostics["feeds_succeeded"] == 1
    assert batch.diagnostics["feeds_failed"] == 1
    assert any("U.S. SEC" in warning for warning in batch.warnings)


def test_rss_all_feed_failures_are_failed(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = OfficialRSSProvider([FED_FEED, SEC_FEED], lookback_hours=48)

    batch = provider.collect(datetime(2026, 9, 8, tzinfo=UTC))

    assert batch.status is ProviderStatus.FAILED
    assert batch.diagnostics["feeds_succeeded"] == 0
    assert batch.diagnostics["feeds_failed"] == 2


def test_rss_instance_name_is_used_for_batch_and_documents(monkeypatch) -> None:
    raw = (FIXTURES / "fed_press_feed.xml").read_text(encoding="utf-8")
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=raw, request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = OfficialRSSProvider(
        [FED_FEED], lookback_hours=168, name="federal-reserve-monetary-rss"
    )

    batch = provider.collect(datetime(2026, 9, 8, tzinfo=UTC))

    assert batch.provider == "federal-reserve-monetary-rss"
    assert batch.documents[0].provider == "federal-reserve-monetary-rss"


def test_rss_failure_warning_does_not_include_raw_exception(monkeypatch) -> None:
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("contact=private@example.com", request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    batch = OfficialRSSProvider([FED_FEED], lookback_hours=48).collect(
        datetime(2026, 9, 8, tzinfo=UTC)
    )

    assert batch.status is ProviderStatus.FAILED
    assert all("private@example.com" not in warning for warning in batch.warnings)


def test_build_providers_registers_sec_and_distinct_named_rss_instances() -> None:
    universe = {
        "assets": [],
        "sec_filings": {"filers": [APPLE], "forms": ["8-K"]},
        "rss_feeds": [
            {**FED_FEED, "provider_name": "federal-reserve-monetary-rss"},
            {**SEC_FEED, "provider_name": "sec-newsroom-rss"},
        ],
    }
    settings = Settings(
        _env_file=None,
        DATA_MODE="live",
        SEC_CONTACT_EMAIL="radar-owner@example.com",
    )

    providers = build_providers(settings, universe)

    assert [provider.name for provider in providers] == [
        "experimental-yahoo-chart",
        "bls-public-api",
        "sec-edgar-submissions",
        "federal-reserve-monetary-rss",
        "sec-newsroom-rss",
    ]


def test_build_providers_excludes_disabled_rss_feed() -> None:
    universe = {
        "assets": [],
        "rss_feeds": [
            {**FED_FEED, "provider_name": "enabled-feed", "enabled": True},
            {**SEC_FEED, "provider_name": "disabled-feed", "enabled": False},
        ],
    }
    settings = Settings(_env_file=None, DATA_MODE="live")

    providers = build_providers(settings, universe)

    assert [provider.name for provider in providers] == [
        "experimental-yahoo-chart",
        "bls-public-api",
        "enabled-feed",
    ]


def test_live_universe_registers_approved_c5_2_sources_with_explicit_bounds() -> None:
    universe = load_universe(Settings(_env_file=None))
    feeds = {feed["provider_name"]: feed for feed in universe["rss_feeds"]}
    expected = {
        "bank-of-japan-whats-new-rss",
        "bank-of-korea-monetary-policy-rss",
        "china-nbs-releases-rss",
        "us-eia-today-in-energy-rss",
        "apple-newsroom-rss",
        "nvidia-press-releases-rss",
    }

    assert expected <= feeds.keys()
    for provider_name in expected:
        feed = feeds[provider_name]
        assert feed["enabled"] is True
        assert 1 <= feed["lookback_hours"] <= 168
        assert 1 <= feed["max_items"] <= 100
        assert 1 <= feed["max_response_bytes"] <= 10_000_000
        assert 1 <= feed["max_content_chars"] <= 20_000
        assert feed["language"] == "en"
        RSSFeedConfig.model_validate(feed)

    entity_ids = {entity["id"] for entity in universe["entities"]}
    assert {
        "bank-of-japan",
        "bank-of-korea",
        "china-nbs",
        "us-eia",
        "apple",
        "nvidia",
    } <= entity_ids

    assets = {asset["symbol"]: asset for asset in universe["assets"]}
    assert {"000001.SS", "399001.SZ", "399006.SZ", "000688.SS", "000905.SS"} <= assets.keys()
    assert assets["DGS2"]["provider"] == "fred"
    assert "federal-reserve-all-releases-rss" in feeds


def test_fred_yield_provider_builds_real_two_year_history(monkeypatch) -> None:
    raw = """observation_date,DGS2
2026-09-14,4.01
2026-09-15,.
2026-09-16,4.08
2026-09-17,4.12
"""
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "DGS2"
        return httpx.Response(200, text=raw, request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = FredYieldProvider(
        [
            {
                "symbol": "DGS2",
                "name": "US 2Y Treasury Yield",
                "region": "US",
                "provider": "fred",
            }
        ]
    )

    batch = provider.collect(datetime(2026, 9, 18, tzinfo=UTC))

    assert batch.status is ProviderStatus.OK
    assert batch.diagnostics == {
        "market_items_expected": 1,
        "market_items_collected": 1,
        "market_items_failed": 0,
    }
    market = batch.markets[0]
    assert market.symbol == "DGS2"
    assert market.close == 4.12
    assert market.previous_close == 4.08
    assert market.change_pct == 0.98
    assert [point.close for point in market.history] == [4.12, 4.08, 4.01]


def test_yahoo_complete_collection_is_ok_despite_experimental_warning(monkeypatch) -> None:
    observed_at = datetime(2026, 9, 8, tzinfo=UTC)
    asset = {"symbol": "^GSPC", "name": "S&P 500", "region": "US"}
    observation = MarketObservation(
        id=stable_id("market", "test"),
        symbol="^GSPC",
        name="S&P 500",
        region="US",
        observed_at=observed_at,
        close=100,
        previous_close=99,
        change_pct=1.01,
        provider="experimental-yahoo-chart",
    )
    provider = YahooChartProvider([asset])
    monkeypatch.setattr(provider, "_fetch", lambda client, item, now: observation)

    batch = provider.collect(observed_at)

    assert batch.status is ProviderStatus.OK
    assert batch.collected_at == observed_at
    assert batch.warnings == [
        "Market prices use an experimental zero-key feed with no project-controlled SLA."
    ]
    assert batch.diagnostics["market_items_collected"] == 1


def test_yahoo_retains_both_daily_bar_timestamps_without_an_extra_request() -> None:
    previous_ts = int(datetime(2026, 9, 10, 13, 30, tzinfo=UTC).timestamp())
    latest_ts = int(datetime(2026, 9, 11, 13, 30, tzinfo=UTC).timestamp())
    request = httpx.Request("GET", "https://query1.finance.yahoo.com")
    response = httpx.Response(
        200,
        request=request,
        json={
            "chart": {
                "result": [
                    {
                        "timestamp": [previous_ts, latest_ts],
                        "indicators": {"quote": [{"close": [100.0, 101.0]}]},
                        "meta": {"currency": "USD"},
                    }
                ]
            }
        },
    )
    client = Mock()
    client.get.return_value = response
    provider = YahooChartProvider([])

    observation = provider._fetch(
        client,
        {"symbol": "^GSPC", "name": "S&P 500", "region": "US"},
        datetime(2026, 9, 11, 22, tzinfo=UTC),
    )

    assert observation.previous_observed_at == datetime(2026, 9, 10, 13, 30, tzinfo=UTC)
    assert observation.observed_at == datetime(2026, 9, 11, 13, 30, tzinfo=UTC)
    client.get.assert_called_once()


def test_live_assets_define_c8_daily_interval_semantics() -> None:
    universe = load_universe(Settings(_env_file=None))
    assets = {item["symbol"]: item for item in universe["assets"]}

    assert all(item.get("reaction_close_time", item.get("close_time")) for item in assets.values())
    assert assets["^VIX"]["reaction_profile"] == "VOLATILITY_INDEX"
    assert assets["BTC-USD"]["reaction_close_time"] == "23:59"


def test_bls_successful_response_without_rows_is_no_data(monkeypatch) -> None:
    request = httpx.Request("POST", BLSMacroProvider.endpoint)
    response = httpx.Response(
        200,
        json={
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {"seriesID": series_id, "data": []} for series_id in BLSMacroProvider.series
                ]
            },
        },
        request=request,
    )
    monkeypatch.setattr("radar.providers.httpx.post", lambda *args, **kwargs: response)

    collected_at = datetime(2026, 9, 8, tzinfo=UTC)
    batch = BLSMacroProvider().collect(collected_at)

    assert batch.status is ProviderStatus.NO_DATA
    assert batch.collected_at == collected_at
    assert batch.macro == []
    assert batch.diagnostics["macro_series_expected"] == 2
    assert batch.diagnostics["macro_series_collected"] == 0


def test_bls_preserves_unknown_release_time_without_source_document(monkeypatch) -> None:
    request = httpx.Request("POST", BLSMacroProvider.endpoint)
    response = httpx.Response(
        200,
        json={
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [
                            {"year": "2026", "period": "M08", "value": "324.5"},
                            {"year": "2026", "period": "M07", "value": "324.0"},
                        ],
                    },
                    {
                        "seriesID": "LNS14000000",
                        "data": [
                            {"year": "2026", "period": "M08", "value": "4.2"},
                            {"year": "2026", "period": "M07", "value": "4.1"},
                        ],
                    },
                ]
            },
        },
        request=request,
    )
    monkeypatch.setattr("radar.providers.httpx.post", lambda *args, **kwargs: response)

    first = BLSMacroProvider().collect(datetime(2026, 9, 8, tzinfo=UTC))
    second = BLSMacroProvider().collect(datetime(2026, 9, 9, tzinfo=UTC))

    assert first.documents == second.documents == []
    assert all(item.source_document_id is None for item in first.macro)
    assert all(item.release_at is None for item in first.macro)
    assert all(item.observed_at == first.collected_at for item in first.macro)
    assert all(item.model_dump()["release_at"] is None for item in first.macro)
    assert [item.source_document_id for item in second.macro] == [
        item.source_document_id for item in first.macro
    ]
