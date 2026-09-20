from __future__ import annotations

from radar.config import Settings, load_universe
from radar.domain import ProviderRunStatus, ProviderStatus
from radar.providers import build_providers
from radar.source_registry import (
    ProviderCapability,
    ProviderRole,
    attach_runtime_coverage,
    build_source_registry,
)


def test_registry_describes_every_runtime_provider_in_stable_order() -> None:
    settings = Settings(data_mode="live", sec_contact_email="ops@example.com")
    universe = load_universe(settings)
    providers = build_providers(settings, universe)

    registry = build_source_registry(settings, universe, providers)

    assert tuple(item.provider_id for item in registry) == tuple(
        provider.name for provider in providers
    )
    assert len({item.provider_id for item in registry}) == len(providers)
    by_id = {item.provider_id: item for item in registry}
    assert by_id["experimental-yahoo-chart"].role is ProviderRole.MARKET_DISCOVERY
    assert ProviderCapability.MARKET_DATA in by_id["experimental-yahoo-chart"].capabilities
    assert by_id["fred-treasury-yields"].role is ProviderRole.MARKET_DISCOVERY
    assert by_id["bls-public-api"].role is ProviderRole.PRIMARY_EVIDENCE
    assert ProviderCapability.MACRO_DATA in by_id["bls-public-api"].capabilities
    assert by_id["sec-edgar-submissions"].role is ProviderRole.COMPANY_FILINGS
    assert by_id["sec-edgar-submissions"].requires_credentials is False
    assert by_id["sec-edgar-submissions"].requires_contact_identity is True
    assert by_id["federal-reserve-all-releases-rss"].role is ProviderRole.PRIMARY_EVIDENCE
    assert by_id["nvidia-press-releases-rss"].role is ProviderRole.PRIMARY_EVIDENCE


def test_runtime_coverage_is_joined_by_provider_identity() -> None:
    settings = Settings(data_mode="live", sec_contact_email="ops@example.com")
    universe = load_universe(settings)
    registry = build_source_registry(settings, universe, build_providers(settings, universe))
    statuses = [
        ProviderRunStatus(
            provider="experimental-yahoo-chart",
            status=ProviderStatus.DEGRADED,
            item_count=21,
            diagnostics={"market_items_expected": 24, "market_items_collected": 21},
            warnings=["three symbols unavailable"],
        )
    ]

    coverage = attach_runtime_coverage(registry, statuses)

    yahoo = next(item for item in coverage if item.provider_id == "experimental-yahoo-chart")
    assert yahoo.status is ProviderStatus.DEGRADED
    assert yahoo.item_count == 21
    assert yahoo.diagnostics == {
        "market_items_expected": 24,
        "market_items_collected": 21,
    }
    fed = next(item for item in coverage if item.provider_id == "federal-reserve-all-releases-rss")
    assert fed.status is None
    assert fed.item_count == 0
