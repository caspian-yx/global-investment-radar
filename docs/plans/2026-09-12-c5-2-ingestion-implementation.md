# C5.2 Ingestion Implementation Plan

> Work test-first and keep every change uncommitted for independent review.

**Goal:** Add six bounded public intelligence feeds, deterministic source-policy validation, and conservative cross-provider canonical-URL deduplication without changing downstream semantic authorization.

**Architecture:** Extend the existing provider-neutral `SourceDocument` seam. Harden `OfficialRSSProvider` for resource and timestamp bounds, validate provenance in ingestion, deduplicate normalized documents once in the pipeline before clustering, and configure each source as a separate provider instance.

**Tech stack:** Python 3.12, Pydantic 2, httpx, ElementTree, pytest, Ruff, mypy, uv.

---

### Task 1: Lock source attribution policy with failing tests

**Files:**
- Modify: `tests/test_ingestion.py`
- Modify: `src/radar/ingestion.py`

1. Add tests for every allowed source category/role/tier combination used by the portfolio.
2. Add parameterized tests proving impossible upward attribution fails closed.
3. Run `uv run pytest tests/test_ingestion.py -q` and confirm the new tests fail.
4. Implement the smallest explicit policy validator and call it from `build_source_document`.
5. Re-run the targeted tests until green.

### Task 2: Add deterministic cross-provider deduplication

**Files:**
- Modify: `tests/test_ingestion.py`
- Modify: `src/radar/ingestion.py`

1. Add tests for equal-ID collapse, conflicting-ID failure, exact canonical-URL collapse, source-quality precedence, stable tie-breaking, and provider-order independence.
2. Confirm the tests fail because the API does not yet exist.
3. Implement a typed deduplication result with diagnostics and a stable total ordering.
4. Re-run ingestion tests.

### Task 3: Harden RSS/Atom parsing with offline fixtures

**Files:**
- Add: `tests/fixtures/boj_whatsnew.xml`
- Add: `tests/fixtures/bok_monetary_policy.xml`
- Add: `tests/fixtures/nbs_china_releases.xml`
- Add: `tests/fixtures/eia_today_in_energy.xml`
- Add: `tests/fixtures/apple_newsroom.xml`
- Add: `tests/fixtures/nvidia_press_releases.xml`
- Modify: `tests/test_providers.py`
- Modify: `src/radar/providers.py`

1. Add representative, minimal local fixtures for all six source formats.
2. Add failing tests for configured timezone fallback, declared offsets, language/region metadata, canonical link dedupe, deterministic ordering, item cap, HTML cleanup, content cap, and immutable/native ID behavior.
3. Add failing collection tests for response-byte limits, disabled feeds, per-feed lookback, and isolated failures.
4. Implement bounded parsing and diagnostics while retaining existing status semantics.
5. Re-run provider and ingestion tests.

### Task 4: Register the approved portfolio

**Files:**
- Modify: `config/universe.json`
- Modify: `tests/test_providers.py`
- Modify: `README.md`

1. Add failing configuration/registration assertions for the six distinct provider names and disabled-feed exclusion.
2. Add explicit bounded configuration for existing and new feeds.
3. Add entities and aliases for BOJ, BOK, NBS China, EIA, Apple, and NVIDIA.
4. Document the source portfolio, quality policy, limits, and deferred sources.
5. Re-run provider tests.

### Task 5: Integrate deduplication before clustering

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `src/radar/pipeline.py`

1. Add a pipeline test with two providers returning the same canonical URL and assert one persisted source/event input survives regardless of provider order.
2. Confirm it fails under the current flatten-and-cluster flow.
3. Apply ingestion deduplication before macro-document exclusion and story clustering; log aggregate diagnostics.
4. Preserve macro-source merging and all C2/C3/C6 seams.
5. Re-run pipeline, deterministic, C6, and C2/C3 tests.

### Task 6: Complete offline verification

1. Run targeted suites:
   - `uv run pytest tests/test_providers.py tests/test_ingestion.py -q`
   - `uv run pytest tests/test_pipeline.py tests/test_deterministic.py -q`
   - `uv run pytest tests/test_relevance.py tests/test_explanation.py tests/test_explanation_bundles.py -q`
2. Run all tests: `uv run pytest -q`.
3. Run `uv run ruff check .`.
4. Run `uv run mypy --strict src/radar`.
5. Run `uv lock --check`.
6. Run `git diff --check` and inspect `git status --short` plus the final diff.
7. Stop without production execution, commit, or push and return the A-N implementation report.
