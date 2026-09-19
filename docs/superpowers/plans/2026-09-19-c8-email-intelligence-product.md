# C8 Email Intelligence Product Implementation Plan

> Execute in this branch with strict TDD. Every behavior test must be observed failing before implementation.

**Goal:** Make the Chinese-first Email Morning Brief sufficient as the daily product by improving recall observability, information-value selection, clustering/deduplication, explanation completeness, future catalysts, China coverage, and concise AI synthesis while preserving canonical intelligence and the C7 Web path.

**Architecture:** Introduce a frozen `EmailIntelligence` projection built from the immutable briefing plus market/provider inputs. The projection centralizes freshness, cluster identity, section membership, explanation fields, future-catalyst eligibility, China capability state, and diagnostics. Email rendering consumes this projection; the existing Web projection remains unchanged. Provider additions are bounded, attributed and fail-closed.

**Tech stack:** Python 3.12, Pydantic, Jinja2, HTTPX, pytest, Ruff, mypy, GitHub Actions.

---

## Task 1: Establish C8 email projection contracts

**Files:**

- Create: `src/radar/email_intelligence.py`
- Create: `tests/test_email_intelligence.py`
- Modify: `src/radar/report.py`

**Steps:**

1. Add failing tests for frozen strict models, 0–5 Focus items, human-readable status fields, stable cluster IDs, and preservation of source/canonical IDs.
2. Run the focused test and confirm failure because the projection does not exist.
3. Implement the minimum projection models and builder seam.
4. Run focused tests to green, then commit.

## Task 2: Implement one-source freshness and information-value selection

**Files:**

- Modify: `src/radar/email_intelligence.py`
- Modify: `tests/test_email_intelligence.py`
- Modify: `tests/test_web_presentation_contract.py` only if a shared immutable input contract needs coverage

**Steps:**

1. Add failing behavior tests for quiet-day zero Focus, stale exclusion, aging-with-material-gain eligibility, and no quota filling.
2. Implement a single deterministic freshness decision consumed by every Email section.
3. Add value/evidence completeness gates without changing canonical C7 scores.
4. Verify focused tests and canonical identity immutability; commit.

## Task 3: Add intelligence clustering and cross-section deduplication

**Files:**

- Modify: `src/radar/email_intelligence.py`
- Modify: `tests/test_email_intelligence.py`

**Steps:**

1. Add failing tests showing VIX/VXN become one volatility cluster, one event plus its market reaction remains one Focus item, and one cluster cannot occupy Focus and Market Signals independently.
2. Implement deterministic identity-first cluster keys with bounded theme/asset/time-window fallback.
3. Implement a section identity registry and representative selection by information value.
4. Verify permutation invariance and no fixture-specific keys; commit.

## Task 4: Replace duplicate watch sections with future catalysts

**Files:**

- Modify: `src/radar/email_intelligence.py`
- Modify: `tests/test_email_intelligence.py`
- Modify: `templates/daily_email.html.j2`
- Modify: `tests/test_report.py`

**Steps:**

1. Add failing tests that reject generic “monitor for updates”, reject stale/demoted story leakage, and accept a precise evidence-backed confirmation condition or scheduled future event.
2. Implement one bounded `next_catalysts` section.
3. Remove Email-only Watch Today duplication and render natural Chinese labels.
4. Verify the C7 Web template still receives its existing fields and tests pass; commit.

## Task 5: Render complete decision-useful Focus explanations

**Files:**

- Modify: `src/radar/email_intelligence.py`
- Modify: `templates/daily_email.html.j2`
- Modify: `tests/test_email_intelligence.py`
- Modify: `tests/test_report.py`

**Steps:**

1. Add failing tests for `发生了什么 / 为什么重要 / 影响什么 / 市场如何反应 / 仍未知`, source links, unresolved-driver wording, and absence of enum leakage.
2. Build fields strictly from briefing cards, validated reactions, deterministic signals and evidence references.
3. Omit unsupported fields rather than generating filler.
4. Update the blue/white email hierarchy and mobile-safe table layout without Web CSS or scripts; commit.

## Task 6: Improve provider recall and observability

**Files:**

- Modify: `src/radar/providers.py`
- Modify: `src/radar/ingestion.py`
- Modify: `src/radar/domain.py` if a typed diagnostic is required
- Modify: `config/universe.json`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_ingestion.py`
- Add fixtures only for verified provider boundary formats

**Steps:**

1. Audit candidate high-quality endpoints and choose only stable, attributable, bounded feeds.
2. Add failing tests for provider item funnels, normalized failures, source roles/tiers, and bounded collection.
3. Add approved provider configs/adapters; do not add an unbounded crawler or paid/credential requirement.
4. Expose aggregate recall funnel diagnostics in pipeline output/logs.
5. Verify fail-closed behavior and commit.

## Task 7: Add market-first evidence needs

**Files:**

- Modify: `src/radar/email_intelligence.py`
- Modify: `src/radar/pipeline.py`
- Modify: `tests/test_email_intelligence.py`
- Modify: `tests/test_pipeline.py`

**Steps:**

1. Add failing tests that abnormal moves create explicit evidence needs, eligible collected evidence may resolve a driver, and missing evidence remains unresolved.
2. Reuse canonical relationships/evidence evaluation; do not infer causality in presentation.
3. Include evidence-need counts and unresolved symbols in diagnostics.
4. Verify Evidence/Claim/Event objects remain unchanged; commit.

## Task 8: Strengthen China/A-share capabilities

**Files:**

- Modify: `src/radar/providers.py`
- Modify: `src/radar/domain.py`
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/email_intelligence.py`
- Modify: `config/universe.json`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_email_intelligence.py`

**Steps:**

1. Add failing contract tests for core indices, explicit taxonomy, sector ranking, breadth, turnover/activity, style, provider provenance and partial availability.
2. Implement one bounded China market provider only if the live endpoint and attribution can be verified; otherwise implement the typed capability boundary and report the live limitation.
3. Ensure Shenwan labels appear only for genuinely Shenwan-classified observations.
4. Pass typed China observations into both projections without demo backfill; commit.

## Task 9: Validate localization and AI Market Brief

**Files:**

- Modify: `src/radar/localization.py`
- Modify: `src/radar/email_intelligence.py`
- Modify: `tests/test_localization.py`
- Modify: `tests/test_email_intelligence.py`
- Modify: `tests/test_report.py`

**Steps:**

1. Add failing tests for Chinese completeness, source/identity preservation, unsupported fact rejection, enum leakage rejection, and compact non-duplicative English reference.
2. Generate/validate a 2–4 paragraph AI brief only from projection facts; retain a deterministic fallback.
3. Ensure AI prose cannot affect Focus identity/order or introduce causes.
4. Verify with focused tests and commit.

## Task 10: Wire the pipeline and diagnostics without breaking Web

**Files:**

- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/report.py`
- Modify: `src/radar/delivery.py` only if needed for the projection handoff
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_web_report_renderer.py`
- Modify: `tests/test_workflow.py`

**Steps:**

1. Add failing integration tests proving Email consumes `EmailIntelligence`, Web consumes the existing C7 projection, retries preserve immutable snapshots, and no Web publishing config exists.
2. Wire one projection per run/recipient locale while preserving delivery semantics.
3. Add C8 product diagnostics to `run-result.json` and logs.
4. Run targeted integration tests and commit.

## Task 11: Full verification and local product QA

**Steps:**

1. Run focused C8 tests.
2. Run all C7 evidence/snapshot/freshness/localization/renderer regression tests.
3. Run `pytest`, `ruff check`, `ruff format --check`, `mypy --strict`, `compileall`, lock validation and `git diff --check`.
4. Generate a non-delivery real-data QA run using the existing environment, inspect the produced Web artifact and captured Email HTML, and fix all observable repetition, leakage or hierarchy defects.
5. Repeat gates after fixes; commit.

## Task 12: PR, CI, merge and one final production email

**Steps:**

1. Push `codex/c8-email-intelligence-product` without force and open a PR to `main`.
2. Attach the PR to the task and wait for GitHub CI.
3. Fix C8-introduced failures and repeat local/remote gates until green.
4. Merge only after every user-specified gate passes.
5. Synchronize local and remote `main` and verify the merge SHA.
6. Trigger exactly one production Daily Radar run from merged `main`; do not send earlier QA emails.
7. Wait for completion, inspect artifacts/logs and confirm exactly one accepted final validation delivery.
8. Report merge SHA, production run URL/result, provider and recall funnel, Focus output, China coverage, quality gates and known limitations.
