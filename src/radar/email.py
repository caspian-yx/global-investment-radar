from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Iterable
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from radar.audience import normalize_email, validate_email
from radar.config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)


def normalize_recipients(
    *sources: str | Iterable[str] | None,
) -> tuple[str, ...]:
    """Return valid recipients once, preserving deterministic first-seen order."""

    unique: dict[str, str] = {}
    for source in sources:
        if source is None:
            continue
        values = (source,) if isinstance(source, str) else source
        for value in values:
            for raw_recipient in value.split(","):
                if not raw_recipient.strip():
                    continue
                recipient = validate_email(raw_recipient)
                unique.setdefault(normalize_email(recipient), recipient)
    return tuple(unique.values())


class EmailSender(Protocol):
    name: str

    def send(self, *, subject: str, html: str) -> str: ...


class RecipientEmailSender(Protocol):
    def send_to(self, *, recipient: str, subject: str, html: str) -> str: ...

    def send_many(self, *, recipients: tuple[str, ...], subject: str, html: str) -> str: ...


class DisabledEmailSender:
    name = "disabled"

    def send(self, *, subject: str, html: str) -> str:
        _ = subject, html
        return "Email delivery disabled; HTML report was still generated."


class FileEmailSender:
    name = "file"

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def send(self, *, subject: str, html: str) -> str:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = "radar@example.invalid"
        message["To"] = "researcher@example.invalid"
        message.set_content("Open this message in an HTML-capable email client.")
        message.add_alternative(html, subtype="html")
        path = self.output_dir / "latest-report.eml"
        path.write_bytes(message.as_bytes())
        return f"Email fixture written to {path}"


class SMTPEmailSender:
    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        required = {
            "EMAIL_FROM": settings.email_from,
            "SMTP_HOST": settings.smtp_host,
            "SMTP_USERNAME": settings.smtp_username,
            "SMTP_PASSWORD": settings.smtp_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing SMTP configuration: {', '.join(missing)}")
        self.settings = settings

    def send(self, *, subject: str, html: str) -> str:
        if not self.settings.email_to:
            raise ValueError("Missing SMTP configuration: EMAIL_TO")
        recipients = normalize_recipients(self.settings.email_to)
        if not recipients:
            raise ValueError("Missing SMTP configuration: EMAIL_TO")
        raw_count = sum(bool(value.strip()) for value in self.settings.email_to.split(","))
        logger.info(
            "SMTP recipient counts raw=%d normalized=%d deduplicated=%d",
            raw_count,
            len(recipients),
            raw_count - len(recipients),
        )
        return self._send_message(
            recipients=recipients,
            subject=subject,
            html=html,
        )

    def send_to(self, *, recipient: str, subject: str, html: str) -> str:
        recipients = normalize_recipients(recipient)
        if len(recipients) != 1:
            raise ValueError("recipient must not be blank")
        return self._send_message(recipients=recipients, subject=subject, html=html)

    def send_many(self, *, recipients: tuple[str, ...], subject: str, html: str) -> str:
        normalized = normalize_recipients(recipients)
        if not normalized:
            raise ValueError("recipients must not be blank")
        return self._send_message(recipients=normalized, subject=subject, html=html)

    def _send_message(self, *, recipients: tuple[str, ...], subject: str, html: str) -> str:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.email_from
        message["To"] = ", ".join(recipients)
        message.set_content("Open this message in an HTML-capable email client.")
        message.add_alternative(html, subtype="html")
        with smtplib.SMTP(
            str(self.settings.smtp_host), self.settings.smtp_port, timeout=20
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(str(self.settings.smtp_username), str(self.settings.smtp_password))
            smtp.send_message(message, to_addrs=recipients)
        return "Email accepted by SMTP server."


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_mode == "file":
        return FileEmailSender(PROJECT_ROOT / "outputs" / "emails")
    if settings.email_mode == "smtp":
        return SMTPEmailSender(settings)
    return DisabledEmailSender()
