import pytest
from pydantic import ValidationError

from radar.config import Settings


def test_audience_settings_are_safe_and_disabled_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.audience_enabled is False
    assert settings.max_active_recipients == 20
    assert settings.operator_email is None
    assert settings.audience_admin_token is None
    assert settings.admin_port == 8765


def test_audience_settings_accept_operator_email_and_token() -> None:
    settings = Settings(
        _env_file=None,
        AUDIENCE_ENABLED=True,
        MAX_ACTIVE_RECIPIENTS=10,
        OPERATOR_EMAIL="operator@example.com",
        AUDIENCE_ADMIN_TOKEN="local-secret",
        ADMIN_PORT=8877,
    )

    assert settings.audience_enabled is True
    assert settings.max_active_recipients == 10
    assert settings.operator_email == "operator@example.com"
    assert settings.audience_admin_token is not None
    assert settings.audience_admin_token.get_secret_value() == "local-secret"
    assert settings.admin_port == 8877


@pytest.mark.parametrize("value", [0, -1, 101])
def test_audience_active_limit_is_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, MAX_ACTIVE_RECIPIENTS=value)


def test_operator_email_rejects_malformed_value() -> None:
    with pytest.raises(ValidationError, match="email"):
        Settings(_env_file=None, OPERATOR_EMAIL="not-an-email")


def test_admin_port_rejects_non_tcp_port() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ADMIN_PORT=80)
