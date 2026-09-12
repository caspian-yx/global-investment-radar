# C9 Daily Intelligence Product Design

## Status and scope

C9 is an approved product-layer phase over the validated C5-C8 intelligence
contracts. It selects, composes, persists, and renders a narrow daily briefing.
It does not create factual truth, re-cluster Stories, change C7/C8 scoring, alter
C2 evidence ownership, add providers, or make AI necessary.

## Architecture

```text
canonical Stories / Events
        + C7 scores
        + C8 impacts
        + C2 bundles / optional C3 explanations
        + provenance / market observations / quality
                         |
                         v
             deterministic C9 selection
                         |
                         v
              strict briefing composition
                         |
              +----------+----------+
              |                     |
      immutable snapshot       thin email template
        migration 006          mobile-first HTML
```

`src/radar/briefing.py` owns product meaning. Jinja receives one strict
`DailyIntelligenceBrief` and renders it without ranking, grouping, or evidence
decisions.

## Canonical models

C9 uses frozen, strict, extra-forbid models and the centralized rule identity
`c9-v1`. The root snapshot records run/view/rule identity, report date,
composition time, AI state, selected items, section order, explanation
provenance, displayed market reactions, source snapshots, market snapshot, data
quality, and controlled selection reasons.

The user-facing model contains:

- `DailyIntelligenceBrief`
- `BriefingStoryCard`
- `OvernightBullet`
- `BriefingMarketReaction`
- `UnderRadarItem`
- `WatchItem`
- `MarketSnapshotGroup`
- `BriefingDataQuality`
- `C9Diagnostics`

Internal IDs stay in the persisted model where required for reconstruction but
are not rendered by the email template.

## Candidate eligibility and ordering

Candidates are canonical Events with a selected-view C7 score. Story-backed
Events use their canonical Story and presentation document; non-Story canonical
macro Events remain eligible. Market/Risk Events are supporting market context,
not primary Story candidates.

Primary eligibility:

- every `MUST_READ` candidate;
- every `IMPORTANT` candidate;
- no `BACKGROUND` or `IGNORE` candidate;
- `WATCH` candidates do not fill primary whitespace.

Base order is deterministic and independent of input order:

1. priority band;
2. importance band;
3. relevance band;
4. importance score;
5. relevance score;
6. Event occurred time, newest first;
7. Event ID.

All CRITICAL candidates are retained before diversity is applied. The normal
primary target is at most five, but the cap is soft if more than five distinct
CRITICAL candidates exist. The minimum is not forced: two strong candidates
produce a two-card brief.

## Conservative presentation suppression

C6.3 remains the only canonical Story clustering authority. C9 suppression is
temporary and does not mutate Story membership or persistence.

A candidate is presentation-redundant only when either:

- it has the same canonical Story ID; or
- both Story identity profiles have the same non-empty authority, release
  domain, reference period, EventType, and region.

No fuzzy titles, embeddings, LLM clustering, or transitive merge is used. A
suppressed item remains auditable and may qualify for Under the Radar only when
it is not redundant with a displayed primary item.

## Diversity

Diversity is a secondary-slot preference among candidates of comparable product
strength. It never excludes CRITICAL news and never promotes weak noise.

For non-CRITICAL slots, C9 prefers candidates that add a new region or EventType
within the same priority and importance tier. Soft saturation limits are two
items per EventType, two per primary canonical entity, and three per region.
When no equally strong diverse alternative exists, the strongest material item
wins rather than a weaker candidate.

## Under the Radar

Up to three non-primary, non-redundant candidates are selected when they are:

- MATERIAL but lost primary slot competition; or
- IMPORTANT and still worth preserving; or
- ROUTINE with HIGH relevance.

LOW-importance filler is never added merely to reach a quota. Ordering uses the
same stable product order.

## Composition

`Overnight in 30 Seconds` is derived only from selected primary cards, in the
same order, with at most five bullets. It combines the canonical development,
the chosen evidence-backed implication, and at most one validated market
reaction. It is not a second ranking pass.

The `Why it matters` hierarchy is:

1. the matching candidate explanation from a valid StructuredExplanation;
2. the linked C2 interpretation Claim from an ExplanationBundle;
3. deterministic C7/EventType semantics;
4. a conservative statement that no evidence-backed market driver is available.

AI may improve explanation prose only. It never changes eligibility, selected
IDs, section assignment, or ordering. Disabled, failed, malformed, or
budget-exhausted AI produces the same selection and a complete deterministic
brief.

## C8 presentation

Only persisted/evaluated C8 records are displayed. Per Story, at most two are
shown in this order: direct target, configured broad market, stronger magnitude,
stronger association, symbol. This changes display only, never impact identity.

FLAT/SMALL copy says the immediate reaction was limited. LOW and ambiguous
associations explicitly state attribution uncertainty. C9 never writes caused,
driven by, triggered, or equivalent causal language from a C8 association.

## Watch Today

The repository has no trusted future-event calendar, so C9 creates no dates,
meetings, earnings releases, or catalysts. Up to three items monitor:

- follow-through in an already linked C8 market target; or
- unresolved follow-up to an existing material POLICY, MACRO, LEGAL, GUIDANCE,
  or SUPPLY_CHAIN Event.

Every item references an existing Event and carries a controlled reason.

## Market snapshot and quality

The market snapshot is secondary and groups existing observations into US,
Asia, Europe, FX, Commodities, Crypto, and Risk/Volatility where present.
Nothing is fetched for C9.

The visible quality footer shows only report status, document/Event/market
counts, AI state, and whether validated market-reaction coverage is present.
Provider-level diagnostics and reason codes remain in logs.

## Persistence

Migration `006_c9_daily_briefings` adds `c9_daily_briefings` with unique identity
`run_id + view_id + rule_version`. The full strict snapshot is stored as JSON;
HTML is not the source of truth.

An identical snapshot is idempotent. A different payload for the same identity
fails closed. Different runs, views, and rule versions coexist, preserving
historical interpretation after configuration changes.

## Visual system

The email is a restrained editorial morning brief, not a dashboard.

- Ink: `#14243A`
- Signal blue: `#285A8E`
- Paper: `#F7F8FA`
- Rule: `#D9E0E7`
- Positive: `#246B4A`
- Caution: `#8A6423`

Typography uses email-safe Aptos/Segoe UI/Arial fallbacks. The memorable element
is a compact dark `30 Seconds` briefing block; all later sections use whitespace
and editorial rules instead of repeated rounded cards. Content is left aligned
inside a fluid `max-width:680px` table. No gradients, scripts, external CSS,
fixed-width data tables, or interactive controls are used.

## Failure and empty states

- No strong Stories: render a concise low-activity brief, no filler.
- No C8 impact: omit per-card reaction and state that no validated linked
  reaction is available in the section.
- No explanation: use the conservative deterministic fallback.
- Limited ingestion: surface one compact quality note.
- AI disabled/failure: retain the complete deterministic product.

## Non-goals

C9 excludes bilingual rendering, company intelligence/scoring, thesis memory,
prediction evaluation, learned rules, weekly reports, web apps, embeddings,
new providers/APIs, portfolio advice, BUY/SELL signals, LLM ranking, C9.1, and
C10.
