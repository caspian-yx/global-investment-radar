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
- Deterministic normalization, URL cleanup, near-duplicate clustering, event classification, entity mapping and explainable importance ranking.
- Evidence-first `SourceDocument → Evidence → Claim → Event` domain model.
- SQLite development storage and PostgreSQL-compatible SQLAlchemy schema.
- Provider-neutral text-generation contract with DeepSeek transport and a controllable fake.
- Deterministic `ExplanationBundle` prompt and strict structured-explanation parser.
- Hard no-AI behavior when `AI_ENABLED=false` or `DAILY_LLM_BUDGET=0`.
- Responsive HTML email report and disabled/file/SMTP email adapters.
- CLI, tests and GitHub Actions CI/daily workflows.
- Cloud-MVP preparation: Asia/Shanghai business date, scheduled live/no-AI execution,
  SMTP delivery, operational logs, failure-preserved artifacts, and optional PostgreSQL.

Not implemented yet: SEC filing body/XBRL/exhibit extraction, licensed global market provider, earnings consensus/transcripts, theme scoring, weekly report or dashboard.

## Quick start

Requirements: Python 3.12 and `uv`.

```bash
uv sync --all-extras
uv run radar doctor
uv run radar daily --data-mode demo --no-ai
```

The generated report is written to `outputs/reports/`. Demo records are synthetic and the report displays a prominent `DEMO DATA` banner.

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
uv run mypy src
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
