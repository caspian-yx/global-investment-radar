# C9.1 Chinese Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive one strictly validated Simplified Chinese localization from the immutable C9 `DailyIntelligenceBrief`, persist it immutably, and render it before the unchanged English canonical briefing.

**Architecture:** Add a pure localization module that builds a bounded payload, issues at most one structured request, rejects any malformed or factually unsafe response, and returns a validated localized content model. Add additive migration 007 for immutable localization snapshots. The pipeline loads the C9 snapshot first, reuses a matching localization snapshot before any model call, and passes the same canonical briefing plus optional localized content to an email-safe bilingual template.

**Tech Stack:** Python 3.12, Pydantic v2, existing `LLMProvider`, SQLAlchemy, Jinja2, pytest, Ruff, strict mypy.

**Spec:** User-supplied C9.1 specification in attachment `a3805aad-60f9-4239-97f7-a3d7b189c35a/pasted-text.txt`.

## Global Constraints

- C9 `DailyIntelligenceBrief` remains the sole canonical intelligence truth.
- Exactly one localization request per briefing; no repair request, retry storm, fallback model, browsing, or new provider.
- AI may write only authorized Chinese text fields; application code owns identity, ordering, enums, URLs, publishers, numbers, market values, and source provenance.
- Localization failure always preserves the complete English briefing.
- Snapshot identity is `briefing_id + locale + localization_rule_version + requested_model`; conflicting writes fail closed.
- C5–C9 intelligence semantics, C8 association semantics, and existing English rendering remain unchanged.
- Do not commit, push, run production, or start C10.

---

### Task 1: Establish strict localization models and payload

**Files:**
- Create: `src/radar/localization.py`
- Test: `tests/test_localization.py`

**Interfaces:**
- Produces `build_localization_payload(briefing)`, `build_localization_prompt(payload)`, `parse_localization_response(raw, payload)`, `validate_localized_content(content, briefing)`, and immutable localization models consumed by Tasks 2–5.

- [ ] **Step 1: Write failing tests** for exact key closure, ordering, extra fields, malformed JSON, Markdown fences, prose wrappers, duplicate keys, and factual/casual/advice rejection.
- [ ] **Step 2: Run `pytest tests/test_localization.py -q`** and confirm the new behavior fails because the module and models do not exist.
- [ ] **Step 3: Implement frozen/strict Pydantic models, deterministic key builders, duplicate-key JSON loading, bounded payload construction, the exact localization prompt, and validators for key closure, numeric/date/percentage atoms, C8 direction, unsupported causality, and investment advice.
- [ ] **Step 4: Run the focused localization tests** and confirm they pass without changing C9 models.

### Task 2: Add localization provider call and failure-safe orchestration

**Files:**
- Modify: `src/radar/config.py`
- Modify: `src/radar/localization.py`
- Test: `tests/test_localization.py`

**Interfaces:**
- Consumes `LLMProvider`, `LLMRequest`, `LLMResponse`, and `Settings`.
- Produces `localize_briefing(briefing, provider, requested_model, ...)` returning validated content or a bounded failure diagnostic without raising provider/parse/validation errors.

- [ ] **Step 1: Add failing tests** for one successful FakeLLM call, disabled localization, provider failure, timeout, malformed JSON, validation failure, and no automatic second call.
- [ ] **Step 2: Run focused tests** and verify the expected failures.
- [ ] **Step 3: Add `LOCALIZATION_ENABLED`, `LOCALIZATION_LOCALE=zh-CN`, and `LOCALIZATION_MODEL` settings; invoke the existing generation provider once with an explicit Chinese-editor prompt and no source-body dump.
- [ ] **Step 4: Implement safe diagnostics containing enabled/locale/model/status/request count/usage/latency/reuse/failure category without prompt or raw-response logging.
- [ ] **Step 5: Run focused tests** and confirm all failure modes return English-fallback state and no second call.

### Task 3: Persist immutable localization snapshots

**Files:**
- Modify: `src/radar/storage.py`
- Test: `tests/test_localization_storage.py`

**Interfaces:**
- Produces `C9_LOCALIZATIONS_MIGRATION = "007_c9_localizations"`, `save_localization_snapshot(snapshot)`, and `load_localization_snapshot(...)`.

- [ ] **Step 1: Write failing migration/storage tests** for schema validation, repeat initialization, idempotent identical writes, conflicting identity writes, different locale/model identities, and SHA-256 tamper detection.
- [ ] **Step 2: Run focused storage tests** and confirm they fail before migration 007 exists.
- [ ] **Step 3: Add the additive `c9_localizations` table with strict non-null schema, immutable identity index, payload JSON, and payload hash; call and validate it from `RadarStore.initialize()` after migration 006.
- [ ] **Step 4: Implement exact model round-trip, identity checks, checksum checks, and conflict fail-closed behavior.
- [ ] **Step 5: Run focused storage tests** and confirm they pass while existing C5–C9 storage tests remain green.

### Task 4: Integrate pipeline retry/source-of-truth and delivery behavior

**Files:**
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/report.py`
- Test: `tests/test_c9_1_pipeline.py`

**Interfaces:**
- Consumes canonical C9 snapshots and localization storage/provider boundaries.
- Produces one canonical briefing plus optional persisted localized content in `report_context`; the same rendered HTML is used for file output and email.

- [ ] **Step 1: Write failing tests** for enabled localization, disabled localization, provider failure fallback, retry reuse with changed upstream model output, and no model call when a matching snapshot exists.
- [ ] **Step 2: Run focused pipeline tests** and confirm the expected failures.
- [ ] **Step 3: After C9 snapshot load/compose, load the matching localization snapshot first; if absent and enabled, perform exactly one localization call from that briefing and persist only validated output.
- [ ] **Step 4: Keep localization failures non-fatal, select a deterministic Chinese subject only on localization success, and pass canonical + localized models through `report_context`.
- [ ] **Step 5: Run focused pipeline tests** and confirm canonical English output is unchanged when localization is unavailable.

### Task 5: Add Chinese-first bilingual email-safe rendering

**Files:**
- Modify: `templates/daily_email.html.j2`
- Modify: `src/radar/report.py`
- Test: `tests/test_localization_report.py`

**Interfaces:**
- Consumes validated localized content plus canonical C9 fields.
- Produces `lang="zh-CN"` Chinese-first HTML followed by a clearly separated `lang="en"` canonical version; without localized content it preserves current English output.

- [ ] **Step 1: Write failing HTML tests** for Chinese labels, Chinese-first order, canonical market values/C8 values/URLs, escaping, no script/external CSS/raw JSON/internal IDs, and English regression.
- [ ] **Step 2: Run focused report tests** and confirm the expected failures.
- [ ] **Step 3: Add centralized deterministic Chinese labels for sections, bands, institutions, publishers, and common market names; render canonical numbers, symbols, URLs, evidence, and metadata outside AI text.
- [ ] **Step 4: Add the bilingual template branch with inline styles, fluid 680px layout, no JavaScript, and a clear English divider.
- [ ] **Step 5: Run focused HTML tests** and inspect a representative rendered string for hierarchy and mobile/email-safe structure.

### Task 6: Observability, documentation, and full verification

**Files:**
- Modify: `README.md`
- Modify: `src/radar/pipeline.py`
- Test: `tests/test_localization.py`, `tests/test_c9_1_pipeline.py`, `tests/test_localization_report.py`

- [ ] **Step 1: Add safe localization diagnostics** to pipeline logs and report quality context without raw prompts/responses/secrets.
- [ ] **Step 2: Add representative FakeLLM Chinese fixture assertions** covering POLICY, MACRO, LEGAL, GUIDANCE, SUPPLY_CHAIN, company Story, C8, FLAT/SMALL, LOW/ambiguous association, Under the Radar, and Watch Today.
- [ ] **Step 3: Run `pytest -q` with Git Bash on PATH, `ruff check .`, `mypy --strict src/radar`, `uv lock --check`, and `git diff --check`.
- [ ] **Step 4: Inspect final diff scope, the generated representative Chinese HTML, migration contract, retry source-of-truth, and all no-fabrication boundaries.
- [ ] **Step 5: Return the A–Z C9.1 report and stop without commit, push, production, or C10.
