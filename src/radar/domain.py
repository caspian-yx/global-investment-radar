from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

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


class SourceCategory(StrEnum):
    OFFICIAL = "official"
    REGULATORY = "regulatory"
    COMPANY_PRIMARY = "company_primary"
    PROFESSIONAL_NEWS = "professional_news"
    AGGREGATOR = "aggregator"
    COMMUNITY = "community"
    OTHER = "other"


class SourceRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


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
    RATE = "RATE"
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


class DocumentStoryProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    document_id: str = Field(min_length=1, pattern=r".*\S.*")
    event_type: EventType
    canonical_entity_ids: tuple[str, ...]
    topics: tuple[Topic, ...]
    region: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    published_at: datetime
    normalized_title_tokens: tuple[str, ...]
    action_terms: tuple[str, ...]
    strict_numeric_facts: tuple[str, ...]
    authority_id: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    release_domain: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    reference_period: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    source_category: SourceCategory
    source_role: SourceRole
    source_tier: SourceTier

    @field_validator("published_at")
    @classmethod
    def normalize_profile_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("story profile timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_canonical_profile_values(self) -> DocumentStoryProfile:
        values = (
            self.canonical_entity_ids,
            self.topics,
            self.normalized_title_tokens,
            self.action_terms,
            self.strict_numeric_facts,
        )
        if any(tuple(sorted(set(items))) != items for items in values):
            raise ValueError("story profile collections must be canonical")
        return self


class Story(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str = Field(min_length=1, pattern=r".*\S.*")
    identity_seed_document_id: str = Field(min_length=1, pattern=r".*\S.*")
    identity_profile: DocumentStoryProfile
    presentation_document_id: str = Field(min_length=1, pattern=r".*\S.*")
    event_type: EventType
    canonical_entity_ids: tuple[str, ...]
    topics: tuple[Topic, ...]
    region: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    source_document_ids: tuple[str, ...] = Field(min_length=1)
    earliest_trusted_published_at: datetime
    latest_published_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")

    @field_validator(
        "earliest_trusted_published_at",
        "latest_published_at",
        "first_seen_at",
        "last_seen_at",
    )
    @classmethod
    def normalize_story_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("story timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_story_contract(self) -> Story:
        if self.identity_profile.document_id != self.identity_seed_document_id:
            raise ValueError("Story identity seed must match its immutable profile")
        if self.identity_seed_document_id not in self.source_document_ids:
            raise ValueError("Story identity seed must remain in its document closure")
        if self.presentation_document_id not in self.source_document_ids:
            raise ValueError("Story presentation document must be in its document closure")
        if self.event_type is not self.identity_profile.event_type:
            raise ValueError("Story EventType must match its immutable identity profile")
        if tuple(sorted(set(self.canonical_entity_ids))) != self.canonical_entity_ids:
            raise ValueError("Story entity IDs must be canonical")
        if tuple(sorted(set(self.topics))) != self.topics:
            raise ValueError("Story topics must be canonical")
        if tuple(sorted(set(self.source_document_ids))) != self.source_document_ids:
            raise ValueError("Story document closure must be canonical")
        if self.earliest_trusted_published_at > self.latest_published_at:
            raise ValueError("Story publication times are inconsistent")
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("Story seen times are inconsistent")
        return self


class StoryDocumentMembership(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str = Field(min_length=1, pattern=r".*\S.*")
    story_id: str = Field(min_length=1, pattern=r".*\S.*")
    source_document_id: str = Field(min_length=1, pattern=r".*\S.*")
    match_method: str = Field(min_length=1, pattern=r".*\S.*")
    rule_version: str = Field(min_length=1, pattern=r".*\S.*")
    first_linked_at: datetime

    @field_validator("first_linked_at")
    @classmethod
    def normalize_membership_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Story membership timestamp must be timezone-aware")
        return value.astimezone(UTC)


class SourceDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, pattern=r".*\S.*")
    provider: str = Field(min_length=1, pattern=r".*\S.*")
    provider_native_id: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    source_category: SourceCategory = SourceCategory.OTHER
    source_role: SourceRole = SourceRole.SECONDARY
    source_tier: SourceTier
    publisher: str = Field(min_length=1, pattern=r".*\S.*")
    original_url: str = Field(min_length=1, pattern=r".*\S.*")
    canonical_url: str = Field(min_length=1, pattern=r".*\S.*")
    title: str = Field(min_length=1, pattern=r".*\S.*")
    content: str
    language: str = Field(default="en", min_length=1, pattern=r".*\S.*")
    region: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    published_at: datetime
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        validation_alias=AliasChoices("collected_at", "retrieved_at"),
    )
    provider_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    content_hash: str
    is_demo: bool = False

    @model_validator(mode="before")
    @classmethod
    def default_original_url(cls, value: Any) -> Any:
        if isinstance(value, dict) and "original_url" not in value:
            value = {**value, "original_url": value.get("canonical_url")}
        return value

    @field_validator("published_at", "collected_at")
    @classmethod
    def normalize_document_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("document timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("provider_metadata")
    @classmethod
    def reject_sensitive_metadata(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        sensitive_key_fragments = {
            "apikey",
            "authorization",
            "password",
            "secret",
            "accesstoken",
            "refreshtoken",
        }

        def is_sensitive_key(key: str) -> bool:
            normalized = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            return any(fragment in normalized for fragment in sensitive_key_fragments)

        def contains_sensitive_key(item: JsonValue) -> bool:
            if isinstance(item, dict):
                return any(
                    is_sensitive_key(key) or contains_sensitive_key(nested)
                    for key, nested in item.items()
                )
            if isinstance(item, list):
                return any(contains_sensitive_key(nested) for nested in item)
            return False

        if contains_sensitive_key(value):
            raise ValueError("provider metadata cannot contain sensitive keys")
        return value

    @property
    def retrieved_at(self) -> datetime:
        """Legacy read compatibility; new code uses collected_at."""
        return self.collected_at


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


class CandidateExplanation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, pattern=r".*\S.*")
    explanation: str = Field(min_length=1, pattern=r".*\S.*")


class GenerationMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, pattern=r".*\S.*")
    requested_model: str = Field(min_length=1, pattern=r".*\S.*")
    returned_model: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float = Field(ge=0)


class StructuredExplanation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_event_id: str = Field(min_length=1, pattern=r".*\S.*")
    headline: str = Field(min_length=1, pattern=r".*\S.*")
    summary: str = Field(min_length=1, pattern=r".*\S.*")
    candidate_explanations: tuple[CandidateExplanation, ...]
    uncertainty: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    generation: GenerationMetadata


class AIRunUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requests_attempted: int = Field(ge=0)
    requests_succeeded: int = Field(ge=0)
    requests_failed: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    targets_skipped_due_to_budget: int = Field(ge=0)
    budget_exhausted: bool


class AIEnrichmentResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    explanations: dict[str, StructuredExplanation]
    usage: AIRunUsage


class MarketHistoryPoint(BaseModel):
    observed_at: datetime
    close: float

    @field_validator("observed_at")
    @classmethod
    def normalize_history_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market history timestamps must be timezone-aware")
        return value.astimezone(UTC)


class MarketObservation(BaseModel):
    id: str
    symbol: str
    name: str
    region: str
    previous_observed_at: datetime | None = None
    observed_at: datetime
    close: float
    previous_close: float
    change_pct: float
    currency: str | None = None
    provider: str
    is_demo: bool = False
    history: tuple[MarketHistoryPoint, ...] = ()

    @field_validator("previous_observed_at", "observed_at")
    @classmethod
    def normalize_market_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market observation timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_market_interval(self) -> MarketObservation:
        if (
            self.previous_observed_at is not None
            and self.previous_observed_at >= self.observed_at
        ):
            raise ValueError("previous market observation must precede latest observation")
        if any(
            current.observed_at <= following.observed_at
            for current, following in zip(self.history, self.history[1:], strict=False)
        ):
            raise ValueError("market history must be ordered newest first")
        return self


class MacroObservation(BaseModel):
    id: str
    series_id: str
    name: str
    period: str
    observed_at: datetime
    release_at: datetime | None = None
    value: float
    previous_value: float | None
    change: float | None
    change_label: str | None
    unit: str
    provider: str
    source_document_id: str | None = Field(
        default=None, min_length=1, pattern=r".*\S.*"
    )
    is_demo: bool = False


class CollectionBatch(BaseModel):
    provider: str
    status: ProviderStatus = ProviderStatus.OK
    documents: list[SourceDocument] = Field(default_factory=list)
    markets: list[MarketObservation] = Field(default_factory=list)
    macro: list[MacroObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, int] = Field(default_factory=dict)
    error_type: str | None = None
    collected_at: datetime | None = None
    is_demo: bool = False

    @field_validator("collected_at")
    @classmethod
    def normalize_collection_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collection timestamp must be timezone-aware")
        return value.astimezone(UTC)


class ProviderRunStatus(BaseModel):
    provider: str
    status: ProviderStatus
    item_count: int
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, int] = Field(default_factory=dict)
    error_type: str | None = None


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
