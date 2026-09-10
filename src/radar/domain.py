from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

ID_NAMESPACE = uuid.UUID("e482538d-22c4-4a71-9713-06e7a4193378")


def stable_id(kind: str, value: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"{kind}:{value}"))


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SourceTier(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ClaimType(StrEnum):
    FACT = "FACT"
    DERIVED = "DERIVED"
    INTERPRETATION = "INTERPRETATION"


class SourceKind(StrEnum):
    DOCUMENT = "DOCUMENT"
    MARKET_OBSERVATION = "MARKET_OBSERVATION"
    MACRO_OBSERVATION = "MACRO_OBSERVATION"


class SessionKind(StrEnum):
    EXCHANGE = "EXCHANGE"
    NEAR_24_HOUR = "NEAR_24_HOUR"
    CONTINUOUS = "CONTINUOUS"


class Topic(StrEnum):
    RATES = "RATES"
    INFLATION = "INFLATION"
    EMPLOYMENT = "EMPLOYMENT"
    REGULATION = "REGULATION"
    TECHNOLOGY = "TECHNOLOGY"
    ENERGY = "ENERGY"
    CHINA = "CHINA"
    US = "US"


class DriverTemporalRelation(StrEnum):
    PRIOR = "PRIOR"
    SAME_WINDOW_AMBIGUOUS = "SAME_WINDOW_AMBIGUOUS"
    POST_EVENT = "POST_EVENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class DriverRelevance(StrEnum):
    EXACT_ENTITY = "EXACT_ENTITY"
    STRONG_TOPIC = "STRONG_TOPIC"
    WEAK = "WEAK"
    NONE = "NONE"


class DriverConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AssetClass(StrEnum):
    EQUITY_INDEX = "EQUITY_INDEX"
    EQUITY_ETF_PROXY = "EQUITY_ETF_PROXY"
    FX = "FX"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"


class ProviderStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    NO_DATA = "NO_DATA"


class ReportQualityStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    INSUFFICIENT = "INSUFFICIENT"


class EventType(StrEnum):
    MARKET_MOVE = "market_move"
    EARNINGS = "earnings"
    GUIDANCE = "guidance"
    MACRO = "macro_release"
    POLICY = "policy"
    M_AND_A = "m_and_a"
    PRODUCT = "product"
    SUPPLY_CHAIN = "supply_chain"
    LEGAL = "legal"
    RISK = "risk"
    OTHER = "other"


class SourceDocument(BaseModel):
    id: str
    provider: str
    source_tier: SourceTier
    publisher: str
    canonical_url: str
    title: str
    content: str
    language: str = "en"
    published_at: datetime
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    content_hash: str
    is_demo: bool = False


class Evidence(BaseModel):
    id: str
    source_kind: SourceKind
    source_id: str = Field(min_length=1, pattern=r".*\S.*")
    source_document_id: str | None = None
    locator: dict[str, Any]
    excerpt: str
    checksum: str
    quality_score: float = Field(ge=0, le=1)
    extraction_method: str = "deterministic"

    @model_validator(mode="after")
    def validate_legacy_document_reference(self) -> Evidence:
        if self.source_kind is SourceKind.DOCUMENT:
            if (
                self.source_document_id is not None
                and self.source_document_id != self.source_id
            ):
                raise ValueError("document provenance IDs must agree")
        elif self.source_document_id is not None:
            raise ValueError("non-document evidence cannot carry source_document_id")
        return self


class Claim(BaseModel):
    id: str
    claim_type: ClaimType
    text: str
    evidence_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    created_by: str = "deterministic"
    as_of: datetime


class Event(BaseModel):
    id: str
    event_key: str
    event_type: EventType
    title: str
    occurred_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    entity_ids: list[str]
    claim_ids: list[str]
    evidence_ids: list[str]
    source_document_ids: list[str]
    importance_score: float = Field(ge=0, le=100)
    importance_components: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    is_demo: bool = False


class DriverCandidate(BaseModel):
    id: str = Field(min_length=1, pattern=r".*\S.*")
    target_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    candidate_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    interpretation_claim_id: str = Field(min_length=1, pattern=r".*\S.*")
    evidence_ids: list[str] = Field(min_length=1)
    target_evidence_ids: list[str] = Field(min_length=1)
    candidate_evidence_ids: list[str] = Field(min_length=1)
    temporal_relation: DriverTemporalRelation
    relevance_relation: DriverRelevance
    confidence: DriverConfidence
    confidence_components: dict[str, str]
    confidence_reasons: list[str] = Field(min_length=1)
    generator: str = Field(min_length=1, pattern=r".*\S.*")
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    as_of: datetime

    @model_validator(mode="after")
    def validate_relationship_shape(self) -> DriverCandidate:
        if self.target_event_id == self.candidate_event_id:
            raise ValueError("target and candidate Events must differ")
        target_ids = sorted(set(self.target_evidence_ids))
        candidate_ids = sorted(set(self.candidate_evidence_ids))
        if target_ids != self.target_evidence_ids:
            raise ValueError("target Evidence IDs must be unique and sorted")
        if candidate_ids != self.candidate_evidence_ids:
            raise ValueError("candidate Evidence IDs must be unique and sorted")
        if sorted(set([*target_ids, *candidate_ids])) != self.evidence_ids:
            raise ValueError("supporting Evidence IDs must be the canonical union")
        return self


class ExplanationBundle(BaseModel):
    target_event: Event
    driver_candidates: list[DriverCandidate]
    claims: list[Claim]
    evidence: list[Evidence]

    @model_validator(mode="after")
    def validate_linked_contents(self) -> ExplanationBundle:
        from radar.ranking import rank_driver_candidates

        if self.target_event.event_type not in {
            EventType.MARKET_MOVE,
            EventType.RISK,
        }:
            raise ValueError("ExplanationBundle target must be a market/risk Event")
        candidate_ids = [item.id for item in self.driver_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("ExplanationBundle DriverCandidate IDs must be unique")
        if any(
            item.target_event_id != self.target_event.id
            for item in self.driver_candidates
        ):
            raise ValueError("ExplanationBundle DriverCandidates must share its target Event")
        if self.driver_candidates != rank_driver_candidates(self.driver_candidates):
            raise ValueError(
                "ExplanationBundle DriverCandidates must use C2.2 semantic order"
            )
        expected_claim_ids = sorted(
            {item.interpretation_claim_id for item in self.driver_candidates}
        )
        if [item.id for item in self.claims] != expected_claim_ids:
            raise ValueError("ExplanationBundle Claims must be its exact linked set")
        expected_evidence_ids = sorted(
            {
                evidence_id
                for candidate in self.driver_candidates
                for evidence_id in candidate.evidence_ids
            }
        )
        if [item.id for item in self.evidence] != expected_evidence_ids:
            raise ValueError("ExplanationBundle Evidence must be its exact linked set")
        return self


class MarketObservation(BaseModel):
    id: str
    symbol: str
    name: str
    region: str
    observed_at: datetime
    close: float
    previous_close: float
    change_pct: float
    currency: str | None = None
    provider: str
    is_demo: bool = False


class MacroObservation(BaseModel):
    id: str
    series_id: str
    name: str
    period: str
    observed_at: datetime
    value: float
    previous_value: float | None
    change: float | None
    change_label: str | None
    unit: str
    provider: str
    is_demo: bool = False


class CollectionBatch(BaseModel):
    provider: str
    status: ProviderStatus = ProviderStatus.OK
    documents: list[SourceDocument] = Field(default_factory=list)
    markets: list[MarketObservation] = Field(default_factory=list)
    macro: list[MacroObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, int] = Field(default_factory=dict)
    is_demo: bool = False


class ProviderRunStatus(BaseModel):
    provider: str
    status: ProviderStatus
    item_count: int
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, int] = Field(default_factory=dict)


class EvidencePack(BaseModel):
    event: Event
    claims: list[Claim]
    evidence: list[Evidence]
    sources: list[SourceDocument]


class StructuredAnalysis(BaseModel):
    event_id: str
    status: str
    why_it_matters: str | None = None
    bull_case: str | None = None
    bear_case: str | None = None
    risk_notes: list[str] = Field(default_factory=list)
    cited_claim_ids: list[str] = Field(default_factory=list)
    provider: str
    model: str | None = None
    is_test_data: bool = False
    unavailable_reason: str | None = None


class DeterministicSummary(BaseModel):
    market_headline: str
    market_leaders: list[MarketObservation]
    macro_headlines: list[str]
    risk_flags: list[str]


class ReportQualityAssessment(BaseModel):
    status: ReportQualityStatus
    market_coverage: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class PipelineResult(BaseModel):
    run_id: str
    report_date: str
    report_path: str
    is_demo: bool
    ai_status: str
    quality_status: ReportQualityStatus
    documents: int
    events: int
    official_events: int
    market_events: int
    macro_events: int
    driver_candidates: int
    markets: int
    macro: int
    email_status: str
    elapsed_seconds: float
    provider_statuses: list[ProviderRunStatus]
