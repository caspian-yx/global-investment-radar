import json
from datetime import timedelta

from sqlalchemy import text
from test_audience import NOW, _recipient

from radar.audience import DeliveryKind, DeliveryMode, DeliveryRecord, DeliveryStatus, EditorialNote
from radar.storage import C9_2_AUDIENCE_DELIVERY_MIGRATION, RadarStore


def _record(**overrides):
    values = {
        "id": "delivery-1",
        "run_id": "run-1",
        "briefing_id": "briefing-1",
        "recipient_id": "recipient-1",
        "recipient_email": "jack@example.com",
        "delivery_kind": DeliveryKind.PRODUCTION,
        "requested_delivery_mode": DeliveryMode.BILINGUAL,
        "requested_locale": "zh-CN",
        "rendered_locale": "zh-CN",
        "subject": "Global Investment Radar — 2026-09-13 全球投资晨报",
        "status": DeliveryStatus.PENDING,
        "attempted_at": None,
        "accepted_at": None,
        "error_type": None,
        "localization_snapshot_id": "snapshot-1",
        "html_hash": "a" * 64,
        "fallback_reason": None,
    }
    values.update(overrides)
    return DeliveryRecord(**values)


def test_migration_008_is_additive_and_idempotent(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'audience.db').as_posix()}")

    store.initialize()
    store.initialize()

    with store.engine.connect() as connection:
        tables = set(connection.dialect.get_table_names(connection))
        versions = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())

    assert {"recipient_profiles", "editorial_notes", "delivery_records"} <= tables
    assert C9_2_AUDIENCE_DELIVERY_MIGRATION in versions


def test_recipient_and_editorial_note_round_trip(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'audience.db').as_posix()}")
    store.initialize()
    recipient = _recipient()
    note = EditorialNote(
        id="note-1",
        briefing_id="briefing-1",
        run_id="run-1",
        message="今天重点看供应链。",
        created_at=NOW,
        updated_at=NOW,
    )

    store.save_recipient(recipient)
    store.save_editorial_note(note)

    assert store.load_recipient(recipient.id) == recipient
    assert store.list_recipients() == (recipient,)
    assert store.load_editorial_note("briefing-1") == note


def test_archive_update_keeps_historical_delivery_record(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'audience.db').as_posix()}")
    store.initialize()
    recipient = _recipient()
    store.save_recipient(recipient)
    record = _record()
    store.save_delivery_record(record)

    archived = recipient.model_copy(
        update={"enabled": False, "archived": True, "updated_at": NOW + timedelta(minutes=1)}
    )
    store.save_recipient(archived)

    assert store.list_recipients() == ()
    assert store.list_recipients(include_archived=True) == (archived,)
    assert store.load_delivery(record.id) == record


def test_delivery_reservation_is_unique_and_status_update_is_bounded(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'audience.db').as_posix()}")
    store.initialize()
    record = _record()

    assert store.reserve_delivery(record) is True
    assert store.reserve_delivery(record) is False
    assert store.reserve_delivery(record.model_copy(update={"id": "delivery-2"})) is False

    sent = record.model_copy(
        update={
            "status": DeliveryStatus.SENT,
            "attempted_at": NOW,
            "accepted_at": NOW,
        }
    )
    store.save_delivery_record(sent)

    loaded = store.load_delivery(record.id)
    assert loaded is not None
    assert loaded.status is DeliveryStatus.SENT
    assert json.loads(store.list_delivery_records("run-1")[0].model_dump_json())["id"] == record.id
