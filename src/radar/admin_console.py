# ruff: noqa: E501
from __future__ import annotations

import html
from datetime import date
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from radar.audience import DeliveryKind, DeliveryMode, DeliveryStatus
from radar.briefing import DailyIntelligenceBrief
from radar.config import Settings
from radar.delivery import AudienceService
from radar.storage import (
    C9_2_AUDIENCE_DELIVERY_MIGRATION,
    RadarStore,
    c9_daily_briefings_table,
    c9_localizations_table,
    delivery_records_table,
)


class _ViewModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DeliveryFilters(_ViewModel):
    kind: DeliveryKind | None = None
    status: DeliveryStatus | None = None
    recipient_id: str | None = None
    delivery_mode: DeliveryMode | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: int = Field(default=50, ge=1, le=100)


class DeliveryHistoryItem(_ViewModel):
    id: str
    run_id: str
    briefing_id: str
    recipient_id: str
    recipient_display_name: str
    recipient_email: str
    delivery_kind: DeliveryKind
    requested_delivery_mode: DeliveryMode
    requested_locale: Literal["en", "zh-CN"]
    rendered_locale: Literal["en", "zh-CN"]
    subject: str
    status: DeliveryStatus
    attempted_at: str | None
    accepted_at: str | None
    error_type: str | None
    localization_snapshot_id: str | None
    html_hash: str | None
    fallback_reason: str | None


class BriefingSummary(_ViewModel):
    briefing_id: str
    run_id: str
    report_date: str
    composed_at: str
    primary_count: int
    health: str
    ai_status: str
    localization_available: bool


class LocalizationSummary(_ViewModel):
    snapshot_id: str
    briefing_id: str
    locale: str
    rule_version: str
    requested_model: str
    returned_model: str | None
    provider: str
    created_at: str


class DashboardView(_ViewModel):
    latest_briefing_id: str | None
    latest_briefing_date: str | None
    primary_count: int
    briefing_health: str
    c9_status: str
    localization_status: str
    localization_requested_model: str | None
    localization_returned_model: str | None
    localization_fallback: str | None
    active_recipient_count: int
    disabled_recipient_count: int
    archived_recipient_count: int
    total_recipient_count: int
    mode_counts: dict[str, int]
    latest_production_status_counts: dict[str, int]
    latest_production_delivery_at: str | None
    latest_successful_production_delivery_at: str | None
    database_connected: bool
    ai_configured: bool
    localization_configured: bool
    smtp_configured: bool
    audience_enabled: bool
    environment: str
    secrets_exposed: bool


class SystemStatusView(_ViewModel):
    database_backend: str
    database_connected: bool
    schema_migration_version: str | None
    c9_2_migration_present: bool
    ai_enabled: bool
    ai_provider_configured: bool
    localization_enabled: bool
    localization_model: str | None
    ai_request_cap: int
    ai_input_token_cap: int | None
    ai_output_token_cap: int | None
    smtp_configured: bool
    operator_email_configured: bool
    audience_enabled: bool
    active_recipient_count: int
    audience_cap: int
    schedule_mode: str
    audience_admin_token_configured: bool
    secret_values: tuple[str, ...]


class OperatorConsoleService:
    def __init__(self, store: RadarStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.audience = AudienceService(store, settings)

    def latest_briefing(self) -> DailyIntelligenceBrief | None:
        with self.store.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        c9_daily_briefings_table.c.run_id,
                        c9_daily_briefings_table.c.view_id,
                        c9_daily_briefings_table.c.rule_version,
                    )
                    .where(c9_daily_briefings_table.c.view_id == self.settings.radar_view_id)
                    .order_by(c9_daily_briefings_table.c.composed_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return self.store.load_daily_briefing(
            str(row["run_id"]), str(row["view_id"]), str(row["rule_version"])
        )

    def localization_summaries(self, briefing_id: str) -> tuple[LocalizationSummary, ...]:
        with self.store.engine.connect() as connection:
            rows = connection.execute(
                select(
                    c9_localizations_table.c.id,
                    c9_localizations_table.c.briefing_id,
                    c9_localizations_table.c.locale,
                    c9_localizations_table.c.localization_rule_version,
                    c9_localizations_table.c.requested_model,
                    c9_localizations_table.c.returned_model,
                    c9_localizations_table.c.provider,
                    c9_localizations_table.c.created_at,
                )
                .where(c9_localizations_table.c.briefing_id == briefing_id)
                .order_by(c9_localizations_table.c.created_at.desc())
            ).mappings()
            return tuple(
                LocalizationSummary(
                    snapshot_id=str(row["id"]),
                    briefing_id=str(row["briefing_id"]),
                    locale=str(row["locale"]),
                    rule_version=str(row["localization_rule_version"]),
                    requested_model=str(row["requested_model"]),
                    returned_model=(
                        str(row["returned_model"]) if row["returned_model"] is not None else None
                    ),
                    provider=str(row["provider"]),
                    created_at=_format_time(row["created_at"]) or "",
                )
                for row in rows
            )

    def briefing_summaries(self, limit: int = 20) -> tuple[BriefingSummary, ...]:
        bounded = max(1, min(limit, 20))
        with self.store.engine.connect() as connection:
            rows = connection.execute(
                select(
                    c9_daily_briefings_table.c.payload_json,
                    c9_daily_briefings_table.c.id,
                    c9_daily_briefings_table.c.run_id,
                    c9_daily_briefings_table.c.report_date,
                    c9_daily_briefings_table.c.composed_at,
                    c9_daily_briefings_table.c.primary_count,
                )
                .where(c9_daily_briefings_table.c.view_id == self.settings.radar_view_id)
                .order_by(c9_daily_briefings_table.c.composed_at.desc())
                .limit(bounded)
            ).mappings()
            result: list[BriefingSummary] = []
            for row in rows:
                brief = DailyIntelligenceBrief.model_validate_json(str(row["payload_json"]))
                result.append(
                    BriefingSummary(
                        briefing_id=str(row["id"]),
                        run_id=str(row["run_id"]),
                        report_date=str(row["report_date"]),
                        composed_at=_format_time(row["composed_at"]) or "",
                        primary_count=int(row["primary_count"]),
                        health=brief.data_quality.status,
                        ai_status=brief.ai_status,
                        localization_available=bool(self.localization_summaries(str(row["id"]))),
                    )
                )
            return tuple(result)

    def delivery_history(
        self, filters: DeliveryFilters | None = None
    ) -> tuple[DeliveryHistoryItem, ...]:
        current = filters or DeliveryFilters()
        statement = select(delivery_records_table).order_by(
            delivery_records_table.c.attempted_at.desc(),
            delivery_records_table.c.id.desc(),
        )
        if current.kind is not None:
            statement = statement.where(
                delivery_records_table.c.delivery_kind == current.kind.value
            )
        if current.status is not None:
            statement = statement.where(delivery_records_table.c.status == current.status.value)
        if current.recipient_id is not None:
            statement = statement.where(
                delivery_records_table.c.recipient_id == current.recipient_id
            )
        if current.delivery_mode is not None:
            statement = statement.where(
                delivery_records_table.c.requested_delivery_mode == current.delivery_mode.value
            )
        if current.date is not None:
            statement = statement.where(
                func.date(delivery_records_table.c.attempted_at) == date.fromisoformat(current.date)
            )
        statement = statement.limit(min(current.limit, 50))
        recipients = {item.id: item for item in self.store.list_recipients(include_archived=True)}
        with self.store.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            result: list[DeliveryHistoryItem] = []
            for row in rows:
                recipient = recipients.get(str(row["recipient_id"]))
                result.append(
                    DeliveryHistoryItem(
                        id=str(row["id"]),
                        run_id=str(row["run_id"]),
                        briefing_id=str(row["briefing_id"]),
                        recipient_id=str(row["recipient_id"]),
                        recipient_display_name=(
                            recipient.display_name
                            if recipient is not None
                            else "Archived recipient"
                        ),
                        recipient_email=str(row["recipient_email"]),
                        delivery_kind=DeliveryKind(str(row["delivery_kind"])),
                        requested_delivery_mode=DeliveryMode(str(row["requested_delivery_mode"])),
                        requested_locale=cast(
                            Literal["en", "zh-CN"], str(row["requested_locale"])
                        ),
                        rendered_locale=cast(
                            Literal["en", "zh-CN"], str(row["rendered_locale"])
                        ),
                        subject=str(row["subject"]),
                        status=DeliveryStatus(str(row["status"])),
                        attempted_at=_format_time(row["attempted_at"]),
                        accepted_at=_format_time(row["accepted_at"]),
                        error_type=(
                            str(row["error_type"]) if row["error_type"] is not None else None
                        ),
                        localization_snapshot_id=(
                            str(row["localization_snapshot_id"])
                            if row["localization_snapshot_id"] is not None
                            else None
                        ),
                        html_hash=(str(row["html_hash"]) if row["html_hash"] is not None else None),
                        fallback_reason=(
                            str(row["fallback_reason"])
                            if row["fallback_reason"] is not None
                            else None
                        ),
                    )
                )
            return tuple(result)

    def system_status(self) -> SystemStatusView:
        connected = False
        migration_version: str | None = None
        try:
            with self.store.engine.connect() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
                migration_version = connection.execute(
                    text("SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1")
                ).scalar_one_or_none()
                connected = True
        except Exception:
            connected = False
        active_count = sum(
            item.enabled and not item.archived for item in self.store.list_recipients()
        )
        smtp_configured = self.settings.email_mode == "smtp" and all(
            (
                self.settings.email_from,
                self.settings.smtp_host,
                self.settings.smtp_username,
                self.settings.smtp_password,
            )
        )
        return SystemStatusView(
            database_backend=self.settings.database_url.split(":", 1)[0],
            database_connected=connected,
            schema_migration_version=str(migration_version) if migration_version else None,
            c9_2_migration_present=(
                migration_version == C9_2_AUDIENCE_DELIVERY_MIGRATION
                or self._has_migration(C9_2_AUDIENCE_DELIVERY_MIGRATION)
            ),
            ai_enabled=self.settings.effective_ai_enabled,
            ai_provider_configured=bool(
                self.settings.llm_provider != "disabled" and self.settings.llm_api_key
            ),
            localization_enabled=self.settings.effective_localization_enabled,
            localization_model=self.settings.localization_model or self.settings.llm_model,
            ai_request_cap=self.settings.ai_max_requests_per_run,
            ai_input_token_cap=self.settings.ai_max_input_tokens_per_run,
            ai_output_token_cap=self.settings.ai_max_output_tokens_per_run,
            smtp_configured=bool(smtp_configured),
            operator_email_configured=bool(self.settings.operator_email),
            audience_enabled=self.settings.audience_enabled,
            active_recipient_count=active_count,
            audience_cap=self.settings.max_active_recipients,
            schedule_mode="audience" if self.settings.audience_enabled else "single",
            audience_admin_token_configured=bool(
                self.settings.audience_admin_token
                and self.settings.audience_admin_token.get_secret_value()
            ),
            secret_values=(),
        )

    def dashboard(self) -> DashboardView:
        briefing = self.latest_briefing()
        localization = self.localization_summaries(briefing.id) if briefing is not None else ()
        recipients = self.store.list_recipients(include_archived=True)
        active = [
            recipient_item for recipient_item in recipients if recipient_item.enabled and not recipient_item.archived
        ]
        disabled = [
            recipient_item
            for recipient_item in recipients
            if not recipient_item.enabled and not recipient_item.archived
        ]
        archived = [recipient_item for recipient_item in recipients if recipient_item.archived]
        mode_counts = {mode.value: 0 for mode in DeliveryMode}
        for recipient_item in recipients:
            mode_counts[recipient_item.delivery_mode.value] += 1
        production = self.delivery_history(DeliveryFilters(kind=DeliveryKind.PRODUCTION, limit=50))
        status_counts = {status.value: 0 for status in DeliveryStatus}
        for delivery_item in production:
            status_counts[delivery_item.status.value] += 1
        latest = production[0] if production else None
        successful = next(
            (delivery_item for delivery_item in production if delivery_item.status is DeliveryStatus.SENT),
            None,
        )
        fallback_reason = next(
            (
                delivery_item.fallback_reason
                for delivery_item in production
                if delivery_item.fallback_reason is not None
            ),
            None,
        )
        system = self.system_status()
        snapshot = localization[0] if localization else None
        return DashboardView(
            latest_briefing_id=briefing.id if briefing is not None else None,
            latest_briefing_date=briefing.report_date if briefing is not None else None,
            primary_count=len(briefing.primary_stories) if briefing is not None else 0,
            briefing_health=briefing.data_quality.status if briefing is not None else "unavailable",
            c9_status="available" if briefing is not None else "unavailable",
            localization_status="available" if snapshot is not None else "unavailable",
            localization_requested_model=(
                snapshot.requested_model if snapshot is not None else None
            ),
            localization_returned_model=snapshot.returned_model if snapshot is not None else None,
            localization_fallback=fallback_reason,
            active_recipient_count=len(active),
            disabled_recipient_count=len(disabled),
            archived_recipient_count=len(archived),
            total_recipient_count=len(recipients),
            mode_counts=mode_counts,
            latest_production_status_counts=status_counts,
            latest_production_delivery_at=latest.attempted_at if latest is not None else None,
            latest_successful_production_delivery_at=(
                successful.accepted_at if successful is not None else None
            ),
            database_connected=system.database_connected,
            ai_configured=system.ai_provider_configured,
            localization_configured=system.localization_enabled,
            smtp_configured=system.smtp_configured,
            audience_enabled=system.audience_enabled,
            environment=self.settings.app_env,
            secrets_exposed=False,
        )

    def _has_migration(self, version: str) -> bool:
        try:
            with self.store.engine.connect() as connection:
                return (
                    connection.execute(
                        text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                        {"version": version},
                    ).first()
                    is not None
                )
        except Exception:
            return False


def _format_time(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


ADMIN_CSS = """
:root{color-scheme:light;--ink:#172033;--muted:#667085;--line:#e7eaf0;--panel:#fff;
--bg:#f7f8fa;--accent:#635bff;--ok:#157347;--warn:#9a6700;--danger:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.app{display:flex;min-height:100vh}.sidebar{width:230px;background:#111827;color:#cbd5e1;padding:24px 14px}
.brand{font-size:17px;font-weight:700;color:#fff;padding:0 12px 28px}.nav-label{font-size:11px;
letter-spacing:.08em;text-transform:uppercase;color:#64748b;padding:14px 12px 6px}
.nav a{display:block;border-radius:8px;color:#cbd5e1;padding:9px 12px}.nav a:hover,.nav a.active{
background:#263247;color:#fff;text-decoration:none}.main{width:min(1280px,100%);padding:30px 36px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:26px}
h1{font-size:25px;letter-spacing:-.03em;margin:0}.subtitle{color:var(--muted);margin:4px 0 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:18px}
.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;
box-shadow:0 1px 2px #1018280a}.card-label{font-size:12px;color:var(--muted);text-transform:uppercase;
letter-spacing:.05em}.metric{font-size:25px;font-weight:700;margin-top:8px}.panel{margin-bottom:18px}
.panel h2{font-size:16px;margin:0 0 14px}.badge{display:inline-flex;border-radius:999px;padding:3px 8px;
font-size:12px;font-weight:600;background:#eef2f6;color:#475467}.badge.ok{background:#e8f6ee;color:var(--ok)}
.badge.warn{background:#fff4d6;color:var(--warn)}.badge.danger{background:#fdecec;color:var(--danger)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);
vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}td small{color:var(--muted)}
form{display:inline-flex;gap:7px;align-items:center;flex-wrap:wrap;margin:3px 0}
input,select,textarea{border:1px solid #d0d5dd;border-radius:7px;background:#fff;padding:8px 9px;font:inherit}
textarea{min-height:84px;min-width:260px}button{border:0;border-radius:7px;background:var(--accent);
color:#fff;padding:8px 12px;font:inherit;font-weight:600;cursor:pointer}button.secondary{background:#eef2f6;color:#344054}
button.danger{background:#b42318}.muted{color:var(--muted)}.stack>*{display:block;margin:6px 0}
.flash{background:#eef4ff;border:1px solid #c7d7fe;border-radius:9px;padding:11px 13px;margin-bottom:16px}
.empty{padding:28px;text-align:center;color:var(--muted)}.actions{display:flex;gap:8px;flex-wrap:wrap}
@media(max-width:760px){.app{display:block}.sidebar{width:auto;padding:14px}.nav{display:flex;gap:4px;overflow:auto}
.nav-label{display:none}.brand{padding:4px 10px 10px}.main{padding:20px 14px}.topbar{align-items:flex-start}}
"""


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _badge(value: str, *, good: bool = False, bad: bool = False) -> str:
    kind = (
        " ok"
        if good
        else " danger"
        if bad
        else " warn"
        if value in {"unavailable", "FAILED"}
        else ""
    )
    return f"<span class='badge{kind}'>{escape(value)}</span>"


def render_layout(page: str, title: str, body: str, csrf_token: str) -> str:
    links = (
        ("dashboard", "Dashboard"),
        ("recipients", "Recipients"),
        ("daily-message", "Daily Message"),
        ("preview", "Preview"),
        ("deliveries", "Deliveries"),
        ("briefings", "Briefings"),
        ("system", "System"),
    )
    nav = "".join(
        f"<a class='{'active' if page == key else ''}' href='/?page={key}'>{label}</a>"
        for key, label in links
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · Radar Admin</title><style>{ADMIN_CSS}</style></head>"
        "<body><div class='app'><aside class='sidebar'><div class='brand'>Global Investment Radar</div>"
        f"<div class='nav-label'>Operator Console</div><nav class='nav'>{nav}</nav></aside>"
        f"<main class='main'><div class='topbar'><div><h1>{escape(title)}</h1>"
        "<p class='subtitle'>Private beta operations · loopback only</p></div>"
        "<span class='badge ok'>Authenticated</span></div>"
        f"<input type='hidden' name='csrf_token' value='{escape(csrf_token)}'>{body}</main></div>"
        "</body></html>"
    )


def render_dashboard_page(console: OperatorConsoleService, csrf_token: str) -> str:
    view = console.dashboard()
    body = (
        "<div class='grid'>"
        f"<div class='card'><div class='card-label'>Latest briefing</div><div class='metric'>"
        f"{escape(view.latest_briefing_date or '—')}</div><small>{escape(view.latest_briefing_id or 'No briefing')}</small></div>"
        f"<div class='card'><div class='card-label'>Primary stories</div><div class='metric'>{view.primary_count}</div>"
        f"<small>C9 {_badge(view.c9_status, good=view.c9_status == 'available')}</small></div>"
        f"<div class='card'><div class='card-label'>Active audience</div><div class='metric'>{view.active_recipient_count}</div>"
        f"<small>{view.total_recipient_count} total · {view.archived_recipient_count} archived</small></div>"
        f"<div class='card'><div class='card-label'>Localization</div><div class='metric'>"
        f"{_badge(view.localization_status, good=view.localization_status == 'available')}</div>"
        f"<small>{escape(view.localization_requested_model or 'Not available')}</small></div></div>"
        "<div class='grid'>"
        f"<div class='panel'><h2>Audience</h2><p>Enabled: {_badge(str(view.audience_enabled), good=view.audience_enabled)}</p>"
        f"<p>Active {view.active_recipient_count} · Disabled {view.disabled_recipient_count} · Archived {view.archived_recipient_count}</p>"
        f"<p>Modes: {escape(view.mode_counts)}</p></div>"
        f"<div class='panel'><h2>Latest production run</h2><p>{escape(view.latest_production_status_counts)}</p>"
        f"<p>Latest attempt: {escape(view.latest_production_delivery_at or '—')}</p>"
        f"<p>Latest success: {escape(view.latest_successful_production_delivery_at or '—')}</p></div>"
        f"<div class='panel'><h2>System</h2><p>PostgreSQL: {_badge(str(view.database_connected), good=view.database_connected)}</p>"
        f"<p>AI configured: {_badge(str(view.ai_configured), good=view.ai_configured)}</p>"
        f"<p>SMTP configured: {_badge(str(view.smtp_configured), good=view.smtp_configured)}</p>"
        f"<p>Environment: {escape(view.environment)}</p></div></div>"
        "<div class='panel'><h2>Localization detail</h2>"
        f"<p>Requested model: {escape(view.localization_requested_model or '—')} · "
        f"Returned model: {escape(view.localization_returned_model or '—')} · "
        f"Fallback: {escape(view.localization_fallback or 'none')}</p></div>"
    )
    return render_layout("dashboard", "Dashboard", body, csrf_token)


def render_preview_page(console: OperatorConsoleService, csrf_token: str) -> str:
    recipients = console.store.list_recipients(include_archived=True)
    rows = "".join(
        "<tr>"
        f"<td>{escape(item.display_name)}</td><td>{escape(item.delivery_mode.value)}</td>"
        f"<td>{escape(item.locale)}</td><td>{_badge('archived' if item.archived else ('enabled' if item.enabled else 'disabled'), good=item.enabled and not item.archived)}</td>"
        f"<td><a href='/preview?recipient={escape(item.id)}' target='_blank' rel='noopener'>Open preview</a></td>"
        "</tr>"
        for item in recipients
    )
    if not rows:
        rows = "<tr><td colspan='5' class='empty'>No recipient profiles available.</td></tr>"
    body = (
        "<div class='panel'><h2>Choose a recipient</h2>"
        "<p class='muted'>The selected profile controls mode and locale. Opening a preview "
        "renders the current delivery output only; it does not call SMTP or reserve delivery.</p>"
        "<table><tr><th>Display name</th><th>Mode</th><th>Locale</th><th>Status</th><th>Action</th></tr>"
        + rows
        + "</table></div>"
    )
    return render_layout("preview", "Preview", body, csrf_token)


def render_deliveries_page(
    console: OperatorConsoleService, csrf_token: str, filters: DeliveryFilters
) -> str:
    records = console.delivery_history(filters)
    if not records:
        rows = (
            "<tr><td colspan='8' class='empty'>No deliveries match the current filters.</td></tr>"
        )
    else:
        rows = "".join(
            "<tr>"
            f"<td>{escape(item.attempted_at or '—')}</td>"
            f"<td>{escape(item.recipient_display_name)}<br><small>{escape(item.recipient_email)}</small></td>"
            f"<td>{escape(item.delivery_kind.value)}</td><td>{_badge(item.status.value, good=item.status is DeliveryStatus.SENT, bad=item.status is DeliveryStatus.FAILED)}</td>"
            f"<td>{escape(item.requested_delivery_mode.value)} / {escape(item.rendered_locale)}</td>"
            f"<td>{escape(item.subject)}</td><td>{escape(item.fallback_reason or 'none')}</td>"
            f"<td><small>{escape(item.id)}<br>{escape(item.run_id)}</small></td></tr>"
            for item in records
        )
    body = (
        "<div class='panel'><h2>Delivery History</h2>"
        "<form method='get'><input type='hidden' name='page' value='deliveries'>"
        "<select name='kind'><option value=''>All kinds</option><option value='production'>Production</option><option value='test'>Test</option></select>"
        "<select name='status'><option value=''>All statuses</option>"
        + "".join(f"<option value='{s.value}'>{s.value}</option>" for s in DeliveryStatus)
        + "</select><select name='mode'><option value=''>All modes</option>"
        + "".join(f"<option value='{m.value}'>{m.value}</option>" for m in DeliveryMode)
        + "</select><input name='date' type='date'><button class='secondary'>Filter</button></form>"
        + "<table><tr><th>Time</th><th>Recipient</th><th>Kind</th><th>Status</th><th>Mode / locale</th><th>Subject</th><th>Fallback</th><th>Identity</th></tr>"
        + rows
        + "</table></div>"
    )
    return render_layout("deliveries", "Deliveries", body, csrf_token)


def render_briefings_page(console: OperatorConsoleService, csrf_token: str) -> str:
    summaries = console.briefing_summaries()
    rows = ""
    for item in summaries:
        localizations = console.localization_summaries(item.briefing_id)
        models = ", ".join(
            f"{summary.requested_model} -> {summary.returned_model or '—'}"
            for summary in localizations
        )
        rows += (
            "<tr>"
            f"<td>{escape(item.report_date)}</td><td>{escape(item.briefing_id)}<br><small>{escape(item.run_id)}</small></td>"
            f"<td>{_badge(item.health, good=item.health.casefold() in {'ok', 'healthy', 'pass'})}</td>"
            f"<td>{item.primary_count}</td><td>{escape(item.composed_at)}</td>"
            f"<td>{_badge(str(item.localization_available), good=item.localization_available)}"
            f"<br><small>{escape(models or 'No snapshot')}</small></td></tr>"
        )
    if not rows:
        rows = "<tr><td colspan='6' class='empty'>No briefings available.</td></tr>"
    return render_layout(
        "briefings",
        "Briefings",
        "<div class='panel'><h2>Recent C9 briefings</h2><p class='muted'>Read-only canonical product snapshots.</p>"
        "<table><tr><th>Date</th><th>Briefing / run</th><th>Health</th><th>Primary</th><th>Composed</th><th>Localization</th></tr>"
        + rows
        + "</table></div>",
        csrf_token,
    )


def render_system_page(console: OperatorConsoleService, csrf_token: str) -> str:
    status = console.system_status()
    rows = (
        ("Database backend", status.database_backend),
        ("PostgreSQL connected", status.database_connected),
        ("Latest schema migration", status.schema_migration_version or "unknown"),
        ("C9.2 migration present", status.c9_2_migration_present),
        ("AI enabled", status.ai_enabled),
        ("AI provider configured", status.ai_provider_configured),
        ("Localization enabled", status.localization_enabled),
        ("Localization model", status.localization_model or "not configured"),
        ("AI request cap", status.ai_request_cap),
        ("SMTP configured", status.smtp_configured),
        ("Operator email configured", status.operator_email_configured),
        ("Audience enabled", status.audience_enabled),
        ("Active recipient count", status.active_recipient_count),
        ("Audience cap", status.audience_cap),
        ("Scheduled delivery mode", status.schedule_mode),
    )
    body = (
        "<div class='panel'><h2>Runtime status</h2><table>"
        + "".join(
            f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in rows
        )
        + "</table></div>"
    )
    return render_layout("system", "System", body, csrf_token)
