# C5.2 Broad Global Intelligence Ingestion Pack

**Status:** Approved on 2026-09-12. Implementation remains uncommitted for independent review.

## Objective

Expand the live intelligence input portfolio with a small set of high-value public sources while preserving explicit provenance, timezone-aware publication semantics, deterministic behavior, bounded resource use, and provider failure isolation.

## Source portfolio

Keep the existing Federal Reserve, ECB, SEC newsroom, SEC filings, and BLS providers. Add independently isolated RSS/Atom providers for:

- Bank of Japan English What's New
- Bank of Korea Monetary Policy Decisions
- National Bureau of Statistics of China English releases
- U.S. EIA Today in Energy
- Apple Newsroom
- NVIDIA press releases

Each source is configured with explicit publisher, source category, source role, source tier, region, language, timezone fallback, lookback, item cap, response-byte cap, content-character cap, and enabled state.

HKMA is deferred because its feed exposes date-level rather than exact publication time. GDELT is deferred because observed shared-runner throttling and timestamp semantics do not satisfy the current deterministic provenance contract. Commercial news feeds are deferred where access or reuse terms are unsuitable.

## Attribution policy

The canonical document builder validates source claims before constructing a document:

- official, regulatory, and company-primary sources must be primary and Tier A;
- professional news must be secondary and Tier B or C;
- aggregators must be secondary and Tier C or D;
- community sources must be secondary and Tier D;
- other sources may be primary or secondary but cannot claim Tier A.

This policy is deterministic and fail-closed. It prevents configuration mistakes from silently promoting weak sources.

## Bounded RSS normalization

The shared RSS/Atom adapter will:

- reject disabled feeds before provider construction;
- enforce per-feed lookback, maximum items, response bytes, and content characters;
- parse declared offsets exactly and use a configured IANA timezone only for naive dates;
- reject missing, invalid, and future dates;
- accept only HTTP(S) links and canonicalize before provider-local deduplication;
- strip markup, collapse whitespace, and truncate content deterministically;
- sort accepted items by publication time and canonical URL before applying the item cap;
- expose aggregate, non-sensitive diagnostics and preserve existing provider status semantics.

Oversize responses fail that provider cleanly. Malformed entries are counted and skipped without aborting the feed.

## Cross-provider deduplication

A dedicated ingestion-stage function runs after collection and before story clustering:

1. Equal document IDs with equal payloads collapse.
2. Equal document IDs with conflicting payloads raise an error.
3. Documents with the same exact canonical URL collapse across providers.
4. The survivor is chosen by source category, primary/secondary role, source tier, provider, and ID in a stable total order.
5. Output is sorted deterministically and is independent of provider execution order.

No fuzzy title or story-similarity merge is added in C5.2. Existing downstream story clustering remains unchanged.

## Pipeline boundaries

Only normalized, deduplicated documents enter deterministic event extraction. Macro source documents continue through their existing persistence path. C2/C3, C6 authorization logic, AI generation, ranking, reporting, and email behavior remain unchanged except for consuming the deduplicated document list.

The entity registry gains the newly covered institutions and companies. Event-classification rules change only if representative offline fixtures demonstrate a provider-neutral gap.

## Verification

Offline tests cover every added feed shape, timezone fallback, exact offsets, malformed data, future dates, canonical URL duplicates, item/content/response limits, attribution-policy failures, deterministic cross-provider survivor selection, provider-order independence, pipeline integration, and failure isolation.

Completion requires targeted tests, the complete test suite, Ruff, strict mypy, `uv lock --check`, and `git diff --check`. No production collection, commit, or push is part of this implementation.
