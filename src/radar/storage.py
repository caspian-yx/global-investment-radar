from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine

from radar.config import PROJECT_ROOT
from radar.domain import Claim, Event, Evidence, MacroObservation, MarketObservation, SourceDocument

metadata = MetaData()

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
    Column("source_document_id", String(36), nullable=False, index=True),
    Column("checksum", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
)

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
                {
                    "id": item.id,
                    "run_id": run_id,
                    "source_document_id": item.source_document_id,
                    "checksum": item.checksum,
                    "payload_json": item.model_dump_json(),
                },
            )

    def save_claims(self, run_id: str, items: Iterable[Claim]) -> None:
        for item in items:
            self._upsert(
                claims_table,
                item.id,
                {
                    "id": item.id,
                    "run_id": run_id,
                    "claim_type": item.claim_type.value,
                    "payload_json": item.model_dump_json(),
                },
            )

    def save_events(self, run_id: str, items: Iterable[Event]) -> None:
        for item in items:
            self._upsert(
                events_table,
                item.id,
                {
                    "id": item.id,
                    "run_id": run_id,
                    "event_key": item.event_key,
                    "event_type": item.event_type.value,
                    "importance_score": item.importance_score,
                    "payload_json": item.model_dump_json(),
                    "is_demo": item.is_demo,
                },
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
                {
                    "id": item.id,
                    "run_id": run_id,
                    "series_id": item.series_id,
                    "period": item.period,
                    "payload_json": item.model_dump_json(),
                },
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
            exists = connection.execute(select(table.c.id).where(table.c.id == row_id)).first()
            if exists:
                connection.execute(table.update().where(table.c.id == row_id).values(**values))
            else:
                connection.execute(table.insert().values(**values))
