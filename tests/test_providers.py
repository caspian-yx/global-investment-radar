from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from radar.config import Settings
from radar.domain import (
    CollectionBatch,
    MarketObservation,
    ProviderStatus,
    SourceCategory,
    SourceRole,
    stable_id,
)
from radar.providers import (
    BLSMacroProvider,
    DemoProvider,
    OfficialRSSProvider,
    YahooChartProvider,
    build_providers,
)

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
        "https://www.federalreserve.gov/newsevents/pressreleases/"
        "enforcement20260904a.htm"
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

    assert [document.title for document in result.documents] == ["Current item"]
    assert result.diagnostics == {
        "total_entries": 5,
        "parsed_entries": 3,
        "filtered_by_date": 1,
        "invalid_date": 1,
        "invalid_link": 1,
        "duplicate_items": 1,
    }


def test_naive_iso_date_uses_utc_and_future_item_is_rejected() -> None:
    raw = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Naive current item</title><link href="https://example.test/current" />
        <updated>2026-09-07T20:00:00</updated>
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
    assert result.diagnostics["invalid_date"] == 1


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


def test_bls_successful_response_without_rows_is_no_data(monkeypatch) -> None:
    request = httpx.Request("POST", BLSMacroProvider.endpoint)
    response = httpx.Response(
        200,
        json={
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {"seriesID": series_id, "data": []}
                    for series_id in BLSMacroProvider.series
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
