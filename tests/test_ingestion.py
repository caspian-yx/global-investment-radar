from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from radar.domain import (
    CollectionBatch,
    ProviderStatus,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
)
from radar.ingestion import (
    build_source_document,
    collect_intelligence_providers,
    source_document_id,
)

PUBLISHED_AT = datetime(2026, 9, 10, 8, tzinfo=UTC)
COLLECTED_AT = datetime(2026, 9, 10, 9, tzinfo=UTC)


def _document(
    *,
    url: str = "https://example.test/releases/item?id=7&utm_source=feed#top",
    content: str = "Initial body.",
    native_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> SourceDocument:
    return build_source_document(
        provider="official-feed",
        trusted_provider_native_id=native_id,
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
        tier=SourceTier.A,
        publisher="Example Authority",
        url=url,
        title="Policy decision",
        content=content,
        language="en",
        region="US",
        published_at=PUBLISHED_AT,
        collected_at=COLLECTED_AT,
        provider_metadata=metadata or {},
    )


def test_trusted_native_id_has_priority_over_url_and_content() -> None:
    first = _document(native_id="release-42")
    moved = _document(
        native_id="release-42",
        url="https://mirror.example.test/moved",
        content="Corrected body.",
    )

    assert first.id == moved.id
    assert first.provider_native_id == "release-42"
    assert first.content_hash != moved.content_hash


def test_canonical_url_identity_ignores_content_and_tracking_changes() -> None:
    first = _document()
    updated = _document(
        url="https://example.test/releases/item?id=7&utm_campaign=daily#different",
        content="Updated body.",
    )

    assert first.id == updated.id
    assert first.original_url != updated.original_url
    assert first.canonical_url == "https://example.test/releases/item?id=7"
    assert updated.canonical_url == "https://example.test/releases/item?id=7"
    assert first.content_hash != updated.content_hash


def test_stable_field_fallback_is_deterministic_without_native_id_or_url() -> None:
    first = source_document_id(
        provider="wire-service",
        trusted_provider_native_id=None,
        canonical_url=None,
        publisher="Example Wire",
        title="Markets close higher",
        published_at=PUBLISHED_AT,
    )
    repeated = source_document_id(
        provider="wire-service",
        trusted_provider_native_id=None,
        canonical_url=None,
        publisher="  Example Wire ",
        title="Markets   close higher",
        published_at=PUBLISHED_AT.astimezone(timezone(timedelta(hours=8))),
    )

    assert first == repeated


def test_source_document_requires_timezone_aware_timestamps_and_normalizes_utc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_source_document(
            provider="official-feed",
            trusted_provider_native_id=None,
            source_category=SourceCategory.OFFICIAL,
            source_role=SourceRole.PRIMARY,
            tier=SourceTier.A,
            publisher="Example Authority",
            url="https://example.test/item",
            title="Policy decision",
            content="Body.",
            published_at=datetime(2026, 9, 10, 8),
            collected_at=COLLECTED_AT,
        )

    shifted = build_source_document(
        provider="official-feed",
        trusted_provider_native_id=None,
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
        tier=SourceTier.A,
        publisher="Example Authority",
        url="https://example.test/item",
        title="Policy decision",
        content="Body.",
        published_at=datetime(2026, 9, 10, 16, tzinfo=timezone(timedelta(hours=8))),
        collected_at=datetime(2026, 9, 10, 17, tzinfo=timezone(timedelta(hours=8))),
    )

    assert shifted.published_at == PUBLISHED_AT
    assert shifted.published_at.tzinfo is UTC
    assert shifted.collected_at == COLLECTED_AT
    assert shifted.collected_at.tzinfo is UTC


@pytest.mark.parametrize(
    "field",
    ["provider", "publisher", "canonical_url", "title", "language"],
)
def test_canonical_document_rejects_blank_core_text(field: str) -> None:
    payload = _document().model_dump()
    payload[field] = "   "

    with pytest.raises(ValidationError):
        SourceDocument.model_validate(payload)


def test_canonical_document_requires_original_url() -> None:
    payload = _document().model_dump()
    payload["original_url"] = None

    with pytest.raises(ValidationError):
        SourceDocument.model_validate(payload)


def test_legacy_retrieved_at_is_input_and_read_compatible_but_not_serialized() -> None:
    payload = _document().model_dump(exclude={"collected_at"})
    payload["retrieved_at"] = COLLECTED_AT

    document = SourceDocument.model_validate(payload)
    serialized = document.model_dump()

    assert document.collected_at == COLLECTED_AT
    assert document.retrieved_at == COLLECTED_AT
    assert serialized["collected_at"] == COLLECTED_AT
    assert "retrieved_at" not in serialized


def test_source_provenance_is_explicit_and_deterministic() -> None:
    document = _document()

    assert document.source_category is SourceCategory.OFFICIAL
    assert document.source_role is SourceRole.PRIMARY
    assert document.source_tier is SourceTier.A
    assert document.region == "US"


def test_provider_metadata_is_json_safe_opaque_and_identity_neutral() -> None:
    first = _document(metadata={"feed_section": "policy", "page": 2})
    changed = _document(metadata={"feed_section": "markets", "page": 9})

    assert first.id == changed.id
    assert first.provider_metadata == {"feed_section": "policy", "page": 2}
    with pytest.raises(ValidationError):
        _document(metadata={"not_json": object()})


@pytest.mark.parametrize(
    "metadata",
    [
        {"Authorization": "Bearer value"},
        {"authorization_header": "Bearer value"},
        {"api-key": "value"},
        {"nested": {"api_key": "value"}},
        {"password": "value"},
    ],
)
def test_provider_metadata_rejects_secret_bearing_keys(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        _document(metadata=metadata)


class StaticProvider:
    def __init__(self, name: str, batch: CollectionBatch) -> None:
        self.name = name
        self.batch = batch
        self.calls = 0

    def collect(self, collected_at: datetime) -> CollectionBatch:
        _ = collected_at
        self.calls += 1
        return self.batch


class BrokenProvider:
    name = "broken"

    def __init__(self) -> None:
        self.calls = 0

    def collect(self, collected_at: datetime) -> CollectionBatch:
        _ = collected_at
        self.calls += 1
        raise OSError("credential=must-not-leak")


def test_mixed_provider_results_are_isolated_and_preserve_successful_documents() -> None:
    successful = StaticProvider(
        "successful",
        CollectionBatch(provider="successful", documents=[_document()]),
    )
    broken = BrokenProvider()
    no_data = StaticProvider(
        "empty",
        CollectionBatch(provider="empty", status=ProviderStatus.NO_DATA),
    )
    later = StaticProvider(
        "later",
        CollectionBatch(provider="later", documents=[_document(native_id="later-1")]),
    )

    batches = collect_intelligence_providers(
        [successful, broken, no_data, later],
        COLLECTED_AT,
    )

    assert [batch.status for batch in batches] == [
        ProviderStatus.OK,
        ProviderStatus.FAILED,
        ProviderStatus.NO_DATA,
        ProviderStatus.OK,
    ]
    assert [document.provider_native_id for batch in batches for document in batch.documents] == [
        None,
        "later-1",
    ]
    assert [successful.calls, broken.calls, no_data.calls, later.calls] == [1, 1, 1, 1]
    assert batches[1].error_type == "OSError"
    assert "credential" not in " ".join(batches[1].warnings)
    assert all(batch.collected_at == COLLECTED_AT for batch in batches)


def test_duplicate_repeated_document_keeps_one_stable_identity() -> None:
    first = _document(content="Version one.")
    second = _document(content="Version two.")

    batches = collect_intelligence_providers(
        [
            StaticProvider("first", CollectionBatch(provider="first", documents=[first])),
            StaticProvider("second", CollectionBatch(provider="second", documents=[second])),
        ],
        COLLECTED_AT,
    )

    assert batches[0].documents[0].id == batches[1].documents[0].id
    assert batches[0].documents[0].content_hash != batches[1].documents[0].content_hash
