import html

from radar.admin import AudienceAdminHandler, authorization_header
from radar.audience import DeliveryMode
from radar.config import Settings
from radar.delivery import AudienceService
from radar.storage import RadarStore


def _service(tmp_path) -> AudienceService:
    store = RadarStore(f"sqlite:///{(tmp_path / 'admin.db').as_posix()}")
    store.initialize()
    return AudienceService(
        store,
        Settings(_env_file=None, APP_ENV="test", DATABASE_URL=store.database_url),
    )


def test_admin_auth_uses_operator_basic_token_and_constant_safe_comparison() -> None:
    token = "test-admin-token"
    assert AudienceAdminHandler.is_authorized(authorization_header("operator", token), token)
    assert not AudienceAdminHandler.is_authorized(authorization_header("operator", "wrong"), token)
    assert not AudienceAdminHandler.is_authorized(authorization_header("other", token), token)
    assert not AudienceAdminHandler.is_authorized("not-basic", token)


def test_admin_html_escapes_untrusted_values() -> None:
    assert html.escape("<script>alert(1)</script>") in AudienceAdminHandler.render_value(
        "<script>alert(1)</script>"
    )


def test_admin_recipient_row_exposes_edit_test_and_preview_controls(tmp_path) -> None:
    service = _service(tmp_path)
    recipient = service.add_recipient(
        "Alice <watcher>", "alice@example.com", DeliveryMode.BILINGUAL, "note <safe>"
    )

    handler = object.__new__(AudienceAdminHandler)
    AudienceAdminHandler.service = service
    row = handler._recipient_row(recipient)

    assert "value='edit'" in row
    assert "value='send_test'" in row
    assert "preview?recipient=" in row
    assert "Latest delivery:" in row
    assert "Alice &lt;watcher&gt;" in row
    assert "note &lt;safe&gt;" in row


def test_admin_index_labels_locale_and_bounded_delivery_history(tmp_path) -> None:
    service = _service(tmp_path)
    service.add_recipient("Alice", "alice@example.com", DeliveryMode.ENGLISH, None)
    handler = object.__new__(AudienceAdminHandler)
    AudienceAdminHandler.service = service

    page = handler._index()

    assert "<th>Locale</th>" in page
    assert "Actions / latest delivery" in page
    assert "Latest delivery:" in page
