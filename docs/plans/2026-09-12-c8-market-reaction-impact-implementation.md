# C8 Market Reaction & Impact Engine — Implementation Plan

**Goal:** Add a deterministic, evidence-first daily market-reaction layer without changing C2, C6, C7, AI, or provider-network scope.

**Observed data boundary:** The current market adapter requests Yahoo `10d/1d` bars and computes the latest close versus the immediately preceding valid close. C8 v1 therefore evaluates only a single daily close-to-close interval. It will retain the already-returned previous-bar timestamp, but will not claim intraday, abnormal-return, volatility-normalized, or causal precision.

**Architecture:** A new pure `radar.impact` module validates canonical target snapshots, derives C8 candidates from existing C6 ALLOW relationships plus explicit configured direct-entity targets, aligns Event time to the daily close interval, measures the raw move, classifies direction/magnitude/confidence, applies competing-event ambiguity, and emits immutable `MarketImpactRecord` values. Pipeline integration is additive after C6 relationship construction and before unchanged C2 construction. Migration 005 stores versioned evaluation snapshots. Report integration only displays bounded reactions for already selected C7 Events.

## Tasks

1. Add failing tests for strict domain models, daily timing, direction, per-profile magnitude boundaries, C6-backed and explicit-direct candidates, rejection cases, ambiguity, no reaction, stable identity, ordering, and C7 independence.
2. Extend `MarketObservation` with an optional timezone-aware `previous_observed_at`; populate it from the existing Yahoo daily response and deterministic demo data.
3. Implement `radar.impact` enums/models/rule tables and pure candidate/evaluation/diagnostic/presentation functions.
4. Add migration `005_c8_market_reactions`, schema validation, idempotent save/load methods, immutable rule-version coexistence, and persistence tests.
5. Integrate C8 into the pipeline without changing C6/C2 inputs or AI calls; persist complete eligible output and expose aggregate diagnostics.
6. Add minimal event-card report output with controlled observational/association language and no causal wording.
7. Run focused tests during TDD, then the full pytest, Ruff, strict mypy, lock check, diff check, and a final contract/diff audit.

No commit, push, production run, or C9 work is part of this plan.
