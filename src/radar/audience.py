from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_DISPLAY_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 2000
MAX_EMAIL_LENGTH = 320
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DeliveryMode(StrEnum):
    ENGLISH = "english"
    CHINESE = "chinese"
    BILINGUAL = "bilingual"


class DeliveryKind(StrEnum):
    PRODUCTION = "production"
    TEST = "test"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def validate_email(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > MAX_EMAIL_LENGTH
        or _EMAIL_PATTERN.fullmatch(candidate) is None
    ):
        raise ValueError("email address is invalid")
    return candidate


class RecipientProfile(_StrictModel):
    id: str = Field(min_length=1, pattern=r".*\S.*")
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH, pattern=r".*\S.*")
    email: str = Field(min_length=1, max_length=MAX_EMAIL_LENGTH)
    normalized_email: str = Field(min_length=1, max_length=MAX_EMAIL_LENGTH)
    enabled: bool
    archived: bool
    delivery_mode: DeliveryMode
    locale: Literal["en", "zh-CN"]
    personal_note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    created_at: datetime
    updated_at: datetime

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return validate_email(value)

    @model_validator(mode="after")
    def validate_consistency(self) -> RecipientProfile:
        if self.normalized_email != normalize_email(self.email):
            raise ValueError("normalized email does not match email")
        if self.archived and self.enabled:
            raise ValueError("archived recipient cannot be enabled")
        expected_locale = "en" if self.delivery_mode is DeliveryMode.ENGLISH else "zh-CN"
        if self.locale != expected_locale:
            raise ValueError("recipient delivery mode and locale are inconsistent")
        return self


class EditorialNote(_StrictModel):
    id: str = Field(min_length=1, pattern=r".*\S.*")
    briefing_id: str = Field(min_length=1, pattern=r".*\S.*")
    run_id: str = Field(min_length=1, pattern=r".*\S.*")
    message: str = Field(max_length=MAX_NOTE_LENGTH)
    created_at: datetime
    updated_at: datetime


class DeliveryRecord(_StrictModel):
    id: str = Field(min_length=1, pattern=r".*\S.*")
    run_id: str = Field(min_length=1, pattern=r".*\S.*")
    briefing_id: str = Field(min_length=1, pattern=r".*\S.*")
    recipient_id: str = Field(min_length=1, pattern=r".*\S.*")
    recipient_email: str = Field(min_length=1, max_length=MAX_EMAIL_LENGTH)
    delivery_kind: DeliveryKind
    requested_delivery_mode: DeliveryMode
    requested_locale: Literal["en", "zh-CN"]
    rendered_locale: Literal["en", "zh-CN"]
    subject: str = Field(min_length=1, pattern=r".*\S.*")
    status: DeliveryStatus
    attempted_at: datetime | None = None
    accepted_at: datetime | None = None
    error_type: str | None = Field(default=None, max_length=80)
    localization_snapshot_id: str | None = Field(default=None, max_length=80)
    html_hash: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    fallback_reason: str | None = Field(default=None, max_length=120)

    @field_validator("recipient_email")
    @classmethod
    def valid_recipient_email(cls, value: str) -> str:
        return validate_email(value)

    @model_validator(mode="after")
    def validate_consistency(self) -> DeliveryRecord:
        expected_locale = "en" if self.requested_delivery_mode is DeliveryMode.ENGLISH else "zh-CN"
        if self.requested_locale != expected_locale:
            raise ValueError("delivery mode and requested locale are inconsistent")
        for timestamp in (self.attempted_at, self.accepted_at):
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError("delivery timestamp must be timezone-aware")
        return self
