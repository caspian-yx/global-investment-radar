from __future__ import annotations

from radar.config import Settings
from radar.llm import DisabledLLMProvider, FakeLLMProvider, build_llm_provider


def test_zero_budget_forces_disabled_provider() -> None:
    settings = Settings(ai_enabled=True, daily_llm_budget=0, llm_provider="future-provider")
    assert isinstance(build_llm_provider(settings), DisabledLLMProvider)


def test_fake_provider_requires_non_production() -> None:
    dev = Settings(app_env="development", ai_enabled=True, daily_llm_budget=1, llm_provider="fake")
    prod = Settings(app_env="production", ai_enabled=True, daily_llm_budget=1, llm_provider="fake")
    assert isinstance(build_llm_provider(dev), FakeLLMProvider)
    assert isinstance(build_llm_provider(prod), DisabledLLMProvider)
