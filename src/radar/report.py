from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar.config import PROJECT_ROOT
from radar.domain import StructuredExplanation, content_hash


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
    structured_explanations: Mapping[str, StructuredExplanation | None] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC),
        "structured_explanations": dict(structured_explanations or {}),
        "c7_scores": {},
        "market_impacts": {},
        "active_view": "Global",
        **kwargs,
    }
