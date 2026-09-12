from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar.briefing import DailyIntelligenceBrief
from radar.config import PROJECT_ROOT
from radar.domain import content_hash


class HtmlReportRenderer:
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


def report_context(
    *,
    briefing: DailyIntelligenceBrief,
    is_demo: bool,
    ai_test_data: bool,
    data_mode: str,
) -> dict[str, Any]:
    return {
        "briefing": briefing,
        "is_demo": is_demo,
        "ai_test_data": ai_test_data,
        "data_mode": data_mode,
    }
