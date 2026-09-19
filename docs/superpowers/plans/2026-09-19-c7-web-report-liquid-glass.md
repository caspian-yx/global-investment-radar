# C7 Web Daily Report + Liquid Glass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing evidence-first daily briefing into a deterministic market-intelligence Web Report with a production Liquid Glass presentation, while keeping the canonical C9 snapshot, localization truth, Admin Console, and email-safe output separate and stable.

**Architecture:** Keep `DailyIntelligenceBrief` as the canonical snapshot and add a pure `web_intelligence` projection that consumes the briefing plus the current structured market observations and configured universe. Split the existing report path into a Web renderer for the saved daily HTML and an Email renderer for delivery, with the same validated source data but different presentation contracts. Add market-history-aware deterministic signals, coverage/freshness metadata, and chart eligibility as intelligence/presentation projections; never let Jinja or browser JavaScript infer market meaning.

**Tech Stack:** Python 3.12, Pydantic v2, Jinja2, existing provider/storage/pipeline contracts, inline SVG/CSS, minimal JavaScript-free Web HTML, pytest, Ruff, strict mypy, Git Bash on Windows.

**Spec:** User-supplied `C7 Full Autonomous Implementation` attachment plus `C:\Users\啊对啦\Downloads\global_investment_radar_2026-09-17_liquid_glass_v4.html` as visual design reference.

## Global Constraints

- `DailyIntelligenceBrief`, `Evidence`, `Claim`, `Event`, C7 ranking, C8 association semantics, and snapshot identity remain canonical and evidence-first.
- Web presentation consumes validated structured intelligence; CSS/Jinja/JavaScript may not rank stories, infer causes, or invent market drivers.
- An unresolved driver renders explicitly when evidence is insufficient; no causal claim is generated from a deterministic signal alone.
- Existing email HTML remains email-safe and functional; advanced Liquid Glass is Web-only progressive enhancement.
- Admin Console files and routes are out of scope.
- Major visual budget defaults to `2`; chart eligibility must be deterministic and testable.
- A-share sector data is rendered only when a validated structured provider supplies it; missing coverage is surfaced rather than filled with demo prose.
- Chinese is primary only when a validated localization snapshot exists; canonical English remains the fallback and reference truth.
- Preserve user-owned untracked files in the original checkout; work only in the managed worktree.

---

### Task 1: Lock the current report boundaries and introduce Web/Email renderer separation

**Files:**
- Create: `src/radar/web_intelligence.py`
- Modify: `src/radar/report.py`
- Modify: `src/radar/pipeline.py`
- Create: `templates/daily_web.html.j2`
- Retain: `templates/daily_email.html.j2` as the email-safe renderer source
- Test: `tests/test_report.py`, `tests/test_c9_2_rendering.py`, `tests/test_c9_pipeline.py`

**Interfaces:**
- `EmailHtmlReportRenderer.render(context) -> str` remains the email-safe renderer used by `render_recipient_email` and email delivery.
- `WebHtmlReportRenderer.render(context) -> str` renders the saved daily Web Report.
- `HtmlReportRenderer` remains a compatibility name for the Web renderer so existing pipeline seams still observe the Web artifact.
- `report_context(..., markets=(), assets=()) -> dict[str, Any]` passes structured market observations and configured assets to the projection without changing the canonical briefing.

- [ ] **Step 1: Write failing tests** that assert the saved pipeline artifact contains Web-only selectors/SVG and the delivery HTML remains table-based, with no `display:grid`, `backdrop-filter`, or `<script>` in email output.
- [ ] **Step 2: Run the focused tests** with `C:\Users\啊对啦\Documents\ChatGPT\New project\.venv\Scripts\python.exe -m pytest tests/test_report.py tests/test_c9_2_rendering.py tests/test_c9_pipeline.py -q` and verify failure because only one renderer/template exists.
- [ ] **Step 3: Add renderer classes and keep `render_recipient_email` bound to `EmailHtmlReportRenderer`; route `run_daily` to render Web HTML for `report_path` and render Email HTML only for the legacy sender path.
- [ ] **Step 4: Add a minimal Web template shell with semantic section slots and a pure projection placeholder; keep all content escaped through Jinja autoescape.
- [ ] **Step 5: Run the focused tests and the full existing suite; update only tests whose contract intentionally changed from the old saved artifact to Web HTML.

### Task 2: Add market history and deterministic C7.1–C7.5 intelligence projections

**Files:**
- Modify: `src/radar/domain.py`
- Modify: `src/radar/providers.py`
- Modify: `src/radar/briefing.py`
- Create: `src/radar/web_intelligence.py`
- Test: `tests/test_market_events.py`, `tests/test_briefing_composition.py`, `tests/test_web_intelligence.py`

**Interfaces:**
- `MarketHistoryPoint` stores one timezone-aware close in a canonical ordered tuple.
- `MarketObservation.history: tuple[MarketHistoryPoint, ...]` is optional and backward-compatible; the latest two points continue to populate `observed_at`, `close`, `previous_observed_at`, and `previous_close`.
- `build_web_intelligence(briefing, markets, assets, *, report_date) -> WebIntelligenceModel` derives freshness, abnormal moves, coverage gaps, cross-asset candidates, and regime without changing C9 identity.
- `MarketMoveSignal` distinguishes `OBSERVED_FACT`, `DETERMINISTIC_SIGNAL`, `EVIDENCE_BACKED_INTERPRETATION`, and `DRIVER_UNRESOLVED` states.

- [ ] **Step 1: Write failing tests** for Yahoo/demo history preservation, stale-vs-fresh story labeling, material-update reactivation metadata, configurable abnormal move thresholds, unresolved-driver output, WTI/VIX/rates coverage gaps, Dow-vs-NDX divergence, VIX-vs-VXN divergence, oil/yield signal, and the `CAUTIOUS / RATES-LED / NOT PANIC` style regime.
- [ ] **Step 2: Run `pytest tests/test_web_intelligence.py tests/test_market_events.py -q`** and confirm the missing model/projection behavior fails.
- [ ] **Step 3: Preserve Yahoo's valid 10-day closes as ordered history and add deterministic demo history derived from its declared fixture values; do not change market identity or provider semantics.
- [ ] **Step 4: Implement pure projection helpers for headline decay/freshness, abnormal move thresholds, missing coverage, and cross-asset signal candidates. Require evidence/briefing support before assigning an interpretation; otherwise return `DRIVER_UNRESOLVED`.
- [ ] **Step 5: Implement regime derivation from observed market data and validated story/evidence state, with explicit observed/signal/interpretation labels.
- [ ] **Step 6: Run focused tests, then `pytest tests/test_briefing_composition.py tests/test_market_events.py tests/test_web_intelligence.py -q`.

### Task 3: Add C7.6–C7.8 China, multi-day context, and What Matters Next models

**Files:**
- Modify: `src/radar/domain.py`
- Modify: `src/radar/briefing.py`
- Modify: `src/radar/providers.py`
- Modify: `src/radar/web_intelligence.py`
- Modify: `config/universe.json`
- Test: `tests/test_web_intelligence.py`, `tests/test_briefing_composition.py`

**Interfaces:**
- `ChinaSectorObservation` is a validated structured observation with stable taxonomy label, 1D move, optional 5D move, optional activity, and source/provider metadata.
- `ChinaIntelligence` contains indices, ranked sectors, breadth, dispersion, leadership, and an explicit unavailable state when sector data is absent.
- `MultiDayContext` exposes level, 1D, 5D/7D change, and regime for eligible assets; normal assets remain compact.
- `NextHorizon` contains separate `next_24_hours` and `next_7_days` lists derived only from existing Watch Today/calendar records; no invented dates or probabilities.

- [ ] **Step 1: Write failing tests** for sector sorting, breadth, dispersion, technology/growth leadership, China index filtering, multi-day change from real history, relevance-gated next horizons, confirmation/invalidation conditions, and empty/unavailable coverage behavior.
- [ ] **Step 2: Run the focused tests** and confirm the new structured projection fields are absent.
- [ ] **Step 3: Add backward-compatible structured input fields and deterministic sorting/aggregation; keep provider absence visible and never synthesize sector rows.
- [ ] **Step 4: Map current configured CN/HK assets into the China section and current Watch Today items into Next 24 Hours; render Next 7 Days as empty/coverage-limited unless a validated future-event record exists.
- [ ] **Step 5: Run focused tests and verify no C9 snapshot identity or localization payload closure changes.

### Task 4: Add explanation quality, bilingual Web presentation context, and chart eligibility

**Files:**
- Modify: `src/radar/web_intelligence.py`
- Modify: `src/radar/report.py`
- Modify: `src/radar/localization.py` only if additive labels are required; do not alter canonical localization payload closure
- Create: `tests/test_web_presentation_contract.py`

**Interfaces:**
- `WebReportModel` contains only presentation-ready copies/references of validated data and all required sections: Market Regime, Global Market Strip, China/A-Shares, Volatility & Rates, Overnight Intelligence, Cross-Asset Intelligence, Watch Today, What Matters Next, Secondary Developments, and Sources/Data Quality.
- `ChartEligibility` contains `kind`, `eligible`, `reason`, and deterministic source keys.
- `select_major_visuals(model, max_visuals=2) -> tuple[ChartEligibility, ...]` is stable and never exceeds the budget.
- `web_report_context(...)` keeps localized copy optional and keeps canonical values/URLs/source provenance outside localized prose.

- [ ] **Step 1: Write failing tests** for low-information explanation rejection, Chinese-first localized rendering with English fallback, snapshot/localization separation, chart eligibility triggers, max-two major visuals, deterministic ordering, and no internal IDs/evidence payload leakage.
- [ ] **Step 2: Run `pytest tests/test_web_presentation_contract.py -q`** and verify the new contract fails.
- [ ] **Step 3: Implement deterministic explanation-quality checks and section projection; preserve existing C9 conservative fallback wording and add explicit unresolved-driver labels.
- [ ] **Step 4: Implement real SVG polyline sparkline generation from market history for VIX, VXN, US 10Y, and WTI, with a no-history empty state; never use Unicode block characters.
- [ ] **Step 5: Implement ranked A-share micro-bars only from `ChinaSectorObservation` data and choose exceptional-asset/global-risk visuals by deterministic information-value rules.
- [ ] **Step 6: Run focused tests and confirm the projection is pure and repeatable for identical inputs.

### Task 5: Build the production Liquid Glass Web renderer

**Files:**
- Modify: `templates/daily_web.html.j2`
- Modify: `src/radar/report.py`
- Test: `tests/test_web_report_renderer.py`

**Interfaces:**
- The template consumes only `web_report` plus safe presentation metadata from `report_context`.
- The Web renderer produces semantic HTML with no frontend inference, uses inline SVG for eligible charts, and has a deterministic static fallback when motion or chart data is unavailable.

- [ ] **Step 1: Write failing renderer tests** for all required section headings, real briefing/story/market values, no hardcoded demo values, SVG sparklines, responsive behavior, keyboard-visible focus, `prefers-reduced-motion`, Liquid Glass tokens, and escaped hostile content.
- [ ] **Step 2: Run the focused renderer tests** and verify failure because the production template does not exist.
- [ ] **Step 3: Implement the deliberate visual system from the reference: cool blue/violet/aqua low-saturation environmental gradients; translucent refractive glass with luminous corner edges; 34–38px primary surfaces, 24–30px modules, 16–22px controls; Apple/PingFang font stack; restrained burgundy/green/blue direction colors; tabular numbers; typography-led section navigation without emoji.
- [ ] **Step 4: Implement the hierarchy: masthead/hero, regime, global strip, China, risk pulse, overnight stories, cross-asset transmission, Watch Today/Next horizons, secondary developments, and sources/data quality. Use real projected data and explicit no-data states.
- [ ] **Step 5: Add progressive enhancement only: CSS edge-light/parallax/reveal motion is decorative, static HTML remains intelligible, `prefers-reduced-motion: reduce` disables animation and transforms, and no JavaScript is needed for meaning.
- [ ] **Step 6: Render a representative demo fixture and visually inspect it in a browser at desktop and mobile widths; iterate on contrast, spacing, and overflow without changing intelligence semantics.

### Task 6: Route the pipeline to Web output while preserving Email and Admin boundaries

**Files:**
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/report.py`
- Test: `tests/test_pipeline.py`, `tests/test_c9_pipeline.py`, `tests/test_c9_1_pipeline.py`, `tests/test_email.py`

**Interfaces:**
- Saved `report_path` is the Web renderer output.
- Legacy sender and audience delivery receive only `EmailHtmlReportRenderer` output.
- Retry still reloads the immutable C9 briefing and never lets presentation write back to it.

- [ ] **Step 1: Write failing integration tests** for Web artifact/email artifact separation, persisted briefing reuse, localization reuse, insufficient-data diagnostics, deterministic HTML hash on identical inputs, and unchanged Admin Console imports/routes.
- [ ] **Step 2: Run the focused integration tests** and confirm the pipeline still sends the Web artifact or cannot see the new presentation boundary.
- [ ] **Step 3: Pass raw markets/assets into report context, build the Web projection after canonical/localization snapshot resolution, write Web HTML to the configured report directory, and render a separate email-safe HTML for delivery.
- [ ] **Step 4: Keep `render_recipient_email` and audience preview on the email renderer; do not add Liquid Glass CSS to the email template.
- [ ] **Step 5: Run the full C7–C9 pipeline and email tests and confirm existing snapshot/retry/localization invariants.

### Task 7: Golden regression, visual QA, static quality gates, and delivery report

**Files:**
- Create/modify: `tests/fixtures` for 2026-09-17 structured golden inputs
- Create: `tests/test_golden_report.py`
- Modify: `README.md` with Web/email renderer boundaries and visual contract

- [ ] **Step 1: Add a golden fixture** representing the 2026-09-17 requirements: Fed first, WTI −4.06% abnormal move, VIX/VXN and 2Y/10Y when available, Dow/NDX divergence, stale CUDA-Q/SEC stories demoted, unresolved driver preserved, China leadership when sector data exists, bilingual fallback, and visual budget ≤2.
- [ ] **Step 2: Run `pytest tests/test_golden_report.py -q` and fix only deterministic implementation defects exposed by the fixture.
- [ ] **Step 3: Run the full suite with Git Bash visible: `python -m pytest -q` and record exact passed/skipped/failed counts.
- [ ] **Step 4: Run `ruff check .`, `mypy --strict src/radar`, `git diff --check`, and the repository lock/dependency validation command available in this checkout; record exact exit status and output.
- [ ] **Step 5: Generate a representative HTML report, inspect the Web artifact and email fallback, verify no Admin Console files changed, verify no canonical snapshot mutation during localization/rendering, and verify the final diff.
- [ ] **Step 6: Complete the final engineering report with baseline, C7.1–C7.13 mapping, root causes, final intelligence pipeline, safety guarantees, golden results, visual system, quality gates, git/worktree status, and explicit remaining limitations.

