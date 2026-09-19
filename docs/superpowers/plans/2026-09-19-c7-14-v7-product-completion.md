# C7.14 + V7 Apple Markets Product Completion Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Keep the PR unmerged and verify each gate before moving on.

**Goal:** Complete the existing C7 Web Report PR by replacing the first-screen Liquid Glass dashboard with a production-grade V7 Apple Markets presentation while preserving canonical intelligence, evidence semantics, email output, and the existing C7.1–C7.13 contracts.

**Architecture:** Add a canonical asset identity layer independent of provider symbols; extend the pure `web_intelligence` presentation projection with market cards, trend series, localized fallback state, humanized cross-asset signals, China capability reporting, visual-slot selection, and an evidence-bounded AI Market Brief. Replace only the Web Jinja/CSS/JS presentation with a progressive-enhancement V7 renderer. Keep the email renderer and Admin Console unchanged. The browser may select and display validated projection data, but cannot rank stories, infer drivers, mutate snapshots, or create intelligence.

**Tech Stack:** Python 3.12, Pydantic v2, Jinja2, inline CSS/SVG, small accessible browser JavaScript, existing provider/storage/pipeline contracts, pytest, Ruff, strict mypy, compileall, GitHub Actions.

**Spec:** User-supplied C7.14 + V7 Apple Markets Product Completion Pass, with `C:\Users\啊对啦\Downloads\global_investment_radar_v7_apple_markets.html` used only as visual/product reference.

## Global Constraints

- Keep PR #1 open and unmerged; push only normal commits to `feat/c7-market-intelligence-web-report`.
- Do not modify Admin Console, canonical `Evidence`, `Claim`, `Event`, snapshot identity, retry semantics, C7 ranking truth, or email-safe CSS.
- Do not hardcode the 2026-09-17 Golden data or add entity-specific freshness rules.
- Use real structured data and fail closed when a provider/capability is unavailable; never fill China or driver explanations with demos, random values, scraping, or invented causal language.
- Presentation localization is downstream of canonical truth. A localization failure must remain explicit and must not silently become English-only structure.
- Maximum two major visual slots. The primary slot is the real interactive Market Trend when eligible; the second is selected by validated importance/abnormality/information-gain signals.
- Support no-JavaScript readability, keyboard/touch access, responsive desktop/mobile layout, and `prefers-reduced-motion`.

## Task 1: Reconnaissance and red tests for the V7 contracts

**Files:** `src/radar/web_intelligence.py`, `src/radar/report.py`, `templates/daily_web.html.j2`, `config/universe.json`, relevant `tests/` files.

- [ ] Inventory current projection models, provider symbols, universe entries, renderer context, localization fallback, chart eligibility, and the email path.
- [ ] Add failing tests for canonical-symbol equivalence, absolute levels/deltas, trend range eligibility, localization-unavailable structure, humanized cross signals, visual-slot budget, and no canonical mutation.
- [ ] Add failing renderer tests for Chinese-first structure, English reference disclosure, V7 cards/controls, required accessibility hooks, and absence of Web CSS/JS in email output.
- [ ] Run the focused tests and record the red failures before implementing.

## Task 2: Canonical asset identity and market-card projection

**Files:** create `src/radar/assets.py`; modify `src/radar/domain.py`, `src/radar/web_intelligence.py`, `config/universe.json`, provider/universe tests.

- [ ] Define a typed canonical identity/alias resolver for the required US, China, Europe, commodity, FX, and crypto assets while retaining provider-specific symbols at collection boundaries.
- [ ] Ensure fixture and production provider symbols resolve to the same canonical key without changing canonical market-observation semantics.
- [ ] Project validated cards containing display name, level, absolute change, percent change, direction/session, canonical key, provider symbol, and data availability; use rate-appropriate basis-point deltas without rewriting stored observations.
- [ ] Add tests for stable ordering, missing values, rate units, symbol aliases, and fail-closed display behavior.

## Task 3: Real Market Trend projection and visual-slot selection

**Files:** modify `src/radar/web_intelligence.py`, `src/radar/report.py`, relevant model/test files.

- [ ] Add `MARKET_TREND` as the primary visual kind and project validated 1D/5D/1M series only when real history satisfies the configured minimum.
- [ ] Preserve server-validated series and metadata for S&P/Nasdaq/VIX/VXN/10Y/WTI/Gold; expose no client-side inference inputs beyond values, timestamps, units, and labels.
- [ ] Implement deterministic Slot 1/Slot 2 selection with `MAX_MAJOR_VISUALS <= 2`; let abnormal move, cross-asset importance, freshness, and China sector availability determine Slot 2 rather than render order.
- [ ] Add tests proving stale stories do not create visuals, eligible history does, and visual selection is deterministic.

## Task 4: China production seam and capability reporting

**Files:** modify `config/universe.json`, provider collection/universe code, `src/radar/web_intelligence.py`, tests.

- [ ] Add only reliable deterministic core index seams already supported by the provider contract, including Shanghai/Shenzhen/CSI300/CSI A500/ChiNext/STAR50/CSI500/CSI1000/HSI/HSTECH where available.
- [ ] Report source, timestamp, capability, taxonomy, and unavailable fields explicitly. Do not fabricate sector ranking, breadth, turnover, leadership, or dispersion when the provider lacks them.
- [ ] Preserve real 申万一级 sector data only when present and show compact Top/Bottom/micro-bar data from validated observations.
- [ ] Add provider/fixture-equivalence and unavailable-capability tests.

## Task 5: Freshness, abnormal moves, cross-asset language, and bounded brief

**Files:** modify `src/radar/web_intelligence.py`, existing deterministic/freshness modules only where contracts require it, tests/fixtures.

- [ ] Generalize repeated-story lifecycle tests for NVIDIA CUDA-Q, SEC shareholder proposal, and SEC Inline XBRL: Day 0 eligible, no-information-gain decay, later demotion, material follow-up reactivation.
- [ ] Project consumer-facing abnormal-move cards with evidence-backed explanations or explicit `主要驱动暂未确认`; keep internal enums in secondary metadata only.
- [ ] Project human-readable cross-asset explanations with observed numbers and a deterministic/interpretive state marker without exposing raw enum codes as primary UI.
- [ ] Add the AI Market Brief projection at the report end, consuming only validated evidence/claims/events/observations/calendar/deterministic signals/unresolved state; if synthesis is unavailable, say so explicitly rather than invent prose.
- [ ] Add tests proving no causal claim is generated from an unresolved move and no generic sentiment paragraph is emitted.

## Task 6: V7 Web renderer and progressive enhancement

**Files:** replace/modify `templates/daily_web.html.j2`, `src/radar/report.py`, renderer tests; do not modify email template.

- [ ] Implement V7 light-neutral layout: selective translucent glass for controls/selected/AI/sticky nav, moderate radius/shadows, typography-led numbers, restrained gain/loss colors, and responsive market tape.
- [ ] Render first-screen market overview with primary strip, expandable regional/secondary detail, levels/absolute/% changes, and accessible cards; remove regime label as the hero while retaining it internally and contextually.
- [ ] Render localized sections for focus, trend, abnormal moves, cross-asset, China, next, secondary, sources/data quality, and AI brief with canonical English reference disclosure and explicit localization-unavailable state.
- [ ] Add inline SVG trend paths/area fills, selectable assets, 1D/5D/1M controls, hover/touch time-value tooltips, smooth transitions, and `prefers-reduced-motion`; keep JS optional and never put presentation logic into canonical data.
- [ ] Render only eligible A-share sector micro-bars and VIX/VXN/10Y/WTI sparklines; do not chart for visual completeness.
- [ ] Add DOM/static tests for accessibility, reduced motion, bilingual structure, visual budget, SVG eligibility, no raw internal signal headings, and Web/email separation.

## Task 7: Golden integration and visual QA

**Files:** create/modify `tests/fixtures` and `tests/test_golden_report.py`; generated artifacts under ignored `work/visual-qa/`.

- [ ] Extend the generic 2026-09-17 fixture with real pipeline inputs covering rates/Fed, WTI −4.06%, Dow/NDX divergence, VIX/VXN, repeated stories, and available/unavailable China capabilities without special-case production code.
- [ ] Generate the final report only through `run_daily → report_context → Web presentation → renderer`; do not edit generated HTML.
- [ ] Open or inspect the generated report on desktop/mobile and test reduced-motion/JS-disabled readability; record any product-level findings separately from engineering findings.
- [ ] Verify canonical objects and snapshots are unchanged before/after localization and rendering; verify email output remains valid and Admin Console files are untouched.

## Task 8: Quality gates, PR update, and acceptance handoff

**Files:** source/tests/docs only as needed; PR #1 metadata.

- [ ] Run focused tests, then full `uv run pytest -q`, `uv run ruff check .`, `uv run mypy --strict src/radar`, `uv run python -m compileall src tests`, `git diff --check`, and dependency/lock validation available in the checkout.
- [ ] Inspect `git diff --stat main...HEAD`, changed files, current worktree, migration/config/dependency changes, and final commit SHA.
- [ ] Commit in coherent checkpoints, push with `git push origin HEAD:refs/heads/feat/c7-market-intelligence-web-report` without force, wait for GitHub CI, and fix only C7 regressions if CI fails.
- [ ] Update PR #1 description with C7.14/V7 completion, renderer separation, safety guarantees, golden QA, test results, known limitations, and explicit no-merge status.
- [ ] Deliver the final HTML path, screenshot/open instructions, A–M acceptance table, product/engineering findings, remaining limitations, and merge recommendation. Do not merge.

