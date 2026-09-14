# C9.3 Operator Admin Console Implementation Plan

> For agentic workers: use the implementation workflow selected after this plan. Steps use checkbox syntax for tracking.

Goal: Extend the existing loopback C9.2 admin endpoint into a complete, safe, server-rendered local operator console without changing C6-C9.2 intelligence or delivery contracts.

Architecture: Keep the standard-library ThreadingHTTPServer and Basic/token authentication in src/radar/admin.py. Put typed bounded read models, read queries, and HTML page rendering in src/radar/admin_console.py. Keep mutations in AudienceService. Add only bounded RadarStore read methods and no migration. Use one lightweight CSS system and minimal confirmation behavior; do not add a frontend build system.

Tech Stack: Python 3, standard-library http.server, SQLAlchemy Core, Pydantic, Typer, and existing repository dependencies.

Spec: docs/superpowers/specs/2026-09-14-c9-3-admin-console-design.md

## Global Constraints

- Admin binds only to 127.0.0.1 and requires Basic auth with operator username plus AUDIENCE_ADMIN_TOKEN.
- Every state-changing request is authenticated POST with a per-process CSRF token checked using constant-time comparison.
- C6/C7/C8/C9/C9.1 canonical facts, scores, claims, evidence, story selection, ordering, and localization snapshots are read-only.
- Production audience delivery keeps the existing dual gate and at-most-once reservation path; C9.3 adds no production-send button.
- Preview creates no DeliveryRecord and makes no SMTP call; Send Test To Me targets only OPERATOR_EMAIL and keeps [TEST].
- Supabase/PostgreSQL and current C9.2 tables remain authoritative. No second database or migration unless a concrete additive need is proven.
- No secret, full HTML, note body, SMTP transcript, database URL, API key, token, or raw provider error is rendered or logged.
- Make one final commit after implementation, validation, and documentation; do not commit per task.

---

### Task 1: Typed bounded operator read service

Files:
- Create src/radar/admin_console.py
- Modify src/radar/storage.py around existing recipient, delivery, briefing, and localization table helpers
- Test tests/test_c9_3_admin_console.py

Interfaces:
- OperatorConsoleService(store, settings)
- dashboard() -> DashboardView
- delivery_history(filters: DeliveryFilters) -> tuple[DeliveryHistoryItem, ...]
- briefing_summaries(limit: int = 20) -> tuple[BriefingSummary, ...]
- system_status() -> SystemStatusView
- latest_briefing() -> DailyIntelligenceBrief | None
- localization_summaries(briefing_id: str) -> tuple[LocalizationSummary, ...]

- [ ] Write failing tests with a briefing, valid snapshot, active/disabled/archived recipients, and delivery records. Assert dashboard counts/modes, a 50-record history cap, approved localization metadata, and secret/body non-disclosure.
- [ ] Run .venv\Scripts\python.exe -m pytest -q tests/test_c9_3_admin_console.py and verify failure is caused by the missing read service.
- [ ] Add frozen typed view models and explicit RadarStore queries. Clamp limits, select only required metadata, use SELECT 1 for database health, and query schema_migrations for C9.2. Derive configuration booleans from Settings and never invent usage.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Secure multi-page shell and CSRF

Files:
- Modify src/radar/admin.py and src/radar/admin_console.py
- Test tests/test_c9_3_admin.py

Interfaces:
- Authenticated routes for dashboard, recipients, daily-message, preview, deliveries, briefings, and system.
- Shared sidebar/header/status-badge shell with page-specific content.

- [ ] Add failing tests for unauthenticated GET/POST, invalid and valid CSRF, route titles, sidebar links, and absence of secrets.
- [ ] Run the focused auth/page tests and verify the expected missing-route or missing-CSRF failures.
- [ ] Generate a per-process token with secrets.token_urlsafe(32), embed it in mutation forms, and compare it with hmac.compare_digest. Preserve Basic auth and 127.0.0.1 binding. Route page query values safely; return bounded 400/404 pages. Add compact responsive CSS, cards, tables, badges, empty/error states, and archive/test confirmation behavior without a frontend build system.
- [ ] Re-run the focused auth/page tests and confirm they pass.

### Task 3: Recipient management and Daily Message

Files:
- Modify src/radar/admin.py and src/radar/delivery.py
- Test tests/test_c9_3_admin.py and tests/test_c9_2_delivery_service.py

Interfaces:
- Existing AudienceService add_recipient, edit_recipient, set_enabled, archive_recipient, and save_editorial_note remain the only mutation domain APIs.

- [ ] Add failing tests for bilingual/zh-CN/enabled creation defaults, edit collision on normalized email, archive history preservation, mode/locale changes, bounded notes, escaping, no auto-send, and briefing-scoped save/clear.
- [ ] Run the focused lifecycle tests and verify the edit-collision regression fails against the current ID-only check.
- [ ] Before saving an edit, reject a different recipient that owns the same normalized email. Render active/disabled/archived views, create/edit fields, locale, note indicator, updated time, latest delivery, and explicit archive confirmation. Add escaped Daily Message save/clear forms showing briefing/run identity. Never send on create/edit.
- [ ] Re-run lifecycle tests and confirm they pass.

### Task 4: Preview and Send Test To Me

Files:
- Modify src/radar/admin.py and src/radar/admin_console.py
- Test tests/test_c9_3_admin.py and tests/test_c9_2_delivery_service.py

Interfaces:
- Existing AudienceService.preview, AudienceService.send_test, persisted C9.1 lookup, and render_recipient_email path.

- [ ] Add failing tests that preview creates no DeliveryRecord and no sender call, bilingual preview reuses a stored snapshot, English-only preview needs no localization, and Send Test has no arbitrary destination, uses OPERATOR_EMAIL, [TEST], TEST kind, and no production identity/fan-out.
- [ ] Run the focused preview/test tests and verify the expected missing UI failures.
- [ ] Build preview from existing service methods and show subject, mode, locale, fallback, and pure HTML output. Keep /preview free of admin chrome and add safe response headers compatible with rendered email. Use existing send_test only; show bounded accepted/failed metadata and never expose SMTP transcript.
- [ ] Re-run the focused tests and confirm they pass.

### Task 5: Dashboard, Deliveries, Briefings, Localization, and System

Files:
- Modify src/radar/admin.py and src/radar/admin_console.py
- Test tests/test_c9_3_admin_console.py and tests/test_c9_3_admin.py

Interfaces:
- Consume the typed read models from Task 1 and canonical storage read paths only.

- [ ] Add failing tests for latest briefing identity/date/count/health, C9/C9.1 state, requested/returned model and fallback, audience counts/modes, latest production statuses, system booleans/caps, delivery filters and 50-row cap, briefing summaries, localization metadata, and secret/body non-disclosure.
- [ ] Run the focused page tests and verify absent-page failures.
- [ ] Render typed status badges and escaped display values. Use bounded filters for kind/status/recipient/date/mode. Shorten IDs only in list views and expose approved full audit IDs in detail metadata. Keep canonical objects read-only; never build full HTML for dashboard/history.
- [ ] Re-run the focused page tests and confirm they pass.

### Task 6: CLI launcher and operator documentation

Files:
- Modify src/radar/cli.py, README.md, and CLOUD_DEPLOYMENT.md
- Test tests/test_c9_2_cli.py and any affected workflow/help tests

Interfaces:
- Add preferred repository-native command audience-admin as a direct call to run_admin(Settings()), while retaining admin as a compatibility alias.

- [ ] Add failing tests for audience-admin help, non-secret startup information, and documented routine workflows.
- [ ] Run .venv\Scripts\python.exe -m pytest -q tests/test_c9_2_cli.py -k admin and verify the expected help failure.
- [ ] Add the alias and document start/auth/add/disable/archive/mode/note/Daily Message/preview/test/history/stop. State that creation does not send, active recipients join the next scheduled run, and production delivery remains workflow-controlled. Startup prints only local URL, backend type, and authentication-required status.
- [ ] Re-run the focused CLI tests and confirm they pass.

### Task 7: Full verification, local validation, and one final commit/push

Files:
- Verify every file changed by Tasks 1-6.

- [ ] Run from repository root: set PATH to include C:\Program Files\Git\bin, set RADAR_TEST_POSTGRES=1, then run .venv\Scripts\python.exe -m pytest -q, .venv\Scripts\ruff.exe check ., .venv\Scripts\mypy.exe --strict src/radar, C:\Users\啊对啦\.local\bin\uv.exe lock --check, and git diff --check. All must exit 0.
- [ ] Start uv run radar audience-admin and authenticate at http://127.0.0.1:<ADMIN_PORT>. Inspect Dashboard, Recipients, Daily Message, Preview, Deliveries, Briefings, and System against the existing operator recipient and shared Supabase. Do not add friends, send production mail, or trigger a workflow.
- [ ] Inspect git diff --stat, git diff --check, git status --short, and every changed file. Confirm no .env, credentials, recipient list, note body, generated HTML, or unrelated C6-C9.2 edit is included.
- [ ] Commit once with message Build C9.3 operator admin console and push origin main without force. Verify HEAD equals origin/main and the working tree is clean. Do not start C10.
