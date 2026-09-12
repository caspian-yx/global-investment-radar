# C7 Global Importance + Investor Relevance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a deterministic, explainable, separately persisted Global Importance and Investor Relevance layer that ranks canonical Stories/Events for a configurable investor view.

**Architecture:** Keep existing Event importance fields and C6/C2 semantics unchanged. Add a focused `importance.py` module containing immutable view profiles, versioned C7 score models, bounded component calculations, controlled reason codes, priority bands, and stable total ordering. Score canonical Events after Story/Event construction, persist immutable per-run/per-view results through additive migration 004, and expose only the selected view's ranking in the existing report.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy 2, SQLite/PostgreSQL-compatible SQLAlchemy Core, pytest, Ruff, strict mypy, existing Jinja report.

**Spec:** User-approved C7 architecture in the pasted C6.3 acceptance request.

## Global Constraints

- C7 makes zero LLM calls, adds zero ingestion requests, and uses no new network APIs.
- C7 must not change C6 authorization, C2 temporal eligibility, DriverCandidate semantics/ranking, or canonical Story matching.
- Importance and relevance are separate deterministic dimensions; neither mutates the other.
- Freshness is bounded and cannot dominate materiality.
- Corroboration counts distinct canonical publishers/source categories conservatively, never raw document count.
- Historical rows are attributable to Event/Story, view, rule version, run identity, score, rank, reasons, and configuration snapshot.
- Migration 004 is additive, repeatable, and must not overwrite prior rule-version results.
- No C8, C9, bilingual rendering, company scoring, embeddings, learning loop, commit, push, or production run.

---

### Task 1: C7 domain contract and configuration

**Files:**
- Create: `src/radar/importance.py`
- Modify: `src/radar/domain.py` only if a shared diagnostic/result model is required
- Modify: `src/radar/config.py` if a bounded `RADAR_VIEW_ID` setting is needed
- Modify: `config/universe.json` to add explicit bounded entity significance and view profiles
- Test: `tests/test_importance.py`

**Interfaces:**
- `C7_RULE_VERSION = "c7-v1"`
- `ViewProfile`, `ImportanceBand`, `RelevanceBand`, `PriorityBand`, `ImportanceReason`, `RelevanceReason`
- `C7Score` with immutable importance/relevance components, bands, reason codes, view snapshot, and optional rank
- `load_view_profiles(universe)` validates configured dimensions and returns stable profiles

- [ ] Write failing tests for strict profile validation, configured view loading, stable enum values, score bounds, and immutable reason/component payloads.
- [ ] Run the focused tests and confirm they fail because the C7 contracts do not exist.
- [ ] Add the minimal frozen Pydantic contracts, controlled enums, rule version, and validated view configuration loader.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Deterministic importance, relevance, and priority engine

**Files:**
- Modify: `src/radar/importance.py`
- Test: `tests/test_importance.py`

**Interfaces:**
- `score_event(event, story, documents, view, universe, now) -> C7Score`
- `score_events(events, stories, documents, view, universe, now) -> list[C7Score]`
- `rank_events(events, scores) -> list[Event]`
- `summarize_c7_scores(scores) -> dict[str, int | str]`

- [ ] Write failing tests for policy/regulatory materiality versus company marketing, source authority, configured entity significance, Story scope, conservative corroboration, and bounded freshness.
- [ ] Write failing tests for direct watched entity, explicit topic/entity/theme, event type, asset class, bounded region-only relevance, unrelated stories, and generic lexical non-matches.
- [ ] Write failing separation tests for high importance/low relevance and moderate importance/high relevance.
- [ ] Write failing ranking tests for priority bands, globally critical events, material direct-watch events, low-low sinking, permutation invariance, and stable Event ID tie-breaks.
- [ ] Run the focused tests and verify the expected RED failures.
- [ ] Implement bounded components with explicit reason codes and no opaque combined score. Use canonical Story fields and SourceDocument provenance only.
- [ ] Implement priority-band classification and a stable tuple sort; assign ranks without mutating Events.
- [ ] Run focused tests and the existing ranking/relevance regressions until green.

### Task 3: Additive persistence migration 004

**Files:**
- Modify: `src/radar/storage.py`
- Test: `tests/test_provenance.py` or a focused `tests/test_importance.py`

**Interfaces:**
- `C7_IMPORTANCE_RELEVANCE_MIGRATION = "004_c7_importance_relevance"`
- `c7_rankings_table`
- `RadarStore.save_c7_scores(run_id, scores)` and `RadarStore.load_c7_scores(run_id, view_id, rule_version)`

- [ ] Write failing tests for fresh-schema creation, repeatable initialization, malformed migration marker/schema rejection, round trip, multiple views, multiple rule versions, stable same-run upsert, and preservation of old rows.
- [ ] Run migration tests and verify RED.
- [ ] Add the additive table, primary/unique/index constraints, migration marker validation, payload round trip, and immutable rule-version/run/view identity checks.
- [ ] Run migration/provenance tests and confirm green.

### Task 4: Pipeline and report integration

**Files:**
- Modify: `src/radar/config.py`
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/report.py`
- Modify: `templates/daily_email.html.j2`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Score all canonical Events after Story/Event construction and before report rendering.
- Persist all configured-view scores for the run; render the selected configured view only.
- Keep full canonical Event/C6/C2/AI processing intact; only the report event list uses C7 ordering.

- [ ] Write failing integration tests proving C7 does not change C6/C2 inputs, scores multiple views, persists results, ranks report events, and remains no-AI/no-network.
- [ ] Write failing report tests for priority, importance, relevance, reason-code display and backward-compatible rendering when C7 context is absent.
- [ ] Implement selected-view configuration, score/persist hooks, aggregate logging, report context, and minimal template fields.
- [ ] Run pipeline/report tests and confirm all existing behavior remains green.

### Task 5: Documentation and final verification

**Files:**
- Modify: `README.md`
- Verify: all changed files; no commit

- [ ] Document C7 contracts, view configuration, additive persistence, deterministic ranking, and explicit exclusions.
- [ ] Run full `pytest`.
- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run mypy --strict src/radar`.
- [ ] Run `uv lock --check`.
- [ ] Run `git diff --check`.
- [ ] Inspect final diff, scope, migration safety, C6/C2 boundaries, and no production/commit/push state.
- [ ] Return the A–T C7 implementation report and exactly one readiness status.
