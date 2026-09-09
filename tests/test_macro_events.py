from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from radar.domain import ClaimType, MacroObservation, SourceKind, stable_id
from radar.storage import (
    RadarStore,
    claims_table,
    events_table,
    evidence_table,
    macro_table,
)

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


def macro_observation(
    period: str,
    *,
    series_id: str = "CUUR0000SA0",
    name: str = "U.S. CPI All Urban Consumers",
    value: float = 324.5,
    previous_value: float | None = 324.0,
    change: float | None = 0.5,
    unit: str = "index",
) -> MacroObservation:
    return MacroObservation(
        id=stable_id("macro", f"BLS|{series_id}|{period}"),
        series_id=series_id,
        name=name,
        period=period,
        observed_at=NOW,
        value=value,
        previous_value=previous_value,
        change=change,
        change_label="vs previous month" if change is not None else None,
        unit=unit,
        provider="bls-public-api",
    )


def test_macro_bootstrap_saves_observation_without_event(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'bootstrap.db').as_posix()}")
    store.initialize()

    events, claims, evidence = store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-08")], NOW
    )

    assert events == claims == evidence == []
    assert store.table_count(macro_table) == 1
    assert store.table_count(events_table) == 0


def test_new_macro_period_creates_exactly_one_stable_derived_event(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'new-period.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-07")], NOW
    )
    current = macro_observation("2026-08")

    first = store.save_macro_with_first_observed_events("run-2", [current], NOW)
    repeated = store.save_macro_with_first_observed_events("run-3", [current], NOW)

    events, claims, evidence = first
    assert len(events) == len(claims) == len(evidence) == 1
    assert repeated == ([], [], [])
    assert store.table_count(events_table) == 1
    assert events[0].event_key == "macro:CUUR0000SA0:2026-08"
    assert claims[0].claim_type is ClaimType.DERIVED
    assert claims[0].claim_type is not ClaimType.INTERPRETATION
    assert evidence[0].source_kind is SourceKind.MACRO_OBSERVATION
    assert evidence[0].source_id == current.id
    assert evidence[0].source_document_id is None
    assert "first observed by Radar" in claims[0].text
    assert "official release" not in claims[0].text.casefold()

    other_store = RadarStore(f"sqlite:///{(tmp_path / 'stable.db').as_posix()}")
    other_store.initialize()
    other_store.save_macro_with_first_observed_events(
        "run-a", [macro_observation("2026-07")], NOW
    )
    other_event = other_store.save_macro_with_first_observed_events(
        "run-b", [current], NOW
    )[0][0]
    assert other_event.id == events[0].id


def test_macro_change_units_are_semantically_correct(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'units.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1",
        [
            macro_observation("2026-07"),
            macro_observation(
                "2026-07",
                series_id="LNS14000000",
                name="U.S. Unemployment Rate",
                value=4.1,
                previous_value=4.0,
                change=0.1,
                unit="%",
            ),
        ],
        NOW,
    )

    _, _, cpi_evidence = store.save_macro_with_first_observed_events(
        "run-2", [macro_observation("2026-08")], NOW
    )
    _, _, unemployment_evidence = store.save_macro_with_first_observed_events(
        "run-2",
        [
            macro_observation(
                "2026-08",
                series_id="LNS14000000",
                name="U.S. Unemployment Rate",
                value=4.2,
                previous_value=4.1,
                change=0.1,
                unit="%",
            )
        ],
        NOW,
    )

    assert "index points" in cpi_evidence[0].excerpt
    assert "+0.5%" not in cpi_evidence[0].excerpt
    assert "percentage points" in unemployment_evidence[0].excerpt


def test_macro_event_failure_does_not_consume_first_observed_state(
    tmp_path, monkeypatch
) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}")
    store.initialize()
    store.save_macro_with_first_observed_events(
        "run-1", [macro_observation("2026-07")], NOW
    )
    current = macro_observation("2026-08")
    original = store._save_macro_event_bundle

    def fail_bundle(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        raise RuntimeError("simulated event persistence failure")

    monkeypatch.setattr(store, "_save_macro_event_bundle", fail_bundle)
    with pytest.raises(RuntimeError, match="simulated event persistence failure"):
        store.save_macro_with_first_observed_events("run-2", [current], NOW)

    assert store.table_count(macro_table) == 1
    monkeypatch.setattr(store, "_save_macro_event_bundle", original)
    events, _, _ = store.save_macro_with_first_observed_events("run-2", [current], NOW)
    assert len(events) == 1
    assert store.table_count(macro_table) == 2
    assert store.table_count(evidence_table) == 1
    assert store.table_count(claims_table) == 1
    assert store.table_count(events_table) == 1
