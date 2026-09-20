# C9 Global Discovery & Coverage Engine Design

**Date:** 2026-09-20  
**Status:** Approved for implementation  
**Primary product:** Chinese-first Email Morning Brief  
**Frozen artifact:** Existing C7 Web renderer

## 1. Outcome

C9 expands what Global Investment Radar can discover while preserving the evidence-first contracts established in C7 and C8. The Morning Brief must know materially more without filling sections, promoting unsupported headlines, inventing drivers, or mutating canonical Evidence, Claim, Event, Story, ranking, localization, or snapshot truth.

The production flow becomes:

```text
heterogeneous discovery + market anomalies + future calendar + China capabilities
  -> canonical source documents / observations / scheduled events
  -> deterministic story clustering and anomaly classification
  -> importance and freshness gates
  -> evidence resolution
  -> validated intelligence clusters
  -> Email intelligence projection
  -> validated zh-CN synthesis
  -> Email renderer
```

The existing C7 Web projection remains an optional diagnostic artifact. C9 does not publish, redesign, or add a Web call-to-action.

## 2. C9.0 delivery hotfix

The current single-delivery path passes the raw comma-separated `EMAIL_TO` string directly to `EmailMessage["To"]`. Python then derives SMTP envelope recipients from that header without project-level normalization or deduplication. Duplicate addresses, whitespace variants, and case variants can therefore reach the SMTP server more than once.

C9 introduces one canonical recipient resolver:

```python
normalize_recipients(*sources: str | Iterable[str] | None) -> tuple[str, ...]
```

It trims values, ignores empty entries, validates addresses, deduplicates case-insensitively, preserves first-seen deterministic order, and combines all configuration sources before delivery. The single-delivery path performs exactly one SMTP submission with the deduplicated envelope. The audience path continues one recipient-specific submission per normalized unique profile and rejects or suppresses cross-path duplication before SMTP.

Application retries may retry only before SMTP acceptance is known. Persisted audience delivery identities continue preventing deterministic resends. C9 does not claim distributed exactly-once semantics after an ambiguous network failure.

The hotfix is implemented and committed independently before C9 feature work. Tests exercise actual mocked SMTP envelope behavior; no real email is sent.

## 3. Source Registry and roles

`source_registry.py` owns immutable provider metadata and runtime coverage summaries. Each entry identifies:

- provider id and display name;
- provider type and explicit role;
- regions, asset classes, and content domains;
- discovery, evidence, calendar, market-data, China, and filings capabilities;
- authority level and credential requirement;
- access/stability notes;
- runtime status, last success, item funnel, and coverage state.

Roles are explicit and non-interchangeable:

- `PRIMARY_EVIDENCE`
- `NEWS_DISCOVERY`
- `MARKET_DISCOVERY`
- `CALENDAR`
- `CHINA_MARKET_STRUCTURE`
- `COMPANY_FILINGS`
- `SECONDARY_DISCOVERY`

High-quality news may discover a candidate but cannot independently establish a causal driver. Official sources and independent corroboration remain evidence inputs. The registry is machine-readable, emitted in diagnostics, and derived from runtime provider construction rather than stale prose.

## 4. Discovery providers

C9 adds a bounded generic public-feed discovery adapter by extending the existing RSS/Atom boundary rather than building site-specific scrapers. Candidate feeds must be public, stable enough for automated consumption, attributable, timestamped, bounded by item/byte/lookback limits, and free of access-control bypasses. Paywalled article bodies are never scraped.

Configuration distinguishes `NEWS_DISCOVERY` feeds from primary evidence feeds. Discovery documents retain source identity and may enter story clustering, but evidence resolution and importance gates determine whether the cluster can enter Focus.

Provider failures are independent. Each provider reports attempted, fetched, parsed, accepted, rejected, duplicate, stale, malformed, and limited counts where applicable.

## 5. Canonical discovery clusters

The existing deterministic Story layer remains canonical. C9 extends its inputs and exposes a focused `DiscoveryCluster` projection containing canonical story identity, entities, region, topics, timestamps, source diversity, authority mix, supporting evidence, market observations, freshness, importance components, confidence, and unresolved fields.

Identity-first matching uses canonical story ids, event type, entities, reference period, region, action terms, hard numeric conflicts, and bounded title similarity. Source count alone never increases importance, and unrestricted LLM clustering is prohibited.

## 6. Explainable importance

`ImportanceComponents` records bounded deterministic components for market magnitude, breadth, systemic relevance, policy/company materiality, cross-asset confirmation, authority, independent source diversity, evidence strength, freshness, information gain, geographic significance, persistence, and future implications.

The component vector produces an explainable ordering but does not overwrite C7 ranking truth. Email Focus remains 0–5 clusters. Existing quality thresholds are not lowered to fill the section.

## 7. Market anomaly discovery

`market_discovery.py` evaluates validated market history using understandable contracts:

- absolute 1D and 5D movement;
- standardized 1D movement when sufficient history exists;
- percentile or rolling-volatility context when sufficient history exists;
- deterministic cross-index, volatility, rate/equity, and sector/index divergences.

Every signal records its rule, inputs, threshold, history sufficiency, affected assets, and observation timestamp. Related VIX/VXN signals share a cluster. An anomaly opens an evidence need; it never supplies its own cause. Evidence resolution produces either supported driver references or the localized unresolved-driver statement.

The market universe expands only through reliable existing Yahoo/FRED-style interfaces and remains fail-closed when history is insufficient.

## 8. Future catalyst calendar

`calendar.py` defines immutable `ScheduledCatalyst` records with canonical identity, scheduled timestamp, timezone, region, category, entities/assets, provenance, and deterministic importance metadata.

Adapters favor official/public schedules for macro releases and central-bank events. Earnings are added only when a maintainable public source is verified. Past events are excluded, duplicate identities collapse, and rescheduled events replace prior schedule state only with source support.

Email “接下来关注” accepts only a future scheduled catalyst or a precise evidence-backed confirmation/invalidation condition. Generic monitoring text is ineligible.

## 9. China market capability layer

`china_market.py` separates capabilities instead of presenting one ticker list as complete China intelligence:

- core indices;
- recognized sector taxonomy and ranking;
- advance/decline breadth;
- turnover/activity;
- style and leadership.

Each capability exposes provider, observed time, freshness, availability, and coverage state. C9 will integrate only publicly accessible, attributable, stable-enough endpoints verified during implementation. If a capability cannot be safely supported, it remains explicitly unavailable; no proxy inference, demo backfill, or mislabeled Shenwan data is allowed.

## 10. Coverage Audit

`coverage.py` compares canonical Radar candidates and selections with an independently sourced benchmark universe built from configured high-quality discovery providers. Benchmark entries retain provenance and do not dictate Radar ranking.

The audit reports:

- benchmark story count and deduplicated identities;
- discovered candidates;
- evidence-resolved candidates;
- Focus selections;
- missed stories with the first attributable miss stage: provider unavailable, not ingested, dedup/classification gap, freshness rejection, importance rejection, evidence failure, geography gap, or asset-class gap;
- recall metrics only when benchmark quality and provenance are sufficient;
- source, geography, asset-class, catalyst, China, and unresolved-driver coverage.

No benchmark contents or recall values are fabricated.

## 11. Email and AI projection

The accepted blue/white, table-safe Email visual design remains unchanged except for fields needed to represent C9 intelligence. `EmailIntelligence` consumes validated clusters, anomalies, catalysts, China capabilities, and coverage diagnostics. Cross-section identity prevents the same intelligence from occupying Focus, Market Signals, China, and Next as separate primary items.

Focus fields remain conditional: 发生了什么、为什么重要、影响什么、市场如何反应、仍未知、evidence links. Empty boilerplate is omitted.

AI receives only the validated Email projection. Validation rejects unsupported facts, causes, catalysts, identity drift, and enum leakage. A deterministic 2–4 paragraph Chinese fallback explains supported regime and quiet-market conditions without exposing diagnostic counts.

## 12. Observability and cost

The run result and diagnostics expose the full staged funnel: providers and roles, provider health, fetched/parsed/accepted/rejected items, documents before/after dedup, clusters, anomalies, calendar events, evidence attempts and successes, freshness/importance/duplicate rejections, Focus and catalyst counts, China capability state, AI/localization validation, and coverage audit.

Retrieval is staged and bounded: cheap deterministic collection and deduplication precede evidence lookup; AI runs only on surviving intelligence. Provider concurrency and payload limits remain bounded. Secrets are never logged.

## 13. Safety and compatibility

- Canonical Evidence, Claim, Event, Story, ranking, snapshot, and localization identities remain immutable.
- Discovery source authority is never silently upgraded.
- No unsupported causal assertion is emitted.
- No fake production data or fixture-specific production branch is introduced.
- Email and Web renderers remain separate; C7 Web behavior is regression-tested and otherwise frozen.
- Admin Console is not redesigned.
- Provider gaps fail closed and are reported as limitations.

## 14. Verification and delivery

Implementation follows strict red/green/refactor TDD. C9.0 has its own commit. Focused suites cover recipients, source roles, discovery clustering, anomalies, freshness, calendar, China capabilities, coverage audit, AI safety, Email deduplication, and Web separation.

Before PR: full pytest, Ruff check/format check, strict mypy, compileall, lock validation, and `git diff --check` must pass. One live AI-enabled zh-CN file-only run is inspected; email delivery remains disabled.

After PR CI and acceptance gates pass, merge to `main`, synchronize local and remote main, and execute exactly one live zh-CN production delivery. Each normalized unique recipient must receive at most one logical copy. Optional provider degradation is reported without resending.
