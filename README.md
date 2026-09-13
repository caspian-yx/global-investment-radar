# Global Investment Radar

Evidence-first global market, macro and event intelligence pipeline. Version 0.1 is intentionally useful with AI completely disabled and requires no paid API or LLM key for its reproducible demo run.

## Milestone scope

Implemented in this milestone:

- Provider interface with isolated failures.
- Reproducible synthetic demo provider with visible safety labels.
- Optional live providers: bounded official and company-primary feeds across the US,
  Europe, China, Korea and Japan; SEC Submissions metadata; the official BLS API;
  and an experimental Yahoo chart adapter.
- Canonical, provider-isolated intelligence ingestion with deterministic source provenance and stable document identity.
- Deterministic normalization, URL cleanup, near-duplicate clustering, event classification,
  entity mapping, explainable C7 ranking and bounded C8 daily market-reaction measurement.
- Evidence-first `SourceDocument → Evidence → Claim → Event` domain model.
- SQLite development storage and PostgreSQL-compatible SQLAlchemy schema.
- Provider-neutral text-generation contract with DeepSeek transport and a controllable fake.
- Deterministic `ExplanationBundle` prompt and strict structured-explanation parser.
- Hard no-AI behavior when `AI_ENABLED=false` or `DAILY_LLM_BUDGET=0`.
- Deterministic C9 Daily Intelligence product with an immutable briefing snapshot and
  mobile-first editorial email.
- CLI, tests and GitHub Actions CI/daily workflows.
- Cloud-MVP preparation: Asia/Shanghai business date, scheduled live/no-AI execution,
  SMTP delivery, operational logs, failure-preserved artifacts, and optional PostgreSQL.

Not implemented yet: SEC filing body/XBRL/exhibit extraction, licensed global market provider,
earnings consensus/transcripts, weekly report or dashboard.

## Quick start

Requirements: Python 3.12 and `uv`.

```bash
uv sync --all-extras
uv run radar doctor
uv run radar daily --data-mode demo --no-ai
```

The generated report is written to `outputs/reports/`. Demo records are synthetic and the report displays a prominent `DEMO DATA` banner.

## Private-beta audience delivery (C9.2)

Audience management is operator-only. Run `uv run radar admin` locally with
`AUDIENCE_ADMIN_TOKEN` configured; the server binds only to `127.0.0.1`.
The CLI and admin use the same service layer (`radar recipients ...`,
`radar editorial ...`, `radar preview`, and `radar send-test`). Preview never
sends mail, and test delivery targets only `OPERATOR_EMAIL`.

Production fan-out requires both `AUDIENCE_ENABLED=true` and the explicit
`uv run radar daily --audience` entrypoint. It also requires SMTP configuration
and a shared durable PostgreSQL `DATABASE_URL`; SQLite is suitable for local
development/tests only. The GitHub Actions workflow keeps `single` as its
default delivery target and never starts the admin server.

Recipient configuration, personal/editorial notes, and delivery records live
in the shared database and are not stored in Git. Production delivery is
at-most-once: a previously reserved `PENDING`, `SENT`, `FAILED`, or `SKIPPED`
identity is not automatically sent again. Removing a recipient archives it so
historical delivery records remain auditable.

To validate the optional structured AI path without an API call:

```bash
uv run radar daily --data-mode demo --ai --llm-provider fake
```

Fake output is labeled `TEST AI OUTPUT`. `FakeLLMProvider` is automatically blocked when `APP_ENV=production`.

## Live no-key mode

```bash
uv run radar daily --data-mode live --no-ai
```

Live mode currently combines:

- Federal Reserve monetary-policy, ECB press-release and SEC newsroom RSS feeds.
- Bank of Japan English updates, Bank of Korea monetary-policy decisions, National
  Bureau of Statistics of China English releases and U.S. EIA Today in Energy.
- Apple Newsroom and NVIDIA press releases as the initial company-primary portfolio.
- Recent SEC filing metadata for the configured CIK watchlist. Set `SEC_CONTACT_EMAIL`
  so requests carry the SEC-required application contact identity.
- BLS public macro API.
- An experimental, unofficial Yahoo chart endpoint for market snapshots.

The market adapter is suitable for zero-cost development, not a production SLA. The HTML report shows the warning. Replace it through the provider interface when a licensed source is selected; no business logic needs to change.

## Intelligence ingestion

External sources enter through the single `IntelligenceProvider.collect(...)` contract. Each
provider returns a `CollectionBatch`; the orchestration seam isolates failures before passing
canonical `SourceDocument` records into the existing deterministic evidence, claim and event
pipeline:

```text
external source
  → IntelligenceProvider
  → CollectionBatch / SourceDocument
  → deterministic Evidence → Claim → Event processing
```

`SourceDocument` keeps source category, primary/secondary role and the existing deterministic
authority tier as canonical provenance. Provider-specific JSON metadata remains opaque and is
not used for downstream ranking or event semantics. Published and collected timestamps are
timezone-aware and normalized to UTC; `retrieved_at` is accepted only for legacy input/read
compatibility, while new serialization uses `collected_at`.

Source attribution is fail-closed: official, regulatory and company-primary records must be
primary Tier A sources; professional news is secondary Tier B/C; aggregators are secondary
Tier C/D; and community sources are secondary Tier D. `other` cannot claim Tier A. Each RSS
feed declares an IANA timezone fallback plus explicit lookback, item, response-byte and
content-character limits. Declared timestamp offsets always take precedence over the fallback.

Document identity prefers an explicitly trusted provider-native immutable ID, then provider plus
conservatively canonicalized URL, then stable provider/publisher/title/publication fields.
`content_hash` is a version fingerprint and no longer changes identity. This intentionally causes
a one-time ID transition for documents created under the earlier content-dependent rule; no old
database migration is performed. Future cursor, `since`, ETag and Last-Modified state can be
added at the provider boundary without changing deterministic downstream contracts.

After provider collection, exact document IDs and exact canonical URLs are deduplicated before
Story consolidation. Conflicting payloads under one ID fail closed; cross-provider URL duplicates
prefer direct primary and higher-quality sources through a stable total order. C5.2 deliberately
does not merge similar titles or inferred stories at ingestion time.

## Persistent Story consolidation

After exact ingestion deduplication, C6.3 derives a provider-neutral semantic profile for each
document and evaluates it against persistent `story-v1` identity seeds. Three conservative paths
cover near-identical titles, entity/event/action agreement, and structured policy or macro release
identity. Event type, explicit region, authority, release-domain/reference-period, action, and
strict same-dimension numeric conflicts reject a match before any positive path runs. `OTHER`
documents remain singleton unless their normalized story identity is exceptionally strong.

Story matching is deterministic and does not use provider metadata, embeddings, translation, an
LLM, or transitive fuzzy unions. Ambiguous candidates fail closed into separate Stories. The
identity seed and Story/Event IDs remain immutable; a later primary or higher-quality source may
become the presentation anchor without rewriting identity. Different bootstrap histories can
still produce different initial Story IDs, an intentional tradeoff that avoids unsafe automatic
Story-to-Story merging.

Migration `003_c6_3_story_consolidation` adds `stories` and `story_documents` without rewriting
historical Events. Every Story member keeps its own `SourceDocument → Evidence → FACT Claim`
chain, and the projected Event references the exact sorted closure. Story occurrence uses the
earliest Tier A/B publication when available; later corroboration cannot move occurrence later,
and C2 continues to evaluate each evidence document's own publication time.

HKMA remains deferred because its current feed exposes date-level publication precision.
GDELT and commercial media feeds remain deferred until rate limits, timestamp semantics and
reuse rights satisfy the same provenance contract.

## C7 importance and investor relevance

C7 scores canonical Events after Story/Event construction. Global Importance is a separate,
bounded deterministic score based on source authority, event materiality, Story scope,
configured canonical entity significance, conservative source corroboration and freshness.
Investor Relevance is scored independently against the selected `RADAR_VIEW_ID` profile using
watched entities, explicit topics/event types/asset classes and bounded region matches. Generic
lexical overlap does not create relevance, and C7 never changes C6 authorization or C2 semantics.

Configured profiles live in `config/universe.json` and are schema-validated at startup. C7
persists per-run, per-view results with rule version, view snapshot, components, reason codes,
priority band and rank through additive migration `004_c7_importance_relevance`; previous rule
versions remain attributable. The report renders the selected view's deterministic priority
bands (`MUST_READ`, `IMPORTANT`, `WATCH`, `BACKGROUND`, `IGNORE`) while canonical low-ranked
Events remain preserved.

## C8 daily market reaction and impact

C8 measures the existing Yahoo adapter's latest daily close against its immediately preceding
valid daily close. The adapter now retains both timestamps from the same response; it still makes
one request per configured symbol and does not add an intraday or historical-data provider. C8
requires an explicit configured daily close boundary and refuses incomplete, missing-time,
pre-event and stale intervals. UTC provider timestamp dates identify the two daily bar labels;
the configured reaction timezone and close time turn those labels into auditable interval bounds.

Every MarketObservation receives a runtime-only C8 target Event so flat and small moves remain
measurable. Candidate source Events are still authorized by the unchanged C6 relationship engine;
C8 does not invent cross-region links or compare generic lexical similarity. An eligible Event
must fall strictly after the reference close and no later than the completed observed close. Same
local session dates classify as `SAME_SESSION`; an Event after the prior close on an earlier local
date classifies as `NEXT_SESSION`.

Raw percentage magnitude bands are versioned under `c8-daily-close-v1`:

| Reaction profile | FLAT below | MODERATE from | LARGE from | EXTREME from |
|---|---:|---:|---:|---:|
| Equity index / ETF proxy | 0.10% | 0.75% | 1.50% | 3.00% |
| FX | 0.05% | 0.30% | 0.75% | 1.50% |
| Commodity | 0.10% | 0.75% | 1.50% | 3.00% |
| Crypto | 0.25% | 1.50% | 3.00% | 6.00% |
| Volatility index | 1.00% | 5.00% | 10.00% | 20.00% |

The interval between FLAT and MODERATE is `SMALL`. Direction is objective (`UP`, `DOWN`,
`FLAT`) and never means good/bad or bullish/bearish. Direct same-session relationships are HIGH
association confidence; direct next-session and non-direct same-session relationships are MEDIUM;
other authorized next-session relationships are LOW. Move magnitude cannot raise confidence.
Multiple valid Events sharing one target/window are all retained and capped at LOW. C8 v1 never
emits `CAUSALLY_SUPPORTED` and report language explicitly avoids causal attribution.

Migration `005_c8_market_reactions` persists the canonical Event/Story, target snapshot, raw and
interpreted interval timestamps, prices, move, classifications, C6 basis, reason/evidence closure,
evaluation run and rule version. Exact identities may refresh idempotently; different runs,
targets, windows and rule versions coexist.

## C9 Daily Intelligence product

C9 turns the validated C5-C8 artifacts into one deterministic daily product. The selection layer
consumes canonical Events/Stories and the selected view's C7 scores; MARKET_MOVE and RISK Events
remain supporting context and cannot enter the primary Story list. `MUST_READ` and `IMPORTANT`
items are eligible, every CRITICAL item is retained, and the usual five-item cap remains soft only
when more distinct CRITICAL items exist. `WATCH` never fills empty primary slots.

Ordering is stable by priority band, importance band, relevance band, both scores, newest Event
time and Event ID. Diversity is applied only within the same priority/importance strength tier:
new regions and EventTypes are preferred before soft saturation at two items per EventType, two
per primary entity and three per region. It never promotes a weaker item. Presentation suppression
is conservative: only an already identical Story or a fully matching non-empty authority,
release-domain, reference-period, EventType and region identity can be hidden. No fuzzy title,
embedding or LLM clustering occurs in C9.

The briefing contains `Overnight in 30 Seconds`, `Things That Matter`, bounded observational C8
reactions, `Under the Radar`, `Watch Today`, a grouped market snapshot and compact quality state.
Watch items refer only to existing Events or evaluated reactions; without a trusted future-event
calendar, C9 invents no dates or catalysts. Explanation copy follows a strict hierarchy: matching
structured AI candidate prose, linked C2 interpretation Claim, deterministic EventType semantics,
then a conservative unavailable statement. AI never changes selection, section assignment or
ordering, and no-AI mode produces the complete product.

Additive migration `006_c9_daily_briefings` stores the full strict snapshot under
`run_id + view_id + rule_version`. An identical write is idempotent; a different payload for the
same identity fails closed. Different runs, views and rules coexist. Because the existing daily
pipeline intentionally reuses one run identity for same-day retries, a retry reuses the already
persisted C9 snapshot instead of rewriting history.

The HTML template is a thin rendering layer over that snapshot. It uses a fluid 680px presentation
table, inline email-safe styling and no JavaScript, external CSS or client-side ranking. The market
adapter is still daily-bar only, C8 associations are not causal claims, and the product is not
investment advice.

### C9.1 Chinese localization

C9.1 is an optional presentation layer over the immutable C9 briefing. It sends at most one
structured request with the already-selected briefing payload, validates key closure, normalized
dates/currency amounts, numeric/percentage atoms, explicit market symbols, market direction,
C8 uncertainty, and the absence of causal or investment-advice language, then stores only the
validated result. It cannot select, rank, merge, explain or alter
the canonical C9 content. No source bodies or external research are supplied to the localization
request.

Localization is disabled by default. When enabled, a matching snapshot is reused before any model
call; the identity is `briefing_id + locale + localization_rule_version + requested_model` and an
immutable conflict or checksum/identity failure is fail-closed. The exact requested model is used
for identity and the exact provider-returned model is retained separately; those names may differ.
Provider errors, malformed output and validation failures make the report fall back to the unchanged
English briefing without a
repair request. A successful result renders as Chinese-first content followed by the complete
English reference, while canonical numbers, metadata, source links and C8 association wording
remain deterministic.

```dotenv
LOCALIZATION_ENABLED=false
LOCALIZATION_LOCALE=zh-CN
LOCALIZATION_MODEL=
LOCALIZATION_TIMEOUT_SECONDS=90
```

`LOCALIZATION_TIMEOUT_SECONDS` is a bounded, localization-only single-request timeout
(default 90 seconds). Full-brief structured output can take longer than short explanation
calls, while provider failure still falls back safely to the unchanged English briefing.
An HTTP 200 response event only confirms that response headers arrived; localization succeeds
only after the complete body, trusted provider response, structured JSON and all validators pass.
If a timeout occurs before that point, usage is reported as unavailable rather than zero and the
English fallback remains authoritative.

## AI configuration

AI is an optional enhancement layer. The default is deterministic:

```dotenv
AI_ENABLED=false
DAILY_LLM_BUDGET=0
AI_MAX_REQUESTS_PER_RUN=10
AI_MAX_INPUT_TOKENS_PER_RUN=
AI_MAX_OUTPUT_TOKENS_PER_RUN=
LLM_PROVIDER=disabled
```

Either `AI_ENABLED=false`, a zero daily budget, or any zero run cap prevents AI calls. Request attempts are hard-capped per run; optional input/output token caps stop later calls once trusted provider usage reaches either cap. Copy `.env.example` to the ignored `.env` and fill in `DEEPSEEK_API_KEY` for an explicitly enabled DeepSeek run.

```bash
uv run python -m radar.llm_smoke
```

The smoke command is opt-in and makes one small `/chat/completions` request. Ordinary settings construction and deterministic runs do not require a DeepSeek credential. An unknown or unavailable analysis provider continues to fall back to deterministic mode.

The structured-explanation layer preserves C2 candidate order and reference closure, accepts
only strict JSON, and attaches provider/model/usage metadata from the trusted local
`LLMResponse`. AI enrichment is optional and fail-open, so deterministic reporting continues
when generation fails or a run-level cap is exhausted.

## Database

The zero-configuration default uses a local SQLite file:

```dotenv
DATABASE_URL=sqlite:///work/radar.db
```

PostgreSQL is supported through SQLAlchemy. Install the optional driver and set a secret connection string:

```bash
uv sync --extra postgres
```

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@host/database?sslmode=require
```

GitHub-hosted runner disks are ephemeral. For durable scheduled history, configure a hosted PostgreSQL `DATABASE_URL`; otherwise the daily workflow still generates and uploads an HTML artifact, but history is not retained between runs. Migration tooling and backup automation are intentionally deferred to the next persistence-hardening milestone.

## Email

HTML generation never requires email credentials. Available modes:

- `EMAIL_MODE=disabled`: generate HTML only.
- `EMAIL_MODE=file`: create a local `.eml` fixture for layout testing.
- `EMAIL_MODE=smtp`: send using environment variables shown in `.env.example`.

The cloud workflow sets `EMAIL_MODE=smtp` and requires all SMTP values through GitHub
Actions Secrets. Missing configuration or an SMTP failure makes the job fail; a report
that was already generated remains available to the workflow's `always()` artifact step.
No credentials are stored in source control.

## Tests and checks

```bash
uv run ruff check .
uv run mypy --strict src/radar
uv run pytest
```

Tests cover the no-AI end-to-end run, same-day idempotency, deterministic deduplication, zero-budget enforcement, fake-output labeling and production blocking of fake AI.

## GitHub Actions

`ci.yml` runs static checks and tests. `daily.yml` supports schedule and manual dispatch,
runs live/no-AI by default, sends email, and uploads the HTML report plus run result.
It runs at 22:17 UTC (06:17 Asia/Shanghai on the following day); GitHub scheduling is
best effort and may be delayed. Required email secrets:

- `EMAIL_FROM`
- `EMAIL_TO`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`

`DATABASE_URL` is optional for durable PostgreSQL history. Without it, SQLite exists only
for the life of the ephemeral runner. The email and uploaded report artifact still persist.

The workflow itself does not require an AI key. It uses read-only repository permissions and a concurrency group to prevent overlapping daily runs.

See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) for private-repository creation, Gmail
SMTP Secrets, optional Neon persistence, first-run validation, cost controls, and the
remaining human checklist.

## Architecture extension points

- Add data sources by implementing `IntelligenceProvider.collect()` and returning a validated `CollectionBatch`.
- Add a text-generation backend by implementing `LLMProvider.generate(LLMRequest)`.
- Add delivery channels by implementing `EmailSender.send()`.
- Core financial data models remain provider-independent.

See the adjacent `DEVELOPMENT_PLAN.md` deliverable for the full research, architecture, risk and phased roadmap.
