from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine

from radar.config import PROJECT_ROOT
from radar.deterministic import build_macro_event
from radar.domain import (
    Claim,
    Event,
    Evidence,
    MacroObservation,
    MarketObservation,
    SourceDocument,
    SourceKind,
)

metadata = MetaData()
EVIDENCE_PROVENANCE_MIGRATION = "001_c1_evidence_provenance"

runs = Table(
    "runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_key", String(160), unique=True, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("status", String(32), nullable=False),
    Column("data_mode", String(16), nullable=False),
    Column("ai_status", String(64), nullable=False),
    Column("details_json", Text, nullable=False, default="{}"),
)

source_documents = Table(
    "source_documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("provider", String(80), nullable=False),
    Column("source_tier", String(1), nullable=False),
    Column("publisher", String(160), nullable=False),
    Column("canonical_url", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("content_hash", String(64), nullable=False, index=True),
    Column("payload_json", Text, nullable=False),
    Column("is_demo", Boolean, nullable=False, default=False),
)

evidence_table = Table(
    "evidence",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("source_kind", String(32), nullable=False),
    Column("source_id", String(80), nullable=False),
    Column("source_document_id", String(36), index=True),
    Column("checksum", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index("ix_evidence_source_kind_source_id", evidence_table.c.source_kind, evidence_table.c.source_id)

claims_table = Table(
    "claims",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("claim_type", String(24), nullable=False),
    Column("payload_json", Text, nullable=False),
)

events_table = Table(
    "events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("event_key", String(64), nullable=False, index=True),
    Column("event_type", String(40), nullable=False),
    Column("importance_score", Float, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("is_demo", Boolean, nullable=False, default=False),
)

market_table = Table(
    "market_observations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("symbol", String(48), nullable=False, index=True),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("payload_json", Text, nullable=False),
)

macro_table = Table(
    "macro_observations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("series_id", String(80), nullable=False, index=True),
    Column("period", String(32), nullable=False),
    Column("payload_json", Text, nullable=False),
)

reports_table = Table(
    "reports",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("report_date", String(10), nullable=False, index=True),
    Column("path", Text, nullable=False),
    Column("html_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class RadarStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = self._resolve_sqlite_url(database_url)
        self.engine: Engine = create_engine(self.database_url, future=True)

    def _resolve_sqlite_url(self, database_url: str) -> str:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            return database_url
        raw = database_url[len(prefix) :]
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"

    def initialize(self) -> None:
        metadata.create_all(self.engine)
        self._migrate_phase_b_to_c1()

    def _migrate_phase_b_to_c1(self) -> None:
        """Apply the single versioned Phase B -> C1 provenance migration."""
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at VARCHAR(40) NOT NULL
                )
                """
            )
            if self._evidence_migration_applied(connection):
                self._validate_evidence_provenance_schema(connection)
                return
            columns = {
                column["name"] for column in inspect(connection).get_columns("evidence")
            }
            if "source_kind" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE evidence ADD COLUMN source_kind VARCHAR(32)"
                )
            if "source_id" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE evidence ADD COLUMN source_id VARCHAR(80)"
                )
            self._backfill_document_evidence(connection)
            connection.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS ix_evidence_source_kind_source_id
                ON evidence (source_kind, source_id)
                """
            )
            self._validate_evidence_provenance_schema(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, :applied_at)"
                ),
                {
                    "version": EVIDENCE_PROVENANCE_MIGRATION,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )

    def _evidence_migration_applied(self, connection: Connection) -> bool:
        return (
            connection.execute(
                text("SELECT version FROM schema_migrations WHERE version = :version"),
                {"version": EVIDENCE_PROVENANCE_MIGRATION},
            ).first()
            is not None
        )

    def _backfill_document_evidence(self, connection: Connection) -> None:
        connection.exec_driver_sql(
            """
            UPDATE evidence
            SET source_kind = COALESCE(source_kind, 'DOCUMENT'),
                source_id = COALESCE(NULLIF(source_id, ''), source_document_id)
            WHERE source_document_id IS NOT NULL
              AND (source_kind IS NULL OR source_id IS NULL OR source_id = '')
            """
        )

    def _validate_evidence_provenance_schema(self, connection: Connection) -> None:
        inspector = inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("evidence")}
        required = {"source_kind", "source_id", "source_document_id"}
        if not required <= columns:
            missing = ", ".join(sorted(required - columns))
            raise RuntimeError(f"Evidence provenance migration is incomplete: {missing}")
        indexes = inspector.get_indexes("evidence")
        if not any(
            index["column_names"] == ["source_kind", "source_id"]
            for index in indexes
        ):
            raise RuntimeError("Evidence provenance migration is incomplete: index missing")
        invalid_rows = connection.execute(
            text(
                """
                SELECT COUNT(*) FROM evidence
                WHERE source_kind IS NULL OR source_id IS NULL OR TRIM(source_id) = ''
                   OR source_kind NOT IN (
                       'DOCUMENT', 'MARKET_OBSERVATION', 'MACRO_OBSERVATION'
                   )
                   OR (source_kind = 'DOCUMENT' AND source_document_id IS NOT NULL
                       AND source_id <> source_document_id)
                   OR (source_kind <> 'DOCUMENT' AND source_document_id IS NOT NULL)
                """
            )
        ).scalar_one()
        if invalid_rows:
            raise RuntimeError(
                "Evidence provenance migration is incomplete: invalid evidence rows"
            )

    def start_run(
        self,
        *,
        run_id: str,
        run_key: str,
        started_at: datetime,
        data_mode: str,
        ai_status: str,
    ) -> None:
        payload = {
            "id": run_id,
            "run_key": run_key,
            "started_at": started_at,
            "status": "running",
            "data_mode": data_mode,
            "ai_status": ai_status,
            "details_json": "{}",
        }
        self._upsert(runs, run_id, payload)

    def complete_run(
        self, run_id: str, completed_at: datetime, details: dict[str, Any]
    ) -> None:
        self._finish_run(run_id, completed_at, "completed", details)

    def fail_run(
        self, run_id: str, completed_at: datetime, details: dict[str, Any]
    ) -> None:
        self._finish_run(run_id, completed_at, "failed", details)

    def _finish_run(
        self,
        run_id: str,
        completed_at: datetime,
        status: str,
        details: dict[str, Any],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(
                    completed_at=completed_at,
                    status=status,
                    details_json=json.dumps(details, ensure_ascii=False),
                )
            )

    def save_documents(self, run_id: str, items: Iterable[SourceDocument]) -> None:
        for item in items:
            self._upsert(
                source_documents,
                item.id,
                {
                    "id": item.id,
                    "run_id": run_id,
                    "provider": item.provider,
                    "source_tier": item.source_tier.value,
                    "publisher": item.publisher,
                    "canonical_url": item.canonical_url,
                    "title": item.title,
                    "published_at": item.published_at,
                    "content_hash": item.content_hash,
                    "payload_json": item.model_dump_json(),
                    "is_demo": item.is_demo,
                },
            )

    def save_evidence(self, run_id: str, items: Iterable[Evidence]) -> None:
        for item in items:
            self._upsert(
                evidence_table,
                item.id,
                self._evidence_values(run_id, item),
            )

    def load_evidence_for_source(
        self, source_kind: SourceKind, source_id: str
    ) -> list[Evidence]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    evidence_table.c.payload_json,
                    evidence_table.c.source_kind,
                    evidence_table.c.source_id,
                    evidence_table.c.source_document_id,
                ).where(
                    evidence_table.c.source_kind == source_kind.value,
                    evidence_table.c.source_id == source_id,
                )
            ).mappings()
            result = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload.update(
                    source_kind=row["source_kind"],
                    source_id=row["source_id"],
                    source_document_id=row["source_document_id"],
                )
                result.append(Evidence.model_validate(payload))
            return result

    def save_claims(self, run_id: str, items: Iterable[Claim]) -> None:
        for item in items:
            self._upsert(
                claims_table,
                item.id,
                self._claim_values(run_id, item),
            )

    def save_events(self, run_id: str, items: Iterable[Event]) -> None:
        for item in items:
            self._upsert(
                events_table,
                item.id,
                self._event_values(run_id, item),
            )

    def save_markets(self, run_id: str, items: Iterable[MarketObservation]) -> None:
        for item in items:
            self._upsert(
                market_table,
                item.id,
                {
                    "id": item.id,
                    "run_id": run_id,
                    "symbol": item.symbol,
                    "observed_at": item.observed_at,
                    "payload_json": item.model_dump_json(),
                },
            )

    def save_macro(self, run_id: str, items: Iterable[MacroObservation]) -> None:
        for item in items:
            self._upsert(
                macro_table,
                item.id,
                self._macro_values(run_id, item),
            )

    def save_macro_with_first_observed_events(
        self, run_id: str, items: Iterable[MacroObservation], now: datetime
    ) -> tuple[list[Event], list[Claim], list[Evidence]]:
        """Atomically save new macro periods and their eligible first-observed events."""
        events: list[Event] = []
        claims: list[Claim] = []
        evidence_items: list[Evidence] = []
        ordered = sorted(items, key=lambda item: (item.series_id, item.period))
        with self.engine.begin() as connection:
            for item in ordered:
                exists = connection.execute(
                    select(macro_table.c.id).where(
                        macro_table.c.series_id == item.series_id,
                        macro_table.c.period == item.period,
                    )
                ).first()
                if exists:
                    continue
                older_exists = connection.execute(
                    select(macro_table.c.id).where(
                        macro_table.c.series_id == item.series_id,
                        macro_table.c.period < item.period,
                    )
                ).first()
                self._upsert_connection(
                    connection,
                    macro_table,
                    item.id,
                    self._macro_values(run_id, item),
                )
                if not older_exists:
                    continue
                event, claim, evidence = build_macro_event(item, now)
                event_exists = connection.execute(
                    select(events_table.c.id).where(events_table.c.id == event.id)
                ).first()
                if event_exists:
                    continue
                self._save_macro_event_bundle(
                    connection, run_id, event, claim, evidence
                )
                events.append(event)
                claims.append(claim)
                evidence_items.append(evidence)
        return events, claims, evidence_items

    def _save_macro_event_bundle(
        self,
        connection: Connection,
        run_id: str,
        event: Event,
        claim: Claim,
        evidence: Evidence,
    ) -> None:
        self._upsert_connection(
            connection,
            evidence_table,
            evidence.id,
            self._evidence_values(run_id, evidence),
        )
        self._upsert_connection(
            connection,
            claims_table,
            claim.id,
            self._claim_values(run_id, claim),
        )
        self._upsert_connection(
            connection,
            events_table,
            event.id,
            self._event_values(run_id, event),
        )

    def save_report(
        self,
        *,
        report_id: str,
        run_id: str,
        report_date: str,
        path: str,
        html_hash: str,
        created_at: datetime,
    ) -> None:
        self._upsert(
            reports_table,
            report_id,
            {
                "id": report_id,
                "run_id": run_id,
                "report_date": report_date,
                "path": path,
                "html_hash": html_hash,
                "created_at": created_at,
            },
        )

    def table_count(self, table: Table) -> int:
        with self.engine.connect() as connection:
            return len(connection.execute(select(table.c.id)).all())

    def _upsert(self, table: Table, row_id: str, values: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            self._upsert_connection(connection, table, row_id, values)

    def _upsert_connection(
        self,
        connection: Connection,
        table: Table,
        row_id: str,
        values: dict[str, Any],
    ) -> None:
        exists = connection.execute(select(table.c.id).where(table.c.id == row_id)).first()
        if exists:
            connection.execute(table.update().where(table.c.id == row_id).values(**values))
        else:
            connection.execute(table.insert().values(**values))

    def _evidence_values(self, run_id: str, item: Evidence) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": run_id,
            "source_kind": item.source_kind.value,
            "source_id": item.source_id,
            "source_document_id": item.source_document_id,
            "checksum": item.checksum,
            "payload_json": item.model_dump_json(),
        }

    def _claim_values(self, run_id: str, item: Claim) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": run_id,
            "claim_type": item.claim_type.value,
            "payload_json": item.model_dump_json(),
        }

    def _event_values(self, run_id: str, item: Event) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": run_id,
            "event_key": item.event_key,
            "event_type": item.event_type.value,
            "importance_score": item.importance_score,
            "payload_json": item.model_dump_json(),
            "is_demo": item.is_demo,
        }

    def _macro_values(self, run_id: str, item: MacroObservation) -> dict[str, Any]:
        return {
            "id": item.id,
            "run_id": run_id,
            "series_id": item.series_id,
            "period": item.period,
            "payload_json": item.model_dump_json(),
        }
