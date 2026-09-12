from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import JsonValue

from radar.domain import (
    CollectionBatch,
    ProviderStatus,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    content_hash,
    stable_id,
)

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}

SOURCE_ATTRIBUTION_POLICY: dict[
    SourceCategory, set[tuple[SourceRole, SourceTier]]
] = {
    SourceCategory.OFFICIAL: {(SourceRole.PRIMARY, SourceTier.A)},
    SourceCategory.REGULATORY: {(SourceRole.PRIMARY, SourceTier.A)},
    SourceCategory.COMPANY_PRIMARY: {(SourceRole.PRIMARY, SourceTier.A)},
    SourceCategory.PROFESSIONAL_NEWS: {
        (SourceRole.SECONDARY, SourceTier.B),
        (SourceRole.SECONDARY, SourceTier.C),
    },
    SourceCategory.AGGREGATOR: {
        (SourceRole.SECONDARY, SourceTier.C),
        (SourceRole.SECONDARY, SourceTier.D),
    },
    SourceCategory.COMMUNITY: {(SourceRole.SECONDARY, SourceTier.D)},
    SourceCategory.OTHER: {
        (SourceRole.PRIMARY, SourceTier.B),
        (SourceRole.PRIMARY, SourceTier.C),
        (SourceRole.PRIMARY, SourceTier.D),
        (SourceRole.SECONDARY, SourceTier.B),
        (SourceRole.SECONDARY, SourceTier.C),
        (SourceRole.SECONDARY, SourceTier.D),
    },
}

SOURCE_CATEGORY_PRIORITY = {
    SourceCategory.OFFICIAL: 0,
    SourceCategory.REGULATORY: 1,
    SourceCategory.COMPANY_PRIMARY: 2,
    SourceCategory.PROFESSIONAL_NEWS: 3,
    SourceCategory.OTHER: 4,
    SourceCategory.AGGREGATOR: 5,
    SourceCategory.COMMUNITY: 6,
}
SOURCE_ROLE_PRIORITY = {SourceRole.PRIMARY: 0, SourceRole.SECONDARY: 1}
SOURCE_TIER_PRIORITY = {
    SourceTier.A: 0,
    SourceTier.B: 1,
    SourceTier.C: 2,
    SourceTier.D: 3,
}


@dataclass(frozen=True)
class DocumentDedupeResult:
    documents: list[SourceDocument]
    diagnostics: dict[str, int]


class IntelligenceProvider(Protocol):
    """Single collection contract for external intelligence/data adapters."""

    name: str

    def collect(self, collected_at: datetime) -> CollectionBatch: ...


def validate_source_attribution(
    source_category: SourceCategory,
    source_role: SourceRole,
    source_tier: SourceTier,
) -> None:
    """Reject source-quality claims that are inconsistent with the source class."""
    if (source_role, source_tier) not in SOURCE_ATTRIBUTION_POLICY[source_category]:
        raise ValueError(
            "Invalid source attribution: "
            f"category={source_category.value}, role={source_role.value}, "
            f"tier={source_tier.value}"
        )


def canonicalize_source_url(url: str) -> str:
    """Conservatively remove fragments and known tracking parameters."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_PARAMS
        and not key.casefold().startswith("utm_")
    ]
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path, urlencode(query), "")
    )


def _normalized_identity_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def source_document_id(
    *,
    provider: str,
    trusted_provider_native_id: str | None,
    canonical_url: str | None,
    publisher: str,
    title: str,
    published_at: datetime,
) -> str:
    """Build identity using trusted native ID, URL, then stable-field fallback."""
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("published_at must be timezone-aware")
    provider_key = _normalized_identity_text(provider)
    if trusted_provider_native_id is not None and trusted_provider_native_id.strip():
        identity = f"{provider_key}|native|{trusted_provider_native_id.strip()}"
    elif canonical_url is not None and canonical_url.strip():
        identity = f"{provider_key}|url|{canonicalize_source_url(canonical_url)}"
    else:
        identity = "|".join(
            (
                provider_key,
                "fields",
                _normalized_identity_text(publisher),
                _normalized_identity_text(title),
                published_at.astimezone(UTC).isoformat(),
            )
        )
    return stable_id("source", identity)


def build_source_document(
    *,
    provider: str,
    trusted_provider_native_id: str | None,
    source_category: SourceCategory,
    source_role: SourceRole,
    tier: SourceTier,
    publisher: str,
    url: str,
    title: str,
    content: str,
    published_at: datetime,
    collected_at: datetime,
    language: str = "en",
    region: str | None = None,
    provider_metadata: dict[str, JsonValue] | None = None,
    is_demo: bool = False,
) -> SourceDocument:
    """Normalize one provider item into the canonical document contract."""
    validate_source_attribution(source_category, source_role, tier)
    canonical_url = canonicalize_source_url(url)
    normalized_content = f"{title.strip()}\n{content.strip()}"
    return SourceDocument(
        id=source_document_id(
            provider=provider,
            trusted_provider_native_id=trusted_provider_native_id,
            canonical_url=canonical_url,
            publisher=publisher,
            title=title,
            published_at=published_at,
        ),
        provider=provider,
        provider_native_id=(
            trusted_provider_native_id.strip()
            if trusted_provider_native_id is not None
            and trusted_provider_native_id.strip()
            else None
        ),
        source_category=source_category,
        source_role=source_role,
        source_tier=tier,
        publisher=publisher,
        original_url=url,
        canonical_url=canonical_url,
        title=title.strip(),
        content=content.strip(),
        language=language,
        region=region,
        published_at=published_at,
        collected_at=collected_at,
        provider_metadata=provider_metadata or {},
        content_hash=content_hash(normalized_content),
        is_demo=is_demo,
    )


def _source_preference(document: SourceDocument) -> tuple[int, int, int, str, str]:
    return (
        SOURCE_CATEGORY_PRIORITY[document.source_category],
        SOURCE_ROLE_PRIORITY[document.source_role],
        SOURCE_TIER_PRIORITY[document.source_tier],
        _normalized_identity_text(document.provider),
        document.id,
    )


def deduplicate_source_documents(
    documents: Iterable[SourceDocument],
) -> DocumentDedupeResult:
    """Conservatively deduplicate exact IDs and exact canonical URLs."""
    source_documents = list(documents)
    by_id: dict[str, SourceDocument] = {}
    duplicate_ids = 0
    for document in source_documents:
        existing = by_id.get(document.id)
        if existing is None:
            by_id[document.id] = document
            continue
        if existing != document:
            raise ValueError(f"conflicting SourceDocument id: {document.id}")
        duplicate_ids += 1

    by_url: dict[str, list[SourceDocument]] = {}
    for document in by_id.values():
        canonical_url = canonicalize_source_url(document.canonical_url)
        by_url.setdefault(canonical_url, []).append(document)

    selected: list[SourceDocument] = []
    duplicate_urls = 0
    for canonical_url in sorted(by_url):
        candidates = by_url[canonical_url]
        selected.append(min(candidates, key=_source_preference))
        duplicate_urls += len(candidates) - 1

    result = sorted(selected, key=lambda document: document.id)
    return DocumentDedupeResult(
        documents=result,
        diagnostics={
            "input_documents": len(source_documents),
            "output_documents": len(result),
            "duplicate_ids": duplicate_ids,
            "duplicate_urls": duplicate_urls,
        },
    )


def collect_intelligence_providers(
    providers: Iterable[IntelligenceProvider],
    collected_at: datetime,
) -> list[CollectionBatch]:
    """Collect each provider once while isolating external failures."""
    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    normalized_collected_at = collected_at.astimezone(UTC)
    batches: list[CollectionBatch] = []
    for provider in providers:
        try:
            batch = provider.collect(normalized_collected_at)
        except Exception as exc:
            error_type = type(exc).__name__
            logger.warning(
                "Provider collection failed provider=%s error_type=%s",
                provider.name,
                error_type,
            )
            batch = CollectionBatch(
                provider=provider.name,
                status=ProviderStatus.FAILED,
                warnings=[f"Provider collection failed ({error_type})."],
                error_type=error_type,
                collected_at=normalized_collected_at,
            )
        else:
            if batch.collected_at is None:
                batch = batch.model_copy(
                    update={"collected_at": normalized_collected_at}
                )
        batches.append(batch)
    return batches
