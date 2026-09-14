from __future__ import annotations

from datetime import UTC, datetime, timedelta

from test_briefing_composition import _compose
from test_localization_storage import _snapshot

from radar.admin_console import DeliveryFilters, OperatorConsoleService
from radar.audience import DeliveryKind, DeliveryMode, DeliveryRecord, DeliveryStatus
from radar.config import Settings
from radar.domain import content_hash
from radar.storage import RadarStore


def _store(tmp_path) -> RadarStore:
    store = RadarStore(f"sqlite:///{(tmp_path / 'console.db').as_posix()}")
    store.initialize()
    return store


def _delivery(briefing, recipient, index: int) -> DeliveryRecord:
    now = datetime(2026, 9, 14, 1, index, tzinfo=UTC)
    return DeliveryRecord(
        id=f"delivery-{index}",
        run_id=f"{briefing.run_id}-{index}",
        briefing_id=briefing.id,
        recipient_id=recipient.id,
        recipient_email=recipient.email,
        delivery_kind=DeliveryKind.TEST,
        requested_delivery_mode=recipient.delivery_mode,
        requested_locale=recipient.locale,
        rendered_locale="en",
        subject=f"[TEST] {index}",
        status=DeliveryStatus.SENT,
        attempted_at=now,
        accepted_at=now + timedelta(seconds=1),
        html_hash=content_hash(f"html-{index}"),
    )


def test_dashboard_exposes_bounded_counts_and_localization_metadata(tmp_path) -> None:
    store = _store(tmp_path)
    service = OperatorConsoleService(
        store,
        Settings(_env_file=None, APP_ENV="test", DATABASE_URL=store.database_url),
    )
    briefing, _ = _compose()
    store.save_daily_briefing(briefing)
    store.save_localization_snapshot(_snapshot(briefing))
    service.audience.add_recipient("Alice", "alice@example.com", DeliveryMode.BILINGUAL, "private")
    disabled = service.audience.add_recipient("Bob", "bob@example.com", DeliveryMode.ENGLISH, None)
    service.audience.set_enabled(disabled.id, False)

    dashboard = service.dashboard()

    assert dashboard.latest_briefing_id == briefing.id
    assert dashboard.primary_count == len(briefing.primary_stories)
    assert dashboard.active_recipient_count == 1
    assert dashboard.disabled_recipient_count == 1
    assert dashboard.mode_counts["bilingual"] == 1
    assert dashboard.localization_requested_model == "deepseek-v4-flash"
    assert dashboard.localization_returned_model == "deepseek-flash"
    assert dashboard.secrets_exposed is False


def test_delivery_history_is_limited_and_contains_no_html_or_note_body(tmp_path) -> None:
    store = _store(tmp_path)
    settings = Settings(_env_file=None, APP_ENV="test", DATABASE_URL=store.database_url)
    service = OperatorConsoleService(store, settings)
    briefing, _ = _compose()
    store.save_daily_briefing(briefing)
    recipient = service.audience.add_recipient(
        "Alice", "alice@example.com", DeliveryMode.ENGLISH, "private note"
    )
    for index in range(55):
        store.save_delivery_record(_delivery(briefing, recipient, index))

    history = service.delivery_history(DeliveryFilters(limit=100))

    assert len(history) == 50
    assert all("private note" not in item.model_dump_json() for item in history)
    assert all("<html" not in item.model_dump_json().casefold() for item in history)


def test_system_and_briefing_views_are_safe_and_bounded(tmp_path) -> None:
    store = _store(tmp_path)
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL=store.database_url,
        DEEPSEEK_API_KEY="secret-api-key",
        AUDIENCE_ADMIN_TOKEN="secret-admin-token",
    )
    service = OperatorConsoleService(store, settings)
    briefing, _ = _compose()
    store.save_daily_briefing(briefing)

    system = service.system_status()
    summaries = service.briefing_summaries(limit=100)

    assert system.database_backend == "sqlite"
    assert system.database_connected is True
    assert system.c9_2_migration_present is True
    assert system.audience_admin_token_configured is True
    assert system.secret_values == ()
    assert len(summaries) <= 20
    assert summaries[0].briefing_id == briefing.id
