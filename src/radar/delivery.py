from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.audience import (
    DeliveryKind,
    DeliveryMode,
    DeliveryRecord,
    DeliveryStatus,
    EditorialNote,
    RecipientProfile,
    normalize_email,
    validate_email,
)
from radar.briefing import DailyIntelligenceBrief
from radar.config import Settings
from radar.domain import content_hash, stable_id
from radar.email import RecipientEmailSender
from radar.localization import LocalizedBriefingContent
from radar.report import RecipientComposition, render_recipient_email
from radar.storage import RadarStore


class AudienceValidationError(ValueError):
    """A safe, bounded audience preflight or lifecycle error."""


@dataclass(frozen=True)
class RenderedDelivery:
    recipient: RecipientProfile
    subject: str
    html: str
    rendered_locale: Literal["en", "zh-CN"]


@dataclass(frozen=True)
class AudienceDeliverySummary:
    attempted_count: int
    sent_count: int
    failed_count: int
    skipped_count: int


def build_delivery_identity(briefing: DailyIntelligenceBrief, recipient: RecipientProfile) -> str:
    return stable_id(
        "c9.2-delivery",
        f"{briefing.run_id}|{briefing.id}|{recipient.id}|{recipient.delivery_mode.value}|{recipient.locale}",
    )


class RecipientInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    delivery_mode: DeliveryMode
    personal_note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_values(self) -> RecipientInput:
        if not self.display_name.strip():
            raise ValueError("display name must not be blank")
        validate_email(self.email)
        if self.personal_note is not None and not self.personal_note.strip():
            raise ValueError("personal note must not be blank")
        return self


def _locale_for_mode(mode: DeliveryMode) -> Literal["en", "zh-CN"]:
    return "en" if mode is DeliveryMode.ENGLISH else "zh-CN"


def build_recipient_id(email: str) -> str:
    return stable_id("c9.2-recipient", normalize_email(email))


class AudienceService:
    def __init__(self, store: RadarStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    def list_recipients(self) -> tuple[RecipientProfile, ...]:
        return self.store.list_recipients()

    def add_recipient(
        self,
        display_name: str,
        email: str,
        delivery_mode: DeliveryMode,
        personal_note: str | None,
    ) -> RecipientProfile:
        try:
            command = RecipientInput(
                display_name=display_name,
                email=email,
                delivery_mode=delivery_mode,
                personal_note=personal_note,
            )
            now = datetime.now(UTC)
            item = RecipientProfile(
                id=build_recipient_id(command.email),
                display_name=command.display_name.strip(),
                email=validate_email(command.email),
                normalized_email=normalize_email(command.email),
                enabled=True,
                archived=False,
                delivery_mode=command.delivery_mode,
                locale=_locale_for_mode(command.delivery_mode),
                personal_note=command.personal_note,
                created_at=now,
                updated_at=now,
            )
            if self.store.load_recipient(item.id) is not None:
                raise AudienceValidationError("recipient email already exists")
            self.store.save_recipient(item)
            return item
        except AudienceValidationError:
            raise
        except (ValueError, TypeError) as exc:
            raise AudienceValidationError("invalid recipient input") from exc

    def edit_recipient(
        self,
        recipient_id: str,
        *,
        display_name: str,
        email: str,
        delivery_mode: DeliveryMode,
        personal_note: str | None,
    ) -> RecipientProfile:
        current = self.store.load_recipient(recipient_id)
        if current is None:
            raise AudienceValidationError("recipient not found")
        try:
            command = RecipientInput(
                display_name=display_name,
                email=email,
                delivery_mode=delivery_mode,
                personal_note=personal_note,
            )
            updated = current.model_copy(
                update={
                    "display_name": command.display_name.strip(),
                    "email": validate_email(command.email),
                    "normalized_email": normalize_email(command.email),
                    "delivery_mode": command.delivery_mode,
                    "locale": _locale_for_mode(command.delivery_mode),
                    "personal_note": command.personal_note,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.store.save_recipient(updated)
            return updated
        except (ValueError, TypeError) as exc:
            raise AudienceValidationError("invalid recipient input") from exc

    def set_enabled(self, recipient_id: str, enabled: bool) -> RecipientProfile:
        current = self.store.load_recipient(recipient_id)
        if current is None:
            raise AudienceValidationError("recipient not found")
        if current.archived and enabled:
            raise AudienceValidationError("archived recipient cannot be enabled")
        updated = current.model_copy(update={"enabled": enabled, "updated_at": datetime.now(UTC)})
        self.store.save_recipient(updated)
        return updated

    def archive_recipient(self, recipient_id: str) -> RecipientProfile:
        current = self.store.load_recipient(recipient_id)
        if current is None:
            raise AudienceValidationError("recipient not found")
        updated = current.model_copy(
            update={"enabled": False, "archived": True, "updated_at": datetime.now(UTC)}
        )
        self.store.save_recipient(updated)
        return updated

    def save_editorial_note(
        self, briefing: DailyIntelligenceBrief, message: str
    ) -> EditorialNote | None:
        if not message.strip():
            self.store.delete_editorial_note(briefing.id)
            return None
        try:
            note = EditorialNote(
                id=stable_id("c9.2-editorial-note", briefing.id),
                briefing_id=briefing.id,
                run_id=briefing.run_id,
                message=message,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.store.save_editorial_note(note)
            return note
        except (ValueError, TypeError) as exc:
            raise AudienceValidationError("invalid editorial note") from exc

    def preflight(
        self, briefing: DailyIntelligenceBrief, *, explicit_audience: bool
    ) -> tuple[RecipientProfile, ...]:
        if not explicit_audience:
            raise AudienceValidationError("explicit audience delivery is required")
        if not self.settings.audience_enabled:
            raise AudienceValidationError("audience delivery is disabled")
        if self.settings.app_env == "production" and self._is_sqlite():
            raise AudienceValidationError("durable PostgreSQL is required for production audience")
        if not briefing.id or not briefing.run_id:
            raise AudienceValidationError("canonical briefing is required")

        active = tuple(
            item for item in self.store.list_recipients() if item.enabled and not item.archived
        )
        if len(active) > self.settings.max_active_recipients:
            raise AudienceValidationError("active recipient count exceeds configured cap")
        normalized: set[str] = set()
        for item in active:
            try:
                if normalize_email(item.email) != item.normalized_email:
                    raise ValueError("email normalization mismatch")
                validate_email(item.email)
            except ValueError as exc:
                raise AudienceValidationError("stored recipient state is invalid") from exc
            if item.normalized_email in normalized:
                raise AudienceValidationError("active recipient emails must be unique")
            normalized.add(item.normalized_email)

        if self.settings.app_env == "production":
            required = {
                "EMAIL_FROM": self.settings.email_from,
                "SMTP_HOST": self.settings.smtp_host,
                "SMTP_USERNAME": self.settings.smtp_username,
                "SMTP_PASSWORD": self.settings.smtp_password,
            }
            if self.settings.email_mode != "smtp" or any(not value for value in required.values()):
                raise AudienceValidationError("complete SMTP configuration is required")
        return active

    def _is_sqlite(self) -> bool:
        return self.settings.database_url.casefold().startswith("sqlite")

    def preview(self, briefing: DailyIntelligenceBrief, recipient_id: str) -> RenderedDelivery:
        recipient = self._require_recipient(recipient_id)
        return self._render(briefing, recipient, is_test=False)

    def send_test(
        self,
        briefing: DailyIntelligenceBrief,
        recipient_id: str,
        sender: RecipientEmailSender,
        localized: LocalizedBriefingContent | None = None,
    ) -> DeliveryRecord | None:
        recipient = self._require_recipient(recipient_id)
        operator_email = self.settings.operator_email
        if not operator_email:
            raise AudienceValidationError("operator email is required for test delivery")
        rendered = self._render(briefing, recipient, is_test=True, localized=localized)
        now = datetime.now(UTC)
        record = DeliveryRecord(
            id=stable_id(
                "c9.2-test-delivery",
                f"{briefing.run_id}|{briefing.id}|{recipient.id}|{operator_email}",
            ),
            run_id=briefing.run_id,
            briefing_id=briefing.id,
            recipient_id=recipient.id,
            recipient_email=operator_email,
            delivery_kind=DeliveryKind.TEST,
            requested_delivery_mode=recipient.delivery_mode,
            requested_locale=recipient.locale,
            rendered_locale=rendered.rendered_locale,
            subject=rendered.subject,
            status=DeliveryStatus.PENDING,
            attempted_at=now,
            accepted_at=None,
            error_type=None,
            localization_snapshot_id=None,
            html_hash=content_hash(rendered.html),
            fallback_reason=(
                "localization_unavailable"
                if rendered.rendered_locale == "en"
                and recipient.delivery_mode is not DeliveryMode.ENGLISH
                else None
            ),
        )
        if not self.store.reserve_delivery(record):
            return self.store.load_delivery(record.id)
        try:
            sender.send_to(recipient=operator_email, subject=rendered.subject, html=rendered.html)
        except Exception as exc:
            failed = record.model_copy(
                update={"status": DeliveryStatus.FAILED, "error_type": type(exc).__name__[:80]}
            )
            self.store.save_delivery_record(failed)
            return failed
        sent = record.model_copy(
            update={"status": DeliveryStatus.SENT, "accepted_at": datetime.now(UTC)}
        )
        self.store.save_delivery_record(sent)
        return sent

    def deliver_briefing_to_audience(
        self,
        briefing: DailyIntelligenceBrief,
        localized: LocalizedBriefingContent | None,
        sender: RecipientEmailSender,
        *,
        localization_snapshot_id: str | None = None,
    ) -> AudienceDeliverySummary:
        recipients = self.preflight(briefing, explicit_audience=True)
        attempted = sent = failed = skipped = 0
        for recipient in recipients:
            rendered = self._render(briefing, recipient, localized=localized)
            now = datetime.now(UTC)
            record = DeliveryRecord(
                id=build_delivery_identity(briefing, recipient),
                run_id=briefing.run_id,
                briefing_id=briefing.id,
                recipient_id=recipient.id,
                recipient_email=recipient.email,
                delivery_kind=DeliveryKind.PRODUCTION,
                requested_delivery_mode=recipient.delivery_mode,
                requested_locale=recipient.locale,
                rendered_locale=rendered.rendered_locale,
                subject=rendered.subject,
                status=DeliveryStatus.PENDING,
                attempted_at=now,
                accepted_at=None,
                error_type=None,
                localization_snapshot_id=localization_snapshot_id,
                html_hash=content_hash(rendered.html),
                fallback_reason=(
                    "localization_unavailable"
                    if rendered.rendered_locale == "en"
                    and recipient.delivery_mode is not DeliveryMode.ENGLISH
                    else None
                ),
            )
            if not self.store.reserve_delivery(record):
                skipped += 1
                continue
            attempted += 1
            try:
                sender.send_to(
                    recipient=recipient.email, subject=rendered.subject, html=rendered.html
                )
            except Exception as exc:
                failed += 1
                self.store.save_delivery_record(
                    record.model_copy(
                        update={
                            "status": DeliveryStatus.FAILED,
                            "error_type": type(exc).__name__[:80],
                        }
                    )
                )
                continue
            sent += 1
            self.store.save_delivery_record(
                record.model_copy(
                    update={"status": DeliveryStatus.SENT, "accepted_at": datetime.now(UTC)}
                )
            )
        return AudienceDeliverySummary(attempted, sent, failed, skipped)

    def _require_recipient(self, recipient_id: str) -> RecipientProfile:
        recipient = self.store.load_recipient(recipient_id)
        if recipient is None:
            raise AudienceValidationError("recipient not found")
        return recipient

    def _render(
        self,
        briefing: DailyIntelligenceBrief,
        recipient: RecipientProfile,
        *,
        is_test: bool = False,
        localized: LocalizedBriefingContent | None = None,
    ) -> RenderedDelivery:
        if localized is None:
            localized = None
        note = self.store.load_editorial_note(briefing.id)
        subject, html, rendered_locale = render_recipient_email(
            briefing,
            localized,
            RecipientComposition(
                delivery_mode=recipient.delivery_mode,
                recipient=recipient,
                editorial_note=note.message if note is not None else None,
                personal_note=recipient.personal_note,
                is_test=is_test,
            ),
        )
        return RenderedDelivery(recipient, subject, html, rendered_locale)
