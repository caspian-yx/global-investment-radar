from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from radar.audience import (
    DeliveryKind,
    DeliveryMode,
    DeliveryRecord,
    DeliveryStatus,
    EditorialNote,
    RecipientProfile,
    normalize_email,
)

NOW = datetime(2026, 9, 13, 5, 0, tzinfo=UTC)


def _recipient(**overrides):
    values = {
        "id": "recipient-1",
        "display_name": "Jack",
        "email": "Jack@example.com",
        "normalized_email": "jack@example.com",
        "enabled": True,
        "archived": False,
        "delivery_mode": DeliveryMode.BILINGUAL,
        "locale": "zh-CN",
        "personal_note": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return RecipientProfile(**values)


def test_recipient_normalizes_email_and_preserves_mode_locale_contract() -> None:
    recipient = _recipient(email="  Jack@Example.COM ", normalized_email="jack@example.com")

    assert recipient.email == "Jack@Example.COM"
    assert normalize_email(recipient.email) == "jack@example.com"
    assert recipient.delivery_mode is DeliveryMode.BILINGUAL
    assert recipient.locale == "zh-CN"


@pytest.mark.parametrize("email", ["not-an-email", "missing-at.example.com", "jack@"])
def test_recipient_rejects_malformed_email(email: str) -> None:
    with pytest.raises(ValueError, match="email"):
        _recipient(email=email, normalized_email=normalize_email(email))


def test_recipient_rejects_mode_locale_mismatch() -> None:
    with pytest.raises(ValueError, match="locale"):
        _recipient(delivery_mode=DeliveryMode.ENGLISH, locale="zh-CN")


def test_archived_recipient_cannot_remain_enabled() -> None:
    with pytest.raises(ValueError, match="archived"):
        _recipient(archived=True, enabled=True)


def test_recipient_rejects_unknown_fields_and_overlong_note() -> None:
    with pytest.raises(ValidationError):
        _recipient(unexpected="value")
    with pytest.raises(ValidationError):
        _recipient(personal_note="x" * 2001)


def test_editorial_note_allows_empty_message_but_rejects_unknown_fields() -> None:
    note = EditorialNote(
        id="note-1",
        briefing_id="briefing-1",
        run_id="run-1",
        message="",
        created_at=NOW,
        updated_at=NOW,
    )

    assert note.message == ""
    with pytest.raises(ValidationError):
        EditorialNote(
            id="note-1",
            briefing_id="briefing-1",
            run_id="run-1",
            message="",
            created_at=NOW,
            updated_at=NOW,
            secret="must be rejected",
        )


def test_delivery_record_has_bounded_kind_status_and_audit_fields() -> None:
    record = DeliveryRecord(
        id="delivery-1",
        run_id="run-1",
        briefing_id="briefing-1",
        recipient_id="recipient-1",
        recipient_email="jack@example.com",
        delivery_kind=DeliveryKind.PRODUCTION,
        requested_delivery_mode=DeliveryMode.BILINGUAL,
        requested_locale="zh-CN",
        rendered_locale="zh-CN",
        subject="Global Investment Radar — 2026-09-13 全球投资晨报",
        status=DeliveryStatus.PENDING,
        attempted_at=None,
        accepted_at=None,
        error_type=None,
        localization_snapshot_id="snapshot-1",
        html_hash="a" * 64,
        fallback_reason=None,
    )

    assert record.delivery_kind is DeliveryKind.PRODUCTION
    assert record.status is DeliveryStatus.PENDING
    with pytest.raises(ValidationError):
        DeliveryRecord(**record.model_dump(), unexpected="value")
