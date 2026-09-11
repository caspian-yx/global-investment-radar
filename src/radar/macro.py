from __future__ import annotations

from radar.domain import (
    MacroObservation,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
)
from radar.explanation import build_macro_observation_evidence
from radar.ingestion import build_source_document

BLS_PROVIDER = "bls-public-api"
BLS_PUBLISHER = "U.S. Bureau of Labor Statistics"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def build_macro_source_document(
    observation: MacroObservation,
) -> SourceDocument | None:
    """Represent an observation as provenance only when release time is trusted."""
    release_at = observation.release_at
    if (
        observation.provider != BLS_PROVIDER
        or release_at is None
        or release_at.tzinfo is None
        or release_at.utcoffset() is None
    ):
        return None
    # BLS series + period is the stable record identity. Value revisions update the
    # document content hash without changing which official observation it represents.
    native_id = f"{observation.series_id}:{observation.period}"
    evidence = build_macro_observation_evidence(observation)
    document = build_source_document(
        provider=BLS_PROVIDER,
        trusted_provider_native_id=native_id,
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
        tier=SourceTier.A,
        publisher=BLS_PUBLISHER,
        url=BLS_API_URL,
        title=f"{observation.name} ({observation.series_id}) period {observation.period}",
        content=evidence.excerpt,
        published_at=release_at,
        collected_at=observation.observed_at,
        region="US",
        is_demo=observation.is_demo,
    )
    if (
        observation.source_document_id is not None
        and observation.source_document_id != document.id
    ):
        raise ValueError("MacroObservation source document identity is inconsistent")
    return document
