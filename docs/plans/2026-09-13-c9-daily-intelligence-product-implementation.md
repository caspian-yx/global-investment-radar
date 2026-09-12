# C9 Daily Intelligence Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, persisted, mobile-first Daily Intelligence briefing from validated C5-C8 outputs.

**Architecture:** Add a pure `briefing.py` product layer that owns strict models, selection, composition, and diagnostics. Persist its immutable snapshot through additive migration 006, then pass that single model to a thin email-safe Jinja template.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy, Jinja2, pytest, Ruff, strict mypy.

**Spec:** `docs/plans/2026-09-13-c9-daily-intelligence-product-design.md`

## Global Constraints

- C9 rule version is `c9-v1`.
- C2/C6/C6.3/C7/C8 canonical semantics remain unchanged.
- C9 adds no provider, network request, secret, prompt, unrestricted AI layer, or investment advice.
- AI never changes selection or ordering; no-AI output remains complete.
- C8 wording remains observational/associational.
- C6.3 remains the only canonical Story clustering authority.
- HTML uses no JavaScript or external CSS and remains Gmail/Outlook compatible.
- Do not commit, push, run production, start C9.1, or start C10.

---

### Task 1: Strict briefing models and deterministic selection

**Files:**

- Create: `src/radar/briefing.py`
- Create: `tests/test_briefing_selection.py`

**Interfaces:**

- Consumes: `Event`, `Story`, `SourceDocument`, selected-view `C7Score`.
- Produces: strict C9 models, `select_daily_stories(...)`, and `C9SelectionResult`.

- [ ] Write failing tests for CRITICAL retention, MATERIAL+HIGH relevance,
  trivial direct-watch exclusion, no filler, 2/3/5-card sizes, permutation
  invariance, stable ties, and strict/frozen model validation.
- [ ] Run `pytest -q tests/test_briefing_selection.py` and verify failures are
  missing C9 APIs rather than fixture errors.
- [ ] Implement `C9_RULE_VERSION`, controlled selection enums, candidate
  snapshots, exact base ordering, primary eligibility, and soft five-card cap.
- [ ] Add failing diversity tests: repeated central-bank candidates, unique
  CRITICAL retention, same-tier region/EventType preference, and no weak-noise
  promotion.
- [ ] Implement conservative exact presentation suppression and same-strength
  diversity selection.
- [ ] Run selection tests and refactor only while green.

### Task 2: Product composition and evidence hierarchy

**Files:**

- Modify: `src/radar/briefing.py`
- Create: `tests/test_briefing_composition.py`

**Interfaces:**

- Consumes: selection result, C8 records, C2 bundles/claims, optional structured
  explanations, markets/assets, provider statuses, quality state, AI state.
- Produces: `compose_daily_brief(...) -> DailyIntelligenceBrief`.

- [ ] Write failing tests for StructuredExplanation → C2 Claim → deterministic
  semantics → conservative fallback precedence.
- [ ] Implement bounded source snapshots and explanation provenance without
  leaking internal IDs into display copy.
- [ ] Write failing tests for valid/absent/FLAT/SMALL/LOW/ambiguous C8 output and
  two-target presentation bounds.
- [ ] Implement C8 presentation sorting and non-causal copy.
- [ ] Write failing tests for Under the Radar eligibility/order/no duplicates,
  Watch Today no-invention behavior, summary bullets derived from primary order,
  grouped Market Snapshot, and compact quality state.
- [ ] Implement composition and `C9Diagnostics`; verify AI success, disabled,
  failed, malformed, and budget-exhausted inputs leave selected IDs/order equal.
- [ ] Run `pytest -q tests/test_briefing_selection.py tests/test_briefing_composition.py`.

### Task 3: Additive migration 006 and immutable history

**Files:**

- Modify: `src/radar/storage.py`
- Create: `tests/test_c9_storage.py`
- Modify: `tests/test_provenance.py`

**Interfaces:**

- Produces: `006_c9_daily_briefings`, `save_daily_briefing(...)`, and
  `load_daily_briefing(...)`.

- [ ] Write failing tests for additive/repeatable migration, required columns,
  PK/index validation, malformed legacy schema fail-closed behavior, and prior
  table preservation.
- [ ] Implement the table and migration validation.
- [ ] Write failing tests proving identical snapshot idempotence, conflicting
  same run/view/rule rejection, and coexistence across runs/views/rules.
- [ ] Implement immutable save/load with stable identity and strict JSON
  round-trip validation.
- [ ] Run `pytest -q tests/test_c9_storage.py tests/test_provenance.py`.

### Task 4: Pipeline and observability integration

**Files:**

- Modify: `src/radar/pipeline.py`
- Create: `tests/test_c9_pipeline.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**

- Calls composition after C7/C8/C2/C3 results exist and before report render.
- Persists the C9 snapshot and passes it to `report_context`.

- [ ] Write a failing end-to-end no-AI pipeline test proving C9 selection,
  persistence, section composition, and unchanged C2/C7/C8 counts.
- [ ] Integrate `compose_daily_brief`, persist once per run/view/rule, log C9
  diagnostics, and add bounded C9 details to the completed run metadata.
- [ ] Add tests proving optional AI changes explanation prose only, not selected
  Event IDs/order; runtime target Events never appear in C9 primary items.
- [ ] Run `pytest -q tests/test_c9_pipeline.py tests/test_pipeline.py`.

### Task 5: Thin report context and editorial email UI

**Files:**

- Modify: `src/radar/report.py`
- Replace: `templates/daily_email.html.j2`
- Modify: `tests/test_report.py`

**Interfaces:**

- Consumes one `DailyIntelligenceBrief` plus demo/test banners.
- Renders Overnight, Things That Matter, Market Reaction, Under the Radar,
  Watch Today, Market Snapshot, and Sources/Data Quality.

- [ ] Write failing structure tests for all product sections, bounded primary
  sources/reactions, low-activity fallback, and no-AI completeness.
- [ ] Write failing safety tests for escaping, balanced structure, no scripts,
  no external CSS, no fixed oversized container, no raw JSON/secrets/internal
  IDs, no BUY/SELL advice, and no unsupported causal wording.
- [ ] Implement the `680px` fluid table layout, dark 30-second block, editorial
  primary cards, compact secondary sections, grouped market rows, and quiet
  quality footer using email-safe inline CSS.
- [ ] Render representative full/no-impact/low-activity contexts and inspect the
  HTML structure; revise any dashboard-like repetition or mobile overflow.
- [ ] Run `pytest -q tests/test_report.py tests/test_email.py`.

### Task 6: Compatibility, documentation, and final audit

**Files:**

- Modify: `README.md`
- Audit: all C9 diff files.

- [ ] Document C9 flow, exact selection/diversity rules, migration 006,
  no-AI behavior, and known limits.
- [ ] Run focused C2/C3/C4/C6/C6.3/C7/C8/provider/workflow suites and correct
  only genuine C9 regressions using a new failing regression test first.
- [ ] Audit noise, repetition, critical retention, personal relevance,
  causality, AI independence, density, email UI, evidence closure, permutation
  stability, and historical reconstruction.
- [ ] Run fresh final gates: `pytest -q`, `ruff check .`,
  `mypy --strict src/radar`, `uv lock --check`, and `git diff --check`.
- [ ] Inspect final working tree and prepare the A-Z report. Stop without commit,
  push, production, C9.1, or C10.
