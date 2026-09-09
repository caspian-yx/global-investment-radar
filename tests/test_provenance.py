from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from radar.domain import Evidence, SourceKind
from radar.storage import RadarStore, evidence_table


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
