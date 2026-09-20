from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from radar.config import Settings
from radar.domain import ProviderRunStatus, ProviderStatus, SourceRole


class _RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RegistryProvider(Protocol):
    name: str


class ProviderRole(StrEnum):
    PRIMARY_EVIDENCE = "PRIMARY_EVIDENCE"
    NEWS_DISCOVERY = "NEWS_DISCOVERY"
    MARKET_DISCOVERY = "MARKET_DISCOVERY"
    CALENDAR = "CALENDAR"
    CHINA_MARKET_STRUCTURE = "CHINA_MARKET_STRUCTURE"
    COMPANY_FILINGS = "COMPANY_FILINGS"
    SECONDARY_DISCOVERY = "SECONDARY_DISCOVERY"


class ProviderCapability(StrEnum):
    DOCUMENTS = "DOCUMENTS"
    NEWS_DISCOVERY = "NEWS_DISCOVERY"
    PRIMARY_EVIDENCE = "PRIMARY_EVIDENCE"
    MARKET_DATA = "MARKET_DATA"
    MACRO_DATA = "MACRO_DATA"
    CALENDAR = "CALENDAR"
    CHINA_MARKET_STRUCTURE = "CHINA_MARKET_STRUCTURE"
    FILINGS = "FILINGS"


class SourceRegistryEntry(_RegistryModel):
    provider_id: str
    provider_name: str
    provider_type: str
    role: ProviderRole
    regions: tuple[str, ...] = ()
    asset_classes: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    capabilities: tuple[ProviderCapability, ...]
    authority_level: str
    requires_credentials: bool = False
    requires_contact_identity: bool = False
    access_notes: str


class SourceCoverage(_RegistryModel):
    provider_id: str
    role: ProviderRole
    capabilities: tuple[ProviderCapability, ...]
    status: ProviderStatus | None = None
    item_count: int = 0
    diagnostics: dict[str, int] = {}
    warnings: tuple[str, ...] = ()


def _sorted_values(values: Iterable[Any], key: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value[key]).strip()
                for value in values
                if isinstance(value, dict) and value.get(key)
            }
        )
    )


def _registry_entry(provider: RegistryProvider) -> SourceRegistryEntry:
    provider_id = str(provider.name)
    provider_type = type(provider).__name__
    if provider_type == "YahooChartProvider":
        assets = getattr(provider, "assets", ())
        return SourceRegistryEntry(
            provider_id=provider_id,
            provider_name="Yahoo Finance chart data",
            provider_type=provider_type,
            role=ProviderRole.MARKET_DISCOVERY,
            regions=_sorted_values(assets, "region"),
            asset_classes=_sorted_values(assets, "asset_class"),
            domains=("global-markets",),
            capabilities=(ProviderCapability.MARKET_DATA,),
            authority_level="experimental-secondary",
            access_notes="Public zero-key chart endpoint; no project-controlled SLA.",
        )
    if provider_type == "FredYieldProvider":
        assets = getattr(provider, "assets", ())
        return SourceRegistryEntry(
            provider_id=provider_id,
            provider_name="Federal Reserve Economic Data",
            provider_type=provider_type,
            role=ProviderRole.MARKET_DISCOVERY,
            regions=("US",),
            asset_classes=_sorted_values(assets, "asset_class"),
            domains=("rates",),
            capabilities=(ProviderCapability.MARKET_DATA,),
            authority_level="official-data",
            access_notes="Public FRED CSV endpoint.",
        )
    if provider_type == "BLSMacroProvider":
        return SourceRegistryEntry(
            provider_id=provider_id,
            provider_name="U.S. Bureau of Labor Statistics",
            provider_type=provider_type,
            role=ProviderRole.PRIMARY_EVIDENCE,
            regions=("US",),
            domains=("inflation", "labor"),
            capabilities=(ProviderCapability.MACRO_DATA, ProviderCapability.PRIMARY_EVIDENCE),
            authority_level="official",
            access_notes="Official public API at bounded unauthenticated volume.",
        )
    if provider_type == "SECFilingsProvider":
        return SourceRegistryEntry(
            provider_id=provider_id,
            provider_name="SEC EDGAR Submissions",
            provider_type=provider_type,
            role=ProviderRole.COMPANY_FILINGS,
            regions=("US",),
            domains=("company-filings",),
            capabilities=(ProviderCapability.DOCUMENTS, ProviderCapability.FILINGS),
            authority_level="official",
            requires_contact_identity=True,
            access_notes="Official public endpoint; requires compliant contact identity.",
        )
    if provider_type == "OfficialRSSProvider":
        feeds = tuple(getattr(provider, "feeds", ()))
        primary = bool(feeds) and all(feed.source_role is SourceRole.PRIMARY for feed in feeds)
        role = ProviderRole.PRIMARY_EVIDENCE if primary else ProviderRole.NEWS_DISCOVERY
        capabilities = (
            (ProviderCapability.DOCUMENTS, ProviderCapability.PRIMARY_EVIDENCE)
            if primary
            else (ProviderCapability.DOCUMENTS, ProviderCapability.NEWS_DISCOVERY)
        )
        tiers = sorted({feed.source_tier.value for feed in feeds})
        return SourceRegistryEntry(
            provider_id=provider_id,
            provider_name=feeds[0].publisher if len(feeds) == 1 else provider_id,
            provider_type=provider_type,
            role=role,
            regions=tuple(sorted({feed.region for feed in feeds if feed.region})),
            domains=tuple(sorted({feed.source_category.value for feed in feeds})),
            capabilities=capabilities,
            authority_level="/".join(tiers) if tiers else "unrated",
            access_notes="Configured public RSS/Atom feed with bounded payload and lookback.",
        )
    return SourceRegistryEntry(
        provider_id=provider_id,
        provider_name=provider_id,
        provider_type=provider_type,
        role=ProviderRole.SECONDARY_DISCOVERY,
        capabilities=(ProviderCapability.DOCUMENTS,),
        authority_level="test-or-unknown",
        access_notes="Provider metadata is limited.",
    )


def build_source_registry(
    settings: Settings,
    universe: dict[str, Any],
    providers: Sequence[RegistryProvider],
) -> tuple[SourceRegistryEntry, ...]:
    _ = settings, universe
    entries = tuple(_registry_entry(provider) for provider in providers)
    ids = [entry.provider_id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("provider ids must be unique")
    return entries


def attach_runtime_coverage(
    registry: Sequence[SourceRegistryEntry],
    statuses: Sequence[ProviderRunStatus],
) -> tuple[SourceCoverage, ...]:
    status_by_provider = {item.provider: item for item in statuses}
    return tuple(
        SourceCoverage(
            provider_id=entry.provider_id,
            role=entry.role,
            capabilities=entry.capabilities,
            status=status.status if (status := status_by_provider.get(entry.provider_id)) else None,
            item_count=status.item_count if status else 0,
            diagnostics=dict(status.diagnostics) if status else {},
            warnings=tuple(status.warnings) if status else (),
        )
        for entry in registry
    )
