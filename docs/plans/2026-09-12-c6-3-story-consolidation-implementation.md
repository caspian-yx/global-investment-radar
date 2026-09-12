# C6.3 Story Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace weak title-only document clustering with a persistent, conservative, provenance-preserving deterministic Story layer.

**Architecture:** Build immutable per-document semantic profiles, consolidate against immutable Story seeds through three explicit deterministic match paths, persist Story identity/membership, and project one existing-domain Event per touched Story. Keep C6, C2, AI, report, provider, and importance contracts unchanged.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy 2, SQLite/PostgreSQL-compatible SQLAlchemy Core, pytest, Ruff, strict mypy.

**Spec:** `docs/plans/2026-09-12-c6-3-story-consolidation-design.md`

## Global Constraints

- Safety preference: false negative over false merge.
- Rule version is exactly `story-v1`.
- No provider metadata, embeddings, LLM, vector database, translation, or automatic Story merge.
- C2/C3 contracts and C6 authorization rules remain unchanged.
- Migration is additive and does not backfill or rewrite historical Events.
- Do not commit, push, or run production.

---

### Task 1: Strict Story domain models

**Files:**
- Modify: `src/radar/domain.py`
- Create: `tests/test_story.py`

**Interfaces:**
- Produces: `DocumentStoryProfile`, `Story`, and `StoryDocumentMembership` frozen Pydantic models with `extra="forbid"` and timezone/canonical-order validation.

- [x] Write tests that reject extra fields, naive timestamps, duplicate/unsorted ID tuples, mismatched seed IDs, and invalid Story temporal order.
- [x] Run `pytest tests/test_story.py -v` and verify collection/import failure because the models do not exist.
- [x] Add the minimal strict models and validators.
- [x] Run the targeted tests and verify they pass.

### Task 2: Deterministic semantic profiles and matching paths

**Files:**
- Create: `src/radar/story.py`
- Modify: `tests/test_story.py`

**Interfaces:**
- Produces: `STORY_RULE_VERSION`, `build_document_story_profile(document, entity_config)`, `match_story_profile(candidate, identity)`, and a typed match result carrying either a method or canonical rejection reasons.
- Consumes: existing `classify_event`, `extract_entities`, `extract_topics`, canonical `SourceDocument` fields only.

- [x] Add failing positive tests for near-identical cross-provider titles, entity/event/action matching, differently worded policy decisions, and macro release identity.
- [x] Add failing rejection tests for EventType, region, authority, release domain/period, action, 72-hour boundary, entity conflict, and strict same-dimension numeric conflict.
- [x] Add failing tests proving generic number differences are ignored, OTHER is singleton by default, and strong OTHER identity is the sole exception.
- [x] Implement normalized tokens, action semantics, narrow numeric dimensions, authority/release/reference-period extraction, hard gates, and Paths A/B/C.
- [x] Run `pytest tests/test_story.py -v` until green; refactor without broadening thresholds.

### Task 3: Deterministic consolidation and stable identity

**Files:**
- Modify: `src/radar/story.py`
- Modify: `tests/test_story.py`

**Interfaces:**
- Produces: `StoryConsolidationResult` and `consolidate_stories(documents, entity_config, existing_stories, existing_memberships, known_documents, now)`.
- Guarantees: stable total ordering; existing membership precedence; immutable seed ID; deterministic presentation selection; ambiguity isolation; no transitive chaining.

- [x] Add failing permutation tests for Story IDs, memberships, ordering, and presentation selection.
- [x] Add failing lifecycle tests for content revision, late corroboration, better presentation source, earlier trusted source, first/last seen, and same-document membership reuse.
- [x] Add failing ambiguity tests where one candidate matches two persisted Story seeds and must create a separate Story.
- [x] Implement stable seed/membership IDs, existing-story matching, aggregate Story updates, presentation selection, and diagnostics.
- [x] Run targeted tests and verify green.

### Task 4: Additive Story persistence migration

**Files:**
- Modify: `src/radar/storage.py`
- Modify: `tests/test_provenance.py`

**Interfaces:**
- Produces: `STORY_CONSOLIDATION_MIGRATION`, `stories_table`, `story_documents_table`, `save_stories`, `save_story_memberships`, `load_stories`, `load_story_memberships`, and `load_source_documents`.
- Consumes: strict Story models and deterministic membership IDs.

- [x] Add failing fresh-schema, C2.1-upgrade, idempotency, malformed-marker/schema, unique-document-membership, round-trip, and legacy-row-preservation tests.
- [x] Verify RED against the absent migration/tables.
- [x] Add tables, unique/index constraints, migration validation, safe loaders, immutable identity checks, and persistence methods.
- [x] Run migration/provenance tests and verify green.

### Task 5: Story-to-Event projection with document-scoped provenance

**Files:**
- Modify: `src/radar/deterministic.py`
- Modify: `tests/test_deterministic.py`
- Modify: `tests/test_story.py`

**Interfaces:**
- Produces: `extract_story_events(stories, documents, now)` returning existing `Event`, `Claim`, and `Evidence` types.
- Guarantees: one Claim/Evidence per document, exact sorted closure, stable Story-derived Event ID/key, presentation-only title, earliest trusted occurrence, and unchanged importance formula.

- [x] Add failing tests proving each Claim cites only its own Evidence and Event closure is exact/sorted.
- [x] Add failing stable Event-ID tests across presentation changes and content revisions.
- [x] Implement projection and remove title-only clustering from the production interface.
- [x] Run deterministic/Story tests and verify green.

### Task 6: Pipeline integration and observability

**Files:**
- Modify: `src/radar/pipeline.py`
- Modify: `tests/test_pipeline.py`
- Modify: `README.md`

**Interfaces:**
- Replaces: production `cluster_documents()` call with load → consolidate → project → persist.
- Preserves: existing C6 and C2 function signatures and ordering.

- [x] Add failing two-run integration tests for persisted identity, late source attachment, no historical Event rewrite, aggregate diagnostics, and post-target evidence exclusion.
- [x] Implement Story loading/consolidation/projection and safe aggregate logging; persist documents, Stories, memberships, then existing downstream objects.
- [x] Update architecture/persistence documentation without changing runtime scope.
- [x] Run pipeline, C6, C2, report, and storage regression tests.

### Task 7: Full verification and scope audit

**Files:**
- Verify all changed files; no commit.

- [x] Run full `pytest` with Git Bash available on PATH.
- [x] Run `ruff check .`.
- [x] Run `mypy --strict src/radar`.
- [x] Run `uv lock --check`.
- [x] Run `git diff --check`.
- [x] Inspect `git status --short` and `git diff --stat`; confirm no C2/C3/C6 rule changes, provider changes, production run, commit, or push.
