from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from radar.config import Settings
from radar.domain import (
    CollectionBatch,
    MacroObservation,
    MarketObservation,
    ProviderStatus,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    stable_id,
)
from radar.ingestion import IntelligenceProvider, build_source_document
from radar.official_sources import SECFilingsProvider

RSS_DIAGNOSTIC_KEYS = (
    "total_entries",
    "parsed_entries",
    "filtered_by_date",
    "invalid_date",
    "invalid_link",
    "duplicate_items",
)


@dataclass
class RSSParseResult:
    documents: list[SourceDocument] = field(default_factory=list)
    diagnostics: dict[str, int] = field(
        default_factory=lambda: {key: 0 for key in RSS_DIAGNOSTIC_KEYS}
    )


def make_document(
    *,
    provider: str,
    tier: SourceTier,
    publisher: str,
    url: str,
    title: str,
    content: str,
    published_at: datetime,
    collected_at: datetime | None = None,
    trusted_provider_native_id: str | None = None,
    source_category: SourceCategory = SourceCategory.OTHER,
    source_role: SourceRole = SourceRole.SECONDARY,
    language: str = "en",
    region: str | None = None,
    provider_metadata: dict[str, JsonValue] | None = None,
    is_demo: bool = False,
) -> SourceDocument:
    return build_source_document(
        provider=provider,
        trusted_provider_native_id=trusted_provider_native_id,
        source_category=source_category,
        source_role=source_role,
        tier=tier,
        publisher=publisher,
        url=url,
        title=title,
        content=content,
        language=language,
        region=region,
        published_at=published_at,
        collected_at=collected_at or datetime.now(UTC),
        provider_metadata=provider_metadata,
        is_demo=is_demo,
    )


class DemoProvider:
    """Deterministic fixture provider. Every record is explicitly marked as demo data."""

    name = "demo-fixtures"

    def collect(self, now: datetime) -> CollectionBatch:
        observed = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        market_specs = [
            ("^GSPC", "S&P 500", "US", 6500.0, 6467.7, "USD"),
            ("^NDX", "Nasdaq 100", "US", 24010.0, 23724.0, "USD"),
            ("^VIX", "VIX", "US", 18.4, 17.1, "USD"),
            ("^HSI", "Hang Seng", "HK", 24980.0, 25105.0, "HKD"),
            ("000300.SS", "CSI 300", "CN", 4720.0, 4682.5, "CNY"),
            ("^N225", "Nikkei 225", "JP", 45210.0, 44850.0, "JPY"),
            ("GC=F", "Gold Futures", "Commodity", 3625.0, 3590.0, "USD"),
            ("CL=F", "WTI Crude Futures", "Commodity", 66.2, 67.1, "USD"),
        ]
        markets = [
            MarketObservation(
                id=stable_id("market", f"demo|{symbol}|{observed.isoformat()}"),
                symbol=symbol,
                name=name,
                region=region,
                observed_at=observed,
                close=close,
                previous_close=previous,
                change_pct=round((close / previous - 1) * 100, 2),
                currency=currency,
                provider=self.name,
                is_demo=True,
            )
            for symbol, name, region, close, previous, currency in market_specs
        ]
        macro = [
            self._macro(observed, "CPI-YOY", "U.S. CPI YoY", "2026-08", 2.6, 2.7, "%"),
            self._macro(observed, "UNRATE", "U.S. Unemployment Rate", "2026-08", 4.2, 4.1, "%"),
            self._macro(
                observed, "US10Y", "U.S. 10Y Treasury Yield", "2026-09-04", 4.08, 4.12, "%"
            ),
        ]
        documents = [
            make_document(
                provider=self.name,
                tier=SourceTier.A,
                publisher="Demo Central Bank",
                url="https://example.invalid/demo/policy-1",
                title=(
                    "Central bank keeps policy rate unchanged and retains data-dependent guidance"
                ),
                content="Test fixture: the policy committee kept its target rate unchanged.",
                published_at=observed - timedelta(hours=5),
                collected_at=now,
                source_category=SourceCategory.OFFICIAL,
                source_role=SourceRole.PRIMARY,
                is_demo=True,
            ),
            make_document(
                provider=self.name,
                tier=SourceTier.B,
                publisher="Demo Newswire",
                url="https://example.invalid/demo/policy-2",
                title="Central bank keeps policy rate unchanged, retains data dependent guidance",
                content="Test fixture: a second source reported the same policy decision.",
                published_at=observed - timedelta(hours=4, minutes=50),
                collected_at=now,
                source_category=SourceCategory.PROFESSIONAL_NEWS,
                source_role=SourceRole.SECONDARY,
                is_demo=True,
            ),
            make_document(
                provider=self.name,
                tier=SourceTier.A,
                publisher="Sample Semiconductor Co IR",
                url="https://example.invalid/demo/guidance",
                title="Sample Semiconductor Co raises revenue guidance on data center demand",
                content=(
                    "Test fixture: management raised its quarterly revenue range and cited "
                    "accelerator demand."
                ),
                published_at=observed - timedelta(hours=8),
                collected_at=now,
                source_category=SourceCategory.COMPANY_PRIMARY,
                source_role=SourceRole.PRIMARY,
                is_demo=True,
            ),
            make_document(
                provider=self.name,
                tier=SourceTier.A,
                publisher="Demo Statistics Office",
                url="https://example.invalid/demo/cpi",
                title="Consumer inflation cools while services inflation remains elevated",
                content=(
                    "Test fixture: headline inflation slowed, but services prices remained firm."
                ),
                published_at=observed - timedelta(hours=12),
                collected_at=now,
                source_category=SourceCategory.OFFICIAL,
                source_role=SourceRole.PRIMARY,
                is_demo=True,
            ),
            make_document(
                provider=self.name,
                tier=SourceTier.B,
                publisher="Demo Energy Agency",
                url="https://example.invalid/demo/oil",
                title="Oil supply forecast rises as demand estimate is revised lower",
                content="Test fixture: the balance points to looser near-term oil fundamentals.",
                published_at=observed - timedelta(hours=16),
                collected_at=now,
                source_category=SourceCategory.OTHER,
                source_role=SourceRole.PRIMARY,
                is_demo=True,
            ),
        ]
        return CollectionBatch(
            provider=self.name,
            documents=documents,
            markets=markets,
            macro=macro,
            warnings=[
                "Demo fixture data is synthetic and must not be treated as investment research."
            ],
            diagnostics={
                "market_items_expected": len(market_specs),
                "market_items_collected": len(markets),
            },
            collected_at=now,
            is_demo=True,
        )

    def _macro(
        self,
        observed: datetime,
        series_id: str,
        name: str,
        period: str,
        value: float,
        previous: float,
        unit: str,
    ) -> MacroObservation:
        return MacroObservation(
            id=stable_id("macro", f"demo|{series_id}|{period}"),
            series_id=series_id,
            name=name,
            period=period,
            observed_at=observed,
            value=value,
            previous_value=previous,
            change=round(value - previous, 2),
            change_label="vs previous observation",
            unit=unit,
            provider=self.name,
            is_demo=True,
        )


class YahooChartProvider:
    """Zero-key experimental market adapter; not an exchange-authoritative feed."""

    name = "experimental-yahoo-chart"

    def __init__(self, assets: list[dict[str, str]], timeout: float = 12.0) -> None:
        self.assets = assets
        self.timeout = timeout

    def collect(self, now: datetime) -> CollectionBatch:
        observations: list[MarketObservation] = []
        warnings = [
            "Market prices use an experimental zero-key feed with no project-controlled SLA."
        ]
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            for asset in self.assets:
                try:
                    observation = self._fetch(client, asset, now)
                    observations.append(observation)
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                    warnings.append(f"{asset['symbol']}: {type(exc).__name__}: {exc}")
        if not observations and self.assets:
            status = ProviderStatus.FAILED
        elif len(observations) < len(self.assets):
            status = ProviderStatus.DEGRADED
        else:
            status = ProviderStatus.OK
        return CollectionBatch(
            provider=self.name,
            status=status,
            markets=observations,
            warnings=warnings,
            diagnostics={
                "market_items_expected": len(self.assets),
                "market_items_collected": len(observations),
                "market_items_failed": len(self.assets) - len(observations),
            },
            collected_at=now,
        )

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=4),
        reraise=True,
    )
    def _fetch(
        self, client: httpx.Client, asset: dict[str, str], now: datetime
    ) -> MarketObservation:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{asset['symbol']}"
        response = client.get(
            url,
            params={"range": "10d", "interval": "1d", "events": "div,splits"},
            headers={"User-Agent": "GlobalInvestmentRadar/0.1"},
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        valid = [(ts, close) for ts, close in zip(timestamps, closes, strict=True) if close]
        if len(valid) < 2:
            raise ValueError("fewer than two valid closes")
        (previous_ts, previous), (latest_ts, latest) = valid[-2:]
        _ = previous_ts
        observed_at = datetime.fromtimestamp(latest_ts, tz=UTC)
        currency = result.get("meta", {}).get("currency")
        return MarketObservation(
            id=stable_id("market", f"{self.name}|{asset['symbol']}|{latest_ts}"),
            symbol=asset["symbol"],
            name=asset["name"],
            region=asset["region"],
            observed_at=observed_at,
            close=float(latest),
            previous_close=float(previous),
            change_pct=round((float(latest) / float(previous) - 1) * 100, 2),
            currency=currency,
            provider=self.name,
        )


class BLSMacroProvider:
    """Official BLS public API adapter that does not require a key at MVP volume."""

    name = "bls-public-api"
    endpoint = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    series = {
        "CUUR0000SA0": ("U.S. CPI All Urban Consumers", "index"),
        "LNS14000000": ("U.S. Unemployment Rate", "%"),
    }

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def collect(self, now: datetime) -> CollectionBatch:
        start_year = now.year - 2
        payload = {
            "seriesid": list(self.series),
            "startyear": str(start_year),
            "endyear": str(now.year),
        }
        response = httpx.post(self.endpoint, json=payload, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "REQUEST_SUCCEEDED":
            raise ValueError(f"BLS request failed: {body.get('message')}")
        observations: list[MacroObservation] = []
        for result in body["Results"]["series"]:
            series_id = result["seriesID"]
            rows = [row for row in result["data"] if row["period"].startswith("M")]
            if not rows:
                continue
            latest = rows[0]
            previous = rows[1] if len(rows) > 1 else None
            name, unit = self.series[series_id]
            period = f"{latest['year']}-{latest['period'][1:]}"
            value = float(latest["value"])
            previous_value = float(previous["value"]) if previous else None
            change = round(value - previous_value, 3) if previous_value is not None else None
            observation = MacroObservation(
                id=stable_id("macro", f"BLS|{series_id}|{period}"),
                series_id=series_id,
                name=name,
                period=period,
                observed_at=now,
                value=value,
                previous_value=previous_value,
                change=change,
                change_label="vs previous month" if change is not None else None,
                unit=unit,
                provider=self.name,
            )
            observations.append(observation)
        expected = len(self.series)
        if not observations:
            status = ProviderStatus.NO_DATA
        elif len(observations) < expected:
            status = ProviderStatus.DEGRADED
        else:
            status = ProviderStatus.OK
        warnings = []
        if len(observations) < expected:
            warnings.append(f"Collected {len(observations)} of {expected} configured BLS series.")
        return CollectionBatch(
            provider=self.name,
            status=status,
            macro=observations,
            warnings=warnings,
            diagnostics={
                "macro_series_expected": expected,
                "macro_series_collected": len(observations),
            },
            collected_at=now,
        )


class OfficialRSSProvider:
    name = "official-rss"

    def __init__(
        self,
        feeds: list[dict[str, Any]],
        lookback_hours: int,
        timeout: float = 15,
        *,
        name: str = "official-rss",
    ) -> None:
        self.feeds = feeds
        self.lookback = timedelta(hours=lookback_hours)
        self.timeout = timeout
        self.name = name

    def collect(self, now: datetime) -> CollectionBatch:
        documents: list[SourceDocument] = []
        warnings: list[str] = []
        diagnostics = {key: 0 for key in RSS_DIAGNOSTIC_KEYS}
        diagnostics.update({"feeds_succeeded": 0, "feeds_failed": 0})
        effective_lookback = self.lookback
        if now.weekday() == 0 and effective_lookback < timedelta(hours=96):
            effective_lookback = timedelta(hours=96)
        headers = {"User-Agent": "GlobalInvestmentRadar/0.1 (public-research-pipeline)"}
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            for feed in self.feeds:
                try:
                    response = client.get(feed["url"])
                    response.raise_for_status()
                    parsed = self._parse(response.text, feed, now, effective_lookback)
                    documents.extend(parsed.documents)
                    diagnostics["feeds_succeeded"] += 1
                    for key in RSS_DIAGNOSTIC_KEYS:
                        diagnostics[key] += parsed.diagnostics[key]
                except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
                    diagnostics["feeds_failed"] += 1
                    warnings.append(
                        f"{feed['publisher']}: feed collection failed "
                        f"({type(exc).__name__})."
                    )
        if self.feeds and diagnostics["feeds_failed"] == len(self.feeds):
            status = ProviderStatus.FAILED
        elif diagnostics["feeds_failed"]:
            status = ProviderStatus.DEGRADED
        elif not documents:
            status = ProviderStatus.NO_DATA
            warnings.append(
                "Official feeds parsed successfully; no items were published within the "
                "lookback window."
            )
        else:
            status = ProviderStatus.OK
        return CollectionBatch(
            provider=self.name,
            status=status,
            documents=documents,
            warnings=warnings,
            diagnostics=diagnostics,
            collected_at=now,
        )

    def _parse(
        self,
        raw_xml: str,
        feed: dict[str, Any],
        now: datetime,
        lookback: timedelta,
    ) -> RSSParseResult:
        root = ET.fromstring(raw_xml.lstrip("\ufeff"))
        result = RSSParseResult()
        seen_links: set[str] = set()
        entries = [
            element
            for element in root.iter()
            if self._local_name(element.tag) in {"item", "entry"}
        ]
        result.diagnostics["total_entries"] = len(entries)
        for item in entries:
            title = (self._child_text(item, "title") or "Untitled").strip()
            url = self._link(item)
            description = (
                self._child_text(item, "description", "summary", "content") or ""
            )
            clean = re.sub(r"<[^>]+>", " ", html.unescape(description))
            clean = re.sub(r"\s+", " ", clean).strip()
            try:
                published = self._published_at(item)
            except ValueError:
                result.diagnostics["invalid_date"] += 1
                continue
            if published > now.astimezone(UTC) + timedelta(minutes=5):
                result.diagnostics["invalid_date"] += 1
                continue
            if not self._valid_link(url):
                result.diagnostics["invalid_link"] += 1
                continue
            result.diagnostics["parsed_entries"] += 1
            if now.astimezone(UTC) - published > lookback:
                result.diagnostics["filtered_by_date"] += 1
                continue
            if url in seen_links:
                result.diagnostics["duplicate_items"] += 1
                continue
            seen_links.add(url)
            native_id = (
                self._child_text(item, "guid", "id")
                if feed.get("native_ids_immutable") is True
                else None
            )
            feed_url = feed.get("url")
            result.documents.append(
                make_document(
                    provider=self.name,
                    tier=SourceTier(feed["source_tier"]),
                    publisher=feed["publisher"],
                    url=url,
                    title=title,
                    content=clean or title,
                    published_at=published,
                    collected_at=now,
                    trusted_provider_native_id=native_id,
                    source_category=SourceCategory(
                        feed.get("source_category", SourceCategory.OTHER.value)
                    ),
                    source_role=SourceRole(
                        feed.get("source_role", SourceRole.SECONDARY.value)
                    ),
                    region=feed.get("region"),
                    provider_metadata=(
                        {"feed_url": feed_url} if isinstance(feed_url, str) else {}
                    ),
                )
            )
        return result

    def _published_at(self, item: ET.Element) -> datetime:
        raw = self._child_text(item, "pubDate", "date", "updated", "published")
        if not raw:
            raise ValueError("missing publication date")
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid publication date") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _child_text(self, item: ET.Element, *names: str) -> str | None:
        for child in list(item):
            if self._local_name(child.tag) in names:
                text = "".join(child.itertext()).strip()
                if text:
                    return text
        return None

    def _link(self, item: ET.Element) -> str:
        for child in list(item):
            if self._local_name(child.tag) != "link":
                continue
            return (child.text or child.attrib.get("href") or "").strip()
        return ""

    def _valid_link(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.netloc)

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]


def build_providers(
    settings: Settings, universe: dict[str, Any]
) -> list[IntelligenceProvider]:
    if settings.data_mode == "demo":
        return [DemoProvider()]
    providers: list[IntelligenceProvider] = [
        YahooChartProvider(universe["assets"]),
        BLSMacroProvider(),
    ]
    sec_config = universe.get("sec_filings")
    if isinstance(sec_config, dict) and sec_config.get("filers"):
        providers.append(
            SECFilingsProvider(
                filers=sec_config.get("filers", []),
                forms=sec_config.get("forms", []),
                lookback_hours=settings.lookback_hours,
                contact_email=settings.sec_contact_email,
            )
        )
    unnamed_feeds: list[dict[str, Any]] = []
    for feed in universe["rss_feeds"]:
        provider_name = feed.get("provider_name")
        if isinstance(provider_name, str) and provider_name.strip():
            providers.append(
                OfficialRSSProvider(
                    [feed],
                    settings.lookback_hours,
                    name=provider_name.strip(),
                )
            )
        else:
            unnamed_feeds.append(feed)
    if unnamed_feeds:
        providers.append(OfficialRSSProvider(unnamed_feeds, settings.lookback_hours))
    return providers
