from __future__ import annotations

from radar.config import Settings
from radar.llm import DeepSeekProvider, LLMError, LLMRequest, LLMResponse


def run_smoke(settings: Settings | None = None) -> LLMResponse:
    """Make one explicit, minimal DeepSeek request."""
    provider = DeepSeekProvider.from_settings(settings or Settings())
    return provider.generate(
        LLMRequest(
            prompt="Reply with exactly: OK",
            max_output_tokens=16,
        )
    )


def main() -> None:
    try:
        response = run_smoke()
    except LLMError as exc:
        raise SystemExit(f"DeepSeek smoke test failed: {exc}") from exc

    print(f"provider={response.provider}")
    print(f"requested_model={response.requested_model}")
    print(f"returned_model={response.returned_model or 'not-reported'}")
    print(f"latency_seconds={response.latency_seconds:.3f}")
    print(f"content={response.content}")


if __name__ == "__main__":
    main()
