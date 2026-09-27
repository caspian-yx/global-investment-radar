import pytest
from test_briefing_composition import _compose
from test_localization_storage import _snapshot
from test_web_intelligence import _market

from radar.audience import DeliveryKind, DeliveryMode, DeliveryStatus
from radar.config import Settings
from radar.delivery import AudienceService, AudienceValidationError
from radar.storage import RadarStore


def _store(tmp_path) -> RadarStore:
    store = RadarStore(f"sqlite:///{(tmp_path / 'audience.db').as_posix()}")
    store.initialize()
    return store


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "audience_enabled": True,
        "max_active_recipients": 2,
        "database_url": "sqlite:///work/test-audience.db",
    }
    values.update(overrides)
    return Settings(**values)


def test_recipient_lifecycle_uses_stable_id_and_archive(tmp_path) -> None:
    service = AudienceService(_store(tmp_path), _settings())

    created = service.add_recipient(" Alice ", " Alice@Example.com ", DeliveryMode.ENGLISH, None)
    edited = service.edit_recipient(
        created.id,
        display_name="Alice Research",
        email="alice@example.com",
        delivery_mode=DeliveryMode.BILINGUAL,
        personal_note="Watch Asia.",
    )

    assert edited.id == created.id
    assert edited.normalized_email == "alice@example.com"
    assert edited.locale == "zh-CN"
    assert service.set_enabled(created.id, False).enabled is False
    archived = service.archive_recipient(created.id)
    assert archived.archived is True
    assert archived.enabled is False
    assert service.list_recipients() == ()


def test_duplicate_normalized_email_is_rejected_even_when_existing_is_disabled(tmp_path) -> None:
    service = AudienceService(_store(tmp_path), _settings())
    first = service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    service.set_enabled(first.id, False)

    with pytest.raises(AudienceValidationError, match="email"):
        service.add_recipient("A2", " A@EXAMPLE.COM ", DeliveryMode.ENGLISH, None)


def test_preflight_requires_both_activation_gates_and_enforces_cap(tmp_path) -> None:
    store = _store(tmp_path)
    briefing, _ = _compose()
    service = AudienceService(store, _settings(max_active_recipients=1))
    service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    service.add_recipient("B", "b@example.com", DeliveryMode.ENGLISH, None)

    with pytest.raises(AudienceValidationError, match="explicit audience"):
        service.preflight(briefing, explicit_audience=False)

    with pytest.raises(AudienceValidationError, match="cap"):
        service.preflight(briefing, explicit_audience=True)

    disabled = AudienceService(store, _settings(audience_enabled=False, max_active_recipients=2))
    with pytest.raises(AudienceValidationError, match="disabled"):
        disabled.preflight(briefing, explicit_audience=True)


def test_production_sqlite_rejected_before_sender_boundary(tmp_path) -> None:
    service = AudienceService(
        _store(tmp_path),
        _settings(
            app_env="production",
            email_mode="smtp",
            email_from="from@example.com",
            smtp_host="smtp.example",
            smtp_username="u",
            smtp_password="p",
        ),
    )
    briefing, _ = _compose()
    service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)

    with pytest.raises(AudienceValidationError, match="durable"):
        service.preflight(briefing, explicit_audience=True)


def test_editorial_note_is_scoped_to_briefing_and_empty_clears(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(store, _settings())
    briefing, _ = _compose()
    note = service.save_editorial_note(briefing, "Read the policy details.")
    assert note is not None
    assert note.briefing_id == briefing.id
    assert note.run_id == briefing.run_id
    assert service.save_editorial_note(briefing, "") is None
    assert store.load_editorial_note(briefing.id) is None


def test_recipient_bounded_input_is_rejected(tmp_path) -> None:
    service = AudienceService(_store(tmp_path), _settings())
    with pytest.raises(AudienceValidationError):
        service.add_recipient("x" * 121, "x@example.com", DeliveryMode.ENGLISH, None)
    with pytest.raises(AudienceValidationError):
        service.add_recipient("X", "x2@example.com", DeliveryMode.ENGLISH, "x" * 2001)


class _Sender:
    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []

    def send_to(self, *, recipient: str, subject: str, html: str) -> str:
        _ = subject, html
        self.calls.append(recipient)
        if recipient in self.fail:
            raise TimeoutError("test timeout")
        return "accepted"


class _BatchSender:
    def __init__(self) -> None:
        self.batch_calls: list[tuple[tuple[str, ...], str, str]] = []

    def send_to(self, *, recipient: str, subject: str, html: str) -> str:
        raise AssertionError(f"individual send leaked: {recipient}")

    def send_many(self, *, recipients: tuple[str, ...], subject: str, html: str) -> str:
        self.batch_calls.append((recipients, subject, html))
        return "accepted"


def test_audience_groups_identical_rendered_messages_into_one_envelope(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(store, _settings())
    service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    service.add_recipient("B", "b@example.com", DeliveryMode.ENGLISH, None)
    briefing, _ = _compose()
    sender = _BatchSender()

    summary = service.deliver_briefing_to_audience(briefing, None, sender)

    assert summary.attempted_count == 2
    assert summary.sent_count == 2
    assert len(sender.batch_calls) == 1
    assert set(sender.batch_calls[0][0]) == {"a@example.com", "b@example.com"}
    assert len(store.list_delivery_records(briefing.run_id)) == 2


def test_delivery_identity_is_deterministic_and_each_recipient_is_independent(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(store, _settings())
    a = service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    b = service.add_recipient("B", "b@example.com", DeliveryMode.ENGLISH, None)
    briefing, _ = _compose()
    sender = _Sender(fail={"b@example.com"})

    summary = service.deliver_briefing_to_audience(briefing, None, sender)

    assert set(sender.calls) == {"a@example.com", "b@example.com"}
    assert summary.sent_count == 1
    assert summary.failed_count == 1
    records = {item.recipient_id: item for item in store.list_delivery_records(briefing.run_id)}
    assert records[a.id].status is DeliveryStatus.SENT
    assert records[b.id].status is DeliveryStatus.FAILED
    assert records[a.id].delivery_kind is DeliveryKind.PRODUCTION


def test_audience_delivery_keeps_full_c7_market_history_in_recipient_email(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(store, _settings())
    service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    briefing, _ = _compose()
    delivered: dict[str, str] = {}

    class CaptureSender:
        def send_to(self, *, recipient: str, subject: str, html: str) -> str:
            _ = recipient, subject
            delivered["html"] = html
            return "accepted"

    summary = service.deliver_briefing_to_audience(
        briefing,
        None,
        CaptureSender(),
        markets=(
            _market(
                "^VIX",
                "VIX",
                -4.1,
                history=(21, 20, 19, 18, 17, 16, 15, 14),
            ),
        ),
    )

    assert summary.sent_count == 1
    assert "Volatility &amp; Rates" in delivered["html"]
    assert "5D" in delivered["html"]
    assert "7D" in delivered["html"]
    assert "7-day history unavailable" not in delivered["html"]


def test_existing_pending_sent_failed_and_skipped_records_are_never_resent(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(store, _settings())
    recipient = service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    briefing, _ = _compose()
    sender = _Sender()
    first = service.deliver_briefing_to_audience(briefing, None, sender)
    second = service.deliver_briefing_to_audience(briefing, None, sender)

    assert first.sent_count == 1
    assert second.sent_count == 0
    assert second.skipped_count == 1
    assert sender.calls == [recipient.email]


def test_preview_is_render_only_and_send_test_targets_operator(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(
        store,
        _settings(operator_email="operator@example.com"),
    )
    recipient = service.add_recipient("A", "a@example.com", DeliveryMode.ENGLISH, None)
    briefing, _ = _compose()

    preview = service.preview(briefing, recipient.id)
    assert preview.html
    assert store.list_delivery_records(briefing.run_id) == ()

    sender = _Sender()
    record = service.send_test(briefing, recipient.id, sender)
    assert record is not None
    assert sender.calls == ["operator@example.com"]
    assert record.delivery_kind is DeliveryKind.TEST
    assert not any(
        item.delivery_kind is DeliveryKind.PRODUCTION
        for item in store.list_delivery_records(briefing.run_id)
    )


def test_bilingual_preview_reuses_persisted_localization_without_delivery(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(store, _settings(localization_model="deepseek-v4-flash"))
    recipient = service.add_recipient("A", "a@example.com", DeliveryMode.BILINGUAL, None)
    briefing, _ = _compose()
    snapshot = _snapshot(briefing)
    store.save_localization_snapshot(snapshot)

    preview = service.preview(briefing, recipient.id)

    assert preview.rendered_locale == "zh-CN"
    assert "全球投资雷达" in preview.html
    assert "English reference" in preview.html
    assert store.list_delivery_records(briefing.run_id) == ()


def test_bilingual_send_test_reuses_snapshot_and_records_its_identity(tmp_path) -> None:
    store = _store(tmp_path)
    service = AudienceService(
        store,
        _settings(operator_email="operator@example.com", localization_model="deepseek-v4-flash"),
    )
    recipient = service.add_recipient("A", "a@example.com", DeliveryMode.BILINGUAL, None)
    briefing, _ = _compose()
    snapshot = _snapshot(briefing)
    store.save_localization_snapshot(snapshot)
    sender = _Sender()

    record = service.send_test(briefing, recipient.id, sender)

    assert record is not None
    assert record.rendered_locale == "zh-CN"
    assert record.localization_snapshot_id == snapshot.id
    assert record.subject.startswith("[TEST] ")
    assert sender.calls == ["operator@example.com"]
