from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from radar.domain import (
    DriverCandidate,
    DriverConfidence,
    DriverRelevance,
    DriverTemporalRelation,
    Evidence,
    MacroObservation,
    SourceKind,
)
from radar.explanation import build_macro_backdrop
from radar.storage import (
    DRIVER_CANDIDATE_MIGRATION,
    RadarStore,
    driver_candidates_table,
    evidence_table,
)


def evidence(
    *,
    evidence_id: str = "evidence-1",
    source_kind: SourceKind = SourceKind.DOCUMENT,
    source_id: str = "source-1",
    source_document_id: str | None = "source-1",
) -> Evidence:
    return Evidence(
        id=evidence_id,
        source_kind=source_kind,
        source_id=source_id,
        source_document_id=source_document_id,
        locator={"type": "test"},
        excerpt="auditable excerpt",
        checksum="a" * 64,
        quality_score=1.0,
    )


def create_phase_b_evidence_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE evidence (
                id VARCHAR(36) PRIMARY KEY,
                run_id VARCHAR(36) NOT NULL,
                source_document_id VARCHAR(36) NOT NULL,
                checksum VARCHAR(64) NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        payload = {
            "id": "legacy-evidence",
            "source_document_id": "legacy-document",
            "locator": {"type": "rss_or_provider_record"},
            "excerpt": "legacy excerpt",
            "checksum": "b" * 64,
            "quality_score": 0.8,
            "extraction_method": "deterministic",
        }
        connection.execute(
            text(
                "INSERT INTO evidence "
                "(id, run_id, source_document_id, checksum, payload_json) "
                "VALUES (:id, :run_id, :source_document_id, :checksum, :payload_json)"
            ),
            {
                "id": "legacy-evidence",
                "run_id": "legacy-run",
                "source_document_id": "legacy-document",
                "checksum": "b" * 64,
                "payload_json": json.dumps(payload),
            },
        )
    engine.dispose()


def test_fresh_database_has_latest_evidence_schema(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    store.initialize()

    columns = {column["name"] for column in inspect(store.engine).get_columns("evidence")}
    indexes = inspect(store.engine).get_indexes("evidence")

    assert {"source_kind", "source_id", "source_document_id"} <= columns
    assert any(index["column_names"] == ["source_kind", "source_id"] for index in indexes)


def test_phase_b_evidence_migration_backfills_and_is_idempotent(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'phase-b.db').as_posix()}"
    create_phase_b_evidence_schema(database_url)
    store = RadarStore(database_url)

    store.initialize()
    first = store.load_evidence_for_source(SourceKind.DOCUMENT, "legacy-document")
    store.initialize()
    second = store.load_evidence_for_source(SourceKind.DOCUMENT, "legacy-document")

    assert len(first) == len(second) == 1
    assert first[0].id == "legacy-evidence"
    assert first[0].source_kind is SourceKind.DOCUMENT
    assert first[0].source_id == "legacy-document"
    assert first[0].source_document_id == "legacy-document"
    assert first[0].checksum == "b" * 64
    assert store.table_count(evidence_table) == 1


@pytest.mark.parametrize(
    ("source_kind", "source_id", "legacy_id"),
    [
        (SourceKind.DOCUMENT, "document-1", "document-1"),
        (SourceKind.MARKET_OBSERVATION, "market-1", None),
        (SourceKind.MACRO_OBSERVATION, "macro-1", None),
    ],
)
def test_evidence_round_trips_and_queries_by_generic_provenance(
    tmp_path,
    source_kind: SourceKind,
    source_id: str,
    legacy_id: str | None,
) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / f'{source_kind.value}.db').as_posix()}")
    store.initialize()
    item = evidence(
        source_kind=source_kind,
        source_id=source_id,
        source_document_id=legacy_id,
    )

    store.save_evidence("run-1", [item])
    loaded = store.load_evidence_for_source(source_kind, source_id)

    assert loaded == [item]


@pytest.mark.parametrize(
    "updates",
    [
        {"source_kind": "UNKNOWN"},
        {"source_id": ""},
        {"source_id": "   "},
        {"source_id": None},
    ],
)
def test_evidence_rejects_invalid_generic_provenance(updates: dict[str, object]) -> None:
    payload = evidence().model_dump()
    payload.update(updates)

    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


def test_evidence_rejects_missing_source_id() -> None:
    payload = evidence().model_dump()
    del payload["source_id"]

    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


@pytest.mark.parametrize(
    "updates",
    [
        {
            "source_kind": SourceKind.MARKET_OBSERVATION,
            "source_id": "market-1",
            "source_document_id": "market-1",
        },
        {
            "source_kind": SourceKind.MACRO_OBSERVATION,
            "source_id": "macro-1",
            "source_document_id": "macro-1",
        },
        {
            "source_kind": SourceKind.DOCUMENT,
            "source_id": "document-1",
            "source_document_id": "different-document",
        },
    ],
)
def test_evidence_rejects_semantically_inconsistent_legacy_document_id(
    updates: dict[str, object],
) -> None:
    payload = evidence().model_dump()
    payload.update(updates)

    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


def test_migration_failure_is_unversioned_and_retryable(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration-failure.db').as_posix()}"
    create_phase_b_evidence_schema(database_url)
    store = RadarStore(database_url)
    original = store._backfill_document_evidence

    def fail_during_backfill(connection: object) -> None:
        connection.exec_driver_sql(  # type: ignore[attr-defined]
            "UPDATE evidence SET source_kind = 'DOCUMENT' WHERE id = 'legacy-evidence'"
        )
        raise RuntimeError("simulated migration backfill failure")

    monkeypatch.setattr(store, "_backfill_document_evidence", fail_during_backfill)
    with pytest.raises(RuntimeError, match="simulated migration backfill failure"):
        store.initialize()

    columns = {column["name"] for column in inspect(store.engine).get_columns("evidence")}
    with store.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT source_kind, source_id, checksum FROM evidence "
                "WHERE id = 'legacy-evidence'"
            )
        ).one()
        version_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = '001_c1_evidence_provenance'"
            )
        ).scalar_one()

    assert {"source_kind", "source_id"} <= columns
    assert row == (None, None, "b" * 64)
    assert version_count == 0

    monkeypatch.setattr(store, "_backfill_document_evidence", original)
    store.initialize()
    loaded = store.load_evidence_for_source(SourceKind.DOCUMENT, "legacy-document")
    assert len(loaded) == 1
    assert loaded[0].checksum == "b" * 64


def test_versioned_migration_validates_instead_of_reapplying(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'versioned.db').as_posix()}"
    create_phase_b_evidence_schema(database_url)
    store = RadarStore(database_url)
    store.initialize()

    def unexpected_backfill(connection: object) -> None:
        _ = connection
        raise AssertionError("completed migration must not reapply")

    monkeypatch.setattr(store, "_backfill_document_evidence", unexpected_backfill)
    store.initialize()


def test_version_marker_cannot_hide_partial_schema(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'false-marker.db').as_posix()}"
    create_phase_b_evidence_schema(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migrations "
            "(version VARCHAR(80) PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"
        )
        connection.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES ('001_c1_evidence_provenance', 'test')"
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="incomplete"):
        RadarStore(database_url).initialize()


def test_evidence_migration_leaves_unrelated_table_rows_unchanged(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'unrelated.db').as_posix()}"
    create_phase_b_evidence_schema(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE unrelated_sentinel (id VARCHAR(20) PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            text("INSERT INTO unrelated_sentinel (id, value) VALUES ('one', 'preserve me')")
        )
    engine.dispose()

    store = RadarStore(database_url)
    store.initialize()
    with store.engine.connect() as connection:
        rows = connection.execute(text("SELECT id, value FROM unrelated_sentinel")).all()

    assert rows == [("one", "preserve me")]


def driver_candidate(
    *, candidate_id: str = "driver-1", target_event_id: str = "target-1"
) -> DriverCandidate:
    return DriverCandidate(
        id=candidate_id,
        target_event_id=target_event_id,
        candidate_event_id="source-event-1",
        interpretation_claim_id="interpretation-claim-1",
        evidence_ids=["candidate-evidence-1", "target-evidence-1"],
        target_evidence_ids=["target-evidence-1"],
        candidate_evidence_ids=["candidate-evidence-1"],
        temporal_relation=DriverTemporalRelation.PRIOR,
        relevance_relation=DriverRelevance.EXACT_ENTITY,
        confidence=DriverConfidence.MEDIUM,
        confidence_components={
            "temporal": "PRIOR",
            "relevance": "EXACT_ENTITY",
            "source_quality": "A_B",
            "independent_publishers": "1",
            "conflict": "NO",
        },
        confidence_reasons=["A single publisher caps confidence at MEDIUM."],
        generator="deterministic-driver-candidate",
        rule_version="c2.1-driver-v1",
        as_of="2026-09-09T08:00:00Z",
    )


def test_fresh_database_has_versioned_driver_candidate_schema(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'fresh-c2-1.db').as_posix()}")
    store.initialize()

    inspector = inspect(store.engine)
    columns = {column["name"] for column in inspector.get_columns("driver_candidates")}
    indexes = inspector.get_indexes("driver_candidates")
    with store.engine.connect() as connection:
        marker = connection.execute(
            text("SELECT version FROM schema_migrations WHERE version = :version"),
            {"version": DRIVER_CANDIDATE_MIGRATION},
        ).scalar_one()

    assert {
        "id",
        "run_id",
        "target_event_id",
        "candidate_event_id",
        "interpretation_claim_id",
        "confidence",
        "payload_json",
    } <= columns
    assert any(index["column_names"] == ["target_event_id"] for index in indexes)
    assert marker == DRIVER_CANDIDATE_MIGRATION


def test_exact_c1_database_upgrades_additively_to_driver_schema(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'c1-upgrade.db').as_posix()}"
    create_phase_b_evidence_schema(database_url)
    c1_store = RadarStore(database_url)
    c1_store._migrate_phase_b_to_c1()
    with c1_store.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE c1_sentinel (id VARCHAR(20) PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            text("INSERT INTO c1_sentinel (id, value) VALUES ('one', 'preserve me')")
        )
    assert not inspect(c1_store.engine).has_table("driver_candidates")

    upgraded = RadarStore(database_url)
    upgraded.initialize()

    with upgraded.engine.connect() as connection:
        sentinel = connection.execute(text("SELECT id, value FROM c1_sentinel")).all()
        versions = set(
            connection.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
    assert sentinel == [("one", "preserve me")]
    assert versions == {"001_c1_evidence_provenance", DRIVER_CANDIDATE_MIGRATION}


def test_driver_migration_recovers_table_and_index_without_losing_rows(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'partial-c2-1.db').as_posix()}"
    store = RadarStore(database_url)
    store.initialize()
    store.save_driver_candidates("run-1", [driver_candidate()])

    with store.engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_driver_candidates_target_event_id")
        connection.execute(
            text("DELETE FROM schema_migrations WHERE version = :version"),
            {"version": DRIVER_CANDIDATE_MIGRATION},
        )

    store.initialize()

    assert store.load_driver_candidates_for_target("target-1") == [driver_candidate()]
    assert store.table_count(driver_candidates_table) == 1
    assert any(
        index["column_names"] == ["target_event_id"]
        for index in inspect(store.engine).get_indexes("driver_candidates")
    )


def test_driver_marker_recovers_missing_table_but_rejects_malformed_table(tmp_path) -> None:
    recover_url = f"sqlite:///{(tmp_path / 'missing-table.db').as_posix()}"
    recover = RadarStore(recover_url)
    recover.initialize()
    with recover.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE driver_candidates")
    recover.initialize()
    assert inspect(recover.engine).has_table("driver_candidates")

    malformed_url = f"sqlite:///{(tmp_path / 'malformed-table.db').as_posix()}"
    engine = create_engine(malformed_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migrations "
            "(version VARCHAR(80) PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"
        )
        connection.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (:version, 'test')"
            ),
            {"version": DRIVER_CANDIDATE_MIGRATION},
        )
        connection.exec_driver_sql(
            "CREATE TABLE driver_candidates "
            "(id VARCHAR(36) PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="DriverCandidate migration is incomplete"):
        RadarStore(malformed_url).initialize()


def test_driver_marker_rejects_wrong_types_and_nullable_columns(tmp_path) -> None:
    malformed_url = f"sqlite:///{(tmp_path / 'malformed-types.db').as_posix()}"
    engine = create_engine(malformed_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migrations "
            "(version VARCHAR(80) PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"
        )
        connection.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (:version, 'test')"
            ),
            {"version": DRIVER_CANDIDATE_MIGRATION},
        )
        connection.exec_driver_sql(
            "CREATE TABLE driver_candidates ("
            "id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36), target_event_id INTEGER, "
            "candidate_event_id VARCHAR(36), interpretation_claim_id VARCHAR(36), "
            "confidence VARCHAR(16), payload_json TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_driver_candidates_target_event_id "
            "ON driver_candidates (target_event_id)"
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="DriverCandidate migration is incomplete"):
        RadarStore(malformed_url).initialize()


def test_driver_marker_rejects_nullable_text_primary_key(tmp_path) -> None:
    malformed_url = f"sqlite:///{(tmp_path / 'nullable-id.db').as_posix()}"
    engine = create_engine(malformed_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE driver_candidates ("
            "id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL, "
            "target_event_id VARCHAR(36) NOT NULL, candidate_event_id VARCHAR(36) NOT NULL, "
            "interpretation_claim_id VARCHAR(36) NOT NULL, confidence VARCHAR(16) NOT NULL, "
            "payload_json TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_driver_candidates_target_event_id "
            "ON driver_candidates (target_event_id)"
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="DriverCandidate migration is incomplete"):
        RadarStore(malformed_url).initialize()


def test_driver_marker_rejects_blank_id_row(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'blank-id.db').as_posix()}"
    store = RadarStore(database_url)
    store.initialize()
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO driver_candidates "
                "(id, run_id, target_event_id, candidate_event_id, "
                "interpretation_claim_id, confidence, payload_json) "
                "VALUES ('', 'run', 'target', 'candidate', 'claim', 'LOW', '{}')"
            )
        )

    with pytest.raises(RuntimeError, match="DriverCandidate migration is incomplete"):
        store.initialize()


def test_driver_candidate_round_trip_and_stable_upsert(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'round-trip.db').as_posix()}")
    store.initialize()
    first = driver_candidate()
    changed = driver_candidate(candidate_id="driver-2")

    store.save_driver_candidates("run-1", [first])
    store.save_driver_candidates("run-2", [first])
    store.save_driver_candidates("run-2", [changed])

    assert store.load_driver_candidates_for_target("target-1") == [first, changed]
    assert store.table_count(driver_candidates_table) == 2


def test_repeated_macro_period_reuses_persisted_first_observation(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'macro-first.db').as_posix()}")
    store.initialize()
    first = MacroObservation(
        id="macro-cpi-2026-08",
        series_id="CUUR0000SA0",
        name="CPI",
        period="2026-08",
        observed_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        value=318.2,
        previous_value=317.5,
        change=0.7,
        change_label="vs previous month",
        unit="index",
        provider="bls-public-api",
    )
    later = first.model_copy(
        update={"observed_at": first.observed_at + timedelta(days=1)}
    )

    store.save_macro_with_first_observed_events("run-1", [first], first.observed_at)
    store.save_macro_with_first_observed_events("run-2", [later], later.observed_at)
    canonical = store.load_canonical_macro_observations([later])

    assert canonical == [first]
    first_claims, first_evidence, _ = build_macro_backdrop([first], [], first.observed_at)
    later_claims, later_evidence, _ = build_macro_backdrop(
        canonical, [], later.observed_at
    )
    assert later_evidence[0].id == first_evidence[0].id
    assert later_claims[0].id == first_claims[0].id
