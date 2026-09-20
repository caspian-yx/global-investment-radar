from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from radar.config import Settings
from radar.domain import ProviderStatus, stable_id


class _CalendarModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ICSCalendarConfig(_CalendarModel):
    provider_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    region: str = Field(min_length=1)
    category: str = Field(min_length=1)
    lookahead_days: int = Field(default=45, ge=1, le=366)
    max_items: int = Field(default=30, ge=1, le=200)
    max_response_bytes: int = Field(default=2_000_000, ge=1, le=10_000_000)


class ScheduledCatalyst(_CalendarModel):
    id: str
    provider_id: str
    provider_name: str
    external_id: str
    title: str
    scheduled_at: datetime
    source_timezone: str
    region: str
    category: str
    importance: str
    source_url: str
    sequence: int = 0

    @field_validator("scheduled_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")
        return value.astimezone(UTC)


class CalendarBatch(_CalendarModel):
    provider_id: str
    status: ProviderStatus
    events: tuple[ScheduledCatalyst, ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics: dict[str, int]
    error_type: str | None = None


_GENERIC_MONITOR_PHRASES = (
    "continue monitoring",
    "monitor for updates",
    "watch for updates",
    "继续关注",
    "持续关注",
)
_TIMEZONE_ALIASES = {
    "US-Eastern": "America/New_York",
    "Eastern Standard Time": "America/New_York",
}


def _unfold_ics(raw: str) -> list[str]:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _event_blocks(raw: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(raw):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key] = value.strip()
    return blocks


def _parse_dtstart(block: dict[str, str]) -> tuple[datetime, str]:
    entry = next(((key, value) for key, value in block.items() if key.startswith("DTSTART")), None)
    if entry is None:
        raise ValueError("missing DTSTART")
    key, value = entry
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), "UTC"
    timezone = "UTC"
    if ";TZID=" in key:
        timezone = key.split(";TZID=", 1)[1]
    timezone = _TIMEZONE_ALIASES.get(timezone, timezone)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown calendar timezone") from exc
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
    return parsed.astimezone(UTC), timezone


class ICSCalendarProvider:
    def __init__(self, config: ICSCalendarConfig, timeout: float = 20.0) -> None:
        self.config = config
        self.timeout = timeout
        self.name = config.provider_id

    def collect(self, now: datetime) -> CalendarBatch:
        headers = {"User-Agent": "GlobalInvestmentRadar/0.1 (public-research-pipeline)"}
        try:
            response = httpx.get(
                self.config.url,
                timeout=self.timeout,
                follow_redirects=True,
                headers=headers,
            )
            response.raise_for_status()
            if len(response.content) > self.config.max_response_bytes:
                raise ValueError("calendar response exceeded size limit")
            return self._parse(response.text, now)
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            return CalendarBatch(
                provider_id=self.name,
                status=ProviderStatus.FAILED,
                warnings=(f"Calendar collection failed ({type(exc).__name__}).",),
                diagnostics=self._empty_diagnostics(),
                error_type=type(exc).__name__,
            )

    def _empty_diagnostics(self) -> dict[str, int]:
        return {
            "events_seen": 0,
            "events_accepted": 0,
            "events_past": 0,
            "events_outside_window": 0,
            "events_malformed": 0,
            "events_duplicate_or_rescheduled": 0,
            "events_limited": 0,
        }

    def _parse(self, raw: str, now: datetime) -> CalendarBatch:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)
        diagnostics = self._empty_diagnostics()
        by_external_id: dict[str, ScheduledCatalyst] = {}
        for block in _event_blocks(raw):
            diagnostics["events_seen"] += 1
            try:
                external_id = block["UID"].strip()
                title = block["SUMMARY"].replace("\\,", ",").strip()
                scheduled_at, source_timezone = _parse_dtstart(block)
                sequence = int(block.get("SEQUENCE", "0"))
                if not external_id or not title:
                    raise ValueError("calendar event identity is blank")
            except (KeyError, TypeError, ValueError):
                diagnostics["events_malformed"] += 1
                continue
            if scheduled_at <= now:
                diagnostics["events_past"] += 1
                continue
            if scheduled_at > now + timedelta(days=self.config.lookahead_days):
                diagnostics["events_outside_window"] += 1
                continue
            categories = block.get("CATEGORIES", "").casefold()
            event = ScheduledCatalyst(
                id=stable_id("calendar", f"{self.name}|{external_id}"),
                provider_id=self.name,
                provider_name=self.config.provider_name,
                external_id=external_id,
                title=title,
                scheduled_at=scheduled_at,
                source_timezone=source_timezone,
                region=self.config.region,
                category=self.config.category,
                importance="high" if "important" in categories else "standard",
                source_url=self.config.url,
                sequence=sequence,
            )
            current = by_external_id.get(external_id)
            if current is not None:
                diagnostics["events_duplicate_or_rescheduled"] += 1
                if (event.sequence, event.scheduled_at) <= (
                    current.sequence,
                    current.scheduled_at,
                ):
                    continue
            by_external_id[external_id] = event
        ordered = sorted(by_external_id.values(), key=lambda item: (item.scheduled_at, item.id))
        diagnostics["events_limited"] = max(0, len(ordered) - self.config.max_items)
        events = tuple(ordered[: self.config.max_items])
        diagnostics["events_accepted"] = len(events)
        return CalendarBatch(
            provider_id=self.name,
            status=ProviderStatus.OK if events else ProviderStatus.NO_DATA,
            events=events,
            diagnostics=diagnostics,
            warnings=() if events else ("Calendar parsed successfully; no future events found.",),
        )


def select_future_catalysts(
    events: tuple[ScheduledCatalyst, ...],
    *,
    as_of: datetime,
    max_items: int = 5,
) -> tuple[ScheduledCatalyst, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    eligible = (
        item
        for item in events
        if item.scheduled_at > as_of
        and not any(phrase in item.title.casefold() for phrase in _GENERIC_MONITOR_PHRASES)
    )
    return tuple(
        sorted(
            eligible,
            key=lambda item: (
                item.scheduled_at,
                item.id,
            ),
        )[:max_items]
    )


def build_calendar_providers(
    settings: Settings, universe: dict[str, object]
) -> list[ICSCalendarProvider]:
    if settings.data_mode != "live":
        return []
    configured = universe.get("calendar_feeds", [])
    if not isinstance(configured, list):
        raise ValueError("calendar_feeds must be a list")
    return [
        ICSCalendarProvider(ICSCalendarConfig.model_validate(item))
        for item in configured
        if isinstance(item, dict) and item.get("enabled", True)
    ]
