# C9.3 — Operator Admin Console Design

## Status

Approved in chat on 2026-09-14. This document defines one bounded implementation slice on top of the production-validated C9.2 audience layer.

## Goal

Turn the existing loopback-only C9.2 admin endpoint into a safe, polished local operator console for daily private-beta operations without changing canonical intelligence, localization, or delivery semantics.

## Non-goals and hard boundaries

- C9.3 never edits C6/C7/C8/C9/C9.1 canonical facts, scores, claims, evidence, story selection, ordering, or persisted localization snapshots.
- C9.3 never adds an unrestricted production-send control and never bypasses the two audience activation gates or at-most-once reservation path.
- Admin remains local-only on `127.0.0.1`; no public bind, deployment, GitHub Actions server, or second persistence layer.
- Existing C9.2 PostgreSQL tables remain authoritative. No migration is expected; any schema need must be additive and independently justified.
- Routine operator actions must not require SQL or source edits. Friends are never auto-created or auto-sent.

## Architecture

Keep the standard-library `ThreadingHTTPServer` and the existing `AudienceAdminHandler` as the transport/auth boundary. Add a focused read-side operator-console service/view model for bounded dashboard, delivery, briefing, localization, and system queries. Continue using `AudienceService` for all recipient, editorial-note, preview, and Send Test To Me mutations so CLI and Admin share the same domain service.

Use server-rendered HTML with a single lightweight CSS system and minimal confirmation behavior. No frontend build system or SPA is introduced. All page data is typed, bounded, and derived from `RadarStore`/`Settings`; no raw SQL is accepted from the client.

## Pages and routes

- `/` or `/?page=dashboard`: dashboard with latest briefing health, counts, C9/C9.1 state, audience state, latest production delivery, system booleans, and bounded AI/budget visibility.
- `/?page=recipients`: active, disabled, and archived recipient management. List fields are display name, email, status, mode, locale, note indicator, latest delivery status, and updated time. Mutations are create, edit, enable, disable, archive, and safe unarchive where supported.
- `/?page=daily-message`: current briefing-scoped EditorialNote view/edit/clear with briefing/run identity and escaped safe preview.
- `/?page=preview`: recipient selector; opening a selected `/preview?recipient=...` link performs the explicit render-only workflow.
- `/preview?recipient=<id>`: explicit render-only page using the existing production renderer and persisted localization snapshot. It creates no DeliveryRecord and calls no SMTP.
- `/?page=deliveries`: at most 50 recent DeliveryRecords with production/test, status, recipient, date, and mode filters; detail is bounded audit metadata only.
- `/?page=briefings`: bounded recent read-only C9 briefings with date, run/briefing IDs, health, Primary count, composed time, and localization availability.
- `/?page=system`: boolean/status diagnostics for PostgreSQL, migration, AI, localization, SMTP, audience, and configured schedule behavior. Secrets are never returned.
- `/preview?recipient=<id>` remains a pure rendered-email endpoint for iframe/explicit preview use and does not mutate state.

## Security and privacy

- Authentication remains Basic auth with username `operator` and `hmac.compare_digest` for both username and token.
- The server binds only to `127.0.0.1`. A per-process cryptographically random CSRF token is embedded in mutation forms and checked with constant-time comparison. All mutations require POST.
- Operator-authored display names, emails, personal notes, and editorial notes are HTML-escaped. Notes are visibly separated from canonical Radar content and are not rendered into canonical briefing data or persisted HTML.
- Delivery history exposes no full HTML, credentials, API keys, admin token, SMTP transcript, or note bodies. Logs contain action type/status only and avoid recipient lists and note content.
- Preview output is rendered from trusted application data with no client-provided HTML or script execution. Safe response headers are used where compatible with email preview.
- Send Test To Me always targets configured `OPERATOR_EMAIL`, retains `[TEST]`, uses one To with no CC/BCC, does not consume a production identity, and does not fan out.

## Domain behavior

- Creation defaults to bilingual / `zh-CN` / enabled and never sends automatically.
- Email validation and normalized-email uniqueness are enforced through `AudienceService`; editing one recipient cannot collide with another normalized email. Archive/disable preserves DeliveryRecord history.
- Preview selects the requested recipient and latest eligible briefing, reuses the persisted C9.1 snapshot when applicable, and reports truthful fallback metadata through the existing renderer.
- Dashboard and lists load bounded metadata only. Full email HTML is produced only for explicit preview or test-send.

## Read-side data contracts

The operator-console read model will expose only typed, bounded fields:

- `DashboardView`: latest briefing identity/count/health, localization metadata, recipient counts and mode breakdown, latest production statuses, and boolean system checks.
- `DeliveryHistoryView`: up to 50 records with IDs, recipient display metadata, kind, status, modes/locales, subject, timestamps, fallback, run/briefing IDs, snapshot ID, hash, and error type.
- `BriefingSummaryView`: recent briefing identity/date/health/count/time and localization availability.
- `SystemStatusView`: backend/migration/connectivity/configuration booleans and numeric caps, never secret values.

These views read through `RadarStore` methods with explicit limits and filters. No canonical payload or persisted email body is edited.

## Error behavior

Expected operator errors are rendered as concise safe messages: database unavailable, no briefing, missing snapshot, recipient cap, duplicate/invalid email, unavailable preview, and failed test send. Raw traces, connection strings, provider diagnostics, and secrets stay in safe local logs only where needed.

## Testing and validation

Tests must cover authentication and CSRF, secret non-disclosure, all page status/count renderings, recipient lifecycle and normalized-email conflicts, note escaping/scoping, preview side-effect absence and snapshot reuse, Send Test To Me destination/identity constraints, bounded delivery filtering, briefing/localization/system visibility, no canonical mutation, and CLI launcher behavior.

After implementation run `pytest -q`, `ruff check .`, `mypy --strict src/radar`, `uv lock --check`, and `git diff --check`. Perform local read/interaction validation against the shared Supabase authority using only the existing operator recipient: login, dashboard, recipients, Daily Message, preview, deliveries, briefings, and system. Do not add friends, send production mail, or start C10.
