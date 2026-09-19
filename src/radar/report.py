from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar.audience import DeliveryMode, RecipientProfile
from radar.briefing import DailyIntelligenceBrief
from radar.config import PROJECT_ROOT
from radar.domain import content_hash
from radar.localization import C91_LABELS, LocalizedBriefingContent
from radar.web_intelligence import build_web_intelligence


@dataclass(frozen=True)
class RecipientComposition:
    delivery_mode: DeliveryMode
    recipient: RecipientProfile | None = None
    editorial_note: str | None = None
    personal_note: str | None = None
    is_test: bool = False


class EmailHtmlReportRenderer:
    def __init__(self) -> None:
        self.environment = Environment(
            loader=FileSystemLoader(PROJECT_ROOT / "templates"),
            autoescape=select_autoescape(
                enabled_extensions=("html", "xml", "j2"),
                default_for_string=True,
            ),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, context: dict[str, Any]) -> str:
        template = self.environment.get_template("daily_email.html.j2")
        return template.render(**context)

    def write(self, html: str, output_dir: Path, report_date: str) -> tuple[Path, str]:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"global-investment-radar-{report_date}.html"
        path.write_text(html, encoding="utf-8")
        return path, content_hash(html)


class WebHtmlReportRenderer:
    def __init__(self) -> None:
        self.environment = Environment(
            loader=FileSystemLoader(PROJECT_ROOT / "templates"),
            autoescape=select_autoescape(
                enabled_extensions=("html", "xml", "j2"),
                default_for_string=True,
            ),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, context: dict[str, Any]) -> str:
        template = self.environment.get_template("daily_web.html.j2")
        return template.render(**context)

    def write(self, html: str, output_dir: Path, report_date: str) -> tuple[Path, str]:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"global-investment-radar-{report_date}.html"
        path.write_text(html, encoding="utf-8")
        return path, content_hash(html)


class HtmlReportRenderer(WebHtmlReportRenderer):
    """Compatibility name for the saved daily Web Report renderer."""


def report_context(
    *,
    briefing: DailyIntelligenceBrief,
    is_demo: bool,
    ai_test_data: bool,
    data_mode: str,
    localized: LocalizedBriefingContent | None = None,
    composition: RecipientComposition | None = None,
    markets: tuple[Any, ...] = (),
    assets: tuple[dict[str, Any], ...] = (),
    china_sectors: tuple[Any, ...] = (),
) -> dict[str, Any]:
    localized_market_names = (
        {item.key: item.text for item in localized.market_names} if localized is not None else {}
    )
    localized_primary = localized is not None and (
        composition is None or composition.delivery_mode is not DeliveryMode.ENGLISH
    )
    include_english_reference = localized_primary and (
        composition is None or composition.delivery_mode is DeliveryMode.BILINGUAL
    )
    note_parts = tuple(
        part.strip()
        for part in (
            composition.editorial_note if composition is not None else None,
            composition.personal_note if composition is not None else None,
        )
        if part is not None and part.strip()
    )
    web_report = build_web_intelligence(
        briefing,
        markets=markets,
        assets=assets,
        china_sectors=china_sectors,
    )
    return {
        "briefing": briefing,
        "is_demo": is_demo,
        "ai_test_data": ai_test_data,
        "data_mode": data_mode,
        "localized": localized,
        "localized_market_names": localized_market_names,
        "localization_labels": C91_LABELS,
        "localized_primary": localized_primary,
        "include_english_reference": include_english_reference,
        "render_english_body": not localized_primary or include_english_reference,
        "note_text": "\n\n".join(note_parts),
        "web_report": web_report,
    }


def render_recipient_email(
    briefing: DailyIntelligenceBrief,
    localized: LocalizedBriefingContent | None,
    composition: RecipientComposition,
) -> tuple[str, str, Literal["en", "zh-CN"]]:
    localized_primary = (
        localized is not None and composition.delivery_mode is not DeliveryMode.ENGLISH
    )
    rendered_locale: Literal["en", "zh-CN"] = "zh-CN" if localized_primary else "en"
    subject = (
        f"Global Investment Radar — {briefing.report_date} 全球投资晨报"
        if localized_primary
        else f"Global Investment Radar — {briefing.report_date}"
    )
    if composition.is_test:
        subject = f"[TEST] {subject}"
    context = report_context(
        briefing=briefing,
        is_demo=False,
        ai_test_data=False,
        data_mode="live",
        localized=localized,
        composition=composition,
    )
    return subject, EmailHtmlReportRenderer().render(context), rendered_locale
