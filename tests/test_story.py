from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from radar.deterministic import extract_story_events
from radar.domain import (
    DocumentStoryProfile,
    EventType,
    SourceCategory,
    SourceRole,
    SourceTier,
    Story,
    StoryDocumentMembership,
    Topic,
    stable_id,
)
from radar.providers import make_document
from radar.story import (
    STORY_RULE_VERSION,
    build_document_story_profile,
    consolidate_stories,
    match_story_profile,
)

NOW = datetime(2026, 9, 12, 8, tzinfo=UTC)
ENTITIES = [
    {
        "id": "european-central-bank",
        "aliases": ["european central bank", "ecb", "governing council"],
    },
    {"id": "federal-reserve", "aliases": ["federal reserve", "fed", "fomc"]},
    {"id": "china-nbs", "aliases": ["national bureau of statistics", "nbs china"]},
    {"id": "nvidia", "aliases": ["nvidia"]},
    {"id": "apple", "aliases": ["apple"]},
]


def profile(**updates: object) -> DocumentStoryProfile:
    payload: dict[str, object] = {
        "document_id": "document-a",
        "event_type": EventType.POLICY,
        "canonical_entity_ids": ("european-central-bank",),
        "topics": (Topic.RATES,),
        "region": "EU",
        "published_at": NOW - timedelta(hours=2),
        "normalized_title_tokens": (
            "decision",
            "monetary",
            "policy",
        ),
        "action_terms": ("rate_decision",),
        "strict_numeric_facts": ("policy_rate_pct=2.00",),
        "authority_id": "european-central-bank",
        "release_domain": "rates",
        "reference_period": "2026-09",
        "source_category": SourceCategory.OFFICIAL,
        "source_role": SourceRole.PRIMARY,
        "source_tier": SourceTier.A,
    }
    payload.update(updates)
    return DocumentStoryProfile.model_validate(payload)


def story(**updates: object) -> Story:
    identity = profile()
    payload: dict[str, object] = {
        "id": "story-a",
        "identity_seed_document_id": identity.document_id,
        "identity_profile": identity,
        "presentation_document_id": identity.document_id,
        "event_type": identity.event_type,
        "canonical_entity_ids": identity.canonical_entity_ids,
        "topics": identity.topics,
        "region": identity.region,
        "source_document_ids": (identity.document_id,),
        "earliest_trusted_published_at": identity.published_at,
        "latest_published_at": identity.published_at,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "rule_version": "story-v1",
    }
    payload.update(updates)
    return Story.model_validate(payload)


def membership(**updates: object) -> StoryDocumentMembership:
    payload: dict[str, object] = {
        "id": "membership-a",
        "story_id": "story-a",
        "source_document_id": "document-a",
        "match_method": "identity_seed",
        "rule_version": "story-v1",
        "first_linked_at": NOW,
    }
    payload.update(updates)
    return StoryDocumentMembership.model_validate(payload)


def document(
    title: str,
    *,
    provider: str = "provider-a",
    publisher: str = "Publisher A",
    url_suffix: str = "a",
    content: str = "",
    published_at: datetime | None = None,
    region: str | None = "US",
    category: SourceCategory = SourceCategory.OFFICIAL,
    role: SourceRole = SourceRole.PRIMARY,
    tier: SourceTier = SourceTier.A,
    collected_at: datetime | None = None,
):
    return make_document(
        provider=provider,
        tier=tier,
        publisher=publisher,
        url=f"https://{provider}.example/{url_suffix}",
        title=title,
        content=content or title,
        published_at=published_at or NOW,
        collected_at=collected_at or NOW,
        source_category=category,
        source_role=role,
        region=region,
    )


@pytest.mark.parametrize(
    ("factory", "extra"),
    [
        (profile, {"unexpected": "value"}),
        (story, {"unexpected": "value"}),
        (membership, {"unexpected": "value"}),
    ],
)
def test_story_models_are_frozen_and_reject_extra_fields(factory, extra) -> None:
    with pytest.raises(ValidationError):
        factory(**extra)

    instance = factory()
    with pytest.raises(ValidationError):
        instance.id = "changed"


@pytest.mark.parametrize(
    ("factory", "updates"),
    [
        (profile, {"published_at": datetime(2026, 9, 12, 8)}),
        (story, {"first_seen_at": datetime(2026, 9, 12, 8)}),
        (membership, {"first_linked_at": datetime(2026, 9, 12, 8)}),
    ],
)
def test_story_models_reject_naive_timestamps(factory, updates) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        factory(**updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"canonical_entity_ids": ("z", "a")},
        {"topics": (Topic.US, Topic.RATES)},
        {"source_document_ids": ("document-b", "document-a")},
        {"source_document_ids": ("document-a", "document-a")},
    ],
)
def test_story_requires_canonical_ordered_unique_closure(updates) -> None:
    with pytest.raises(ValidationError, match="canonical"):
        story(**updates)


def test_story_requires_identity_seed_to_match_profile_and_membership() -> None:
    with pytest.raises(ValidationError, match="identity seed"):
        story(identity_seed_document_id="different")

    with pytest.raises(ValidationError, match="identity seed"):
        story(source_document_ids=("document-b",))


def test_story_requires_valid_temporal_order() -> None:
    with pytest.raises(ValidationError, match="publication"):
        story(
            earliest_trusted_published_at=NOW,
            latest_published_at=NOW - timedelta(hours=1),
        )

    with pytest.raises(ValidationError, match="seen"):
        story(first_seen_at=NOW, last_seen_at=NOW - timedelta(seconds=1))


def test_near_identical_story_titles_match_across_providers() -> None:
    first = build_document_story_profile(
        document(
            "NVIDIA launches Blackwell platform for enterprise AI",
            content="NVIDIA launched the new product platform.",
        ),
        ENTITIES,
    )
    second = build_document_story_profile(
        document(
            "NVIDIA launches Blackwell platform for enterprise AI customers",
            provider="provider-b",
            publisher="Publisher B",
            url_suffix="b",
            content="The NVIDIA product launch expands enterprise computing.",
        ),
        ENTITIES,
    )

    match = match_story_profile(second, first)

    assert match.match_method == "near_identical_title"
    assert match.rejection_reasons == ()


def test_entity_event_action_path_allows_conservative_title_variation() -> None:
    first = build_document_story_profile(
        document(
            "NVIDIA launches Blackwell enterprise AI platform",
            content="NVIDIA announced a product launch for enterprise data centers.",
        ),
        ENTITIES,
    )
    second = build_document_story_profile(
        document(
            "Blackwell computing arrives for NVIDIA enterprise customers",
            provider="provider-b",
            url_suffix="b",
            content="The NVIDIA product launch brings Blackwell to AI infrastructure.",
        ),
        ENTITIES,
    )

    match = match_story_profile(second, first)

    assert match.match_method == "entity_event_action"


def test_differently_worded_policy_release_matches_structured_identity() -> None:
    first = build_document_story_profile(
        document(
            "ECB interest rate decision for September 2026",
            content="The ECB kept its policy rate at 2.00 percent.",
            region="EU",
        ),
        ENTITIES,
    )
    second = build_document_story_profile(
        document(
            "Governing Council maintains financing conditions",
            provider="provider-b",
            url_suffix="b",
            content=(
                "The September 2026 monetary policy decision maintained the benchmark "
                "rate at 2.00 percent."
            ),
            region="EU",
        ),
        ENTITIES,
    )

    match = match_story_profile(second, first)

    assert match.match_method == "policy_release"


def test_macro_release_matches_on_authority_domain_and_reference_period() -> None:
    first = build_document_story_profile(
        document(
            "National Bureau of Statistics Consumer Price Index August 2026",
            content="Consumer price index inflation was 2.1 percent in August 2026.",
            region="CN",
        ),
        ENTITIES,
    )
    second = build_document_story_profile(
        document(
            "NBS China reports August 2026 inflation data",
            provider="provider-b",
            url_suffix="b",
            content="August 2026 CPI inflation was 2.1 percent.",
            region="CN",
        ),
        ENTITIES,
    )

    match = match_story_profile(second, first)

    assert match.match_method == "macro_release"


@pytest.mark.parametrize(
    ("left", "right", "expected_reason"),
    [
        (
            document("NVIDIA earnings revenue rises", content="NVIDIA earnings profit rose."),
            document(
                "NVIDIA launches analytics product",
                provider="provider-b",
                url_suffix="b",
            ),
            "incompatible_event_type",
        ),
        (
            document("NVIDIA launches AI product"),
            document(
                "SEC investigation of NVIDIA conduct",
                provider="provider-b",
                url_suffix="b",
            ),
            "incompatible_event_type",
        ),
        (
            document("NVIDIA launches AI product", region="US"),
            document(
                "NVIDIA launches AI product",
                provider="provider-b",
                url_suffix="b",
                region="EU",
            ),
            "incompatible_region",
        ),
        (
            document("NVIDIA launches AI product"),
            document(
                "Apple launches AI product",
                provider="provider-b",
                url_suffix="b",
            ),
            "conflicting_entities",
        ),
        (
            document(
                "ECB monetary policy decision September 2026",
                content="ECB policy rate decision.",
                region="EU",
            ),
            document(
                "Federal Reserve monetary policy decision September 2026",
                provider="provider-b",
                url_suffix="b",
                content="Federal Reserve policy rate decision.",
                region="EU",
            ),
            "incompatible_authority",
        ),
        (
            document(
                "NBS China CPI inflation August 2026",
                content="Consumer price index inflation data.",
                region="CN",
            ),
            document(
                "NBS China employment August 2026",
                provider="provider-b",
                url_suffix="b",
                content="Employment report and labor market data.",
                region="CN",
            ),
            "incompatible_release_domain",
        ),
        (
            document(
                "NBS China CPI inflation August 2026",
                content="Consumer price index inflation data.",
                region="CN",
            ),
            document(
                "NBS China CPI inflation July 2026",
                provider="provider-b",
                url_suffix="b",
                content="Consumer price index inflation data.",
                region="CN",
            ),
            "incompatible_reference_period",
        ),
        (
            document("NVIDIA launches AI data center product"),
            document(
                "NVIDIA launches crude oil energy product",
                provider="provider-b",
                url_suffix="b",
            ),
            "incompatible_topic",
        ),
        (
            document("Apple launches Vision Pro product"),
            document(
                "Apple receives trial result approval for Vision Pro",
                provider="provider-b",
                url_suffix="b",
            ),
            "incompatible_action",
        ),
        (
            document("NVIDIA launches AI product"),
            document(
                "NVIDIA launches AI product",
                provider="provider-b",
                url_suffix="b",
                published_at=NOW + timedelta(hours=72, seconds=1),
            ),
            "outside_time_window",
        ),
        (
            document(
                "ECB policy rate decision September 2026",
                content="ECB kept the policy rate at 2.00 percent.",
                region="EU",
            ),
            document(
                "ECB policy rate decision September 2026",
                provider="provider-b",
                url_suffix="b",
                content="ECB kept the policy rate at 3.00 percent.",
                region="EU",
            ),
            "numeric_conflict",
        ),
    ],
)
def test_hard_rejection_gates(left, right, expected_reason: str) -> None:
    identity = build_document_story_profile(left, ENTITIES)
    candidate = build_document_story_profile(right, ENTITIES)

    match = match_story_profile(candidate, identity)

    assert match.match_method is None
    assert expected_reason in match.rejection_reasons


def test_unrelated_numbers_do_not_create_numeric_conflict() -> None:
    first = build_document_story_profile(
        document(
            "Apple launches Product Aurora",
            content="Apple launched Product Aurora in 12 countries.",
        ),
        ENTITIES,
    )
    second = build_document_story_profile(
        document(
            "Apple launches Product Aurora globally",
            provider="provider-b",
            url_suffix="b",
            content="The Apple product launch reaches 18 retail markets.",
        ),
        ENTITIES,
    )

    assert first.strict_numeric_facts == ()
    assert second.strict_numeric_facts == ()
    assert match_story_profile(second, first).match_method is not None


def test_two_same_day_product_launches_for_same_company_remain_separate() -> None:
    vision = build_document_story_profile(
        document("Apple launches Vision Pro product"), ENTITIES
    )
    airpods = build_document_story_profile(
        document(
            "Apple launches AirPods Max product",
            provider="provider-b",
            url_suffix="b",
        ),
        ENTITIES,
    )

    match = match_story_profile(airpods, vision)

    assert match.match_method is None
    assert "insufficient_matching_evidence" in match.rejection_reasons


def test_exact_72_hour_boundary_remains_time_compatible() -> None:
    first = build_document_story_profile(
        document("Apple launches Vision Pro product"), ENTITIES
    )
    boundary = build_document_story_profile(
        document(
            "Apple launches Vision Pro product",
            provider="provider-b",
            url_suffix="b",
            published_at=NOW + timedelta(hours=72),
        ),
        ENTITIES,
    )

    assert match_story_profile(boundary, first).match_method == "near_identical_title"


def test_other_is_singleton_without_exceptionally_strong_title_identity() -> None:
    first = build_document_story_profile(
        document("Apple community program reaches developers"), ENTITIES
    )
    weak = build_document_story_profile(
        document(
            "Apple community initiative expands for developers",
            provider="provider-b",
            url_suffix="b",
        ),
        ENTITIES,
    )
    exact = build_document_story_profile(
        document(
            "Apple community program reaches developers",
            provider="provider-c",
            url_suffix="c",
        ),
        ENTITIES,
    )

    assert first.event_type is EventType.OTHER
    assert match_story_profile(weak, first).match_method is None
    assert match_story_profile(exact, first).match_method == "near_identical_title"


def test_consolidation_is_independent_of_input_order() -> None:
    primary = document(
        "NVIDIA launches Blackwell platform for enterprise AI",
        provider="nvidia-primary",
        publisher="NVIDIA",
        category=SourceCategory.COMPANY_PRIMARY,
    )
    secondary = document(
        "NVIDIA launches Blackwell platform for enterprise AI customers",
        provider="news-secondary",
        publisher="Independent News",
        url_suffix="b",
        category=SourceCategory.PROFESSIONAL_NEWS,
        role=SourceRole.SECONDARY,
        tier=SourceTier.B,
    )

    first = consolidate_stories([primary, secondary], ENTITIES, now=NOW)
    second = consolidate_stories([secondary, primary], ENTITIES, now=NOW)

    assert first.stories == second.stories
    assert first.memberships == second.memberships
    assert len(first.stories) == 1
    assert first.stories[0].identity_seed_document_id == primary.id
    assert first.stories[0].source_document_ids == tuple(sorted([primary.id, secondary.id]))
    assert first.diagnostics["match_method_counts"] == {
        "identity_seed": 1,
        "near_identical_title": 1,
    }


def test_later_better_source_changes_presentation_without_rewriting_identity() -> None:
    secondary = document(
        "NVIDIA launches Blackwell platform for enterprise AI",
        provider="news-secondary",
        publisher="Independent News",
        category=SourceCategory.PROFESSIONAL_NEWS,
        role=SourceRole.SECONDARY,
        tier=SourceTier.B,
    )
    initial = consolidate_stories([secondary], ENTITIES, now=NOW)
    original = initial.stories[0]
    primary = document(
        "NVIDIA launches Blackwell enterprise AI platform",
        provider="nvidia-primary",
        publisher="NVIDIA",
        url_suffix="primary",
        category=SourceCategory.COMPANY_PRIMARY,
        published_at=NOW - timedelta(hours=1),
        collected_at=NOW + timedelta(hours=3),
    )

    updated = consolidate_stories(
        [primary],
        ENTITIES,
        existing_stories=initial.stories,
        existing_memberships=initial.memberships,
        known_documents=[secondary],
        now=NOW + timedelta(hours=3),
    )
    result = updated.stories[0]

    assert result.id == original.id
    assert result.identity_seed_document_id == secondary.id
    assert result.identity_profile == original.identity_profile
    assert result.presentation_document_id == primary.id
    assert result.earliest_trusted_published_at == primary.published_at
    assert result.first_seen_at == secondary.collected_at
    assert result.last_seen_at == primary.collected_at
    assert updated.diagnostics["documents_attached_to_existing_stories"] == 1


def test_content_revision_reuses_existing_membership_and_story_identity() -> None:
    original_document = document(
        "Apple launches Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        category=SourceCategory.COMPANY_PRIMARY,
    )
    initial = consolidate_stories([original_document], ENTITIES, now=NOW)
    revised = document(
        "Apple launches updated Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        content="Apple updated the Product Aurora launch details.",
        category=SourceCategory.COMPANY_PRIMARY,
        collected_at=NOW + timedelta(hours=4),
    )
    assert revised.id == original_document.id
    assert revised.content_hash != original_document.content_hash

    updated = consolidate_stories(
        [revised],
        ENTITIES,
        existing_stories=initial.stories,
        existing_memberships=initial.memberships,
        known_documents=[original_document],
        now=NOW + timedelta(hours=4),
    )

    assert updated.stories[0].id == initial.stories[0].id
    assert updated.stories[0].identity_profile == initial.stories[0].identity_profile
    assert updated.memberships == initial.memberships
    assert updated.diagnostics["match_method_counts"] == {"existing_membership": 1}


def test_content_revision_cannot_move_story_occurrence_later() -> None:
    original = document(
        "Apple launches Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        category=SourceCategory.COMPANY_PRIMARY,
        published_at=NOW - timedelta(hours=2),
    )
    initial = consolidate_stories([original], ENTITIES, now=NOW)
    revised = document(
        "Apple launches updated Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        content="Apple updated the Product Aurora launch details.",
        category=SourceCategory.COMPANY_PRIMARY,
        published_at=NOW + timedelta(hours=2),
        collected_at=NOW + timedelta(hours=4),
    )

    updated = consolidate_stories(
        [revised],
        ENTITIES,
        existing_stories=initial.stories,
        existing_memberships=initial.memberships,
        known_documents=[original],
        now=NOW + timedelta(hours=4),
    )

    assert (
        updated.stories[0].earliest_trusted_published_at
        == initial.stories[0].earliest_trusted_published_at
    )


def test_ambiguous_match_fails_closed_into_separate_story() -> None:
    first_document = document(
        "NVIDIA launches Product Aurora for enterprise AI",
        provider="provider-a",
        url_suffix="first",
    )
    second_document = document(
        "NVIDIA launches Product Aurora for enterprise AI",
        provider="provider-b",
        url_suffix="second",
    )
    first = consolidate_stories([first_document], ENTITIES, now=NOW)
    second = consolidate_stories([second_document], ENTITIES, now=NOW)
    candidate = document(
        "NVIDIA launches Product Aurora for enterprise AI",
        provider="provider-c",
        url_suffix="candidate",
    )

    result = consolidate_stories(
        [candidate],
        ENTITIES,
        existing_stories=[*first.stories, *second.stories],
        existing_memberships=[*first.memberships, *second.memberships],
        known_documents=[first_document, second_document],
        now=NOW + timedelta(hours=1),
    )

    assert len(result.stories) == 1
    assert result.stories[0].identity_seed_document_id == candidate.id
    assert result.memberships[0].match_method == "ambiguous_singleton"
    assert result.diagnostics["ambiguous_matches"] == 1


def test_consolidation_does_not_chain_transitive_matches() -> None:
    first = document("NVIDIA launches alpha beta gamma delta epsilon product", url_suffix="a")
    bridge = document(
        "NVIDIA launches alpha beta gamma delta zeta product",
        url_suffix="b",
    )
    distant = document(
        "NVIDIA launches zeta theta iota kappa product",
        url_suffix="c",
    )

    result = consolidate_stories([first, bridge, distant], ENTITIES, now=NOW)

    assert len(result.stories) == 2
    story_by_seed = {item.identity_seed_document_id: item for item in result.stories}
    assert story_by_seed[first.id].source_document_ids == tuple(sorted([first.id, bridge.id]))
    assert story_by_seed[distant.id].source_document_ids == (distant.id,)


def test_story_diagnostics_are_aggregate_and_provider_neutral() -> None:
    primary = document(
        "Apple launches Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        category=SourceCategory.COMPANY_PRIMARY,
    )
    secondary = document(
        "Apple launches Product Aurora",
        provider="news-secondary",
        publisher="Independent News",
        url_suffix="b",
        category=SourceCategory.PROFESSIONAL_NEWS,
        role=SourceRole.SECONDARY,
        tier=SourceTier.B,
    )

    result = consolidate_stories([primary, secondary], ENTITIES, now=NOW)

    assert result.diagnostics == {
        "documents_considered": 2,
        "stories_created": 1,
        "documents_attached_to_existing_stories": 0,
        "singleton_stories": 0,
        "multi_document_stories": 1,
        "multi_source_stories": 1,
        "rejected_match_attempts": 0,
        "rejected_by_reason": {},
        "ambiguous_matches": 0,
        "max_story_size": 2,
        "match_method_counts": {"identity_seed": 1, "near_identical_title": 1},
        "source_category_counts": {"company_primary": 1, "professional_news": 1},
        "source_role_counts": {"primary": 1, "secondary": 1},
        "rule_version": STORY_RULE_VERSION,
    }


def test_story_event_projection_uses_document_scoped_provenance() -> None:
    primary = document(
        "NVIDIA launches Blackwell platform for enterprise AI",
        provider="nvidia-primary",
        publisher="NVIDIA",
        category=SourceCategory.COMPANY_PRIMARY,
    )
    secondary = document(
        "NVIDIA launches Blackwell platform for enterprise AI customers",
        provider="news-secondary",
        publisher="Independent News",
        url_suffix="b",
        category=SourceCategory.PROFESSIONAL_NEWS,
        role=SourceRole.SECONDARY,
        tier=SourceTier.B,
    )
    consolidated = consolidate_stories([primary, secondary], ENTITIES, now=NOW)

    events, claims, evidence = extract_story_events(
        consolidated.stories,
        [primary, secondary],
        NOW,
    )

    assert len(events) == 1
    assert len(claims) == len(evidence) == 2
    event = events[0]
    assert event.id == stable_id("event", consolidated.stories[0].id)
    assert event.event_key == f"story:{consolidated.stories[0].id}"
    assert event.source_document_ids == sorted([primary.id, secondary.id])
    assert event.claim_ids == sorted(item.id for item in claims)
    assert event.evidence_ids == sorted(item.id for item in evidence)
    evidence_by_document = {item.source_id: item for item in evidence}
    assert len(evidence_by_document) == 2
    for claim in claims:
        assert len(claim.evidence_ids) == 1
        supporting = next(item for item in evidence if item.id == claim.evidence_ids[0])
        assert supporting.source_id in event.source_document_ids
        assert claim.text == next(
            item.title for item in [primary, secondary] if item.id == supporting.source_id
        )


def test_presentation_change_does_not_rewrite_story_event_identity() -> None:
    secondary = document(
        "NVIDIA launches Blackwell platform for enterprise AI",
        provider="news-secondary",
        publisher="Independent News",
        category=SourceCategory.PROFESSIONAL_NEWS,
        role=SourceRole.SECONDARY,
        tier=SourceTier.B,
    )
    initial = consolidate_stories([secondary], ENTITIES, now=NOW)
    first_events, _, _ = extract_story_events(initial.stories, [secondary], NOW)
    primary = document(
        "NVIDIA launches Blackwell enterprise AI platform",
        provider="nvidia-primary",
        publisher="NVIDIA",
        url_suffix="primary",
        category=SourceCategory.COMPANY_PRIMARY,
        published_at=NOW - timedelta(hours=1),
        collected_at=NOW + timedelta(hours=3),
    )
    updated = consolidate_stories(
        [primary],
        ENTITIES,
        existing_stories=initial.stories,
        existing_memberships=initial.memberships,
        known_documents=[secondary],
        now=NOW + timedelta(hours=3),
    )
    updated_events, _, _ = extract_story_events(
        updated.stories,
        [secondary, primary],
        NOW + timedelta(hours=3),
    )

    assert updated_events[0].id == first_events[0].id
    assert updated_events[0].event_key == first_events[0].event_key
    assert updated_events[0].title == primary.title
    assert updated_events[0].occurred_at == primary.published_at


def test_content_revision_keeps_event_and_claim_identity_but_refreshes_evidence() -> None:
    original = document(
        "Apple launches Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        category=SourceCategory.COMPANY_PRIMARY,
    )
    initial = consolidate_stories([original], ENTITIES, now=NOW)
    first_events, first_claims, first_evidence = extract_story_events(
        initial.stories, [original], NOW
    )
    revised = document(
        "Apple launches updated Product Aurora",
        provider="apple-primary",
        publisher="Apple",
        content="Apple updated the Product Aurora launch details.",
        category=SourceCategory.COMPANY_PRIMARY,
        collected_at=NOW + timedelta(hours=4),
    )
    updated = consolidate_stories(
        [revised],
        ENTITIES,
        existing_stories=initial.stories,
        existing_memberships=initial.memberships,
        known_documents=[original],
        now=NOW + timedelta(hours=4),
    )
    second_events, second_claims, second_evidence = extract_story_events(
        updated.stories, [revised], NOW + timedelta(hours=4)
    )

    assert second_events[0].id == first_events[0].id
    assert second_claims[0].id == first_claims[0].id
    assert second_evidence[0].id != first_evidence[0].id
    assert second_claims[0].evidence_ids == [second_evidence[0].id]


def test_story_event_projection_fails_when_member_document_is_missing() -> None:
    source = document("Apple launches Product Aurora")
    consolidated = consolidate_stories([source], ENTITIES, now=NOW)

    with pytest.raises(ValueError, match="missing SourceDocument"):
        extract_story_events(consolidated.stories, [], NOW)
