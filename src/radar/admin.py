# ruff: noqa: E501
from __future__ import annotations

import base64
import binascii
import hmac
import html
import logging
import secrets
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlparse

from radar.admin_console import (
    AdminLanguage,
    DeliveryFilters,
    OperatorConsoleService,
    normalize_admin_language,
    render_briefings_page,
    render_dashboard_page,
    render_deliveries_page,
    render_layout,
    render_preview_page,
    render_settings_page,
    render_system_page,
    ui,
)
from radar.audience import (
    DeliveryKind,
    DeliveryMode,
    DeliveryStatus,
    EditorialNote,
    RecipientProfile,
)
from radar.briefing import DailyIntelligenceBrief
from radar.config import Settings
from radar.delivery import AudienceService, AudienceValidationError
from radar.email import RecipientEmailSender, build_email_sender
from radar.storage import RadarStore, c9_daily_briefings_table

logger = logging.getLogger(__name__)


def authorization_header(username: str, token: str) -> str:
    encoded = base64.b64encode(f"{username}:{token}".encode()).decode("ascii")
    return f"Basic {encoded}"


class AudienceAdminHandler(BaseHTTPRequestHandler):
    service: AudienceService | None = None
    console_service: OperatorConsoleService | None = None
    admin_token: str | None = None
    csrf_token: str | None = None

    @staticmethod
    def render_value(value: str) -> str:
        return html.escape(value, quote=True)

    @classmethod
    def is_authorized(cls, header: str | None, token: str) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            username, supplied = decoded.split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        return hmac.compare_digest(username, "operator") and hmac.compare_digest(supplied, token)

    def _authorized(self) -> bool:
        token = type(self).admin_token
        return token is not None and type(self).is_authorized(
            self.headers.get("Authorization"), token
        )

    def _service(self) -> AudienceService:
        service = type(self).service
        if service is None:
            raise RuntimeError("admin service is not configured")
        return service

    def _console(self) -> OperatorConsoleService:
        service = self._service()
        return OperatorConsoleService(service.store, service.settings)

    @classmethod
    def is_csrf_valid(cls, supplied: str | None, expected: str | None) -> bool:
        return (
            supplied is not None
            and expected is not None
            and bool(supplied)
            and hmac.compare_digest(supplied, expected)
        )

    def _reply(
        self, status: int, body: str, *, content_type: str = "text/html; charset=utf-8"
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:;",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str, *, set_cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _current_location(self) -> str:
        parsed = urlparse(self.path)
        return (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")

    def _language(self) -> AdminLanguage:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return "en"
        morsel = cookie.get("radar_admin_language")
        return normalize_admin_language(morsel.value if morsel is not None else None)

    def _unauthorized(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="radar-admin"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._unauthorized()
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/preview":
                recipient_id = parse_qs(parsed.query).get("recipient", [""])[0]
                briefing = self._latest_briefing()
                if briefing is None:
                    raise AudienceValidationError("no canonical briefing is available")
                self._reply(
                    200,
                    self._service().preview(briefing, recipient_id).html,
                    content_type="text/html; charset=utf-8",
                )
                return
            query = parse_qs(parsed.query)
            page = query.get("page", ["dashboard"])[0]
            language = self._language()
            if page == "dashboard":
                body = render_dashboard_page(self._console(), self._csrf(), language)
            elif page == "recipients":
                body = render_layout(
                    "recipients", ui(language, "recipients"), self._index(language), self._csrf(), language
                )
            elif page == "daily-message":
                body = render_layout(
                    "daily-message",
                    ui(language, "daily_message"),
                    self._daily_message_page(language),
                    self._csrf(),
                    language,
                )
            elif page == "preview":
                body = render_preview_page(self._console(), self._csrf(), language)
            elif page == "deliveries":
                body = render_deliveries_page(
                    self._console(), self._csrf(), self._delivery_filters(query), language
                )
            elif page == "briefings":
                body = render_briefings_page(self._console(), self._csrf(), language)
            elif page == "settings":
                body = render_settings_page(self._csrf(), language)
            elif page == "system":
                body = render_system_page(self._console(), self._csrf(), language)
            else:
                self._reply(404, "Page not found.")
                return
            self._reply(200, body)
        except AudienceValidationError:
            self._reply(400, "Request could not be completed.")
        except (TypeError, ValueError):
            self._reply(400, "Request could not be completed.")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._unauthorized()
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            self._reply(413, "Request too large.")
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = {key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()}
        if not self.is_csrf_valid(fields.get("csrf_token"), self._csrf()):
            self._reply(403, "Request could not be completed.")
            return
        try:
            self._mutate(fields)
            if fields.get("action") == "set_language":
                language = normalize_admin_language(fields.get("language"))
                self._redirect(
                    self._current_location(),
                    set_cookie=(
                        f"radar_admin_language={language}; Path=/; Max-Age=31536000; "
                        "HttpOnly; SameSite=Lax"
                    ),
                )
            else:
                self._redirect(self._current_location())
        except (AudienceValidationError, ValueError, KeyError):
            self._reply(400, "Request could not be completed.")

    def _mutate(self, fields: dict[str, str]) -> None:
        service = self._service()
        action = fields.get("action", "")
        if action == "set_language":
            if fields.get("language") not in {"en", "zh-CN"}:
                raise AudienceValidationError("unsupported admin language")
            return
        if action == "add":
            service.add_recipient(
                fields["display_name"],
                fields["email"],
                DeliveryMode(fields["mode"]),
                fields.get("note") or None,
            )
        elif action == "edit":
            service.edit_recipient(
                fields["recipient_id"],
                display_name=fields["display_name"],
                email=fields["email"],
                delivery_mode=DeliveryMode(fields["mode"]),
                personal_note=fields.get("note") or None,
            )
        elif action in {"enable", "disable"}:
            service.set_enabled(fields["recipient_id"], action == "enable")
        elif action == "remove":
            service.archive_recipient(fields["recipient_id"])
        elif action in {"editorial_save", "editorial_clear"}:
            briefing = self._latest_briefing()
            if briefing is None:
                raise AudienceValidationError("no canonical briefing is available")
            service.save_editorial_note(
                briefing,
                fields.get("message", "") if action == "editorial_save" else "",
            )
        elif action == "send_test":
            briefing = self._latest_briefing()
            if briefing is None:
                raise AudienceValidationError("no canonical briefing is available")
            service.send_test(
                briefing,
                fields["recipient_id"],
                cast(RecipientEmailSender, build_email_sender(service.settings)),
            )
        else:
            raise AudienceValidationError("unknown admin action")

    def _index(self, language: AdminLanguage = "en") -> str:
        service = self._service()
        recipients = service.store.list_recipients(include_archived=True)
        rows = "".join(self._recipient_row(item, language) for item in recipients)
        note = self._latest_note()
        note_value = self.render_value(note.message if note is not None else "")
        csrf = self._csrf()
        add_form = (
            "<form method='post'><input type='hidden' name='action' value='add'>"
            f"<input type='hidden' name='csrf_token' value='{self.render_value(csrf)}'>"
            f"<input name='display_name' placeholder='{('显示名称' if language == 'zh-CN' else 'Display name')}' required>"
            "<input name='email' type='email' placeholder='Email' required>"
            "<select name='mode'><option value='english'>english</option>"
            "<option value='chinese'>chinese</option><option value='bilingual' selected>bilingual</option></select>"
            "<small class='muted'>Locale follows mode: en for English, zh-CN otherwise.</small>"
            f"<textarea name='note' maxlength='2000' placeholder='{('可选个人备注' if language == 'zh-CN' else 'Optional personal note')}'></textarea>"
            f"<button>{'添加收件人' if language == 'zh-CN' else 'Add recipient'}</button></form>"
        )
        message_form = (
            "<form method='post'><input type='hidden' name='action' value='editorial_save'>"
            f"<input type='hidden' name='csrf_token' value='{self.render_value(csrf)}'>"
            f"<textarea name='message' maxlength='2000'>{note_value}</textarea>"
            f"<button>{'保存编辑备注' if language == 'zh-CN' else 'Save editorial note'}</button></form>"
            "<form method='post'><input type='hidden' name='action' value='editorial_clear'>"
            f"<input type='hidden' name='csrf_token' value='{self.render_value(csrf)}'>"
            f"<button>{'清除编辑备注' if language == 'zh-CN' else 'Clear editorial note'}</button></form>"
        )
        active_count = sum(item.enabled and not item.archived for item in recipients)
        delivery = (
            f"<p>{'活跃收件人' if language == 'zh-CN' else 'Active recipients'}: {active_count} / {service.settings.max_active_recipients}</p>"
        )
        return (
            f"<div class='panel'><h2>{ui(language, 'recipients')}</h2><p class='muted'>{('管理私有测试收件人配置。创建不会自动发送邮件。' if language == 'zh-CN' else 'Manage private-beta audience profiles. Creation never sends automatically.')}</p>" + add_form + "<table>"
            f"<tr><th>{('名称' if language == 'zh-CN' else 'Name')}</th><th>Email</th><th>Mode</th><th>{('状态' if language == 'zh-CN' else 'State')}</th>"
            f"<th>Locale</th><th>{('备注' if language == 'zh-CN' else 'Note')}</th><th>{('状态' if language == 'zh-CN' else 'Status')}</th><th>{('更新时间' if language == 'zh-CN' else 'Updated')}</th>"
            f"<th>{('操作 / 最近投递' if language == 'zh-CN' else 'Actions / latest delivery')}</th></tr>"
            + rows
            + "</table></div><div class='panel'><h2>"
            + ui(language, "daily_message")
            + "</h2>"
            + message_form
            + "</div><div class='panel'><h2>"
            + ("收件人安全" if language == "zh-CN" else "Audience safety")
            + "</h2>"
            + delivery
            + ("<p>生产投递仍由 workflow 控制。每个收件人都可以单独预览或发送运营者测试邮件。</p>"
               if language == "zh-CN"
               else "<p>Production delivery remains workflow-controlled. Preview and operator test delivery are available per recipient.</p>")
            + "</div>"
        )

    def _recipient_row(self, item: RecipientProfile, language: AdminLanguage = "en") -> str:
        action = "disable" if item.enabled else "enable"
        mode_options = "".join(
            (
                f"<option value='{mode.value}'"
                + (" selected" if item.delivery_mode is mode else "")
                + f">{mode.value}</option>"
            )
            for mode in DeliveryMode
        )
        edit_form = (
            "<details><summary>Edit</summary>"
            "<form method='post'><input type='hidden' name='action' value='edit'>"
            f"<input type='hidden' name='csrf_token' value='{self.render_value(self._csrf())}'>"
            + f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
            + f"<input name='display_name' value='{self.render_value(item.display_name)}' required>"
            + f"<input name='email' type='email' value='{self.render_value(item.email)}' required>"
            + f"<select name='mode'>{mode_options}</select>"
            + (
                "<textarea name='note' maxlength='2000'>"
                f"{self.render_value(item.personal_note or '')}</textarea>"
            )
            + "<button>Save recipient</button></form></details>"
        )
        test_form = (
            f"<form method='post' onsubmit=\"return confirm('{('只发送给运营者吗？' if language == 'zh-CN' else 'Send a test to the operator only?')}')\">"
            "<input type='hidden' name='action' value='send_test'>"
            f"<input type='hidden' name='csrf_token' value='{self.render_value(self._csrf())}'>"
            + f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
            + f"<button>{'发送测试邮件给我' if language == 'zh-CN' else 'Send Test To Me'}</button></form>"
        )
        toggle_form = (
            "<span class='badge'>archived</span>"
            if item.archived
            else (
                f"<form method='post' onsubmit=\"return confirm('{('更改此收件人状态吗？' if language == 'zh-CN' else 'Change this recipient status?')}')\">"
                f"<input type='hidden' name='action' value='{action}'>"
                f"<input type='hidden' name='csrf_token' value='{self.render_value(self._csrf())}'>"
                f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
                f"<button class='secondary'>{('启用' if action == 'enable' else '停用') if language == 'zh-CN' else action}</button></form>"
            )
        )
        archive_form = (
            f"<form method='post' onsubmit=\"return confirm('{('归档此收件人吗？' if language == 'zh-CN' else 'Archive this recipient?')}')\">"
            "<input type='hidden' name='action' value='remove'>"
            f"<input type='hidden' name='csrf_token' value='{self.render_value(self._csrf())}'>"
            f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
            f"<button class='danger'>{'归档' if language == 'zh-CN' else 'archive'}</button></form>"
            if not item.archived
            else ""
        )
        return (
            "<tr><td>"
            + self.render_value(item.display_name)
            + "</td><td>"
            + self.render_value(item.email)
            + "</td><td>"
            + self.render_value(item.delivery_mode.value)
            + "</td><td>"
            + self.render_value(item.locale)
            + "</td><td>"
            + ("yes" if item.personal_note else "no")
            + "</td><td>"
            + ("archived" if item.archived else ("enabled" if item.enabled else "disabled"))
            + "</td><td>"
            + self.render_value(item.updated_at.isoformat())
            + "</td><td>"
            + toggle_form
            + archive_form
            + edit_form
            + test_form
            + f"<a href='/preview?recipient={self.render_value(item.id)}'>preview</a>"
            + self._delivery_history(item)
            + "</td></tr>"
        )

    def _delivery_history(self, item: RecipientProfile) -> str:
        briefing = self._latest_briefing()
        if briefing is None:
            return "<small>Latest delivery: no briefing</small>"
        records = [
            record
            for record in self._service().store.list_delivery_records(briefing.run_id)
            if record.recipient_id == item.id
        ]
        if not records:
            return "<small>Latest delivery: none</small>"
        entries = "".join(
            "<li>"
            + self.render_value(record.delivery_kind.value)
            + " / "
            + self.render_value(record.status.value)
            + "</li>"
            for record in records[-5:]
        )
        return "<ul class='delivery-history'>" + entries + "</ul>"

    def _csrf(self) -> str:
        return type(self).csrf_token or ""

    def _delivery_filters(self, query: dict[str, list[str]]) -> DeliveryFilters:
        values: dict[str, object] = {}
        kind = query.get("kind", [""])[0]
        status = query.get("status", [""])[0]
        mode = query.get("mode", [""])[0]
        requested_date = query.get("date", [""])[0]
        if kind:
            values["kind"] = DeliveryKind(kind)
        if status:
            values["status"] = DeliveryStatus(status)
        if mode:
            values["delivery_mode"] = DeliveryMode(mode)
        if requested_date:
            values["date"] = requested_date
        return DeliveryFilters.model_validate(values)

    def _daily_message_page(self, language: AdminLanguage = "en") -> str:
        briefing = self._latest_briefing()
        if briefing is None:
            return (
                f"<div class='panel'><h2>{ui(language, 'daily_message')}</h2>"
                "<div class='empty'>No canonical briefing is available.</div></div>"
            )
        note = self._latest_note()
        message = self.render_value(note.message if note is not None else "")
        csrf = self.render_value(self._csrf())
        body = (
            f"<div class='panel'><h2>{'编辑备注' if language == 'zh-CN' else 'Editorial note'}</h2>"
            f"<p class='muted'>Applies to briefing {self.render_value(briefing.id)} "
            f"from run {self.render_value(briefing.run_id)} · {self.render_value(briefing.report_date)}</p>"
            + ("<p>仅用于外部投递元数据，不会修改 Radar intelligence。</p>"
               if language == "zh-CN"
               else "<p>External delivery metadata only; it does not modify Radar intelligence.</p>")
            + "<form method='post' class='stack'><input type='hidden' name='action' value='editorial_save'>"
            + f"<input type='hidden' name='csrf_token' value='{csrf}'>"
            + f"<textarea name='message' maxlength='2000' placeholder='Optional note'>{message}</textarea>"
            + f"<button>{'保存备注' if language == 'zh-CN' else 'Save note'}</button></form>"
            + "<form method='post'><input type='hidden' name='action' value='editorial_clear'>"
            + f"<input type='hidden' name='csrf_token' value='{csrf}'><button class='secondary'>{'清除备注' if language == 'zh-CN' else 'Clear note'}</button></form>"
            + "</div>"
        )
        return (
            f"<div class='panel'><h2>{'当前简报' if language == 'zh-CN' else 'Current briefing'}</h2>"
            f"<p>{self.render_value(briefing.view_name)} · {self.render_value(briefing.report_date)} · "
            f"Primary {len(briefing.primary_stories)}</p></div>" + body
        )

    def _latest_note(self) -> EditorialNote | None:
        briefing = self._latest_briefing()
        return self._service().store.load_editorial_note(briefing.id) if briefing else None

    def _latest_briefing(self) -> DailyIntelligenceBrief | None:
        service = self._service()
        with service.store.engine.connect() as connection:
            row = (
                connection.execute(
                    c9_daily_briefings_table.select()
                    .where(c9_daily_briefings_table.c.view_id == service.settings.radar_view_id)
                    .order_by(c9_daily_briefings_table.c.composed_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return service.store.load_daily_briefing(
            str(row["run_id"]), str(row["view_id"]), str(row["rule_version"])
        )

    def log_message(self, format: str, *args: object) -> None:
        logger.info("admin request completed status=%s", args[1] if len(args) > 1 else "unknown")


def run_admin(settings: Settings) -> None:
    token = settings.audience_admin_token
    if token is None or not token.get_secret_value():
        raise ValueError("AUDIENCE_ADMIN_TOKEN is required")
    store = RadarStore(settings.database_url)
    store.initialize()
    AudienceAdminHandler.service = AudienceService(store, settings)
    AudienceAdminHandler.console_service = OperatorConsoleService(store, settings)
    AudienceAdminHandler.admin_token = token.get_secret_value()
    AudienceAdminHandler.csrf_token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", settings.admin_port), AudienceAdminHandler)
    print(
        "Admin Console:\n"
        f"http://127.0.0.1:{settings.admin_port}\n"
        f"Database backend: {settings.database_url.split(':', 1)[0]}\n"
        "Authentication: required",
        flush=True,
    )
    logger.info(
        "audience admin ready url=http://127.0.0.1:%s backend=%s auth=required",
        settings.admin_port,
        settings.database_url.split(":", 1)[0],
    )
    server.serve_forever()
