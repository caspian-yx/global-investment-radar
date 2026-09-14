from __future__ import annotations

import base64
import binascii
import hmac
import html
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlparse

from radar.audience import DeliveryMode, EditorialNote, RecipientProfile
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
    admin_token: str | None = None

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

    def _reply(
        self, status: int, body: str, *, content_type: str = "text/html; charset=utf-8"
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
                self._reply(200, self._service().preview(briefing, recipient_id).html)
                return
            self._reply(200, self._index())
        except AudienceValidationError:
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
        try:
            self._mutate(fields)
            self._reply(200, "OK")
        except (AudienceValidationError, ValueError, KeyError):
            self._reply(400, "Request could not be completed.")

    def _mutate(self, fields: dict[str, str]) -> None:
        service = self._service()
        action = fields.get("action", "")
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

    def _index(self) -> str:
        service = self._service()
        recipients = service.list_recipients()
        rows = "".join(self._recipient_row(item) for item in recipients)
        note = self._latest_note()
        note_value = self.render_value(note.message if note is not None else "")
        add_form = (
            "<form method='post'><input type='hidden' name='action' value='add'>"
            "<input name='display_name' placeholder='Display name' required>"
            "<input name='email' type='email' placeholder='Email' required>"
            "<select name='mode'><option>english</option><option>chinese</option>"
            "<option>bilingual</option></select><textarea name='note' maxlength='2000'></textarea>"
            "<button>Add recipient</button></form>"
        )
        message_form = (
            "<form method='post'><input type='hidden' name='action' value='editorial_save'>"
            f"<textarea name='message' maxlength='2000'>{note_value}</textarea>"
            "<button>Save editorial note</button></form>"
            "<form method='post'><input type='hidden' name='action' value='editorial_clear'>"
            "<button>Clear editorial note</button></form>"
        )
        delivery = (
            f"<p>Active recipients: {len(recipients)} / "
            f"{service.settings.max_active_recipients}</p>"
        )
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<title>Radar Admin</title></head><body><h1>Audience</h1>" + add_form + "<table>"
            "<tr><th>Name</th><th>Email</th><th>Mode</th><th>Status</th>"
            "<th>Locale</th><th>Actions / latest delivery</th></tr>"
            + rows
            + "</table><h2>Message</h2>"
            + message_form
            + "<h2>Delivery</h2>"
            + delivery
            + "<p>Preview and operator test delivery are available per recipient.</p>"
            "</body></html>"
        )

    def _recipient_row(self, item: RecipientProfile) -> str:
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
            "<form method='post'><input type='hidden' name='action' value='send_test'>"
            + f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
            + "<button>Send Test To Me</button></form>"
        )
        return (
            "<tr><td>"
            + self.render_value(item.display_name)
            + "</td><td>"
            + self.render_value(item.email)
            + "</td><td>"
            + self.render_value(item.delivery_mode.value)
            + "</td><td>"
            + ("enabled" if item.enabled else "disabled")
            + "</td><td>"
            + self.render_value(item.locale)
            + "</td><td>"
            + f"<form method='post'><input type='hidden' name='action' value='{action}'>"
            + f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
            + f"<button>{action}</button></form>"
            + "<form method='post'><input type='hidden' name='action' value='remove'>"
            + f"<input type='hidden' name='recipient_id' value='{self.render_value(item.id)}'>"
            + "<button>remove</button></form>"
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
    AudienceAdminHandler.admin_token = token.get_secret_value()
    server = ThreadingHTTPServer(("127.0.0.1", settings.admin_port), AudienceAdminHandler)
    logger.info("audience admin listening on loopback port=%s", settings.admin_port)
    server.serve_forever()
