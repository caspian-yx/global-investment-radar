from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlencode

import pytest
from test_briefing_composition import _compose
from test_localization_storage import _snapshot

from radar.admin import AudienceAdminHandler, authorization_header
from radar.admin_console import OperatorConsoleService
from radar.audience import DeliveryKind, DeliveryMode, DeliveryStatus
from radar.config import Settings
from radar.delivery import AudienceService, AudienceValidationError
from radar.storage import RadarStore


def _service(tmp_path) -> AudienceService:
    store = RadarStore(f"sqlite:///{(tmp_path / 'admin.db').as_posix()}")
    store.initialize()
    return AudienceService(
        store,
        Settings(_env_file=None, APP_ENV="test", DATABASE_URL=store.database_url),
    )


def test_admin_routes_have_shared_shell_and_require_csrf_for_mutations(tmp_path) -> None:
    service = _service(tmp_path)
    settings = service.settings
    token = "test-admin-token"
    AudienceAdminHandler.service = service
    AudienceAdminHandler.console_service = OperatorConsoleService(service.store, settings)
    AudienceAdminHandler.admin_token = token
    AudienceAdminHandler.csrf_token = "csrf-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), AudienceAdminHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        headers = {"Authorization": authorization_header("operator", token)}
        connection.request("GET", "/?page=system", headers=headers)
        response = connection.getresponse()
        page = response.read().decode()

        assert response.status == 200
        assert "System" in page
        assert "Dashboard" in page
        assert "Recipients" in page
        assert "Daily Message" in page
        assert "Deliveries" in page
        assert "Briefings" in page
        assert "AUDIENCE_ADMIN_TOKEN" not in page
        assert token not in page

        form = urlencode({"action": "editorial_clear"})
        connection.request(
            "POST",
            "/",
            body=form,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert connection.getresponse().status == 403
        assert AudienceAdminHandler.is_csrf_valid("csrf-test-token", "csrf-test-token")
        assert not AudienceAdminHandler.is_csrf_valid("wrong", "csrf-test-token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_successful_recipient_submission_redirects_back_to_current_page(tmp_path) -> None:
    service = _service(tmp_path)
    token = "test-admin-token"
    AudienceAdminHandler.service = service
    AudienceAdminHandler.admin_token = token
    AudienceAdminHandler.csrf_token = "csrf-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), AudienceAdminHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        headers = {
            "Authorization": authorization_header("operator", token),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        connection.request(
            "POST",
            "/?page=recipients",
            body=urlencode(
                {
                    "action": "add",
                    "csrf_token": "csrf-test-token",
                    "display_name": "Alice",
                    "email": "alice@example.com",
                    "mode": "bilingual",
                }
            ),
            headers=headers,
        )
        response = connection.getresponse()
        response.read()

        assert response.status == 303
        assert response.getheader("Location") == "/?page=recipients"
        recipient = service.list_recipients()[0]
        assert recipient.display_name == "Alice"

        connection.request(
            "POST",
            "/?page=recipients",
            body=urlencode(
                {
                    "action": "disable",
                    "csrf_token": "csrf-test-token",
                    "recipient_id": recipient.id,
                }
            ),
            headers=headers,
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 303
        assert response.getheader("Location") == "/?page=recipients"
        assert service.list_recipients()[0].enabled is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_admin_settings_switches_ui_language_without_translating_values(tmp_path) -> None:
    service = _service(tmp_path)
    token = "test-admin-token"
    AudienceAdminHandler.service = service
    AudienceAdminHandler.admin_token = token
    AudienceAdminHandler.csrf_token = "csrf-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), AudienceAdminHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        headers = {"Authorization": authorization_header("operator", token)}
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/?page=settings", headers=headers)
        response = connection.getresponse()
        english_page = response.read().decode()
        assert response.status == 200
        assert "Settings" in english_page
        assert "中文" in english_page

        connection.request(
            "POST",
            "/?page=settings",
            body=urlencode(
                {
                    "action": "set_language",
                    "language": "zh-CN",
                    "csrf_token": "csrf-test-token",
                }
            ),
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        cookie = response.getheader("Set-Cookie")
        assert response.status == 303
        assert response.getheader("Location") == "/?page=settings"
        assert cookie is not None and "radar_admin_language=zh-CN" in cookie

        connection.request(
            "GET",
            "/?page=system",
            headers={**headers, "Cookie": "radar_admin_language=zh-CN"},
        )
        response = connection.getresponse()
        chinese_page = response.read().decode()
        assert response.status == 200
        assert "设置" in chinese_page
        assert "系统" in chinese_page
        assert "PostgreSQL" in chinese_page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_admin_edit_rejects_normalized_email_owned_by_another_recipient(tmp_path) -> None:
    service = _service(tmp_path)
    first = service.add_recipient("Alice", "alice@example.com", DeliveryMode.ENGLISH, None)
    service.add_recipient("Bob", "bob@example.com", DeliveryMode.ENGLISH, None)

    with pytest.raises(AudienceValidationError, match="email"):
        service.edit_recipient(
            first.id,
            display_name="Alice",
            email=" BOB@example.com ",
            delivery_mode=DeliveryMode.ENGLISH,
            personal_note=None,
        )


def test_admin_preview_is_snapshot_backed_and_has_no_delivery_side_effect(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'preview.db').as_posix()}")
    store.initialize()
    service = AudienceService(
        store,
        Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=store.database_url,
            LOCALIZATION_MODEL="deepseek-v4-flash",
        ),
    )
    briefing, _ = _compose()
    service.store.save_daily_briefing(briefing)
    recipient = service.add_recipient("Alice", "alice@example.com", DeliveryMode.BILINGUAL, None)
    service.store.save_localization_snapshot(_snapshot(briefing))
    token = "test-admin-token"
    AudienceAdminHandler.service = service
    AudienceAdminHandler.admin_token = token
    AudienceAdminHandler.csrf_token = "csrf-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), AudienceAdminHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "GET",
            f"/preview?recipient={recipient.id}",
            headers={"Authorization": authorization_header("operator", token)},
        )
        response = connection.getresponse()
        page = response.read().decode()

        assert response.status == 200
        assert "English reference" in page
        assert response.getheader("Cache-Control") == "no-store"
        assert service.store.list_delivery_records(briefing.run_id) == ()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_admin_console_pages_cover_operations_and_localization_visibility(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'pages.db').as_posix()}")
    store.initialize()
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=store.database_url,
        LOCALIZATION_MODEL="deepseek-v4-flash",
    )
    service = AudienceService(store, settings)
    briefing, _ = _compose()
    store.save_daily_briefing(briefing)
    store.save_localization_snapshot(_snapshot(briefing))
    service.add_recipient("Alice", "alice@example.com", DeliveryMode.BILINGUAL, None)
    token = "test-admin-token"
    AudienceAdminHandler.service = service
    AudienceAdminHandler.admin_token = token
    AudienceAdminHandler.csrf_token = "csrf-test-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), AudienceAdminHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        headers = {"Authorization": authorization_header("operator", token)}
        for page_name, marker in (
            ("dashboard", "Dashboard"),
            ("recipients", "Recipients"),
            ("daily-message", "Daily Message"),
            ("preview", "Preview"),
            ("deliveries", "Deliveries"),
            ("briefings", "Briefings"),
            ("system", "System"),
        ):
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", f"/?page={page_name}", headers=headers)
            response = connection.getresponse()
            page = response.read().decode()
            assert response.status == 200
            assert marker in page
            assert "<html" in page

        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/?page=briefings", headers=headers)
        page = connection.getresponse().read().decode()
        assert "deepseek-v4-flash" in page
        assert "deepseek-flash" in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_admin_delivery_query_parameters_are_strictly_parsed(tmp_path) -> None:
    handler = object.__new__(AudienceAdminHandler)

    filters = handler._delivery_filters(
        {
            "kind": ["production"],
            "status": ["SENT"],
            "mode": ["bilingual"],
            "date": ["2026-09-14"],
        }
    )

    assert filters.kind is DeliveryKind.PRODUCTION
    assert filters.status is DeliveryStatus.SENT
    assert filters.delivery_mode is DeliveryMode.BILINGUAL
    assert filters.date == "2026-09-14"
