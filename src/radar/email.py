from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from radar.config import PROJECT_ROOT, Settings


class EmailSender(Protocol):
    name: str

    def send(self, *, subject: str, html: str) -> str: ...


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
            "EMAIL_TO": settings.email_to,
            "SMTP_HOST": settings.smtp_host,
            "SMTP_USERNAME": settings.smtp_username,
            "SMTP_PASSWORD": settings.smtp_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing SMTP configuration: {', '.join(missing)}")
        self.settings = settings

    def send(self, *, subject: str, html: str) -> str:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.email_from
        message["To"] = self.settings.email_to
        message.set_content("Open this message in an HTML-capable email client.")
        message.add_alternative(html, subtype="html")
        with smtplib.SMTP(
            str(self.settings.smtp_host), self.settings.smtp_port, timeout=20
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(str(self.settings.smtp_username), str(self.settings.smtp_password))
            smtp.send_message(message)
        return "Email accepted by SMTP server."


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_mode == "file":
        return FileEmailSender(PROJECT_ROOT / "outputs" / "emails")
    if settings.email_mode == "smtp":
        return SMTPEmailSender(settings)
    return DisabledEmailSender()
