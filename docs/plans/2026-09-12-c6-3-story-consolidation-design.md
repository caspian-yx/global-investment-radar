# C6.3 Story Consolidation Design

## Purpose

C6.3 determines which canonical `SourceDocument` records describe the same real-world
story. It does not decide importance, market causality, user relevance, or AI similarity.
The safety preference is always false negatives over false merges.

## Architecture

```text
provider collection
→ exact SourceDocument deduplication
→ provider-neutral DocumentStoryProfile
→ persistent deterministic Story consolidation
→ Event projection
→ unchanged C6
→ unchanged C2
→ unchanged optional AI/report
```

The existing title-only `cluster_documents()` algorithm is removed from the production
path. Story matching uses immutable identity profiles and explicit match paths. A Story is
persistent; its identity seed never changes, while a better source may become its presentation
anchor.

## Canonical models

`DocumentStoryProfile` is frozen and strict. It contains only canonical document fields and
deterministic derivations: document ID, EventType, canonical entity IDs, topics, region,
publication time, normalized title tokens, semantic action terms, strict numeric facts,
authority, release domain/reference period, source category/role/tier.

`Story` is frozen and strict. It records its immutable ID and seed profile, current presentation
document, deterministic document closure, EventType, canonical entities/topics/region,
earliest trusted and latest publication times, first/last seen, and `story-v1` rule version.

`StoryDocumentMembership` is frozen and strict. One deterministic membership ID is derived
from `(rule_version, source_document_id)`, making one active story per document enforceable.

## Matching contract

All paths first apply hard rejection gates: incompatible EventType, explicit region conflict,
conflicting entity-centric identities, incompatible authority or release domain/reference
period, clear same-dimension numeric conflict, materially incompatible action semantics, or a
publication distance greater than 72 hours.

- Path A (`near_identical_title`) accepts strong normalized-title agreement with no conflicts.
- Path B (`entity_event_action`) accepts entity-centric documents with a shared canonical
  entity, compatible actions/topics, conservative supporting title evidence, and no conflict.
- Path C (`policy_release` / `macro_release`) uses matching authority/region, release domain,
  reference period where available, action semantics, and time. It does not universally require
  high title similarity.
- `OTHER` remains singleton unless normalized identity is exact or exceptionally strong.

A candidate must match the immutable seed profile. There is no fuzzy transitive union. If it
matches multiple Stories, it becomes a separate Story and increments an ambiguity diagnostic.
No Story-to-Story automatic merge is performed.

Numeric extraction is deliberately narrow. Only values with an identified policy-rate,
inflation, unemployment, or GDP dimension can veto a match. Generic number differences are
ignored.

## Identity and presentation

For a new initial set, documents are processed in a stable total order using source category,
role, tier, publication time, canonical URL, and document ID. The first member becomes the
immutable identity seed:

```text
story_id = stable_id("story", "story-v1|" + identity_seed_document_id)
event_id = stable_id("event", story_id)
event_key = "story:" + story_id
```

Existing membership always wins for the same canonical document ID, including content
revisions. A newly attached Tier A, primary, earlier, or clearer source can become the
presentation document without changing Story/Event identity. Different bootstrap histories can
still create different initial IDs; C6.3 documents this unavoidable tradeoff and does not hide
it with unsafe automatic merging.

## Persistence and compatibility

Migration `003_c6_3_story_consolidation` adds `stories` and `story_documents`. It is additive,
repeatable, validates required columns/indexes/constraints, and never rewrites historical Event
rows. Existing historical Events and new Story-derived Events may coexist; only current touched
Stories are projected into the current run.

The repository's current production-shaped SQLite database contains migrations 001 and 002 and
no Story tables, so the migration begins with an empty Story registry rather than speculative
backfill.

## Provenance and temporal semantics

Every member document creates its own Evidence and FACT Claim. A Claim references only that
document's Evidence. The Event references the sorted exact union of member document, Claim, and
Evidence IDs.

Story occurrence is the earliest publication among Tier A/B members, with an all-member fallback
only when no trusted member exists. Later corroboration updates latest publication and last seen,
not occurrence. A later-discovered earlier trusted source may move occurrence earlier without
changing identity. C2 continues checking each evidence document's own publication time.

## Observability

Safe aggregate diagnostics include documents considered, Stories created, documents attached to
existing Stories, singleton/multi-document/multi-source Stories, rejected attempts by reason,
ambiguous matches, maximum Story size, match-method counts, source category/role diversity, and
rule version. Titles, article bodies, secrets, and provider metadata are not logged.

## Out of scope

Embeddings, vector databases, LLM matching, multilingual fuzzy matching, automatic Story merges,
final corroboration scoring, Importance redesign, C2/C3 changes, broader C6 channels, UI changes,
and new providers remain out of scope.
