# C8 Email Intelligence Product Design

**Date:** 2026-09-19  
**Status:** Approved product boundary; implementation design  
**Primary product:** Email Morning Brief  
**Secondary artifact:** Existing C7 Web Report, retained for diagnostics only

## 1. Product outcome

C8 succeeds when a reader can open only the daily email and understand:

1. what the global market is trading;
2. the three to five highest-information developments, or fewer when the day is quiet;
3. why each development matters and which assets or themes it affects;
4. how markets reacted, without turning association into causation;
5. which facts remain unresolved; and
6. which genuinely future-dated catalysts deserve attention next.

The email is Chinese-first. English is a compact source/reference aid, not a duplicate report.

## 2. Non-goals and invariants

- Do not build Web publishing, GitHub Pages, public URLs, `WEB_REPORT_BASE_URL`, or an email Web CTA.
- Do not remove or redesign the C7 Liquid Glass Web renderer. It must remain generatable and tested.
- Do not redesign the Admin Console.
- Do not let Email presentation mutate canonical `Evidence`, `Claim`, `Event`, story identity, snapshots, or ranking records.
- Do not fabricate news, drivers, future catalysts, China market statistics, or missing provider data.
- Do not fill a section merely to reach a target count.

## 3. Architecture

The pipeline remains layered:

```text
Providers
  -> canonical documents / market observations
  -> story consolidation and event extraction
  -> canonical scoring and ranking
  -> immutable daily briefing snapshot
  -> C8 email intelligence projection
  -> localization projection
  -> Email renderer

Existing immutable briefing
  -> existing C7 Web projection and renderer (compatibility path)
```

The new C8 email projection is a frozen, validated presentation model. It references canonical IDs and source links but owns only presentation decisions such as section membership, cluster labels, concise explanatory fields, and display ordering. It never writes back into canonical records.

## 4. Provider and discovery strategy

Use a two-lane discovery model:

### 4.1 Scheduled high-recall collection

- Keep authoritative official/company feeds.
- Add only sources with stable attribution, timestamps, canonical URLs, bounded payloads, and acceptable terms/access behavior.
- Separate primary-source authority from secondary-source discovery value.
- Apply per-provider lookback, item and response-size budgets.
- Emit provider diagnostics for expected/collected/filtered/failed items, latency where available, and normalized failure categories.

### 4.2 Market-first evidence search

Abnormal market moves are detected deterministically from observations before presentation. A move opens an evidence need. Existing collected documents are searched for temporally and semantically eligible drivers. If no sufficiently reliable evidence survives validation, the product says the driver is unresolved.

C8 will not introduce an unbounded search crawler. Additional network retrieval must be provider-based, bounded, observable and testable.

## 5. Information value and clustering

### 5.1 Eligibility

A candidate may enter Today's Focus only when it passes:

- materiality/relevance threshold;
- freshness and information-gain policy;
- reliable evidence threshold;
- cross-section distinctness;
- a minimum explanation completeness check.

Zero Focus items is valid. Five is a maximum, not a quota.

### 5.2 Intelligence clusters

Presentation clusters group items that describe the same investable phenomenon. Cluster keys use canonical event/story identities first, then deterministic theme/asset/time-window keys. Examples include:

- VIX and VXN falling together -> one volatility cluster;
- Fed decision, front-end yield repricing and equity reaction -> one policy-transmission cluster when evidence supports the relationship;
- several articles about one regulatory action -> one regulatory cluster.

Within a cluster, the highest-information representative becomes the Focus item and the remaining observations become supporting evidence or market reaction context. This is presentation-level consolidation; canonical records remain unchanged.

### 5.3 Cross-section exclusion

An identity registry records every cluster shown in Focus, Market Signals, China and Next. The same cluster cannot reappear as a separate high-priority item. Supporting references are allowed when they add a different role, such as a market reaction under a news event.

## 6. Freshness consistency

Freshness is computed once for the email projection using the briefing's immutable timestamps and persisted story state. Every downstream section consumes the same decision:

- `current`: eligible for Focus if other thresholds pass;
- `aging_with_gain`: eligible only when a material follow-up adds information;
- `demoted`: secondary/context only;
- `stale`: excluded from Focus and future catalysts.

A stale or demoted story cannot re-enter “接下来关注” merely through a generic monitoring sentence.

## 7. Email information model

### 7.1 Header

Date, concise product promise, quality state only when actionable.

### 7.2 Market Snapshot

Compact global strip plus one natural-language regime summary. Internal signal enums are never displayed.

### 7.3 Today's Focus

Zero to five distinct clusters. Each item exposes:

- `发生了什么`
- `为什么重要`
- `影响什么`
- `市场如何反应`
- `仍未知`
- evidence/source links

Missing evidence is stated plainly. Empty fields are omitted rather than filled with boilerplate.

### 7.4 Market Signals

Consolidated deterministic signals and abnormal moves not already fully represented in Focus. Related volatility indices form one signal. Observation, interpretation and unknown are expressed in reader language.

### 7.5 China / A-Shares

Capability-driven subsections:

- core indices;
- Shenwan industry leaders/laggards;
- advance/decline breadth;
- turnover/activity;
- style rotation.

Every capability has provider provenance and an availability state. Partial coverage is displayed partially and labelled; unavailable data is not inferred.

### 7.6 接下来关注

This replaces the overlapping Watch Today / What Matters Next presentation. Items require a real future catalyst, scheduled release/decision, or a precise confirmation condition supported by current evidence. Generic “monitor for updates” text is ineligible.

### 7.7 AI Market Brief

Two to four concise Chinese paragraphs synthesized only from the validated email projection. It must connect regime, leading Focus clusters, market reaction and next catalysts. It cannot introduce new facts or causes. If AI output fails validation, a deterministic concise summary is used.

### 7.8 Sources and quality

Compact provider health, material coverage gaps, unresolved drivers, and source links. Detailed diagnostics remain in run logs/artifacts.

## 8. Localization

Localization happens after canonical intelligence and before final email rendering. Structured field identities and source URLs are fixed. Validators reject missing required Chinese fields, identity drift, accidental enum leakage and unsupported factual additions. English is limited to names, source titles where useful, and a compact reference block.

## 9. Email rendering

Keep the current blue/white visual system and table-based email compatibility. Improvements are limited to:

- clearer hierarchy between snapshot, Focus, signals and future catalysts;
- less repeated copy;
- shorter evidence metadata;
- mobile-safe spacing and typography;
- restrained market colors;
- no interactive Web CSS, scripts or glass effects.

The saved HTML report continues to use the existing Web renderer. The delivered email uses the C8 email projection and email template.

## 10. Observability and quality gates

Diagnostics must report:

- provider attempts, success/no-data/degraded/failure and item funnel;
- documents and stories before/after canonical deduplication;
- Focus candidates rejected by freshness, evidence, value or cluster duplication;
- final cluster count and section membership;
- unresolved abnormal moves;
- China capability coverage;
- localization and AI-summary validation outcomes.

Product safeguards include tests for empty quiet-day Focus, volatility clustering, stale-story exclusion from future catalysts, cross-section uniqueness, natural-language output, unsupported-cause rejection, partial China coverage and Email/Web separation.

## 11. Production acceptance

After automated verification, run the real production pipeline once using the existing environment and inspect the actual email HTML for hierarchy, duplication, Chinese completeness, links and mobile-safe structure. If delivery is already authorized, send exactly one final C8 validation email. The final report records provider coverage, recall, ranking/dedup decisions, China capabilities, test results and limitations.

The C8 branch may be pushed and a pull request created for review, but it must not be merged automatically.
