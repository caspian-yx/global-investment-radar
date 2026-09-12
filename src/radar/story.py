from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from radar.deterministic import classify_event, extract_entities
from radar.domain import (
    DocumentStoryProfile,
    EventType,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
    Story,
    StoryDocumentMembership,
    Topic,
    stable_id,
)
from radar.explanation import extract_topics

STORY_RULE_VERSION = "story-v1"
STORY_WINDOW = timedelta(hours=72)

_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}
_GENERIC_STORY_TOKENS = {
    "announce",
    "announced",
    "announces",
    "company",
    "decision",
    "enterprise",
    "launch",
    "launched",
    "launches",
    "new",
    "platform",
    "policy",
    "product",
    "release",
    "report",
    "update",
}
_AUTHORITY_IDS = {
    "bank-of-japan",
    "bank-of-korea",
    "china-nbs",
    "european-central-bank",
    "federal-reserve",
}
_ENTITY_CENTRIC_TYPES = {
    EventType.EARNINGS,
    EventType.GUIDANCE,
    EventType.M_AND_A,
    EventType.PRODUCT,
    EventType.SUPPLY_CHAIN,
    EventType.LEGAL,
    EventType.RISK,
}
_DISTINCT_TOPIC_FAMILIES = {Topic.TECHNOLOGY, Topic.ENERGY, Topic.REGULATION}
_MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}
_ACTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings_result", ("earnings", "financial results", "quarterly results")),
    ("revenue_result", ("revenue", "sales result")),
    ("guidance_update", ("guidance", "outlook", "forecast")),
    ("acquisition", ("acquire", "acquisition", "takeover")),
    ("merger", ("merger", "merge with")),
    ("rate_decision", ("interest rate decision", "policy rate", "benchmark rate")),
    ("rate_hike", ("rate hike", "raises interest rate")),
    ("rate_cut", ("rate cut", "cuts interest rate")),
    ("monetary_policy", ("monetary policy", "fomc")),
    ("product_launch", ("launch", "launched", "launches", "product launch")),
    ("product_introduction", ("introduces", "introduced", "unveils", "unveiled")),
    ("product_approval", ("product approval", "trial result")),
    ("supply_update", ("supply chain", "supply", "inventory", "capacity")),
    ("legal_action", ("charges", "lawsuit", "enforcement", "investigation")),
    ("regulatory_action", ("regulatory action", "regulation", "regulations")),
    ("inflation_release", ("consumer price index", "producer price index", "inflation")),
    ("employment_release", ("employment report", "unemployment", "payroll")),
    ("gdp_release", ("gross domestic product", "gdp")),
    ("risk_event", ("war", "tariff", "sanction", "default", "bankruptcy")),
)


@dataclass(frozen=True)
class StoryProfileMatch:
    match_method: str | None
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoryConsolidationResult:
    stories: tuple[Story, ...]
    memberships: tuple[StoryDocumentMembership, ...]
    documents_by_story: dict[str, tuple[SourceDocument, ...]]
    diagnostics: dict[str, object]


_CATEGORY_PRIORITY = {
    SourceCategory.OFFICIAL: 0,
    SourceCategory.REGULATORY: 1,
    SourceCategory.COMPANY_PRIMARY: 2,
    SourceCategory.PROFESSIONAL_NEWS: 3,
    SourceCategory.OTHER: 4,
    SourceCategory.AGGREGATOR: 5,
    SourceCategory.COMMUNITY: 6,
}
_ROLE_PRIORITY = {SourceRole.PRIMARY: 0, SourceRole.SECONDARY: 1}
_TIER_PRIORITY = {
    SourceTier.A: 0,
    SourceTier.B: 1,
    SourceTier.C: 2,
    SourceTier.D: 3,
}


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9.%$€£¥]+", " ", value.casefold()).strip()


def _title_tokens(title: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+", title.casefold())
    return tuple(sorted({word for word in words if word not in _TITLE_STOPWORDS}))


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized = _normalized_text(phrase)
    return bool(
        normalized
        and re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text)
    )


def _action_terms(text: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            action
            for action, phrases in _ACTION_PATTERNS
            if any(_contains_phrase(text, phrase) for phrase in phrases)
        )
    )


def _authority_id(entity_ids: tuple[str, ...]) -> str | None:
    authorities = sorted(set(entity_ids) & _AUTHORITY_IDS)
    return authorities[0] if len(authorities) == 1 else None


def _release_domain(event_type: EventType, topics: tuple[Topic, ...], text: str) -> str | None:
    topic_set = set(topics)
    if event_type is EventType.POLICY:
        if Topic.RATES in topic_set:
            return "rates"
        if Topic.REGULATION in topic_set:
            return "regulation"
        if _contains_phrase(text, "monetary policy"):
            return "monetary_policy"
    if event_type is EventType.MACRO:
        if Topic.INFLATION in topic_set:
            return "inflation"
        if Topic.EMPLOYMENT in topic_set:
            return "employment"
        if _contains_phrase(text, "gross domestic product") or _contains_phrase(text, "gdp"):
            return "gdp"
    return None


def _reference_period(text: str) -> str | None:
    month_names = "|".join(_MONTHS)
    month_first = re.search(rf"\b({month_names})\s+(20\d{{2}})\b", text)
    if month_first:
        return f"{month_first.group(2)}-{_MONTHS[month_first.group(1)]}"
    year_first = re.search(rf"\b(20\d{{2}})\s+({month_names})\b", text)
    if year_first:
        return f"{year_first.group(1)}-{_MONTHS[year_first.group(2)]}"
    quarter = re.search(r"\b(?:q([1-4])|(first|second|third|fourth) quarter)\s+(20\d{2})\b", text)
    if quarter:
        quarter_number = quarter.group(1) or {
            "first": "1",
            "second": "2",
            "third": "3",
            "fourth": "4",
        }[quarter.group(2)]
        return f"{quarter.group(3)}-Q{quarter_number}"
    return None


def _decimal_text(value: str) -> str | None:
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        return None
    return format(decimal_value.normalize(), "f")


def _strict_numeric_facts(text: str) -> tuple[str, ...]:
    patterns = {
        "policy_rate_pct": re.compile(
            r"(?:policy|interest|benchmark|federal funds) rates?"
            r"(?:\s+[a-z]+){0,4}\s+(?:at|to|of)\s+"
            r"(\d+(?:\.\d+)?)\s*(?:%|percent)"
        ),
        "inflation_pct": re.compile(
            r"(?:inflation|consumer price index|cpi)"
            r"(?:\s+[a-z]+){0,5}\s+(?:at|was|to|of)\s+"
            r"(\d+(?:\.\d+)?)\s*(?:%|percent)"
        ),
        "unemployment_pct": re.compile(
            r"unemployment rate(?:\s+[a-z]+){0,4}\s+(?:at|was|to|of)\s+"
            r"(\d+(?:\.\d+)?)\s*(?:%|percent)"
        ),
        "gdp_pct": re.compile(
            r"(?:gross domestic product|gdp)(?:\s+[a-z]+){0,5}\s+"
            r"(?:at|was|to|of|by)\s+(\d+(?:\.\d+)?)\s*(?:%|percent)"
        ),
    }
    facts: set[str] = set()
    for dimension, pattern in patterns.items():
        for match in pattern.finditer(text):
            value = _decimal_text(match.group(1))
            if value is not None:
                facts.add(f"{dimension}={value}")
    return tuple(sorted(facts))


def build_document_story_profile(
    document: SourceDocument,
    entity_config: list[dict[str, Any]],
) -> DocumentStoryProfile:
    combined = _normalized_text(f"{document.title} {document.content}")
    event_type = classify_event(combined)
    entity_ids = tuple(extract_entities(combined, entity_config))
    topics = tuple(sorted(extract_topics(combined)))
    return DocumentStoryProfile(
        document_id=document.id,
        event_type=event_type,
        canonical_entity_ids=entity_ids,
        topics=topics,
        region=document.region,
        published_at=document.published_at,
        normalized_title_tokens=_title_tokens(document.title),
        action_terms=_action_terms(combined),
        strict_numeric_facts=_strict_numeric_facts(combined),
        authority_id=_authority_id(entity_ids),
        release_domain=_release_domain(event_type, topics, combined),
        reference_period=_reference_period(combined),
        source_category=document.source_category,
        source_role=document.source_role,
        source_tier=document.source_tier,
    )


def _title_similarity(left: DocumentStoryProfile, right: DocumentStoryProfile) -> float:
    left_tokens = set(left.normalized_title_tokens)
    right_tokens = set(right.normalized_title_tokens)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _distinctive_title_tokens(profile: DocumentStoryProfile) -> set[str]:
    entity_tokens = {
        token
        for entity_id in profile.canonical_entity_ids
        for token in re.findall(r"[a-z0-9]+", entity_id.casefold())
    }
    return (
        set(profile.normalized_title_tokens)
        - _GENERIC_STORY_TOKENS
        - entity_tokens
    )


def _numeric_conflict(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    def by_dimension(values: tuple[str, ...]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for value in values:
            dimension, _, amount = value.partition("=")
            result.setdefault(dimension, set()).add(amount)
        return result

    left_by_dimension = by_dimension(left)
    right_by_dimension = by_dimension(right)
    return any(
        left_by_dimension[dimension].isdisjoint(right_by_dimension[dimension])
        for dimension in left_by_dimension.keys() & right_by_dimension.keys()
    )


def _hard_rejection_reasons(
    candidate: DocumentStoryProfile,
    identity: DocumentStoryProfile,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    if candidate.event_type is not identity.event_type:
        reasons.add("incompatible_event_type")
    if candidate.region and identity.region and candidate.region != identity.region:
        reasons.add("incompatible_region")
    if (
        candidate.event_type in _ENTITY_CENTRIC_TYPES
        and candidate.canonical_entity_ids
        and identity.canonical_entity_ids
        and not set(candidate.canonical_entity_ids) & set(identity.canonical_entity_ids)
    ):
        reasons.add("conflicting_entities")
    if (
        candidate.authority_id
        and identity.authority_id
        and candidate.authority_id != identity.authority_id
    ):
        reasons.add("incompatible_authority")
    if (
        candidate.release_domain
        and identity.release_domain
        and candidate.release_domain != identity.release_domain
    ):
        reasons.add("incompatible_release_domain")
    if (
        candidate.reference_period
        and identity.reference_period
        and candidate.reference_period != identity.reference_period
    ):
        reasons.add("incompatible_reference_period")
    candidate_distinct_topics = set(candidate.topics) & _DISTINCT_TOPIC_FAMILIES
    identity_distinct_topics = set(identity.topics) & _DISTINCT_TOPIC_FAMILIES
    if (
        candidate_distinct_topics
        and identity_distinct_topics
        and candidate_distinct_topics.isdisjoint(identity_distinct_topics)
    ):
        reasons.add("incompatible_topic")
    if (
        candidate.event_type in _ENTITY_CENTRIC_TYPES
        and candidate.action_terms
        and identity.action_terms
        and set(candidate.action_terms).isdisjoint(identity.action_terms)
    ):
        reasons.add("incompatible_action")
    if _numeric_conflict(candidate.strict_numeric_facts, identity.strict_numeric_facts):
        reasons.add("numeric_conflict")
    if abs(candidate.published_at - identity.published_at) > STORY_WINDOW:
        reasons.add("outside_time_window")
    return tuple(sorted(reasons))


def match_story_profile(
    candidate: DocumentStoryProfile,
    identity: DocumentStoryProfile,
) -> StoryProfileMatch:
    rejection_reasons = _hard_rejection_reasons(candidate, identity)
    if rejection_reasons:
        return StoryProfileMatch(None, rejection_reasons)

    similarity = _title_similarity(candidate, identity)
    exact_title = candidate.normalized_title_tokens == identity.normalized_title_tokens
    shared_entities = set(candidate.canonical_entity_ids) & set(
        identity.canonical_entity_ids
    )
    shared_actions = set(candidate.action_terms) & set(identity.action_terms)
    shared_topics = set(candidate.topics) & set(identity.topics)
    shared_distinctive_title = _distinctive_title_tokens(candidate) & (
        _distinctive_title_tokens(identity)
    )

    if candidate.event_type is EventType.OTHER:
        if exact_title or (
            similarity >= 0.95
            and len(candidate.normalized_title_tokens) >= 5
            and (shared_entities or not candidate.canonical_entity_ids)
        ):
            return StoryProfileMatch("near_identical_title")
        return StoryProfileMatch(None, ("other_requires_exceptional_identity",))

    if exact_title or (
        similarity >= 0.82
        and (
            candidate.event_type not in _ENTITY_CENTRIC_TYPES
            or bool(shared_distinctive_title)
        )
    ):
        return StoryProfileMatch("near_identical_title")

    if (
        candidate.event_type is EventType.POLICY
        and candidate.authority_id is not None
        and candidate.authority_id == identity.authority_id
        and candidate.release_domain is not None
        and candidate.release_domain == identity.release_domain
        and (shared_actions or similarity >= 0.25)
    ):
        return StoryProfileMatch("policy_release")

    if (
        candidate.event_type is EventType.MACRO
        and candidate.release_domain is not None
        and candidate.release_domain == identity.release_domain
        and candidate.reference_period is not None
        and candidate.reference_period == identity.reference_period
        and (
            candidate.authority_id == identity.authority_id
            or candidate.authority_id is None
            or identity.authority_id is None
        )
    ):
        return StoryProfileMatch("macro_release")

    if (
        candidate.event_type in _ENTITY_CENTRIC_TYPES
        and shared_entities
        and shared_actions
        and shared_distinctive_title
        and similarity >= 0.30
        and (shared_topics or not candidate.topics or not identity.topics)
    ):
        return StoryProfileMatch("entity_event_action")

    return StoryProfileMatch(None, ("insufficient_matching_evidence",))


def _document_order(document: SourceDocument) -> tuple[object, ...]:
    return (
        _CATEGORY_PRIORITY[document.source_category],
        _ROLE_PRIORITY[document.source_role],
        _TIER_PRIORITY[document.source_tier],
        document.published_at,
        document.canonical_url,
        document.id,
    )


def _unique_documents(documents: Iterable[SourceDocument]) -> dict[str, SourceDocument]:
    result: dict[str, SourceDocument] = {}
    for document in documents:
        existing = result.get(document.id)
        if existing is not None and existing != document:
            raise ValueError(f"Conflicting SourceDocument payload for {document.id}")
        result.setdefault(document.id, document)
    return result


def _new_story(
    document: SourceDocument,
    profile: DocumentStoryProfile,
) -> Story:
    story_id = stable_id("story", f"{STORY_RULE_VERSION}|{document.id}")
    return Story(
        id=story_id,
        identity_seed_document_id=document.id,
        identity_profile=profile,
        presentation_document_id=document.id,
        event_type=profile.event_type,
        canonical_entity_ids=profile.canonical_entity_ids,
        topics=profile.topics,
        region=profile.region,
        source_document_ids=(document.id,),
        earliest_trusted_published_at=document.published_at,
        latest_published_at=document.published_at,
        first_seen_at=document.collected_at,
        last_seen_at=document.collected_at,
        rule_version=STORY_RULE_VERSION,
    )


def _membership(
    story_id: str,
    document_id: str,
    match_method: str,
    linked_at: datetime,
) -> StoryDocumentMembership:
    return StoryDocumentMembership(
        id=stable_id("story-membership", f"{STORY_RULE_VERSION}|{document_id}"),
        story_id=story_id,
        source_document_id=document_id,
        match_method=match_method,
        rule_version=STORY_RULE_VERSION,
        first_linked_at=linked_at,
    )


def _updated_story(
    story: Story,
    document: SourceDocument,
    profile: DocumentStoryProfile,
    known_documents: dict[str, SourceDocument],
    entity_config: list[dict[str, Any]],
) -> Story:
    document_ids = tuple(sorted({*story.source_document_ids, document.id}))
    missing = [document_id for document_id in document_ids if document_id not in known_documents]
    if missing:
        raise ValueError(
            "Story update requires every member SourceDocument: " + ", ".join(missing)
        )
    members = [known_documents[document_id] for document_id in document_ids]
    member_profiles = [
        profile
        if member.id == document.id
        else build_document_story_profile(member, entity_config)
        for member in members
    ]
    # Existing aggregate semantics remain authoritative when a historical profile cannot be
    # reconstructed with the current entity registry. New canonical semantics are additive.
    entity_ids = tuple(
        sorted(
            {
                *story.canonical_entity_ids,
                *profile.canonical_entity_ids,
                *(entity_id for item in member_profiles for entity_id in item.canonical_entity_ids),
            }
        )
    )
    topics = tuple(
        sorted(
            {
                *story.topics,
                *profile.topics,
                *(topic for item in member_profiles for topic in item.topics),
            }
        )
    )
    regions = {member.region for member in members if member.region is not None}
    region = next(iter(regions)) if len(regions) == 1 else story.region
    trusted = [member for member in members if member.source_tier in {SourceTier.A, SourceTier.B}]
    occurrence_pool = trusted or members
    presentation = min(members, key=_document_order)
    payload = story.model_dump()
    payload.update(
        {
            "presentation_document_id": presentation.id,
            "canonical_entity_ids": entity_ids,
            "topics": topics,
            "region": region,
            "source_document_ids": document_ids,
            # Keep lifecycle publication bounds monotonic. A content revision may carry an
            # accidental publication-time change, and a late corroborator must never move
            # the Story/Event occurrence later.
            "earliest_trusted_published_at": min(
                story.earliest_trusted_published_at,
                *(item.published_at for item in occurrence_pool),
            ),
            "latest_published_at": max(
                story.latest_published_at,
                *(item.published_at for item in members),
            ),
            "first_seen_at": min(story.first_seen_at, *(item.collected_at for item in members)),
            "last_seen_at": max(story.last_seen_at, *(item.collected_at for item in members)),
        }
    )
    return Story.model_validate(payload)


def consolidate_stories(
    documents: Iterable[SourceDocument],
    entity_config: list[dict[str, Any]],
    *,
    existing_stories: Iterable[Story] = (),
    existing_memberships: Iterable[StoryDocumentMembership] = (),
    known_documents: Iterable[SourceDocument] = (),
    now: datetime,
) -> StoryConsolidationResult:
    incoming_by_id = _unique_documents(documents)
    known_by_id = _unique_documents(known_documents)
    known_by_id.update(incoming_by_id)

    stories_by_id: dict[str, Story] = {}
    for story in existing_stories:
        if story.rule_version != STORY_RULE_VERSION:
            continue
        persisted_story = stories_by_id.get(story.id)
        if persisted_story is not None and persisted_story != story:
            raise ValueError(f"Conflicting Story payload for {story.id}")
        stories_by_id.setdefault(story.id, story)
    original_story_ids = set(stories_by_id)

    memberships_by_document: dict[str, StoryDocumentMembership] = {}
    for membership in existing_memberships:
        if membership.rule_version != STORY_RULE_VERSION:
            continue
        persisted_membership = memberships_by_document.get(
            membership.source_document_id
        )
        if persisted_membership is not None and persisted_membership != membership:
            raise ValueError(
                "SourceDocument belongs to multiple story-v1 Stories: "
                f"{membership.source_document_id}"
            )
        memberships_by_document.setdefault(membership.source_document_id, membership)

    touched_story_ids: set[str] = set()
    created = 0
    attached_to_existing = 0
    ambiguous = 0
    match_methods: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    rejected_attempts = 0

    for document in sorted(incoming_by_id.values(), key=_document_order):
        profile = build_document_story_profile(document, entity_config)
        existing_membership = memberships_by_document.get(document.id)
        if existing_membership is not None:
            existing_story = stories_by_id.get(existing_membership.story_id)
            if existing_story is None:
                raise ValueError(
                    f"Story membership references missing Story {existing_membership.story_id}"
                )
            stories_by_id[existing_story.id] = _updated_story(
                existing_story, document, profile, known_by_id, entity_config
            )
            touched_story_ids.add(existing_story.id)
            match_methods["existing_membership"] += 1
            continue

        matched: list[tuple[Story, str]] = []
        for story in sorted(stories_by_id.values(), key=lambda item: item.id):
            decision = match_story_profile(profile, story.identity_profile)
            if decision.match_method is None:
                rejected_attempts += 1
                rejected.update(decision.rejection_reasons)
            else:
                matched.append((story, decision.match_method))

        if len(matched) == 1:
            matched_story, method = matched[0]
            membership = _membership(matched_story.id, document.id, method, now)
            memberships_by_document[document.id] = membership
            stories_by_id[matched_story.id] = _updated_story(
                matched_story, document, profile, known_by_id, entity_config
            )
            touched_story_ids.add(matched_story.id)
            match_methods[method] += 1
            if matched_story.id in original_story_ids:
                attached_to_existing += 1
            continue

        method = "ambiguous_singleton" if len(matched) > 1 else "identity_seed"
        if len(matched) > 1:
            ambiguous += 1
        story = _new_story(document, profile)
        stories_by_id[story.id] = story
        memberships_by_document[document.id] = _membership(
            story.id, document.id, method, now
        )
        touched_story_ids.add(story.id)
        match_methods[method] += 1
        created += 1

    touched_stories = tuple(stories_by_id[item_id] for item_id in sorted(touched_story_ids))
    touched_document_ids = {
        document_id
        for story in touched_stories
        for document_id in story.source_document_ids
    }
    touched_memberships = tuple(
        memberships_by_document[document_id]
        for document_id in sorted(touched_document_ids)
        if document_id in memberships_by_document
    )
    documents_by_story = {
        story.id: tuple(
            known_by_id[document_id]
            for document_id in story.source_document_ids
            if document_id in known_by_id
        )
        for story in touched_stories
    }
    story_sizes = [len(item.source_document_ids) for item in touched_stories]
    all_touched_documents = [
        document
        for values in documents_by_story.values()
        for document in values
    ]
    diagnostics: dict[str, object] = {
        "documents_considered": len(incoming_by_id),
        "stories_created": created,
        "documents_attached_to_existing_stories": attached_to_existing,
        "singleton_stories": sum(size == 1 for size in story_sizes),
        "multi_document_stories": sum(size > 1 for size in story_sizes),
        "multi_source_stories": sum(
            len({document.provider for document in documents_by_story[story.id]}) > 1
            for story in touched_stories
        ),
        "rejected_match_attempts": rejected_attempts,
        "rejected_by_reason": dict(sorted(rejected.items())),
        "ambiguous_matches": ambiguous,
        "max_story_size": max(story_sizes, default=0),
        "match_method_counts": dict(sorted(match_methods.items())),
        "source_category_counts": dict(
            sorted(Counter(item.source_category.value for item in all_touched_documents).items())
        ),
        "source_role_counts": dict(
            sorted(Counter(item.source_role.value for item in all_touched_documents).items())
        ),
        "rule_version": STORY_RULE_VERSION,
    }
    return StoryConsolidationResult(
        stories=touched_stories,
        memberships=touched_memberships,
        documents_by_story=documents_by_story,
        diagnostics=diagnostics,
    )
