# C9 Production Latency Hardening Design

## Incident evidence

Daily Radar run `35503657014` started the pipeline at 09:56:20 UTC, completed
collection and deterministic intelligence at 09:57:41 UTC, and was cancelled by
the 25-minute GitHub Actions ceiling at 10:21:24 UTC. It never logged report
quality, localization, rendering, or email delivery. The uploaded result JSON was
empty and no report was produced.

The preceding C8 run on the same PostgreSQL database spent 547.8 seconds between
`Collection complete` and `Report quality` for 30 documents, 32 events, and 288
C7 score rows. C9 expanded those counts to 106 documents, 109 events, and 981 C7
score rows. The storage implementation opens a transaction and performs existence
checks for every row. After the cancelled C9 run, the shared database contained
698 C7 rows for the deterministic run identity, up from the preceding run's 288,
which confirms cancellation occurred during row-at-a-time persistence.

The legacy analysis provider used in this path is deterministic-disabled for the
configured DeepSeek provider, so the six legacy `analyze()` calls did not perform
network requests. Source collection completed in 81.5 seconds. Neither is the
primary timeout cause.

## Design

1. Replace hot-path row-at-a-time transactions with bounded bulk persistence.
   Materialize and validate each collection, preload existing identities in one
   query, then execute set-oriented inserts/updates inside one transaction per
   artifact type. Preserve all immutable identity and conflict checks.
2. Add structured stage timing for startup, collection, discovery, evidence and
   market intelligence, persistence, localization, rendering, and delivery.
   DeepSeek request logging records request type, outcome, phase, and elapsed time
   without prompts or credentials.
3. Keep the workflow's 25-minute outer ceiling. The internal contract is that
   persistence is set-oriented and all external AI requests retain bounded
   per-request timeouts. Optional AI/localization failures degrade deterministically.
4. Make delivery structurally ineligible until quality acceptance and report
   persistence have completed. Cancellation or exceptions before that point cannot
   enter SMTP delivery.
5. Preserve canonical Evidence, Claim, Event, Story, ranking, briefing snapshot,
   localization snapshot, and recipient normalization identities.

## Non-goals

- No Web renderer or Admin Console changes.
- No evidence threshold, ranking, or unresolved-driver semantic changes.
- No longer workflow timeout as the primary fix.
- No new provider, dependency, migration, or fabricated fallback.
