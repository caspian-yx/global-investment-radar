from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, cast

import typer

from radar.admin import run_admin
from radar.audience import DeliveryMode
from radar.briefing import DailyIntelligenceBrief
from radar.config import Settings
from radar.delivery import AudienceService, AudienceValidationError
from radar.email import RecipientEmailSender, build_email_sender
from radar.pipeline import run_daily
from radar.storage import RadarStore, c9_daily_briefings_table

app = typer.Typer(no_args_is_help=True, help="Global Investment Radar command line interface.")
recipients_app = typer.Typer(no_args_is_help=True)
editorial_app = typer.Typer(no_args_is_help=True)
app.add_typer(recipients_app, name="recipients")
app.add_typer(editorial_app, name="editorial")


def _service() -> AudienceService:
    settings = Settings()
    store = RadarStore(settings.database_url)
    store.initialize()
    return AudienceService(store, settings)


def _latest_briefing(service: AudienceService, run_id: str | None) -> DailyIntelligenceBrief | None:
    if run_id:
        return service.store.load_daily_briefing(run_id, service.settings.radar_view_id, "c9-v1")
    with service.store.engine.connect() as connection:
        row = (
            connection.execute(
                c9_daily_briefings_table.select()
                .where(c9_daily_briefings_table.c.view_id == service.settings.radar_view_id)
                .order_by(c9_daily_briefings_table.c.composed_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return service.store.load_daily_briefing(
        str(row["run_id"]), str(row["view_id"]), str(row["rule_version"])
    )


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
    audience: Annotated[
        bool, typer.Option("--audience", help="Explicitly run gated audience fan-out.")
    ] = False,
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
    result = run_daily(settings, audience=audience)
    typer.echo(result.model_dump_json(indent=2))


@recipients_app.command("list")
def list_recipients() -> None:
    """List recipient status without personal notes."""
    for item in _service().list_recipients():
        typer.echo(
            f"{item.id} {item.display_name} {item.email} "
            f"{item.delivery_mode.value} enabled={item.enabled}"
        )


@recipients_app.command("add")
def add_recipient(
    display_name: str,
    email: str,
    mode: Annotated[str, typer.Option("--mode")] = "english",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    try:
        delivery_mode = DeliveryMode(mode)
        item = _service().add_recipient(display_name, email, delivery_mode, note)
    except (ValueError, AudienceValidationError) as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(item.id)


@recipients_app.command("edit")
def edit_recipient(
    recipient_id: str,
    display_name: str,
    email: str,
    mode: Annotated[str, typer.Option("--mode")] = "english",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    try:
        item = _service().edit_recipient(
            recipient_id,
            display_name=display_name,
            email=email,
            delivery_mode=DeliveryMode(mode),
            personal_note=note,
        )
    except (ValueError, AudienceValidationError) as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(item.id)


def _set_recipient_enabled(recipient_id: str, enabled: bool) -> None:
    try:
        item = _service().set_enabled(recipient_id, enabled)
    except AudienceValidationError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"{item.id} enabled={item.enabled}")


@recipients_app.command("enable")
def enable_recipient(recipient_id: str) -> None:
    _set_recipient_enabled(recipient_id, True)


@recipients_app.command("disable")
def disable_recipient(recipient_id: str) -> None:
    _set_recipient_enabled(recipient_id, False)


@recipients_app.command("remove")
def remove_recipient(recipient_id: str) -> None:
    try:
        item = _service().archive_recipient(recipient_id)
    except AudienceValidationError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"{item.id} archived=true")


def _briefing_or_error(service: AudienceService, run_id: str | None) -> DailyIntelligenceBrief:
    briefing = _latest_briefing(service, run_id)
    if briefing is None:
        raise typer.BadParameter("no canonical C9 briefing is available")
    return briefing


@editorial_app.command("save")
def save_editorial_note(
    message: str,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    service = _service()
    briefing = _briefing_or_error(service, run_id)
    service.save_editorial_note(briefing, message)
    typer.echo("editorial note saved")


@editorial_app.command("clear")
def clear_editorial_note(run_id: Annotated[str | None, typer.Option("--run-id")] = None) -> None:
    service = _service()
    briefing = _briefing_or_error(service, run_id)
    service.save_editorial_note(briefing, "")
    typer.echo("editorial note cleared")


@app.command()
def preview(
    recipient: Annotated[str, typer.Option("--recipient")],
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    service = _service()
    briefing = _briefing_or_error(service, run_id)
    rendered = service.preview(briefing, recipient)
    typer.echo(rendered.html)


@app.command("send-test")
def send_test(
    recipient: Annotated[str, typer.Option("--recipient")],
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    service = _service()
    briefing = _briefing_or_error(service, run_id)
    record = service.send_test(
        briefing,
        recipient,
        cast(RecipientEmailSender, build_email_sender(service.settings)),
    )
    if record is None:
        raise typer.Exit(code=1)
    typer.echo(record.model_dump_json())


@app.command()
def admin() -> None:
    """Run the loopback-only operator admin server."""
    run_admin(Settings())


@app.command()
def doctor() -> None:
    """Show effective non-secret configuration and whether AI can be called."""
    settings = Settings()
    safe = {
        "app_env": settings.app_env,
        "data_mode": settings.data_mode,
        "business_timezone": settings.business_timezone,
        "radar_view_id": settings.radar_view_id,
        "database_driver": settings.database_url.split(":", 1)[0],
        "report_output_dir": str(settings.resolved_report_output_dir),
        "ai_enabled": settings.ai_enabled,
        "effective_ai_enabled": settings.effective_ai_enabled,
        "daily_llm_budget": settings.daily_llm_budget,
        "ai_max_requests_per_run": settings.ai_max_requests_per_run,
        "ai_max_input_tokens_per_run": settings.ai_max_input_tokens_per_run,
        "ai_max_output_tokens_per_run": settings.ai_max_output_tokens_per_run,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "localization_enabled": settings.localization_enabled,
        "localization_locale": settings.localization_locale,
        "localization_model": settings.localization_model,
        "localization_timeout_seconds": settings.localization_timeout_seconds,
        "email_mode": settings.email_mode,
        "audience_enabled": settings.audience_enabled,
        "max_active_recipients": settings.max_active_recipients,
    }
    typer.echo(json.dumps(safe, indent=2))


if __name__ == "__main__":
    app()
