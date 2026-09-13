from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from radar.audience import validate_email

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration. No provider credential is required for the default run."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    data_mode: Literal["demo", "live"] = Field(default="demo", validation_alias="DATA_MODE")
    database_url: str = Field(
        default="sqlite:///work/radar.db", validation_alias="DATABASE_URL"
    )
    report_output_dir: Path = Field(
        default=Path("outputs/reports"), validation_alias="REPORT_OUTPUT_DIR"
    )
    business_timezone: str = Field(
        default="Asia/Shanghai", validation_alias="BUSINESS_TIMEZONE"
    )
    universe_path: Path = Field(
        default=Path("config/universe.json"), validation_alias="UNIVERSE_PATH"
    )
    radar_view_id: str = Field(default="global", validation_alias="RADAR_VIEW_ID", min_length=1)
    lookback_hours: int = Field(default=48, validation_alias="LOOKBACK_HOURS", ge=1, le=168)

    ai_enabled: bool = Field(default=False, validation_alias="AI_ENABLED")
    daily_llm_budget: float = Field(
        default=0.0, validation_alias="DAILY_LLM_BUDGET", ge=0
    )
    ai_max_requests_per_run: int = Field(
        default=10,
        validation_alias="AI_MAX_REQUESTS_PER_RUN",
        ge=0,
    )
    ai_max_input_tokens_per_run: int | None = Field(
        default=None,
        validation_alias="AI_MAX_INPUT_TOKENS_PER_RUN",
        ge=0,
    )
    ai_max_output_tokens_per_run: int | None = Field(
        default=None,
        validation_alias="AI_MAX_OUTPUT_TOKENS_PER_RUN",
        ge=0,
    )
    localization_enabled: bool = Field(
        default=False, validation_alias="LOCALIZATION_ENABLED"
    )
    localization_locale: Literal["zh-CN"] = Field(
        default="zh-CN", validation_alias="LOCALIZATION_LOCALE"
    )
    localization_model: str | None = Field(
        default=None, validation_alias="LOCALIZATION_MODEL"
    )
    localization_timeout_seconds: float = Field(
        default=90.0,
        validation_alias="LOCALIZATION_TIMEOUT_SECONDS",
        gt=0,
        le=180,
    )
    llm_provider: str = Field(default="disabled", validation_alias="LLM_PROVIDER")
    llm_model: str | None = Field(default=None, validation_alias="LLM_MODEL")
    llm_base_url: str | None = Field(default=None, validation_alias="LLM_BASE_URL")
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "LLM_API_KEY"),
    )
    llm_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="LLM_TIMEOUT_SECONDS",
        gt=0,
    )

    email_mode: Literal["disabled", "file", "smtp"] = Field(
        default="disabled", validation_alias="EMAIL_MODE"
    )
    email_from: str | None = Field(default=None, validation_alias="EMAIL_FROM")
    email_to: str | None = Field(default=None, validation_alias="EMAIL_TO")
    audience_enabled: bool = Field(default=False, validation_alias="AUDIENCE_ENABLED")
    max_active_recipients: int = Field(
        default=20,
        validation_alias="MAX_ACTIVE_RECIPIENTS",
        ge=1,
        le=100,
    )
    operator_email: str | None = Field(default=None, validation_alias="OPERATOR_EMAIL")
    audience_admin_token: SecretStr | None = Field(
        default=None, validation_alias="AUDIENCE_ADMIN_TOKEN"
    )
    admin_port: int = Field(default=8765, validation_alias="ADMIN_PORT", ge=1024, le=65535)
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, validation_alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASSWORD")
    sec_contact_email: str | None = Field(default=None, validation_alias="SEC_CONTACT_EMAIL")

    @field_validator(
        "ai_max_input_tokens_per_run",
        "ai_max_output_tokens_per_run",
        "llm_model",
        "llm_base_url",
        "llm_api_key",
        "operator_email",
        "audience_admin_token",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("business_timezone")
    @classmethod
    def valid_business_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("operator_email")
    @classmethod
    def valid_operator_email(cls, value: str | None) -> str | None:
        return validate_email(value) if value is not None else None

    def resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def resolved_report_output_dir(self) -> Path:
        return self.resolve_path(self.report_output_dir)

    @property
    def resolved_universe_path(self) -> Path:
        return self.resolve_path(self.universe_path)

    @property
    def effective_ai_enabled(self) -> bool:
        return (
            self.ai_enabled
            and self.daily_llm_budget > 0
            and self.ai_max_requests_per_run > 0
            and self.ai_max_input_tokens_per_run != 0
            and self.ai_max_output_tokens_per_run != 0
        )

    @property
    def effective_localization_enabled(self) -> bool:
        return self.localization_enabled and self.effective_ai_enabled


def load_universe(settings: Settings) -> dict[str, Any]:
    with settings.resolved_universe_path.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data
