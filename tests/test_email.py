from __future__ import annotations

import ssl
from email.message import EmailMessage
from typing import Any

import pytest

from radar.config import Settings
from radar.email import FileEmailSender, SMTPEmailSender


def test_file_email_sender_writes_an_html_message(tmp_path) -> None:
    status = FileEmailSender(tmp_path).send(subject="Daily radar", html="<b>Ready</b>")

    message_path = tmp_path / "latest-report.eml"
    assert status == f"Email fixture written to {message_path}"
    message = message_path.read_text(encoding="utf-8")
    assert "Subject: Daily radar" in message
    assert "<b>Ready</b>" in message


def test_smtp_sender_uses_tls_login_and_html(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            calls["connection"] = (host, port, timeout)

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def starttls(self, *, context: ssl.SSLContext) -> None:
            calls["tls"] = isinstance(context, ssl.SSLContext)

        def login(self, username: str, password: str) -> None:
            calls["login"] = (username, password)

        def send_message(self, message: EmailMessage) -> None:
            calls["message"] = message

    monkeypatch.setattr("radar.email.smtplib.SMTP", FakeSMTP)
    settings = Settings(
        email_mode="smtp",
        email_from="radar@example.com",
        email_to="owner@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="smtp-user",
        smtp_password="test-only-password",
    )
    status = SMTPEmailSender(settings).send(subject="Daily radar", html="<b>Ready</b>")

    assert status == "Email accepted by SMTP server."
    assert calls["connection"] == ("smtp.example.com", 587, 20)
    assert calls["tls"] is True
    assert calls["login"] == ("smtp-user", "test-only-password")
    message = calls["message"]
    assert isinstance(message, EmailMessage)
    assert message["To"] == "owner@example.com"
    assert "<b>Ready</b>" in message.get_body(preferencelist=("html",)).get_content()


def test_smtp_sender_rejects_missing_credentials() -> None:
    with pytest.raises(ValueError, match="Missing SMTP configuration"):
        SMTPEmailSender(Settings(email_mode="smtp"))


def test_smtp_sender_send_to_creates_one_independent_recipient_message(monkeypatch) -> None:
    calls: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            _ = host, port, timeout

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def starttls(self, *, context: ssl.SSLContext) -> None:
            _ = context

        def login(self, username: str, password: str) -> None:
            _ = username, password

        def send_message(self, message: EmailMessage) -> None:
            calls.append(message)

    monkeypatch.setattr("radar.email.smtplib.SMTP", FakeSMTP)
    settings = Settings(
        email_mode="smtp",
        email_from="radar@example.com",
        smtp_host="smtp.example.com",
        smtp_username="smtp-user",
        smtp_password="test-only-password",
    )
    SMTPEmailSender(settings).send_to(
        recipient="alice@example.com", subject="Daily", html="<b>Only Alice</b>"
    )

    assert len(calls) == 1
    assert calls[0]["To"] == "alice@example.com"
    assert calls[0].get_all("Cc") is None
    assert calls[0].get_all("Bcc") is None
