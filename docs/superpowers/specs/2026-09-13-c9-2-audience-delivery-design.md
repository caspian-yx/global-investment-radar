# C9.2 Audience & Delivery Management Design

**Status:** Design frozen for user review
**Date:** 2026-09-13
**Scope:** Private-beta audience management and recipient-specific delivery around the existing C9/C9.1 product.

## Goal

Add a small operator-controlled audience and delivery layer that manages private-beta recipients, composes safe operator notes, previews recipient-specific mail, sends an operator-only test message, and delivers one independent message per active recipient without changing C5-C9.1 intelligence semantics.

## Non-goals and frozen boundaries

- `DailyIntelligenceBrief` remains the canonical intelligence source of truth.
- C9 selection, C7 ranking, C8 market impacts, C2 explanations, C9 snapshots, and C9.1 localization snapshots are not modified by audience data.
- No per-recipient LLM call, personalization prompt, new intelligence fact, tracking pixel, open tracking, click tracking, or commercial unsubscribe platform.
- GitHub Actions does not start the admin server and never reads recipient data from Git files or committed configuration.
- Preview, operator test delivery, legacy single-recipient delivery, and production audience fan-out are separate explicit paths.
- The implementation remains offline-testable and must not send real SMTP from tests.
- C10 is out of scope.

## 1. Authoritative persistence and local-to-production synchronization

### Decision

Shared PostgreSQL is the authoritative store for audience configuration and delivery metadata. The local admin and GitHub Actions runner connect to the same private database through `DATABASE_URL`; no file synchronization mechanism is introduced.

```text
local Admin (.env DATABASE_URL)
        │ write/read
        ▼
private PostgreSQL
        ▲
        │ read/write delivery records
GitHub Actions (repository secret DATABASE_URL)
```

This reuses the current `RadarStore` SQLAlchemy architecture and additive `schema_migrations` convention. The database connection string is stored only in the local untracked `.env` and the GitHub repository secret. Recipient email addresses, personal notes, editorial notes, and delivery content are never placed in Git, workflow YAML, artifacts, or committed JSON.

### Safety rules

- `AUDIENCE_ENABLED` defaults to `false`.
- The current `EMAIL_TO` single-recipient path remains authoritative while audience delivery is disabled.
- The production audience entrypoint requires a durable non-SQLite database. If `APP_ENV=production` and audience delivery is requested with a SQLite URL, preflight fails before any SMTP call.
- Local development may use SQLite for offline tests, but that database is not a production audience source.
- Both admin and runner call `RadarStore.initialize()`; migration application is additive and idempotent.
- The runner does not import, upload, or reconstruct local database files.
- The GitHub Actions workflow exposes an explicit `delivery_target` choice with `single` as the default. Scheduled runs use the existing single-recipient route; only `delivery_target=audience` invokes fan-out.

Required operational configuration is documented, not committed:

```text
Local .env:
DATABASE_URL=postgresql+psycopg://...
AUDIENCE_ADMIN_TOKEN=...
SMTP_* and EMAIL_FROM values

GitHub repository secrets:
DATABASE_URL=the same private PostgreSQL connection string
SMTP_* and EMAIL_FROM values
OPERATOR_EMAIL=the operator/test destination
```

The database URL should use the existing TLS-capable PostgreSQL deployment. Secret values are never printed by `doctor`, admin diagnostics, or delivery logs.

## 2. Domain models and migration

Add strict frozen models following existing conventions.

### `RecipientProfile`

```text
id, display_name, email, normalized_email, enabled
delivery_mode: english | chinese | bilingual
locale: en for english, zh-CN for chinese/bilingual
personal_note: optional bounded operator-authored plain text
created_at, updated_at: timezone-aware datetimes
```

Email validation is deterministic and rejects malformed addresses. Normalized email is used for uniqueness and duplicate-active checks; SMTP receives the original validated address. Unknown fields are rejected. SMTP credentials are not model fields. The service enforces a conservative `MAX_ACTIVE_RECIPIENTS=20` default. A disabled profile is never selected for production delivery and can be re-enabled without creating a second profile for the same normalized address.

### `EditorialNote`

```text
id, briefing_id, run_id
message: bounded operator-authored plain text
created_at, updated_at
```

There is at most one current note per briefing/run identity. Empty content clears the note. It is external delivery metadata and never changes the immutable briefing payload.

### `DeliveryRecord`

```text
id, run_id, briefing_id, recipient_id, recipient_email
delivery_kind: production | test
requested_delivery_mode, requested_locale, rendered_locale
subject, status: PENDING | SENT | FAILED | SKIPPED
attempted_at, accepted_at, error_type
localization_snapshot_id, html_hash, fallback_reason
```

The table stores references and bounded audit metadata, not full HTML, SMTP conversations, SMTP passwords, API keys, or personal-note bodies. Migration `008_c9_2_audience_delivery` adds the three tables and indexes without rewriting or deleting historical C5-C9.1 rows. Re-running initialization validates the schema and is idempotent.

## 3. Delivery identity, idempotency, and failure isolation

For production fan-out, the deterministic delivery identity is:

```text
stable_id("c9.2-delivery",
          run_id | briefing_id | recipient_id | requested_delivery_mode | requested_locale)
```

The database has a unique constraint on this identity. An existing `SENT`, `PENDING`, `FAILED`, or `SKIPPED` record is not silently replaced by a retry. Re-running the same workflow cannot create an automatic duplicate send. A deliberate resend mechanism is not implemented in C9.2.

Audience preflight validates the entire active set before the first SMTP call: valid emails and modes, unique normalized active emails, active count within the hard cap, durable production database, SMTP/`EMAIL_FROM`, canonical briefing, and resolvable localization requirements. Any preflight error aborts before sending.

Once preflight passes, each recipient is processed independently. One SMTP exception produces that recipient's `FAILED` record and the loop continues. No new automatic SMTP retry is added. Each message has exactly one `To` address; no recipient is exposed through another message's `To`, `CC`, or `BCC`.

## 4. Rendering and delivery modes

The existing `HtmlReportRenderer` remains the sole HTML rendering path for report, preview, test mail, and production mail. It receives an explicit recipient composition context.

- `english`: canonical English C9 report only.
- `chinese`: Chinese-first localized report without the full English reference section; canonical briefing and localization snapshot remain the factual inputs.
- `bilingual`: current C9.1 Chinese primary plus complete English reference.

Subjects are deterministic:

```text
English:   Global Investment Radar — YYYY-MM-DD
Localized: Global Investment Radar — YYYY-MM-DD 全球投资晨报
Test:      [TEST] Global Investment Radar — YYYY-MM-DD 全球投资晨报
```

The localized subject is used only when the actual primary content is localized. An English fallback never claims successful Chinese localization and uses English subject/metadata.

## 5. Editorial and personal notes

The final operator-authored composition is `global editorial note + recipient personal note`. An optional one-off note is deferred because it would require another run-scoped editing surface without being necessary for this private-beta vertical slice.

Notes render in an optional, visually distinct `Editor's Note / 今日附言` section near the top. No note means no section. Notes are plain text with automatic HTML escaping, no Markdown interpretation, no JavaScript, no external CSS, and no raw HTML injection. Notes never enter canonical Story/Event/Claim/Evidence objects or any C9/C9.1 snapshot.

## 6. Localization reuse and fallback

The pipeline creates at most one C9.1 localization request for a briefing identity. All chinese/bilingual recipients reuse the same persisted localization snapshot; recipient count never multiplies LLM calls.

- English recipients do not require localization.
- Chinese/bilingual recipients use the immutable snapshot when available.
- If localization fails, no Chinese is fabricated. Production audience delivery follows the existing C9.1 English-fallback behavior for chinese and bilingual requests, recording `rendered_locale=en` and a safe fallback category.
- Fallback subject and `lang` metadata correspond to actual English content.

## 7. Admin surface

Implement a small server-rendered Python admin using the standard-library HTTP server; no React, Next.js, or new web framework.

Security is operator-only: bind exclusively to `127.0.0.1` by default and reject non-loopback bind configuration in this phase; require `AUDIENCE_ADMIN_TOKEN` through HTTP Basic authentication; compare it using constant-time comparison; return generic unauthorized/validation responses; never log secrets, complete recipient lists, or personal-note bodies; and never launch admin from GitHub Actions.

The UI has three areas:

- **Audience:** list name/email/mode/status; add/edit display name, email, mode, personal note; enable/disable/remove.
- **Message:** show current briefing/run identity; edit/save/clear editorial note; preview resulting mail.
- **Delivery:** show active count/cap; choose a recipient for preview; provide `Send Test To Me`.

Preview never calls SMTP. Send-test sends only to `OPERATOR_EMAIL`, adds `[TEST]`, uses the production renderer, and never creates a production recipient `SENT` record or fans out.

## 8. Shared CLI service path

Typer commands call the same domain service as admin routes:

```text
radar recipients list
radar recipients add ...
radar recipients disable ...
radar recipients remove ...
radar editorial save/clear ...
radar preview --recipient ...
radar send-test --recipient ...
radar daily --audience
```

The existing `radar daily` behavior remains single-recipient unless `--audience` is explicitly supplied. Preview and test commands do not invoke real SMTP during tests.

## 9. Workflow and backward compatibility

Add a manual `delivery_target` choice to `.github/workflows/daily.yml`:

```text
single   (default)
audience
```

Scheduled runs resolve to `single` and retain current AI/localization behavior. Manual `single` retains the current `EMAIL_TO` route. Only manual `audience` invokes `radar daily --audience`. The workflow never starts admin. Audience mode fails before sends when durable `DATABASE_URL` or production SMTP configuration is absent; `OPERATOR_EMAIL` is additionally required only for the operator-only send-test path. No audience recipients are auto-imported from `EMAIL_TO`.

## 10. Observability and privacy

Safe diagnostics expose only bounded counts and categories:

```text
audience_enabled, active_recipient_count, delivery_attempts
sent_count, failed_count, skipped_count
duplicate_send_skipped_count, localization_snapshot_reused
```

Logs do not contain recipient lists, personal notes, SMTP credentials, API keys, raw SMTP conversations, or full rendered HTML. SMTP acceptance is not read/open confirmation.

## 11. Verification contract

Offline tests cover strict recipient/note/mode/email validation, cap enforcement, independent SMTP messages, failure isolation, idempotency, disabled recipients, delivery records, all rendering modes, note escaping and scope, localization snapshot reuse, localization failure fallback, preview, send-test isolation, admin authentication, migration idempotence, workflow explicitness, and legacy single-recipient compatibility.

Final gates:

```text
pytest -q
ruff check .
mypy --strict src/radar
uv lock --check
git diff --check
```

No commit, push, production audience fan-out, or C10 work is part of the implementation phase.

## 12. Known bounded limitations

- Only English, `zh-CN`, and bilingual output are implemented; `ko-KR` and `ja-JP` remain future extensions.
- There is no commercial unsubscribe flow; disabling a profile is the operator control.
- There is no deliberate resend workflow in this phase.
- Admin is intentionally local-only and has no multi-user account system.
- One-off delivery notes are deferred.
- Shared durable PostgreSQL is required before enabling production audience delivery.
