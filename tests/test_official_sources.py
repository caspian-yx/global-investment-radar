from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from radar.domain import ProviderStatus, SourceCategory, SourceRole, SourceTier
from radar.ingestion import collect_intelligence_providers
from radar.official_sources import SECFilingsProvider
from radar.providers import OfficialRSSProvider

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
APPLE = {"cik": "0000320193", "name": "Apple Inc."}
MICROSOFT = {"cik": "0000789019", "name": "Microsoft Corp."}
FORMS = [
    "8-K",
    "8-K/A",
    "10-Q",
    "10-Q/A",
    "10-K",
    "10-K/A",
    "6-K",
    "6-K/A",
    "20-F",
    "20-F/A",
]


def _submissions(
    *,
    cik: int = 320193,
    name: str = "Apple Inc.",
    rows: list[dict[str, Any]] | None = None,
    historical_files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    filings = rows if rows is not None else [
        {
            "accessionNumber": "0000320193-26-000101",
            "filingDate": "2026-09-11",
            "reportDate": "2026-09-10",
            "acceptanceDateTime": "2026-09-11T10:15:30.000Z",
            "act": "34",
            "form": "8-K",
            "fileNumber": "001-36743",
            "filmNumber": "261234567",
            "items": "2.02,9.01",
            "size": 12345,
            "isXBRL": 1,
            "isInlineXBRL": 1,
            "primaryDocument": "aapl-20260910.htm",
            "primaryDocDescription": "Current report",
        }
    ]
    keys = {
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "act",
        "form",
        "fileNumber",
        "filmNumber",
        "items",
        "size",
        "isXBRL",
        "isInlineXBRL",
        "primaryDocument",
        "primaryDocDescription",
    }
    recent = {key: [row.get(key, "") for row in filings] for key in keys}
    return {
        "cik": cik,
        "entityType": "operating",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "name": name,
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "filings": {
            "recent": recent,
            "files": historical_files or [],
        },
    }


def _mock_clients(monkeypatch: Any, handler: Any) -> None:
    original_client = httpx.Client
    monkeypatch.setattr(
        "radar.official_sources.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_sec_maps_recent_filing_to_source_document(monkeypatch) -> None:
    payload = _submissions(
        historical_files=[
            {"name": "CIK0000320193-submissions-001.json", "filingCount": 1000}
        ]
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    _mock_clients(monkeypatch, handler)
    provider = SECFilingsProvider(
        filers=[APPLE],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    )

    batch = provider.collect(NOW)

    assert batch.status is ProviderStatus.OK
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://data.sec.gov/submissions/CIK0000320193.json"
    )
    assert "GlobalInvestmentRadar" in requests[0].headers["User-Agent"]
    assert "radar-owner@example.com" in requests[0].headers["User-Agent"]
    assert len(batch.documents) == 1
    document = batch.documents[0]
    assert document.provider == "sec-edgar-submissions"
    assert document.provider_native_id == "0000320193-26-000101"
    assert document.canonical_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019326000101/0000320193-26-000101-index.html"
    )
    assert document.published_at == datetime(2026, 9, 11, 10, 15, 30, tzinfo=UTC)
    assert document.source_category is SourceCategory.REGULATORY
    assert document.source_role is SourceRole.PRIMARY
    assert document.source_tier is SourceTier.A
    assert document.publisher == "Apple Inc."
    assert document.region == "US"
    assert document.provider_metadata == {
        "cik": "0000320193",
        "form": "8-K",
        "report_date": "2026-09-10",
        "items": "2.02,9.01",
        "primary_document": "aapl-20260910.htm",
    }
    assert batch.diagnostics["ciks_expected"] == 1
    assert batch.diagnostics["ciks_succeeded"] == 1
    assert batch.diagnostics["qualified_filings"] == 1
    assert batch.diagnostics["historical_files_ignored"] == 1


def test_sec_accepts_amendments_filters_forms_and_ignores_old_or_bad_dates(
    monkeypatch,
) -> None:
    rows = [
        {
            "accessionNumber": "0000320193-26-000201",
            "form": "10-Q/A",
            "acceptanceDateTime": "2026-09-11T09:00:00Z",
            "reportDate": "2026-06-30",
            "items": "",
            "primaryDocument": "aapl-10qa.htm",
        },
        {
            "accessionNumber": "0000320193-26-000205",
            "form": "8-K",
            "acceptanceDateTime": "2026-09-11T08:30:00Z",
            "reportDate": "2026-09-10",
            "items": "8.01",
            "primaryDocument": "aapl-8k.htm",
        },
        {
            "accessionNumber": "0000320193-26-000202",
            "form": "4",
            "acceptanceDateTime": "2026-09-11T08:00:00Z",
            "reportDate": "2026-09-10",
            "items": "",
            "primaryDocument": "xslF345X05/form4.xml",
        },
        {
            "accessionNumber": "0000320193-26-000203",
            "form": "8-K",
            "acceptanceDateTime": "2026-09-01T08:00:00Z",
            "reportDate": "2026-09-01",
            "items": "8.01",
            "primaryDocument": "old.htm",
        },
        {
            "accessionNumber": "0000320193-26-000204",
            "form": "8-K",
            "acceptanceDateTime": "not-a-date",
            "reportDate": "2026-09-11",
            "items": "8.01",
            "primaryDocument": "bad.htm",
        },
        {
            "accessionNumber": "",
            "form": "8-K",
            "acceptanceDateTime": "2026-09-11T07:00:00Z",
            "reportDate": "2026-09-11",
            "items": "8.01",
            "primaryDocument": "missing-accession.htm",
        },
        {
            "accessionNumber": "0000320193-26-000206",
            "form": "8-K",
            "acceptanceDateTime": "",
            "reportDate": "2026-09-11",
            "items": "8.01",
            "primaryDocument": "missing-acceptance.htm",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_submissions(rows=rows), request=request)

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert [item.provider_metadata["form"] for item in batch.documents] == [
        "10-Q/A",
        "8-K",
    ]
    assert batch.diagnostics["excluded_forms"] == 1
    assert batch.diagnostics["outside_lookback"] == 1
    assert batch.diagnostics["malformed_filings"] == 3


def test_sec_forms_are_limited_by_configuration(monkeypatch) -> None:
    rows = [
        {
            "accessionNumber": "0000320193-26-000207",
            "form": "10-Q",
            "acceptanceDateTime": "2026-09-11T09:00:00Z",
            "reportDate": "2026-06-30",
            "items": "",
            "primaryDocument": "aapl-10q.htm",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_submissions(rows=rows), request=request)

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE],
        forms=["8-K"],
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert batch.status is ProviderStatus.NO_DATA
    assert batch.documents == []
    assert batch.diagnostics["excluded_forms"] == 1


def test_sec_identity_is_stable_when_mutable_payload_changes(monkeypatch) -> None:
    row = {
        "accessionNumber": "0000320193-26-000301",
        "form": "8-K",
        "acceptanceDateTime": "2026-09-11T08:00:00Z",
        "reportDate": "2026-09-10",
        "items": "2.02",
        "primaryDocument": "first.htm",
    }
    payloads = [
        _submissions(name="Apple Inc.", rows=[row]),
        _submissions(
            name="APPLE INC",
            rows=[{**row, "items": "2.02,9.01", "primaryDocument": "revised.htm"}],
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payloads.pop(0), request=request)

    _mock_clients(monkeypatch, handler)
    provider = SECFilingsProvider(
        filers=[APPLE],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    )

    first = provider.collect(NOW).documents[0]
    second = provider.collect(NOW).documents[0]

    assert first.id == second.id
    assert first.content_hash != second.content_hash


def test_sec_missing_or_invalid_contact_fails_without_request(monkeypatch) -> None:
    def unexpected_client(**kwargs: Any) -> Any:
        raise AssertionError("SEC request must not be attempted")

    monkeypatch.setattr("radar.official_sources.httpx.Client", unexpected_client)

    for contact in (None, "", "not-an-email"):
        batch = SECFilingsProvider(
            filers=[APPLE],
            forms=FORMS,
            lookback_hours=48,
            contact_email=contact,
        ).collect(NOW)
        assert batch.status is ProviderStatus.FAILED
        assert batch.error_type == "SECContactConfigurationError"
        assert batch.documents == []
        assert batch.diagnostics["requests_attempted"] == 0


def test_sec_empty_success_is_no_data(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_submissions(rows=[]), request=request)

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert batch.status is ProviderStatus.NO_DATA
    assert batch.documents == []
    assert batch.diagnostics["ciks_succeeded"] == 1


def test_sec_partial_failure_is_degraded_keeps_documents_and_redacts_error(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "0000320193" in str(request.url):
            return httpx.Response(200, json=_submissions(), request=request)
        raise httpx.ConnectError(
            "upstream included secret-contact@example.com", request=request
        )

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE, MICROSOFT],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert batch.status is ProviderStatus.DEGRADED
    assert len(batch.documents) == 1
    assert batch.diagnostics["ciks_succeeded"] == 1
    assert batch.diagnostics["ciks_failed"] == 1
    assert batch.diagnostics["requests_attempted"] == 2
    assert all("secret-contact" not in warning for warning in batch.warnings)
    assert all("radar-owner@example.com" not in warning for warning in batch.warnings)


def test_sec_multiple_ciks_succeed_with_one_request_each(monkeypatch) -> None:
    microsoft_row = {
        "accessionNumber": "0000789019-26-000501",
        "form": "10-K",
        "acceptanceDateTime": "2026-09-11T07:30:00Z",
        "reportDate": "2026-06-30",
        "items": "",
        "primaryDocument": "msft-10k.htm",
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "0000320193" in str(request.url):
            return httpx.Response(200, json=_submissions(), request=request)
        return httpx.Response(
            200,
            json=_submissions(
                cik=789019,
                name="Microsoft Corp.",
                rows=[microsoft_row],
            ),
            request=request,
        )

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE, MICROSOFT],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert batch.status is ProviderStatus.OK
    assert len(requests) == 2
    assert len(batch.documents) == 2
    assert [item.provider_metadata["cik"] for item in batch.documents] == [
        "0000320193",
        "0000789019",
    ]


def test_sec_complete_failure_is_failed(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE, MICROSOFT],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert batch.status is ProviderStatus.FAILED
    assert batch.documents == []
    assert batch.diagnostics["ciks_failed"] == 2
    assert batch.error_type == "SECCollectionError"


def test_sec_cik_mismatch_fails_without_using_accession_prefix(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_submissions(cik=789019, name="Microsoft Corp."),
            request=request,
        )

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert batch.status is ProviderStatus.FAILED
    assert batch.documents == []
    assert batch.diagnostics["ciks_failed"] == 1


def test_sec_duplicate_accession_is_emitted_once(monkeypatch) -> None:
    row = {
        "accessionNumber": "0000320193-26-000401",
        "form": "8-K",
        "acceptanceDateTime": "2026-09-11T08:00:00Z",
        "reportDate": "2026-09-10",
        "items": "8.01",
        "primaryDocument": "event.htm",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_submissions(rows=[row, row]),
            request=request,
        )

    _mock_clients(monkeypatch, handler)
    batch = SECFilingsProvider(
        filers=[APPLE],
        forms=FORMS,
        lookback_hours=48,
        contact_email="radar-owner@example.com",
    ).collect(NOW)

    assert len(batch.documents) == 1
    assert batch.diagnostics["duplicate_filings"] == 1


def test_fed_and_ecb_rss_have_distinct_attribution_and_url_identity(monkeypatch) -> None:
    fed = {
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "publisher": "Federal Reserve",
        "source_tier": "A",
        "source_category": "official",
        "source_role": "primary",
        "native_ids_immutable": False,
        "region": "US",
    }
    ecb = {
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "publisher": "European Central Bank",
        "source_tier": "A",
        "source_category": "official",
        "source_role": "primary",
        "native_ids_immutable": False,
        "region": "EU",
    }
    documents = {
        str(fed["url"]): """<rss><channel><item>
          <title>Federal Reserve policy decision</title>
          <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260910a.htm</link>
          <guid>changing-fed-guid</guid>
          <pubDate>Thu, 10 Sep 2026 18:00:00 GMT</pubDate>
          <description>Policy statement.</description>
        </item></channel></rss>""",
        str(ecb["url"]): """<rss><channel><item>
          <title>ECB monetary policy decisions</title>
          <link>https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260910.en.html</link>
          <guid>changing-ecb-guid</guid>
          <pubDate>Thu, 10 Sep 2026 13:15:00 GMT</pubDate>
          <description>Governing Council decision.</description>
        </item></channel></rss>""",
    }

    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=documents[str(request.url)], request=request)

    monkeypatch.setattr(
        "radar.providers.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    providers = [
        OfficialRSSProvider(
            [fed], lookback_hours=48, name="federal-reserve-monetary-rss"
        ),
        OfficialRSSProvider([ecb], lookback_hours=48, name="ecb-official-press-rss"),
    ]

    batches = collect_intelligence_providers(providers, NOW)

    assert [batch.provider for batch in batches] == [
        "federal-reserve-monetary-rss",
        "ecb-official-press-rss",
    ]
    assert [batch.documents[0].provider for batch in batches] == [
        "federal-reserve-monetary-rss",
        "ecb-official-press-rss",
    ]
    assert all(batch.documents[0].provider_native_id is None for batch in batches)
    assert all(batch.status is ProviderStatus.OK for batch in batches)


def test_mixed_sec_and_ecb_survive_fed_failure_on_single_orchestration_seam(
    monkeypatch,
) -> None:
    fed = {
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "publisher": "Federal Reserve",
        "source_tier": "A",
        "source_category": "official",
        "source_role": "primary",
        "native_ids_immutable": False,
        "region": "US",
    }
    ecb = {
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "publisher": "European Central Bank",
        "source_tier": "A",
        "source_category": "official",
        "source_role": "primary",
        "native_ids_immutable": False,
        "region": "EU",
    }
    rss = """<rss><channel><item>
      <title>Official policy update</title>
      <link>{link}</link>
      <pubDate>Thu, 10 Sep 2026 18:00:00 GMT</pubDate>
    </item></channel></rss>"""
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "data.sec.gov" in url:
            return httpx.Response(200, json=_submissions(), request=request)
        if "federalreserve" in url:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            text=rss.format(
                link=(
                    "https://www.ecb.europa.eu/press/pr/date/2026/html/"
                    "ecb.mp260910.en.html"
                )
            ),
            request=request,
        )

    monkeypatch.setattr(
        "radar.official_sources.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    providers = [
        SECFilingsProvider(
            filers=[APPLE],
            forms=FORMS,
            lookback_hours=48,
            contact_email="radar-owner@example.com",
        ),
        OfficialRSSProvider(
            [fed], lookback_hours=48, name="federal-reserve-monetary-rss"
        ),
        OfficialRSSProvider([ecb], lookback_hours=48, name="ecb-official-press-rss"),
    ]

    batches = collect_intelligence_providers(providers, NOW)

    assert [batch.provider for batch in batches] == [
        "sec-edgar-submissions",
        "federal-reserve-monetary-rss",
        "ecb-official-press-rss",
    ]
    assert [len(batch.documents) for batch in batches] == [1, 0, 1]
    assert [batch.status for batch in batches] == [
        ProviderStatus.OK,
        ProviderStatus.FAILED,
        ProviderStatus.OK,
    ]


def test_rss_url_identity_ignores_guid_changes() -> None:
    feed = {
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "publisher": "European Central Bank",
        "source_tier": "A",
        "source_category": "official",
        "source_role": "primary",
        "native_ids_immutable": False,
        "region": "EU",
    }
    provider = OfficialRSSProvider(
        [], lookback_hours=48, name="ecb-official-press-rss"
    )

    def item(guid: str, description: str) -> str:
        return f"""<rss><channel><item>
          <title>ECB monetary policy decisions</title>
          <link>https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260910.en.html</link>
          <guid>{guid}</guid>
          <pubDate>Thu, 10 Sep 2026 13:15:00 GMT</pubDate>
          <description>{description}</description>
        </item></channel></rss>"""

    first = provider._parse(item("guid-one", "Original"), feed, NOW, provider.lookback)
    second = provider._parse(item("guid-two", "Updated"), feed, NOW, provider.lookback)

    assert first.documents[0].id == second.documents[0].id
    assert first.documents[0].content_hash != second.documents[0].content_hash


def test_production_universe_uses_verified_ciks_and_official_feeds() -> None:
    universe = json.loads((PROJECT_ROOT / "config" / "universe.json").read_text("utf-8"))

    assert universe["sec_filings"]["filers"] == [
        {"cik": "0000320193", "name": "Apple Inc."},
        {"cik": "0000789019", "name": "Microsoft Corp."},
        {"cik": "0001045810", "name": "NVIDIA Corp."},
        {"cik": "0001652044", "name": "Alphabet Inc."},
        {"cik": "0001018724", "name": "Amazon.com Inc."},
        {"cik": "0001326801", "name": "Meta Platforms, Inc."},
        {"cik": "0001318605", "name": "Tesla, Inc."},
        {"cik": "0000019617", "name": "JPMorgan Chase & Co."},
    ]
    feeds = {feed["provider_name"]: feed for feed in universe["rss_feeds"]}
    assert feeds["federal-reserve-monetary-rss"]["url"] == (
        "https://www.federalreserve.gov/feeds/press_monetary.xml"
    )
    assert feeds["ecb-official-press-rss"]["url"] == (
        "https://www.ecb.europa.eu/rss/press.html"
    )
    assert feeds["federal-reserve-monetary-rss"]["native_ids_immutable"] is False
    assert feeds["ecb-official-press-rss"]["native_ids_immutable"] is False
    assert any(entity["id"] == "european-central-bank" for entity in universe["entities"])
