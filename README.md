# Global Investment Radar

Evidence-first global market, macro and event intelligence pipeline. Version 0.1 is intentionally useful with AI completely disabled and requires no paid API or LLM key for its reproducible demo run.

## Milestone scope

Implemented in this milestone:

- Provider interface with isolated failures.
- Reproducible synthetic demo provider with visible safety labels.
- Optional live zero-key providers: official Federal Reserve/SEC RSS, official BLS API, and an experimental Yahoo chart adapter.
- Deterministic normalization, URL cleanup, near-duplicate clustering, event classification, entity mapping and explainable importance ranking.
- Evidence-first `SourceDocument → Evidence → Claim → Event` domain model.
- SQLite development storage and PostgreSQL-compatible SQLAlchemy schema.
- Optional `LLMProvider` protocol with disabled and fake implementations.
- Hard no-AI behavior when `AI_ENABLED=false` or `DAILY_LLM_BUDGET=0`.
- Responsive HTML email report and disabled/file/SMTP email adapters.
- CLI, tests and GitHub Actions CI/daily workflows.
- Cloud-MVP preparation: Asia/Shanghai business date, scheduled live/no-AI execution,
  SMTP delivery, operational logs, failure-preserved artifacts, and optional PostgreSQL.

Not implemented yet: real LLM adapters, company filing/XBRL extraction, licensed global market provider, earnings consensus/transcripts, theme scoring, weekly report or dashboard.

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

- Federal Reserve and SEC official RSS feeds.
- BLS public macro API.
- An experimental, unofficial Yahoo chart endpoint for market snapshots.

The market adapter is suitable for zero-cost development, not a production SLA. The HTML report shows the warning. Replace it through the provider interface when a licensed source is selected; no business logic needs to change.

## AI configuration

AI is an optional enhancement layer. The default is deterministic:

```dotenv
AI_ENABLED=false
DAILY_LLM_BUDGET=0
LLM_PROVIDER=disabled
```

Either `AI_ENABLED=false` or a zero budget prevents AI calls. Version 0.1 installs no vendor AI SDK. Future adapters can read centralized settings without changing event, claim, evidence, ranking or report code:

```dotenv
AI_ENABLED=true
DAILY_LLM_BUDGET=1.50
LLM_PROVIDER=future-provider
LLM_MODEL=model-name
LLM_BASE_URL=https://provider.example/v1
LLM_API_KEY=secret
```

An unknown or unavailable provider falls back to deterministic mode. Real adapters must accept an `EvidencePack`, return `StructuredAnalysis`, validate cited claim IDs and never perform deterministic financial calculations.

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

- Add data sources by implementing `DataProvider.collect()` and returning a validated `CollectionBatch`.
- Add an AI backend by implementing `LLMProvider.analyze(EvidencePack)`.
- Add delivery channels by implementing `EmailSender.send()`.
- Core financial data models remain provider-independent.

See the adjacent `DEVELOPMENT_PLAN.md` deliverable for the full research, architecture, risk and phased roadmap.
