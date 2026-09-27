# C9 Production Latency Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the C9 production pipeline complete safely within its 25-minute outer ceiling by removing row-at-a-time PostgreSQL persistence and exposing actionable stage timings.

**Architecture:** Keep the canonical pipeline and snapshots unchanged. Convert storage hot paths to one validated, set-oriented transaction per artifact type; add stage/request timing logs; and gate SMTP behind accepted report completion.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Pydantic 2, pytest, Ruff, mypy, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-c9-production-latency-hardening-design.md`

## Global Constraints

- Preserve the 25-minute GitHub Actions outer safety ceiling.
- Preserve canonical Evidence, Claim, Event, Story, ranking, snapshot, and retry identities.
- Preserve C9.0 recipient normalization and a single SMTP envelope.
- Do not modify Web renderer or Admin Console behavior.
- Do not add migrations, dependencies, provider fabrication, or unrelated formatting churn.
- Use TDD: observe each new behavioral test fail before production edits.

---

### Task 1: Reproduce the PostgreSQL transaction amplification contract

**Files:**
- Create: `tests/test_storage_batching.py`
- Modify: `tests/test_c7_storage.py`

**Interfaces:**
- Consumes: `RadarStore.save_documents`, `save_stories`, `save_story_memberships`, and `save_c7_scores`.
- Produces: behavioral tests requiring one transaction per collection while preserving refresh and immutable identity semantics.

- [ ] **Step 1: Write failing transaction-count tests**

Use SQLAlchemy's `begin` event against a real SQLite store after initialization.
Persist multiple rows and assert one transaction for each collection-level save,
plus unchanged round-trip payloads and conflict failures.

- [ ] **Step 2: Verify RED**

Run the targeted tests and confirm current code reports N transactions for N rows.

- [ ] **Step 3: Commit test evidence with the implementation task**

Do not modify production code until the RED result is recorded.

### Task 2: Implement set-oriented persistence

**Files:**
- Modify: `src/radar/storage.py`
- Test: `tests/test_storage_batching.py`
- Test: `tests/test_c7_storage.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Produces: `RadarStore._upsert_many(table, rows)` and collection saves that use one transaction.
- Preserves: existing public method signatures and validation errors.

- [ ] **Step 1: Add the minimal generic batch upsert helper**

Materialize rows, reject duplicate IDs with conflicting payloads, preload existing
IDs once, batch insert missing rows, and batch update existing rows using bound
parameters inside one transaction.

- [ ] **Step 2: Batch simple artifact saves**

Apply the helper to documents, evidence, claims, events, driver candidates, and
market observations.

- [ ] **Step 3: Batch validated Story and membership saves**

Preload document/story/membership identities, run the existing immutable checks in
memory, then batch write in one transaction per method.

- [ ] **Step 4: Batch C7 and C8 evaluations**

Preload exact IDs and natural identities, preserve deterministic conflict checks,
then batch insert/update without per-row transactions.

- [ ] **Step 5: Verify GREEN**

Run batching, C7 storage, provenance, and pipeline targeted tests.

### Task 3: Add latency observability and delivery safety

**Files:**
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/llm.py`
- Modify: `src/radar/config.py` only if a bounded setting is proven necessary.
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_deepseek_llm.py`

**Interfaces:**
- Produces: structured stage logs with stage elapsed and total elapsed values.
- Produces: safe DeepSeek request outcome logs without prompts, payloads, or secrets.
- Preserves: deterministic fallback and no-retry provider behavior.

- [ ] **Step 1: Write failing observability and delivery-order tests**

Assert timing records for persistence and AI/localization boundaries, partial AI
fallback, and that an insufficient or interrupted report never calls the email
sender.

- [ ] **Step 2: Verify RED**

Run the focused tests and confirm the missing timing/delivery contracts fail.

- [ ] **Step 3: Implement stage timing and request outcome logs**

Use monotonic time; log stage/request names, outcome, timeout phase, and elapsed
seconds only. Do not log prompts or credentials.

- [ ] **Step 4: Move acceptance before delivery**

Require quality acceptance and persisted report completion before either single or
audience delivery can start.

- [ ] **Step 5: Verify GREEN**

Run pipeline, LLM, localization, workflow, and email tests.

### Task 4: Full verification and file-only live QA

**Files:**
- No source changes unless a test or QA defect is reproduced first.

**Interfaces:**
- Produces: complete gate evidence and one SMTP-disabled live artifact set.

- [ ] **Step 1: Run full local gates**

Run full pytest with Git Bash on PATH, Ruff, strict mypy, compileall, lock check,
diff check, and changed-file formatter check.

- [ ] **Step 2: Run exactly one live AI zh-CN file-only QA**

Set `EMAIL_MODE=file`, use live data and AI/localization, and record actual stage
timings. Inspect the HTML/EML/run result for truth, localization, coverage, China,
calendar, unresolved semantics, and enum/debug leakage.

- [ ] **Step 3: Review scope and secrets**

Confirm no credentials, artifacts, temporary files, unrelated formatter changes,
Web changes, or Admin changes are tracked.

### Task 5: PR, CI, merge, and one new production acceptance attempt

**Files:**
- No additional implementation files unless CI exposes a reproduced defect.

**Interfaces:**
- Produces: merged hotfix on `main` and at most one new production workflow run.

- [ ] **Step 1: Commit, push, and create the hotfix PR**

Document the production timeline, confirmed persistence root cause, latency
contract, TDD evidence, and file-only QA.

- [ ] **Step 2: Wait for CI and repair only reproduced failures**

Merge only after CI is green and all acceptance gates pass.

- [ ] **Step 3: Verify merged main**

Confirm `main == origin/main`, tracked files are clean, and post-merge smoke tests pass.

- [ ] **Step 4: Trigger at most one new production acceptance run**

Only after proving run #39 had zero SMTP attempts. Monitor to terminal state; do
not redispatch for stale UI or delayed logs.

- [ ] **Step 5: Audit final delivery**

Confirm one delivery attempt, normalized case-insensitive recipient deduplication,
one SMTP envelope, final artifact integrity, and no second send.
