from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from radar.config import Settings
from radar.pipeline import run_daily

app = typer.Typer(no_args_is_help=True, help="Global Investment Radar command line interface.")


@app.command()
def daily(
    data_mode: Annotated[str | None, typer.Option(help="Override demo or live data mode.")] = None,
    ai: Annotated[
        bool | None, typer.Option("--ai/--no-ai", help="Enable optional AI layer.")
    ] = None,
    llm_provider: Annotated[
        str | None, typer.Option(help="disabled or fake in v0.1; future providers are pluggable.")
    ] = None,
    output_dir: Annotated[Path | None, typer.Option(help="Report output directory.")] = None,
) -> None:
    """Run the daily pipeline from collection through HTML report."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    updates: dict[str, object] = {}
    if data_mode is not None:
        if data_mode not in {"demo", "live"}:
            raise typer.BadParameter("data-mode must be demo or live")
        updates["data_mode"] = data_mode
    if ai is not None:
        updates["ai_enabled"] = ai
        if ai and settings.daily_llm_budget <= 0:
            updates["daily_llm_budget"] = 1.0
    if llm_provider is not None:
        updates["llm_provider"] = llm_provider
    if output_dir is not None:
        updates["report_output_dir"] = output_dir
    if updates:
        settings = settings.model_copy(update=updates)
    result = run_daily(settings)
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def doctor() -> None:
    """Show effective non-secret configuration and whether AI can be called."""
    settings = Settings()
    safe = {
        "app_env": settings.app_env,
        "data_mode": settings.data_mode,
        "business_timezone": settings.business_timezone,
        "database_driver": settings.database_url.split(":", 1)[0],
        "report_output_dir": str(settings.resolved_report_output_dir),
        "ai_enabled": settings.ai_enabled,
        "effective_ai_enabled": settings.effective_ai_enabled,
        "daily_llm_budget": settings.daily_llm_budget,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "email_mode": settings.email_mode,
    }
    typer.echo(json.dumps(safe, indent=2))


if __name__ == "__main__":
    app()
