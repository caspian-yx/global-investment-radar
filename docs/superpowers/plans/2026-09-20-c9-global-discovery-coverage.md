# C9 Global Discovery & Coverage Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix deterministic multi-recipient duplicate email delivery, then broaden and audit the evidence-first discovery universe feeding the Chinese Email Morning Brief.

**Architecture:** Preserve the canonical C7/C8 pipeline and add typed, deterministic seams for provider registry metadata, discovery clusters, market anomalies, scheduled catalysts, China capabilities, and benchmark coverage. Email consumes validated projections only; Web remains frozen.

**Tech Stack:** Python 3.12, Pydantic, HTTPX, SQLAlchemy, Jinja2, pytest, Ruff, mypy, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-c9-global-discovery-coverage-design.md`

## Global Constraints

- Email Morning Brief remains the primary product; existing C7 Web output stays optional, unpublished, and visually unchanged.
- No real email before the final merged-main production run.
- No paywall bypass, brittle scraping, fake production data, unsupported causal claims, or fixture-specific production rules.
- Canonical Evidence, Claim, Event, Story, ranking, snapshot, and localization identity must not be mutated by C9 projections.
- Focus remains 0–5 and is never quota-filled.
- Discovery sources cannot silently become primary evidence.
- Missing China or provider capability is explicit and fail-closed.
- C9.0 must be an independently identifiable commit.

---

### Task 1: C9.0 canonical recipient resolution and one-send SMTP behavior

**Files:**
- Modify: `src/radar/email.py`
- Modify: `src/radar/config.py`
- Modify: `src/radar/delivery.py`
- Modify: `tests/test_email.py`
- Modify: `tests/test_c9_2_delivery_service.py`

**Interfaces:**
- Produces: `normalize_recipients(*sources: str | Iterable[str] | None) -> tuple[str, ...]`
- Produces: one SMTP `send_message(..., to_addrs=recipients)` call for legacy/single delivery.
- Consumes: existing `normalize_email()` and `validate_email()` semantics.

- [ ] Add parameterized parser tests with literal expected tuples for one address, three addresses, duplicates, whitespace, case variants, sender overlap, empty fragments, and multiple source strings.
- [ ] Add SMTP integration-double tests asserting one connection and one `send_message` call whose explicit envelope contains each normalized unique address once.
- [ ] Add audience preflight tests proving duplicate normalized profiles or overlapping configured addresses cannot fan out twice.
- [ ] Run the focused tests and confirm failures are caused by absent normalization/deduplication.
- [ ] Implement first-seen stable, case-insensitive normalization and pass explicit deduplicated envelope recipients to SMTP.
- [ ] Keep one-recipient `send_to()` behavior privacy-safe and validate its recipient through the same resolver.
- [ ] Run focused email/delivery tests and the existing workflow tests.
- [ ] Commit only the hotfix as `fix: deduplicate production email recipients`.

### Task 2: Machine-readable Source Registry and runtime role audit

**Files:**
- Create: `src/radar/source_registry.py`
- Create: `tests/test_source_registry.py`
- Modify: `src/radar/domain.py`
- Modify: `src/radar/providers.py`
- Modify: `src/radar/pipeline.py`

**Interfaces:**
- Produces: `ProviderRole`, `ProviderCapability`, `SourceRegistryEntry`, `SourceCoverage`.
- Produces: `build_source_registry(settings, universe, providers) -> tuple[SourceRegistryEntry, ...]`.
- Produces: runtime source coverage diagnostics keyed by provider id.

- [ ] Write failing tests for every current production provider, explicit role/capability, credential declaration, region/asset coverage, stable order, and missing metadata rejection.
- [ ] Verify the registry tests fail because the typed registry is absent.
- [ ] Implement immutable strict models and derive registry entries from actual provider construction/configuration.
- [ ] Map existing Yahoo/FRED/BLS/SEC/RSS providers to explicit discovery/evidence/calendar/market/filings roles without changing source authority.
- [ ] Attach runtime health and item counts to registry coverage diagnostics.
- [ ] Run focused tests and commit as `feat: add C9 source registry`.

### Task 3: Bounded high-quality news discovery lane

**Files:**
- Modify: `src/radar/providers.py`
- Modify: `src/radar/domain.py`
- Modify: `config/universe.json`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_ingestion.py`
- Add: verified XML fixtures under `tests/fixtures/`

**Interfaces:**
- Consumes: existing `OfficialRSSProvider` transport/parser limits.
- Produces: discovery `SourceDocument` objects with `SourceRole.SECONDARY`, explicit discovery category/metadata, and bounded diagnostics.

- [ ] Verify candidate public endpoints manually for attribution, stable timestamps, public feed behavior, payload size, and access restrictions; document rejected candidates in registry notes.
- [ ] Add failing provider tests for a successful discovery feed, malformed feed, unavailable feed, duplicate headlines/URLs, stale items, and byte/item limits.
- [ ] Extend source categories/config metadata so discovery and primary evidence are distinguishable at runtime.
- [ ] Configure only verified public feeds; do not scrape article bodies or add credential requirements.
- [ ] Confirm one provider failure does not fail other providers or promote a headline as causal evidence.
- [ ] Run provider/ingestion tests and commit as `feat: expand bounded financial news discovery`.

### Task 4: Canonical discovery cluster projection and explainable value

**Files:**
- Create: `src/radar/discovery.py`
- Create: `tests/test_discovery.py`
- Modify: `src/radar/story.py`
- Modify: `src/radar/email_intelligence.py`

**Interfaces:**
- Produces: `ImportanceComponents`, `DiscoveryCluster`, `DiscoveryDiagnostics`.
- Produces: `build_discovery_clusters(stories, documents, events, scores, *, now) -> DiscoveryResult`.
- Consumes: canonical story ids and existing deterministic story membership.

- [ ] Write failing tests for same event across discovery/official sources, source diversity retention, source-count non-inflation, similar but unrelated titles, numeric conflicts, region/reference-period conflicts, malformed source isolation, and deterministic permutation invariance.
- [ ] Add failing tests for structured importance components and 0–5 evidence/value-gated Focus candidates.
- [ ] Implement a projection over canonical Stories; do not create an alternate LLM story identity system.
- [ ] Add deterministic component scoring and rejection reasons while preserving C7 ranking objects unchanged.
- [ ] Feed eligible cluster identities into `EmailIntelligence` cross-section deduplication.
- [ ] Run discovery/story/email tests and commit as `feat: add canonical discovery clusters`.

### Task 5: Market-first anomaly discovery

**Files:**
- Create: `src/radar/market_discovery.py`
- Create: `tests/test_market_discovery.py`
- Modify: `src/radar/domain.py`
- Modify: `config/universe.json`
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/email_intelligence.py`

**Interfaces:**
- Produces: `MarketAnomaly`, `AnomalyRule`, `EvidenceNeed`, `MarketDiscoveryResult`.
- Produces: `discover_market_anomalies(markets, assets, *, as_of) -> MarketDiscoveryResult`.

- [ ] Write failing tests for an abnormal 1D move, normal move, 5D context, sufficient/insufficient standardized history, VIX/VXN grouping, Dow/NDX divergence, rate/equity divergence, and anomaly-without-driver unresolved state.
- [ ] Implement transparent thresholds and history sufficiency; every signal stores inputs and rule id.
- [ ] Expand the market universe only for reliable assets supported by current providers.
- [ ] Connect anomaly evidence needs to existing relationship/driver validation, never directly to causal prose.
- [ ] Render supported explanation or localized unresolved state through the Email projection.
- [ ] Run anomaly/pipeline/email tests and commit as `feat: add market anomaly discovery`.

### Task 6: Future catalyst calendar

**Files:**
- Create: `src/radar/calendar.py`
- Create: `tests/test_calendar.py`
- Modify: `src/radar/providers.py`
- Modify: `config/universe.json`
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/email_intelligence.py`

**Interfaces:**
- Produces: `ScheduledCatalyst`, `CalendarBatch`, `CalendarDiagnostics`.
- Produces: `build_future_catalysts(calendar_items, confirmation_conditions, *, as_of) -> tuple[EmailCatalyst, ...]`.

- [ ] Verify official/public macro and central-bank schedule endpoints before integration.
- [ ] Write failing tests for future/past events, timezone conversion, duplicate identity, supported rescheduling, malformed/unavailable sources, stale rejection, and generic-monitor rejection.
- [ ] Implement immutable scheduled event identity and bounded provider adapters for verified sources.
- [ ] Deduplicate future events by canonical identity and source-supported schedule state.
- [ ] Replace news-derived generic Next items with scheduled catalysts or precise supported confirmation conditions only.
- [ ] Run calendar/email tests and commit as `feat: add evidence-backed catalyst calendar`.

### Task 7: China capability provider layer

**Files:**
- Create: `src/radar/china_market.py`
- Create: `tests/test_china_market.py`
- Modify: `src/radar/providers.py`
- Modify: `config/universe.json`
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/email_intelligence.py`

**Interfaces:**
- Produces: `ChinaCapabilityState`, `ChinaIndexSnapshot`, `ChinaSectorSnapshot`, `ChinaBreadth`, `ChinaActivity`, `ChinaLeadership`, `ChinaMarketIntelligence`.
- Produces: provider-attributed partial capability results with observed time and freshness.

- [ ] Investigate stable public endpoints for core indices, recognized sector taxonomy, breadth, turnover, and style; reject brittle or unattributable sources.
- [ ] Write failing full/partial/unavailable/stale capability tests, taxonomy-label tests, and no-proxy-inference tests.
- [ ] Add reliable core index coverage including CSI A500/CSI 1000 only where the provider supplies valid history.
- [ ] Implement each verified structured capability independently so one unavailable capability does not erase others.
- [ ] Integrate typed China intelligence into Email without demo backfill and keep Web-compatible existing inputs unchanged.
- [ ] Run China/provider/email/Web regression tests and commit as `feat: add capability-driven China intelligence`.

### Task 8: Coverage Audit and miss attribution

**Files:**
- Create: `src/radar/coverage.py`
- Create: `tests/test_coverage.py`
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/domain.py`

**Interfaces:**
- Produces: `BenchmarkStory`, `CoverageMiss`, `CoverageAudit`, `audit_coverage(benchmark, clusters, evidence_resolved_ids, focus_ids) -> CoverageAudit`.

- [ ] Write failing tests for benchmark detection, explicit miss stages, duplicate benchmark stories, unavailable benchmark provider, evidence resolution rate, and hand-calculated recall.
- [ ] Implement identity-first benchmark deduplication using configured discovery provenance.
- [ ] Attribute each miss to the first known funnel stage without inventing benchmark truth.
- [ ] Emit recall only when benchmark provenance and quality are sufficient; otherwise emit `not_measurable` with reason.
- [ ] Add geography, asset, catalyst, China, source diversity, and unresolved-driver coverage diagnostics.
- [ ] Run coverage/pipeline tests and commit as `feat: add major story coverage audit`.

### Task 9: Validated C9 Email intelligence and AI brief

**Files:**
- Modify: `src/radar/email_intelligence.py`
- Modify: `src/radar/localization.py`
- Modify: `src/radar/report.py`
- Modify: `templates/daily_email.html.j2`
- Modify: `tests/test_email_intelligence.py`
- Modify: `tests/test_localization.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: validated clusters, anomalies, catalysts, China intelligence, coverage audit.
- Produces: frozen `EmailIntelligence` with Focus, Market Signals, China, Next, concise AI brief, and diagnostics.

- [ ] Write failing quiet-day and busy-day tests for Focus 0–5, cross-section uniqueness, conditional explanation fields, source links, natural Chinese, and hidden internal enums.
- [ ] Write failing AI validation tests for unsupported fact, cause, catalyst, identity change, and deterministic fallback.
- [ ] Extend Email projection inputs while preserving canonical ids and ordering truth.
- [ ] Generate 2–4 Chinese paragraphs from validated facts only; use a useful deterministic quiet-market fallback rather than diagnostic counts.
- [ ] Make only minimal content-model changes to the accepted blue/white email template.
- [ ] Confirm Web renderer output and templates are unchanged except shared-compatible data contracts.
- [ ] Run email/localization/report/Web tests and commit as `feat: integrate C9 email intelligence`.

### Task 10: Pipeline observability and immutable snapshot integration

**Files:**
- Modify: `src/radar/pipeline.py`
- Modify: `src/radar/domain.py`
- Modify: `src/radar/storage.py` only if persistence is necessary for retry identity
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_c9_pipeline.py`
- Modify: `tests/test_workflow.py`

**Interfaces:**
- Produces: complete C9 funnel in `PipelineResult` and structured logs.
- Preserves: existing run key, daily briefing snapshot, localization snapshot, and delivery identity semantics.

- [ ] Write failing integration tests for provider-role diagnostics, discovery counts, clusters, anomalies, calendar, evidence resolution, rejection stages, Focus, catalysts, China state, coverage audit, and AI/localization state.
- [ ] Add retry/snapshot tests proving canonical and localized identities are reused rather than recomputed inconsistently.
- [ ] Wire staged C9 components after collection and before Email projection.
- [ ] Keep optional provider failures independent and classify quality degradation honestly.
- [ ] Confirm no Web publishing variables, CTA, or deployment infrastructure are introduced.
- [ ] Run pipeline/workflow/storage/Web separation tests and commit as `feat: expose C9 discovery diagnostics`.

### Task 11: Automated quality gates and one live file-only QA

**Files:**
- Modify only defects proven by QA, always with a failing regression test first.
- Produce: `outputs/c9-live-file-qa-*/` artifacts outside committed source.

**Interfaces:**
- Consumes: existing production environment with `EMAIL_MODE=file` or disabled SMTP.
- Produces: Email `.eml`/HTML, run result, diagnostics, coverage audit report, and acceptance findings.

- [ ] Run all focused C9 suites.
- [ ] Run full `pytest -q` with Git Bash available in PATH.
- [ ] Run `ruff check .`, `ruff format --check .`, strict `mypy src`, `python -m compileall -q src tests`, lock validation, and `git diff --check`.
- [ ] Run exactly one live AI-enabled zh-CN file-only QA with SMTP disabled.
- [ ] Inspect actual Email content for major-story recall, Focus usefulness, clustering, causal safety, anomaly resolution, China improvement, future catalysts, AI usefulness, Chinese quality, enum leakage, repetition, and 3–5 minute readability.
- [ ] Run Coverage Audit against the live benchmark universe and document misses/reasons.
- [ ] For each product defect, add a failing regression test, fix it, rerun relevant tests, then repeat full gates. Do not send email.
- [ ] Commit verified QA fixes and artifacts/notes that are suitable for source control.

### Task 12: PR, CI, merge, and exactly one final production delivery

**Files:**
- No new product scope; only CI-proven fixes with regression tests.

**Interfaces:**
- Produces: PR to `main`, merge commit, synchronized main, one production workflow run, and final delivery report.

- [ ] Verify clean branch and summarize commits, config/dependency/migration impact, and acceptance gates.
- [ ] Push `codex/c9-global-discovery-coverage` without force and create a PR to `main`.
- [ ] Attach the PR to the task and wait for all GitHub CI checks.
- [ ] If CI fails, reproduce, add/adjust regression coverage, fix, rerun local gates, push, and wait again.
- [ ] Merge only when CI, live file-only QA, safety contracts, and C9 acceptance gates pass.
- [ ] Sync local `main`, verify `main == origin/main == merge result`, and confirm no tracked working-tree changes.
- [ ] Trigger exactly one production workflow from merged main with live data, AI enabled, zh-CN localization, and configured production recipients.
- [ ] Verify SMTP acceptance, normalized unique recipient count, artifacts, provider health, AI/localization, Focus, catalysts, coverage audit, and China state. Do not resend for optional degradation.
- [ ] Deliver the final report with C9.0 root cause/hotfix SHA, architecture, source coverage, funnel, Coverage Audit, China capabilities, Email contents/status, quality gates, PR/merge/main identities, working tree, and limitations.
