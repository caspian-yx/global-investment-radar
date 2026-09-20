from __future__ import annotations

from datetime import UTC, datetime

from radar.calendar import (
    ICSCalendarConfig,
    ICSCalendarProvider,
    build_calendar_providers,
    select_future_catalysts,
)
from radar.config import Settings, load_universe
from radar.domain import ProviderStatus

NOW = datetime(2026, 9, 20, 0, tzinfo=UTC)


def _provider() -> ICSCalendarProvider:
    return ICSCalendarProvider(
        ICSCalendarConfig(
            provider_id="bls-release-calendar",
            provider_name="U.S. Bureau of Labor Statistics release calendar",
            url="https://www.bls.gov/schedule/news_release/bls.ics",
            region="US",
            category="macro_release",
            lookahead_days=45,
            max_items=30,
        )
    )


def test_calendar_excludes_past_converts_timezone_and_deduplicates_reschedule() -> None:
    raw = """BEGIN:VCALENDAR
BEGIN:VEVENT
SEQUENCE:1
UID:employment-2026-10
DTSTART;TZID=US-Eastern:20261002T083000
SUMMARY:Employment Situation
CATEGORIES:IMPORTANT, BLS
END:VEVENT
BEGIN:VEVENT
SEQUENCE:2
UID:employment-2026-10
DTSTART;TZID=US-Eastern:20261003T083000
SUMMARY:Employment Situation
CATEGORIES:IMPORTANT, BLS
END:VEVENT
BEGIN:VEVENT
SEQUENCE:1
UID:cpi-2026-09
DTSTART;TZID=US-Eastern:20260911T083000
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
SEQUENCE:1
UID:ppi-2026-10
DTSTART;TZID=US-Eastern:20261014T083000
SUMMARY:Producer Price Index
END:VEVENT
END:VCALENDAR"""

    batch = _provider()._parse(raw, NOW)

    assert batch.status is ProviderStatus.OK
    assert [item.title for item in batch.events] == [
        "Employment Situation",
        "Producer Price Index",
    ]
    assert batch.events[0].scheduled_at == datetime(2026, 10, 3, 12, 30, tzinfo=UTC)
    assert batch.events[0].sequence == 2
    assert batch.diagnostics == {
        "events_seen": 4,
        "events_accepted": 2,
        "events_past": 1,
        "events_outside_window": 0,
        "events_malformed": 0,
        "events_duplicate_or_rescheduled": 1,
        "events_limited": 0,
    }


def test_calendar_malformed_and_no_data_are_fail_closed() -> None:
    raw = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:broken
SUMMARY:Missing date
END:VEVENT
END:VCALENDAR"""

    batch = _provider()._parse(raw, NOW)

    assert batch.status is ProviderStatus.NO_DATA
    assert batch.events == ()
    assert batch.diagnostics["events_malformed"] == 1


def test_future_catalyst_selection_rejects_generic_monitor_filler() -> None:
    raw = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:generic
DTSTART:20260921T080000Z
SUMMARY:Continue monitoring market developments
END:VEVENT
BEGIN:VEVENT
UID:cpi
DTSTART:20260922T123000Z
SUMMARY:Consumer Price Index
END:VEVENT
END:VCALENDAR"""
    events = _provider()._parse(raw, NOW).events

    selected = select_future_catalysts(events, as_of=NOW)

    assert [item.title for item in selected] == ["Consumer Price Index"]


def test_production_config_registers_official_bls_calendar() -> None:
    settings = Settings(data_mode="live")

    providers = build_calendar_providers(settings, load_universe(settings))

    assert [provider.name for provider in providers] == ["bls-release-calendar"]
    assert providers[0].config.provider_name == ("U.S. Bureau of Labor Statistics release calendar")
