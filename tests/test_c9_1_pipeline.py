from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from test_localization import _translated_payload

from radar.config import Settings
from radar.llm import FakeLLMProvider, LLMProviderError, LLMResponse
from radar.localization import (
    C91_LOCALE,
    C91_RULE_VERSION,
    LocalizationResult,
    localize_briefing,
)
from radar.pipeline import run_daily
from radar.storage import RadarStore, runs


def test_localization_settings_are_explicit_and_disabled_by_default() -> None:
    settings = Settings()

    assert settings.localization_enabled is False
    assert settings.localization_locale == "zh-CN"
    assert settings.localization_model is None
    assert settings.localization_timeout_seconds == 90


def test_localization_timeout_is_bounded_and_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, LOCALIZATION_TIMEOUT_SECONDS=0)
    with pytest.raises(ValueError):
        Settings(_env_file=None, LOCALIZATION_TIMEOUT_SECONDS=181)


def _success_result(
    briefing, requested_model="localization-model", returned_model="localization-returned"
):
    response = LLMResponse(
        content=json.dumps(_translated_payload(briefing), ensure_ascii=False),
        provider="fake",
        requested_model=requested_model,
        returned_model=returned_model,
        latency_seconds=0,
    )
    return localize_briefing(
        briefing,
        FakeLLMProvider(response=response),
        enabled=True,
        requested_model=requested_model,
    )


def test_pipeline_persists_localization_and_reuses_it_on_retry(tmp_path, monkeypatch) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c91-pipeline.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        ai_max_requests_per_run=10,
        llm_provider="fake",
        llm_model="analysis-model",
        localization_enabled=True,
        localization_model="localization-model",
        email_mode="disabled",
    )
    captured: list[object] = []

    def fake_localize(briefing, provider, **kwargs):
        _ = provider, kwargs
        result = _success_result(briefing)
        captured.append(result)
        return result

    def capture_render(_renderer, context):
        assert context["localized"] is not None
        return "<html><body>bilingual</body></html>"

    monkeypatch.setattr("radar.pipeline.localize_briefing", fake_localize)
    monkeypatch.setattr("radar.report.HtmlReportRenderer.render", capture_render)
    first = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    assert len(captured) == 1

    store = RadarStore(settings.database_url)
    briefing = store.load_daily_briefing(first.run_id, "global", "c9-v1")
    assert briefing is not None
    stored = store.load_localization_snapshot(
        briefing.id, C91_LOCALE, C91_RULE_VERSION, "localization-model"
    )
    assert stored is not None

    def fail_localize(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("persisted localization must be reused")

    monkeypatch.setattr("radar.pipeline.localize_briefing", fail_localize)
    run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))


def test_pipeline_keeps_english_when_localization_provider_fails(tmp_path, monkeypatch) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c91-fallback.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=False,
        localization_enabled=True,
        email_mode="disabled",
    )
    captured: dict[str, object] = {}

    def fake_localize(briefing, provider, **kwargs):
        _ = briefing, provider, kwargs
        return LocalizationResult(
            content=None,
            response=None,
            diagnostics=localize_briefing(
                briefing,
                None,
                enabled=True,
                requested_model=None,
            ).diagnostics,
        )

    def capture_render(_renderer, context):
        captured.update(context)
        return "<html><body>english</body></html>"

    monkeypatch.setattr("radar.pipeline.localize_briefing", fake_localize)
    monkeypatch.setattr("radar.report.HtmlReportRenderer.render", capture_render)
    run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert captured["localized"] is None


def test_localization_uses_explicit_model_when_analysis_model_differs(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c91-model.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        llm_provider="fake",
        llm_model="analysis-model",
        localization_enabled=True,
        localization_model="deepseek-v4-flash",
        localization_timeout_seconds=91,
        email_mode="disabled",
    )
    observed_configs: list[tuple[str | None, float]] = []

    def fake_build(config):
        observed_configs.append((config.llm_model, config.llm_timeout_seconds))
        provider = FakeLLMProvider()
        provider.model = config.llm_model
        return provider

    def check_localization_provider(briefing, provider, **kwargs):
        _ = kwargs
        assert provider.model == "deepseek-v4-flash"
        return _success_result(briefing, "deepseek-v4-flash", "deepseek-flash")

    monkeypatch.setattr("radar.pipeline.build_generation_provider", fake_build)
    monkeypatch.setattr("radar.pipeline.localize_briefing", check_localization_provider)

    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))
    assert ("deepseek-v4-flash", 91) in observed_configs
    store = RadarStore(settings.database_url)
    briefing = store.load_daily_briefing(result.run_id, "global", "c9-v1")
    assert briefing is not None
    snapshot = store.load_localization_snapshot(
        briefing.id, "zh-CN", C91_RULE_VERSION, "deepseek-v4-flash"
    )
    assert snapshot is not None
    assert snapshot.requested_model == "deepseek-v4-flash"
    assert snapshot.returned_model == "deepseek-flash"
    with store.engine.connect() as connection:
        details = json.loads(connection.execute(select(runs.c.details_json)).scalar_one())
    assert details["c91"]["requested_model"] == "deepseek-v4-flash"
    assert details["c91"]["returned_model"] == "deepseek-flash"


@pytest.mark.parametrize("failure_kind", ["provider", "json", "validation"])
def test_enabled_localization_failure_emits_english_product_and_safe_diagnostics(
    tmp_path, monkeypatch, failure_kind: str
) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / f'c91-{failure_kind}.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        ai_enabled=True,
        daily_llm_budget=1,
        llm_provider="fake",
        llm_model="analysis-model",
        localization_enabled=True,
        localization_model="deepseek-v4-flash",
        email_mode="disabled",
    )
    delivered: dict[str, str] = {}

    class CaptureEmail:
        def send(self, *, subject: str, html: str) -> str:
            delivered.update({"subject": subject, "html": html})
            return "captured"

    def failing_localization(briefing, provider, **kwargs):
        _ = provider, kwargs
        if failure_kind == "provider":
            failing_provider = FakeLLMProvider(error=LLMProviderError("private provider detail"))
        elif failure_kind == "json":
            content = "not JSON"
        else:
            payload = _translated_payload(briefing)
            payload["primary_stories"][0]["headline"] = "2027 fabricated result"
            content = json.dumps(payload, ensure_ascii=False)
        if failure_kind != "provider":
            response = LLMResponse(
                content=content,
                provider="fake",
                requested_model="deepseek-v4-flash",
                returned_model="deepseek-flash",
                latency_seconds=0,
            )
            failing_provider = FakeLLMProvider(response=response)
        return localize_briefing(
            briefing,
            failing_provider,
            enabled=True,
            requested_model="deepseek-v4-flash",
        )

    monkeypatch.setattr("radar.pipeline.localize_briefing", failing_localization)
    monkeypatch.setattr("radar.pipeline.build_email_sender", lambda _settings: CaptureEmail())
    result = run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert delivered["subject"] == "Global Investment Radar — 2026-09-07"
    assert '<html lang="en">' in delivered["html"]
    assert "A focused global intelligence brief" in delivered["html"]
    assert "Things That Matter" in delivered["html"]
    assert "中文本地化简报" not in delivered["html"]
    store = RadarStore(settings.database_url)
    with store.engine.connect() as connection:
        details = json.loads(connection.execute(select(runs.c.details_json)).scalar_one())
    assert details["c91"]["status"] == "failed"
    assert details["c91"]["request_count"] == 1
    assert "2027 fabricated result" not in json.dumps(details)
    assert "private provider detail" not in json.dumps(details)
    assert result.report_path


def test_disabled_localization_skips_call_and_preserves_english_subject(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(
        app_env="test",
        data_mode="demo",
        database_url=f"sqlite:///{(tmp_path / 'c91-disabled.db').as_posix()}",
        report_output_dir=tmp_path / "reports",
        localization_enabled=False,
        email_mode="disabled",
    )
    delivered: dict[str, str] = {}

    class CaptureEmail:
        def send(self, *, subject: str, html: str) -> str:
            delivered.update({"subject": subject, "html": html})
            return "captured"

    def forbidden_call(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("localization call is forbidden when disabled")

    monkeypatch.setattr("radar.pipeline.localize_briefing", forbidden_call)
    monkeypatch.setattr("radar.pipeline.build_email_sender", lambda _settings: CaptureEmail())
    run_daily(settings, now=datetime(2026, 9, 7, 9, tzinfo=UTC))

    assert delivered["subject"] == "Global Investment Radar — 2026-09-07"
    assert '<html lang="en">' in delivered["html"]
    assert "中文本地化简报" not in delivered["html"]
