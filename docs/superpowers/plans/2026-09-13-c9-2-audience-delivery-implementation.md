# C9.2 Audience & Delivery Management Implementation Plan

> **For agentic workers:** This plan is executed inline in the current session with TDD checkpoints. No commit, push, production audience fan-out, beta email, or C10 work is permitted.

**Goal:** Implement the approved private-beta audience, note, preview, test-delivery, and explicitly gated production fan-out vertical slice around the existing C9/C9.1 product.

**Architecture:** Keep intelligence snapshots immutable and put recipient configuration plus delivery audit data in a new delivery domain. Local admin and GitHub Actions use the same durable PostgreSQL through the existing `RadarStore`; SQLite remains for local tests but production audience mode rejects it. CLI, admin, preview, test mail, and production fan-out call one delivery service.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy Core, Typer, standard-library `http.server`, Jinja2, existing SMTP adapter, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-13-c9-2-audience-delivery-design.md`

## Global Constraints

- `DailyIntelligenceBrief` remains the canonical intelligence source of truth.
- `AUDIENCE_ENABLED` defaults to `false`.
- Production audience delivery requires both `AUDIENCE_ENABLED=true` and explicit `radar daily --audience` / workflow `delivery_target=audience`.
- Shared PostgreSQL is the authoritative store; production audience mode rejects SQLite before SMTP.
- `MAX_ACTIVE_RECIPIENTS` defaults to `20`; over-cap audience validation sends zero messages.
- Production delivery is at-most-once: an existing `PENDING`, `SENT`, `FAILED`, or `SKIPPED` identity never auto-sends again.
- Recipient remove is archive/soft-delete; historical `DeliveryRecord.recipient_id` remains readable.
- Each recipient gets one independent SMTP message with one `To` address and no audience addresses in headers/body/logs.
- C9.1 localization is generated at most once per briefing identity and reused by Chinese/bilingual recipients.
- Preview makes zero SMTP calls; send-test sends only to `OPERATOR_EMAIL` and never creates a production `SENT` record.
- Notes are plain text, HTML-escaped, bounded, and outside C9/C9.1 snapshots.
- No new automatic retry, JSON repair, fallback model, tracking, public signup, or C10 functionality.
- Recipient data, personal notes, editorial notes, database URLs, tokens, and SMTP credentials are never committed to Git, workflow YAML, or artifacts.
- Tests make no real SMTP, DeepSeek, or external-network calls.
- Final verification is `pytest -q`, `ruff check .`, `mypy --strict src/radar`, `uv lock --check`, and `git diff --check`.

---

### Task 1: Add strict C9.2 domain models and configuration

**Files:**
- Create: `src/radar/audience.py`
- Modify: `src/radar/config.py`
- Test: `tests/test_audience.py`
- Test: `tests/test_c9_2_config.py`

**Interfaces:**
- `DeliveryMode`: `english`, `chinese`, `bilingual`.
- `DeliveryKind`: `production`, `test`.
- `DeliveryStatus`: `PENDING`, `SENT`, `FAILED`, `SKIPPED`.
- `RecipientProfile`: strict frozen model with `id`, `display_name`, `email`, `normalized_email`, `enabled`, `archived`, `delivery_mode`, `locale`, `personal_note`, `created_at`, and `updated_at`.
- `EditorialNote`: strict frozen model with `id`, `briefing_id`, `run_id`, `message`, `created_at`, and `updated_at`.
- `DeliveryRecord`: strict frozen model containing the approved audit fields plus `rendered_locale`, `delivery_kind`, `html_hash`, and `fallback_reason`.
- `normalize_email(value: str) -> str` and `validate_email(value: str) -> str`.
- Settings fields: `audience_enabled=False`, `max_active_recipients=20`, `operator_email=None`, `audience_admin_token=None`, and `admin_port=8765`.

- [ ] Write failing tests for valid profiles, malformed/normalized emails, strict extra fields, mode/locale consistency, bounded names/notes, valid settings, invalid cap, and missing operator token behavior.
- [ ] Run `pytest tests/test_audience.py tests/test_c9_2_config.py -q`; confirm failures are from missing models/settings.
- [ ] Implement only the model/configuration contract above; use `SecretStr` for the admin token and no SMTP credential fields.
- [ ] Run the focused tests and confirm they pass.
- [ ] Run `mypy --strict src/radar/audience.py` and fix type errors without weakening strictness.

### Task 2: Add migration 008 schema and persistence primitives

**Files:**
- Modify: `src/radar/storage.py`
- Test: `tests/test_c9_2_storage.py`

**Interfaces:**
- Add `C9_2_AUDIENCE_DELIVERY_MIGRATION = "008_c9_2_audience_delivery"`.
- Add tables `recipient_profiles`, `editorial_notes`, and `delivery_records`.
- `RadarStore.save_recipient(profile: RecipientProfile) -> None`.
- `RadarStore.list_recipients(include_archived: bool = False) -> tuple[RecipientProfile, ...]`.
- `RadarStore.load_recipient(recipient_id: str) -> RecipientProfile | None`.
- `RadarStore.save_editorial_note(note: EditorialNote) -> None` and `load_editorial_note(briefing_id: str) -> EditorialNote | None`.
- `RadarStore.reserve_delivery(record: DeliveryRecord) -> bool` and `save_delivery_record(record: DeliveryRecord) -> None`.
- `RadarStore.load_delivery(delivery_id: str) -> DeliveryRecord | None`.
- `RadarStore.list_delivery_records(run_id: str) -> tuple[DeliveryRecord, ...]`.

- [ ] Write failing SQLite tests for first initialization, repeated initialization, schema migration marker, strict round trips, unique normalized email, delivery identity uniqueness, and no cascade deletion of delivery history.
- [ ] Run the focused storage tests and confirm the migration/table failures are expected.
- [ ] Add the three SQLAlchemy Core tables with additive indexes and unique constraints; call `_migrate_c9_1_to_c9_2()` from `RadarStore.initialize()`.
- [ ] Validate required columns, primary keys, nullability, unique indexes, and invalid rows using the repository's existing migration validation style.
- [ ] Implement serialization/deserialization through strict models. Archive updates must leave delivery rows intact.
- [ ] Implement atomic delivery reservation so a unique identity returns `False` instead of overwriting an existing state.
- [ ] Run focused storage tests and confirm migration and persistence behavior pass.

### Task 3: Implement recipient lifecycle and audience preflight service

**Files:**
- Create: `src/radar/delivery.py`
- Modify: `src/radar/storage.py`
- Test: `tests/test_c9_2_delivery_service.py`

**Interfaces:**
- `AudienceValidationError(ValueError)` for safe preflight failures.
- `RecipientInput` strict command input.
- `AudienceService(store: RadarStore, settings: Settings)`.
- `AudienceService.list_recipients() -> tuple[RecipientProfile, ...]`.
- `AudienceService.add_recipient(display_name: str, email: str, mode: DeliveryMode, personal_note: str | None) -> RecipientProfile`.
- `AudienceService.edit_recipient(recipient_id: str, ...) -> RecipientProfile`.
- `AudienceService.set_enabled(recipient_id: str, enabled: bool) -> RecipientProfile`.
- `AudienceService.archive_recipient(recipient_id: str) -> RecipientProfile`.
- `AudienceService.save_editorial_note(briefing: DailyIntelligenceBrief, message: str) -> EditorialNote | None`.
- `AudienceService.preflight(briefing: DailyIntelligenceBrief, *, explicit_audience: bool) -> tuple[RecipientProfile, ...]`.

- [ ] Write failing tests for lifecycle operations, duplicate normalized email, disabled/archived exclusion, mode/locale consistency, cap overflow, malformed state, `AUDIENCE_ENABLED` dual gate combinations, and production SQLite rejection before any sender call.
- [ ] Run focused service tests and confirm expected missing-service failures.
- [ ] Implement lifecycle updates with stable IDs, archive instead of delete, and bounded safe errors; never delete delivery rows.
- [ ] Implement preflight as a complete audience validation before SMTP, requiring both the setting and explicit invocation, and rejecting production SQLite.
- [ ] Run focused tests and confirm all four dual-gate combinations and cap behavior pass.

### Task 4: Refactor SMTP seam for independent recipient messages

**Files:**
- Modify: `src/radar/email.py`
- Test: `tests/test_email.py`
- Test: `tests/test_c9_2_delivery_service.py`

**Interfaces:**
- Preserve existing `EmailSender.send(subject: str, html: str) -> str` for legacy single-recipient delivery.
- Add `SMTPEmailSender.send_to(*, recipient: str, subject: str, html: str) -> str`.
- Add an injectable `EmailTransport`/fake seam used by audience service tests so no test opens SMTP.

- [ ] Write failing tests proving each audience message has exactly one `To`, no `CC`/`BCC`, and does not contain other recipient addresses.
- [ ] Run focused email tests and confirm the new recipient-specific seam is absent.
- [ ] Implement `send_to` using the existing STARTTLS/login path and a newly constructed `EmailMessage`; do not alter timeout/retry policy.
- [ ] Run existing email tests plus focused independent-header tests.

### Task 5: Add shared recipient composition and rendering modes

**Files:**
- Modify: `src/radar/report.py`
- Modify: `templates/daily_email.html.j2`
- Test: `tests/test_c9_2_rendering.py`

**Interfaces:**
- `RecipientComposition` with `delivery_mode`, `recipient`, `editorial_note`, `personal_note`, and `is_test`.
- `render_recipient_email(briefing, localized, composition) -> tuple[str, str, str]` returning subject, HTML, and rendered locale.
- Existing `HtmlReportRenderer` remains the only renderer.

- [ ] Write failing tests for English, Chinese-only, bilingual, deterministic subjects, truthful English fallback metadata, absent/global/personal notes, combined note order, HTML escaping, and notes absent from canonical briefing serialization.
- [ ] Run the focused rendering tests and confirm missing composition/mode behavior.
- [ ] Extend `report_context` and the existing template with explicit mode and note context. Keep C9.1 default bilingual rendering unchanged when no recipient composition is supplied.
- [ ] Add a separate escaped `Editor's Note / 今日附言` section near the top; do not put notes in intelligence sections.
- [ ] Render market names/values and source links from canonical briefing fields exactly as current C9/C9.1 does.
- [ ] Run focused rendering tests and existing report/localization report tests.

### Task 6: Implement at-most-once delivery orchestration and snapshot reuse

**Files:**
- Modify: `src/radar/delivery.py`
- Modify: `src/radar/pipeline.py`
- Test: `tests/test_c9_2_delivery_service.py`
- Test: `tests/test_c9_2_pipeline.py`

**Interfaces:**
- `build_delivery_identity(briefing, recipient) -> str` using run, briefing, recipient, requested mode, and locale.
- `AudienceService.preview(briefing, recipient_id) -> RenderedDelivery` with no persistence side effect.
- `AudienceService.send_test(briefing, recipient_id, provider) -> DeliveryRecord | None` sending only to `settings.operator_email`.
- `AudienceService.deliver_briefing_to_audience(briefing, localized, sender) -> AudienceDeliverySummary`.

- [ ] Write failing tests for deterministic identity, reservation-before-SMTP, existing `PENDING/SENT/FAILED/SKIPPED` blocking all resend, unknown crash window, one failure continuing to the next recipient, zero sends on global preflight failure, and accurate record states.
- [ ] Write failing pipeline tests proving one localization snapshot/call is reused for many Chinese/bilingual recipients and English-only delivery does not require localization.
- [ ] Run focused tests and confirm failures expose missing orchestration.
- [ ] Implement preflight, reserve `PENDING`, render, call one-recipient SMTP, then persist `SENT` or `FAILED`; never auto-retry or overwrite an existing record. Treat existing `PENDING` as unknown outcome.
- [ ] Integrate the audience path only behind both `settings.audience_enabled` and explicit audience invocation. Keep legacy `EMAIL_TO` delivery untouched otherwise.
- [ ] Load the existing C9.1 localization snapshot once and pass it to all applicable recipients; do not invoke localization inside the recipient loop.
- [ ] Run focused pipeline/delivery tests and existing C9.1 pipeline tests.

### Task 7: Add operator CLI commands

**Files:**
- Modify: `src/radar/cli.py`
- Test: `tests/test_c9_2_cli.py`

**Interfaces:**
- Typer groups/commands: `recipients list/add/edit/enable/disable/remove`, `editorial save/clear`, `preview --recipient`, `send-test --recipient`, and `daily --audience`.
- CLI adapters call `AudienceService`; they do not implement validation or SMTP orchestration.

- [ ] Write failing CLI tests for lifecycle commands, safe validation errors, preview with zero SMTP, send-test operator-only behavior, and the dual activation gate.
- [ ] Run focused CLI tests and confirm missing commands fail.
- [ ] Implement thin Typer adapters using `Settings` and `RadarStore`; keep output bounded and never print notes, tokens, passwords, or full recipient lists at INFO.
- [ ] Add `--audience` to `daily` as an explicit invocation flag; do not make it the default.
- [ ] Run focused CLI tests and existing CLI/workflow tests.

### Task 8: Add local loopback-only admin server

**Files:**
- Create: `src/radar/admin.py`
- Modify: `src/radar/cli.py`
- Test: `tests/test_c9_2_admin.py`

**Interfaces:**
- `run_admin(settings: Settings) -> None`.
- `AudienceAdminHandler` using `AudienceService` for GET/POST actions.
- HTTP Basic auth with username `operator` and `AUDIENCE_ADMIN_TOKEN`, constant-time token comparison.

- [ ] Write failing tests for loopback-only settings, missing/wrong/correct token, generic unauthorized errors, recipient CRUD, editorial save/clear, preview without SMTP, and send-test routing.
- [ ] Run focused admin tests and confirm the server/route failures are expected.
- [ ] Implement standard-library server-rendered HTML with Audience, Message, and Delivery sections; bind only to `127.0.0.1`, reject non-loopback host configuration, and never start it from workflow.
- [ ] Escape all displayed and submitted note/name/email values and avoid logging body content or secrets.
- [ ] Add a thin `radar admin` command; all mutation and delivery behavior must call `AudienceService`.
- [ ] Run focused admin tests.

### Task 9: Add explicit workflow activation and operator documentation

**Files:**
- Modify: `.github/workflows/daily.yml`
- Modify: `README.md`
- Modify: `CLOUD_DEPLOYMENT.md`
- Modify: `.env.example`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Manual workflow input `delivery_target: single | audience`, default `single`.
- `single` uses the existing `radar daily` / `EMAIL_TO` path.
- `audience` uses `radar daily --audience` and requires `AUDIENCE_ENABLED=true` plus durable `DATABASE_URL`.

- [ ] Write failing workflow tests proving schedule/default/manual-single never fan out, audience requires explicit path, no recipient/note values occur in YAML, and no admin command occurs in workflow.
- [ ] Run workflow tests and confirm the new choice/branch is absent.
- [ ] Add the manual choice and branch conservatively; keep scheduled AI/localization behavior unchanged and set audience activation only for the explicit audience branch.
- [ ] Document shared PostgreSQL setup, local admin `.env`, matching GitHub `DATABASE_URL` secret, admin token, operator email, migration, dual gate, at-most-once semantics, and the fact that SQLite is not production authority.
- [ ] Run focused workflow/documentation checks.

### Task 10: Full offline verification and final scope audit

**Files:**
- Modify only files required by Tasks 1-9.
- Test: all repository tests and C9.2 tests.

- [ ] Run `pytest -q` with no network/SMTP credentials and confirm all tests pass.
- [ ] Run `ruff check .` and fix only C9.2 lint issues.
- [ ] Run `mypy --strict src/radar` and fix only typed C9.2 issues.
- [ ] Run `uv lock --check` without changing dependencies unless a genuine missing dependency is proven.
- [ ] Run `git diff --check`.
- [ ] Inspect `git diff --stat`, `git diff --name-only`, and `git status --short`.
- [ ] Search the diff for recipient addresses, note bodies, tokens, passwords, database URLs, tracking code, retries, fallback models, and unintended C5-C9.1 modifications.
- [ ] Report A-X implementation results, all five explicit safety answers as `No`, known limitations, and a future production validation plan.
- [ ] Do not commit, push, run production audience delivery, email beta users, or start C10.

## Plan self-review

- Coverage: Tasks 1-2 cover strict models and migration 008; Tasks 3-4 cover lifecycle, preflight, independent SMTP and at-most-once reservation; Task 5 covers all three modes and note rendering; Task 6 covers pipeline, localization reuse, fallback and isolation; Tasks 7-8 cover shared CLI/admin; Task 9 covers workflow and durable PostgreSQL operations; Task 10 covers every required gate and scope audit.
- Safety: audience activation has two gates, full preflight precedes the first SMTP call, PENDING is treated as unknown, and archived recipients remain available for historical delivery references.
- Dependency review: no new dependency is planned; the admin uses `http.server`, existing SQLAlchemy/Typer/Jinja2/SMTP remain the stack, and PostgreSQL support is already an optional locked extra.
- Persistence review: migration is additive and idempotent; unique delivery reservation is performed before SMTP; no local SQLite file is transferred to a runner.
- Side-effect review: preview has no SMTP or delivery persistence, send-test is operator-only and non-production, and default/scheduled workflow behavior remains single-recipient.
- Placeholder scan: no placeholder tokens or unspecified implementation steps are present.
- Type review: public interfaces name their concrete models, return types, and ownership boundaries; CLI/admin adapters consume `AudienceService` rather than duplicating rules.
