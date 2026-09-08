from __future__ import annotations

from typing import Protocol

from radar.config import Settings
from radar.domain import EvidencePack, StructuredAnalysis


class LLMProvider(Protocol):
    name: str

    def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis: ...


class DisabledLLMProvider:
    name = "disabled"

    def __init__(self, reason: str = "AI analysis is disabled by configuration.") -> None:
        self.reason = reason

    def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis:
        return StructuredAnalysis(
            event_id=evidence_pack.event.id,
            status="disabled",
            provider=self.name,
            unavailable_reason=self.reason,
        )


class FakeLLMProvider:
    """A test-only provider that validates the structured AI data path without an API call."""

    name = "fake"

    def analyze(self, evidence_pack: EvidencePack) -> StructuredAnalysis:
        event = evidence_pack.event
        claim_ids = [claim.id for claim in evidence_pack.claims]
        return StructuredAnalysis(
            event_id=event.id,
            status="test-data",
            why_it_matters=(
                f"TEST DATA: {event.event_type.value} event ranked "
                f"{event.importance_score:.1f}/100 "
                "from the supplied evidence pack."
            ),
            bull_case=(
                "TEST DATA: A constructive interpretation would require corroborating evidence."
            ),
            bear_case=(
                "TEST DATA: The event may be transient or already reflected in market prices."
            ),
            risk_notes=[
                "This output is synthetic and must never be treated as production research."
            ],
            cited_claim_ids=claim_ids,
            provider=self.name,
            model="deterministic-test-double",
            is_test_data=True,
        )


def build_llm_provider(settings: Settings) -> LLMProvider:
    if not settings.ai_enabled:
        return DisabledLLMProvider()
    if settings.daily_llm_budget <= 0:
        return DisabledLLMProvider("Daily LLM budget is zero; deterministic mode is active.")
    provider = settings.llm_provider.casefold().strip()
    if provider == "fake":
        if settings.app_env == "production":
            return DisabledLLMProvider("Fake LLM is blocked in production.")
        return FakeLLMProvider()
    if provider in {"", "disabled", "none"}:
        return DisabledLLMProvider("No LLM provider was configured.")
    return DisabledLLMProvider(
        f"LLM provider '{settings.llm_provider}' is not installed; "
        "deterministic fallback is active."
    )
