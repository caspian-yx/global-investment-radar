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
    Integer,
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

from radar.briefing import DailyIntelligenceBrief
from radar.config import PROJECT_ROOT
from radar.deterministic import build_macro_event
from radar.domain import (
    Claim,
    DriverCandidate,
    Event,
    Evidence,
    MacroObservation,
    MarketObservation,
    SourceDocument,
    SourceKind,
    Story,
    StoryDocumentMembership,
    content_hash,
    stable_id,
)
from radar.impact import MarketImpactRecord
from radar.importance import C7Score
from radar.ranking import rank_driver_candidates

metadata = MetaData()
EVIDENCE_PROVENANCE_MIGRATION = "001_c1_evidence_provenance"
DRIVER_CANDIDATE_MIGRATION = "002_c2_1_driver_candidates"
STORY_CONSOLIDATION_MIGRATION = "003_c6_3_story_consolidation"
C7_IMPORTANCE_RELEVANCE_MIGRATION = "004_c7_importance_relevance"
C8_MARKET_REACTIONS_MIGRATION = "005_c8_market_reactions"
C9_DAILY_BRIEFINGS_MIGRATION = "006_c9_daily_briefings"

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

driver_candidates_table = Table(
    "driver_candidates",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("target_event_id", String(36), nullable=False),
    Column("candidate_event_id", String(36), nullable=False),
    Column("interpretation_claim_id", String(36), nullable=False),
    Column("confidence", String(16), nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index(
    "ix_driver_candidates_target_event_id",
    driver_candidates_table.c.target_event_id,
)

stories_table = Table(
    "stories",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("created_run_id", String(36), nullable=False),
    Column("last_run_id", String(36), nullable=False, index=True),
    Column("identity_seed_document_id", String(36), nullable=False),
    Column("presentation_document_id", String(36), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("earliest_trusted_published_at", DateTime(timezone=True), nullable=False),
    Column("latest_published_at", DateTime(timezone=True), nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index(
    "ux_stories_identity_seed_rule",
    stories_table.c.identity_seed_document_id,
    stories_table.c.rule_version,
    unique=True,
)

story_documents_table = Table(
    "story_documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("story_id", String(36), nullable=False, index=True),
    Column("source_document_id", String(36), nullable=False),
    Column("match_method", String(48), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("first_linked_at", DateTime(timezone=True), nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index(
    "ux_story_documents_source_rule",
    story_documents_table.c.source_document_id,
    story_documents_table.c.rule_version,
    unique=True,
)

c7_rankings_table = Table(
    "c7_rankings",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("event_id", String(36), nullable=False),
    Column("story_id", String(36)),
    Column("view_id", String(80), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("scored_at", DateTime(timezone=True), nullable=False),
    Column("priority_rank", Integer),
    Column("priority_band", String(24), nullable=False),
    Column("importance_score", Float, nullable=False),
    Column("relevance_score", Float, nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index(
    "ux_c7_rankings_run_event_view_rule",
    c7_rankings_table.c.run_id,
    c7_rankings_table.c.event_id,
    c7_rankings_table.c.view_id,
    c7_rankings_table.c.rule_version,
    unique=True,
)
Index("ix_c7_rankings_view_rank", c7_rankings_table.c.view_id, c7_rankings_table.c.priority_rank)

c8_market_impacts_table = Table(
    "c8_market_impacts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("record_id", String(36), nullable=False, index=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("story_id", String(36)),
    Column("event_id", String(36), nullable=False),
    Column("target_symbol", String(48), nullable=False),
    Column("reaction_window", String(24), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column("direction", String(16), nullable=False),
    Column("magnitude", String(16), nullable=False),
    Column("association_confidence", String(16), nullable=False),
    Column("epistemic_status", String(24), nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index(
    "ux_c8_impacts_run_event_target_window_rule",
    c8_market_impacts_table.c.run_id,
    c8_market_impacts_table.c.event_id,
    c8_market_impacts_table.c.target_symbol,
    c8_market_impacts_table.c.reaction_window,
    c8_market_impacts_table.c.rule_version,
    unique=True,
)
Index(
    "ix_c8_impacts_event_target",
    c8_market_impacts_table.c.event_id,
    c8_market_impacts_table.c.target_symbol,
)

c9_daily_briefings_table = Table(
    "c9_daily_briefings",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), nullable=False, index=True),
    Column("report_date", String(10), nullable=False, index=True),
    Column("view_id", String(80), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("composed_at", DateTime(timezone=True), nullable=False),
    Column("primary_count", Integer, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
)
Index(
    "ux_c9_briefings_run_view_rule",
    c9_daily_briefings_table.c.run_id,
    c9_daily_briefings_table.c.view_id,
    c9_daily_briefings_table.c.rule_version,
    unique=True,
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
        self._migrate_c1_to_c2_1()
        self._migrate_c2_1_to_c6_3()
        self._migrate_c6_3_to_c7()
        self._migrate_c7_to_c8()
        self._migrate_c8_to_c9()

    def _migrate_c8_to_c9(self) -> None:
        """Apply and validate additive C9 briefing persistence."""
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at VARCHAR(40) NOT NULL
                )
                """
            )
            self._validate_c9_schema(connection)
            applied = connection.execute(
                text("SELECT version FROM schema_migrations WHERE version = :version"),
                {"version": C9_DAILY_BRIEFINGS_MIGRATION},
            ).first()
            if applied is None:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, :applied_at)"
                    ),
                    {
                        "version": C9_DAILY_BRIEFINGS_MIGRATION,
                        "applied_at": datetime.now(UTC).isoformat(),
                    },
                )

    def _validate_c9_schema(self, connection: Connection) -> None:
        inspector = inspect(connection)
        required_columns = {
            "id",
            "run_id",
            "report_date",
            "view_id",
            "rule_version",
            "composed_at",
            "primary_count",
            "payload_hash",
            "payload_json",
        }
        columns = {column["name"]: column for column in inspector.get_columns("c9_daily_briefings")}
        if not required_columns <= set(columns):
            missing = ", ".join(sorted(required_columns - set(columns)))
            raise RuntimeError(f"C9 migration is incomplete: c9_daily_briefings missing {missing}")
        expected_types = {
            "composed_at": DateTime,
            "primary_count": Integer,
            "payload_json": Text,
        }
        for name, expected_type in expected_types.items():
            if not isinstance(columns[name]["type"], expected_type):
                raise RuntimeError(f"C9 migration is incomplete: invalid briefing column {name}")
        if any(column["nullable"] for column in columns.values()):
            raise RuntimeError("C9 migration is incomplete: nullable briefing column")
        if inspector.get_pk_constraint("c9_daily_briefings")["constrained_columns"] != ["id"]:
            raise RuntimeError("C9 migration is incomplete: invalid briefing primary key")
        indexes = inspector.get_indexes("c9_daily_briefings")
        if not any(
            index["column_names"] == ["run_id", "view_id", "rule_version"] and index["unique"]
            for index in indexes
        ):
            raise RuntimeError("C9 migration is incomplete: identity index missing")
        invalid_rows = connection.execute(
            text(
                "SELECT COUNT(*) FROM c9_daily_briefings "
                "WHERE id IS NULL OR TRIM(id) = '' "
                "OR run_id IS NULL OR TRIM(run_id) = '' "
                "OR report_date IS NULL OR TRIM(report_date) = '' "
                "OR view_id IS NULL OR TRIM(view_id) = '' "
                "OR rule_version IS NULL OR TRIM(rule_version) = '' "
                "OR primary_count < 0 "
                "OR payload_hash IS NULL OR LENGTH(payload_hash) <> 64 "
                "OR payload_json IS NULL OR TRIM(payload_json) = ''"
            )
        ).scalar_one()
        if invalid_rows:
            raise RuntimeError("C9 migration is incomplete: invalid persisted rows")

    def _migrate_c7_to_c8(self) -> None:
        """Apply and validate additive C8 market-impact persistence."""
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at VARCHAR(40) NOT NULL
                )
                """
            )
            self._validate_c8_schema(connection)
            applied = connection.execute(
                text("SELECT version FROM schema_migrations WHERE version = :version"),
                {"version": C8_MARKET_REACTIONS_MIGRATION},
            ).first()
            if applied is None:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, :applied_at)"
                    ),
                    {
                        "version": C8_MARKET_REACTIONS_MIGRATION,
                        "applied_at": datetime.now(UTC).isoformat(),
                    },
                )

    def _validate_c8_schema(self, connection: Connection) -> None:
        inspector = inspect(connection)
        required_columns = {
            "id",
            "record_id",
            "run_id",
            "story_id",
            "event_id",
            "target_symbol",
            "reaction_window",
            "rule_version",
            "evaluated_at",
            "direction",
            "magnitude",
            "association_confidence",
            "epistemic_status",
            "payload_json",
        }
        columns = {column["name"]: column for column in inspector.get_columns("c8_market_impacts")}
        if not required_columns <= set(columns):
            missing = ", ".join(sorted(required_columns - set(columns)))
            raise RuntimeError(f"C8 migration is incomplete: c8_market_impacts missing {missing}")
        expected_types = {"evaluated_at": DateTime, "payload_json": Text}
        for name, expected_type in expected_types.items():
            if not isinstance(columns[name]["type"], expected_type):
                raise RuntimeError(f"C8 migration is incomplete: invalid impact column {name}")
        for name, column in columns.items():
            if name != "story_id" and column["nullable"]:
                raise RuntimeError(f"C8 migration is incomplete: nullable impact column {name}")
        if inspector.get_pk_constraint("c8_market_impacts")["constrained_columns"] != ["id"]:
            raise RuntimeError("C8 migration is incomplete: invalid impact primary key")
        indexes = inspector.get_indexes("c8_market_impacts")
        identity_columns = [
            "run_id",
            "event_id",
            "target_symbol",
            "reaction_window",
            "rule_version",
        ]
        if not any(
            index["column_names"] == identity_columns and index["unique"] for index in indexes
        ):
            raise RuntimeError("C8 migration is incomplete: identity index missing")
        invalid_rows = connection.execute(
            text(
                "SELECT COUNT(*) FROM c8_market_impacts "
                "WHERE id IS NULL OR TRIM(id) = '' "
                "OR record_id IS NULL OR TRIM(record_id) = '' "
                "OR run_id IS NULL OR TRIM(run_id) = '' "
                "OR event_id IS NULL OR TRIM(event_id) = '' "
                "OR target_symbol IS NULL OR TRIM(target_symbol) = '' "
                "OR reaction_window IS NULL OR TRIM(reaction_window) = '' "
                "OR rule_version IS NULL OR TRIM(rule_version) = '' "
                "OR payload_json IS NULL OR TRIM(payload_json) = ''"
            )
        ).scalar_one()
        if invalid_rows:
            raise RuntimeError("C8 migration is incomplete: invalid persisted rows")

    def _migrate_c6_3_to_c7(self) -> None:
        """Apply and validate additive C7 score persistence."""
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at VARCHAR(40) NOT NULL
                )
                """
            )
            self._validate_c7_schema(connection)
            applied = connection.execute(
                text("SELECT version FROM schema_migrations WHERE version = :version"),
                {"version": C7_IMPORTANCE_RELEVANCE_MIGRATION},
            ).first()
            if applied is None:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, :applied_at)"
                    ),
                    {
                        "version": C7_IMPORTANCE_RELEVANCE_MIGRATION,
                        "applied_at": datetime.now(UTC).isoformat(),
                    },
                )

    def _validate_c7_schema(self, connection: Connection) -> None:
        inspector = inspect(connection)
        required_columns = {
            "id",
            "run_id",
            "event_id",
            "story_id",
            "view_id",
            "rule_version",
            "scored_at",
            "priority_rank",
            "priority_band",
            "importance_score",
            "relevance_score",
            "payload_json",
        }
        columns = {column["name"]: column for column in inspector.get_columns("c7_rankings")}
        if not required_columns <= set(columns):
            missing = ", ".join(sorted(required_columns - set(columns)))
            raise RuntimeError(f"C7 migration is incomplete: c7_rankings missing {missing}")
        expected_types = {
            "scored_at": DateTime,
            "importance_score": Float,
            "relevance_score": Float,
            "priority_rank": Integer,
            "payload_json": Text,
        }
        for name, expected_type in expected_types.items():
            if not isinstance(columns[name]["type"], expected_type):
                raise RuntimeError(f"C7 migration is incomplete: invalid c7_rankings column {name}")
        for name, column in columns.items():
            if name not in {"story_id", "priority_rank"} and column["nullable"]:
                raise RuntimeError(
                    f"C7 migration is incomplete: nullable c7_rankings column {name}"
                )
        if inspector.get_pk_constraint("c7_rankings")["constrained_columns"] != ["id"]:
            raise RuntimeError("C7 migration is incomplete: invalid c7_rankings primary key")
        indexes = inspector.get_indexes("c7_rankings")
        if not any(
            index["column_names"] == ["run_id", "event_id", "view_id", "rule_version"]
            and index["unique"]
            for index in indexes
        ):
            raise RuntimeError("C7 migration is incomplete: identity index missing")
        invalid_rows = connection.execute(
            text(
                "SELECT COUNT(*) FROM c7_rankings WHERE id IS NULL OR TRIM(id) = '' "
                "OR run_id IS NULL OR TRIM(run_id) = '' "
                "OR event_id IS NULL OR TRIM(event_id) = '' "
                "OR view_id IS NULL OR TRIM(view_id) = '' "
                "OR rule_version IS NULL OR TRIM(rule_version) = '' "
                "OR payload_json IS NULL OR TRIM(payload_json) = ''"
            )
        ).scalar_one()
        if invalid_rows:
            raise RuntimeError("C7 migration is incomplete: invalid persisted rows")

    def _migrate_c2_1_to_c6_3(self) -> None:
        """Apply and validate the additive C2.1 -> C6.3 Story migration."""
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at VARCHAR(40) NOT NULL
                )
                """
            )
            self._validate_story_schema(connection)
            applied = connection.execute(
                text("SELECT version FROM schema_migrations WHERE version = :version"),
                {"version": STORY_CONSOLIDATION_MIGRATION},
            ).first()
            if applied is None:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, :applied_at)"
                    ),
                    {
                        "version": STORY_CONSOLIDATION_MIGRATION,
                        "applied_at": datetime.now(UTC).isoformat(),
                    },
                )

    def _validate_story_schema(self, connection: Connection) -> None:
        inspector = inspect(connection)
        required_story_columns = {
            "id",
            "created_run_id",
            "last_run_id",
            "identity_seed_document_id",
            "presentation_document_id",
            "rule_version",
            "event_type",
            "earliest_trusted_published_at",
            "latest_published_at",
            "first_seen_at",
            "last_seen_at",
            "payload_json",
        }
        required_membership_columns = {
            "id",
            "story_id",
            "source_document_id",
            "match_method",
            "rule_version",
            "first_linked_at",
            "payload_json",
        }
        story_columns_by_name = {
            column["name"]: column for column in inspector.get_columns("stories")
        }
        membership_columns_by_name = {
            column["name"]: column for column in inspector.get_columns("story_documents")
        }
        story_columns = set(story_columns_by_name)
        membership_columns = set(membership_columns_by_name)
        if not required_story_columns <= story_columns:
            missing = ", ".join(sorted(required_story_columns - story_columns))
            raise RuntimeError(
                f"Story consolidation migration is incomplete: stories missing {missing}"
            )
        if not required_membership_columns <= membership_columns:
            missing = ", ".join(sorted(required_membership_columns - membership_columns))
            raise RuntimeError(
                f"Story consolidation migration is incomplete: story_documents missing {missing}"
            )
        story_datetime_columns = {
            "earliest_trusted_published_at",
            "latest_published_at",
            "first_seen_at",
            "last_seen_at",
        }
        membership_datetime_columns = {"first_linked_at"}
        for name in required_story_columns:
            column = story_columns_by_name[name]
            expected_type = DateTime if name in story_datetime_columns else String
            if name == "payload_json":
                expected_type = Text
            if not isinstance(column["type"], expected_type) or column["nullable"]:
                raise RuntimeError(
                    f"Story consolidation migration is incomplete: invalid stories column {name}"
                )
        for name in required_membership_columns:
            column = membership_columns_by_name[name]
            expected_type = DateTime if name in membership_datetime_columns else String
            if name == "payload_json":
                expected_type = Text
            if not isinstance(column["type"], expected_type) or column["nullable"]:
                raise RuntimeError(
                    "Story consolidation migration is incomplete: "
                    f"invalid story_documents column {name}"
                )
        if inspector.get_pk_constraint("stories")["constrained_columns"] != ["id"]:
            raise RuntimeError(
                "Story consolidation migration is incomplete: invalid stories primary key"
            )
        if inspector.get_pk_constraint("story_documents")["constrained_columns"] != ["id"]:
            raise RuntimeError(
                "Story consolidation migration is incomplete: invalid story_documents primary key"
            )
        story_indexes = inspector.get_indexes("stories")
        membership_indexes = inspector.get_indexes("story_documents")
        if not any(
            index["column_names"] == ["identity_seed_document_id", "rule_version"]
            and index["unique"]
            for index in story_indexes
        ):
            raise RuntimeError(
                "Story consolidation migration is incomplete: identity index missing"
            )
        if not any(
            index["column_names"] == ["source_document_id", "rule_version"] and index["unique"]
            for index in membership_indexes
        ):
            raise RuntimeError(
                "Story consolidation migration is incomplete: membership index missing"
            )
        if not any(index["column_names"] == ["story_id"] for index in membership_indexes):
            raise RuntimeError(
                "Story consolidation migration is incomplete: story lookup index missing"
            )
        invalid_stories = connection.execute(
            text(
                "SELECT COUNT(*) FROM stories WHERE id IS NULL OR TRIM(id) = '' "
                "OR identity_seed_document_id IS NULL OR TRIM(identity_seed_document_id) = '' "
                "OR presentation_document_id IS NULL OR TRIM(presentation_document_id) = '' "
                "OR rule_version IS NULL OR TRIM(rule_version) = '' "
                "OR payload_json IS NULL OR TRIM(payload_json) = ''"
            )
        ).scalar_one()
        invalid_memberships = connection.execute(
            text(
                "SELECT COUNT(*) FROM story_documents "
                "WHERE id IS NULL OR TRIM(id) = '' "
                "OR story_id IS NULL OR TRIM(story_id) = '' "
                "OR source_document_id IS NULL OR TRIM(source_document_id) = '' "
                "OR rule_version IS NULL OR TRIM(rule_version) = '' "
                "OR payload_json IS NULL OR TRIM(payload_json) = ''"
            )
        ).scalar_one()
        if invalid_stories or invalid_memberships:
            raise RuntimeError(
                "Story consolidation migration is incomplete: invalid persisted rows"
            )

    def _migrate_c1_to_c2_1(self) -> None:
        """Apply and validate the additive C1 -> C2.1 DriverCandidate migration."""
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(80) PRIMARY KEY,
                    applied_at VARCHAR(40) NOT NULL
                )
                """
            )
            columns = {
                column["name"] for column in inspect(connection).get_columns("driver_candidates")
            }
            required = {
                "id",
                "run_id",
                "target_event_id",
                "candidate_event_id",
                "interpretation_claim_id",
                "confidence",
                "payload_json",
            }
            if not required <= columns:
                missing = ", ".join(sorted(required - columns))
                raise RuntimeError(f"DriverCandidate migration is incomplete: missing {missing}")
            connection.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS ix_driver_candidates_target_event_id
                ON driver_candidates (target_event_id)
                """
            )
            self._validate_driver_candidate_schema(connection)
            applied = connection.execute(
                text("SELECT version FROM schema_migrations WHERE version = :version"),
                {"version": DRIVER_CANDIDATE_MIGRATION},
            ).first()
            if applied is None:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES (:version, :applied_at)"
                    ),
                    {
                        "version": DRIVER_CANDIDATE_MIGRATION,
                        "applied_at": datetime.now(UTC).isoformat(),
                    },
                )

    def _validate_driver_candidate_schema(self, connection: Connection) -> None:
        inspector = inspect(connection)
        columns_by_name = {
            column["name"]: column for column in inspector.get_columns("driver_candidates")
        }
        columns = set(columns_by_name)
        required = {
            "id",
            "run_id",
            "target_event_id",
            "candidate_event_id",
            "interpretation_claim_id",
            "confidence",
            "payload_json",
        }
        if not required <= columns:
            missing = ", ".join(sorted(required - columns))
            raise RuntimeError(f"DriverCandidate migration is incomplete: missing {missing}")
        for name in required:
            column = columns_by_name[name]
            if not isinstance(column["type"], String):
                raise RuntimeError(
                    f"DriverCandidate migration is incomplete: invalid type for {name}"
                )
            if column["nullable"]:
                raise RuntimeError(
                    f"DriverCandidate migration is incomplete: {name} must be NOT NULL"
                )
        primary_key = inspector.get_pk_constraint("driver_candidates")["constrained_columns"]
        if primary_key != ["id"]:
            raise RuntimeError("DriverCandidate migration is incomplete: invalid primary key")
        indexes = inspector.get_indexes("driver_candidates")
        if not any(index["column_names"] == ["target_event_id"] for index in indexes):
            raise RuntimeError("DriverCandidate migration is incomplete: index missing")
        invalid_rows = connection.execute(
            text(
                """
                SELECT COUNT(*) FROM driver_candidates
                WHERE id IS NULL OR TRIM(id) = ''
                   OR target_event_id IS NULL OR TRIM(target_event_id) = ''
                   OR candidate_event_id IS NULL OR TRIM(candidate_event_id) = ''
                   OR interpretation_claim_id IS NULL OR TRIM(interpretation_claim_id) = ''
                   OR confidence NOT IN ('LOW', 'MEDIUM', 'HIGH')
                   OR payload_json IS NULL OR TRIM(payload_json) = ''
                """
            )
        ).scalar_one()
        if invalid_rows:
            raise RuntimeError("DriverCandidate migration is incomplete: invalid rows")

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
            columns = {column["name"] for column in inspect(connection).get_columns("evidence")}
            if "source_kind" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE evidence ADD COLUMN source_kind VARCHAR(32)"
                )
            if "source_id" not in columns:
                connection.exec_driver_sql("ALTER TABLE evidence ADD COLUMN source_id VARCHAR(80)")
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
        if not any(index["column_names"] == ["source_kind", "source_id"] for index in indexes):
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
            raise RuntimeError("Evidence provenance migration is incomplete: invalid evidence rows")

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

    def complete_run(self, run_id: str, completed_at: datetime, details: dict[str, Any]) -> None:
        self._finish_run(run_id, completed_at, "completed", details)

    def fail_run(self, run_id: str, completed_at: datetime, details: dict[str, Any]) -> None:
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

    def load_source_documents(self, document_ids: Iterable[str]) -> list[SourceDocument]:
        ids = sorted(set(document_ids))
        if not ids:
            return []
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(source_documents.c.payload_json).where(source_documents.c.id.in_(ids))
            ).scalars()
            documents = [SourceDocument.model_validate_json(payload) for payload in payloads]
        return sorted(documents, key=lambda item: item.id)

    def save_stories(self, run_id: str, items: Iterable[Story]) -> None:
        for item in items:
            with self.engine.begin() as connection:
                persisted_document_ids = set(
                    connection.execute(
                        select(source_documents.c.id).where(
                            source_documents.c.id.in_(item.source_document_ids)
                        )
                    ).scalars()
                )
                missing_document_ids = sorted(
                    set(item.source_document_ids) - persisted_document_ids
                )
                if missing_document_ids:
                    raise ValueError(
                        "Story persistence is missing SourceDocument: "
                        + ", ".join(missing_document_ids)
                    )
                row = (
                    connection.execute(
                        select(
                            stories_table.c.created_run_id,
                            stories_table.c.payload_json,
                        ).where(stories_table.c.id == item.id)
                    )
                    .mappings()
                    .first()
                )
                seed_row = connection.execute(
                    select(stories_table.c.id).where(
                        stories_table.c.identity_seed_document_id == item.identity_seed_document_id,
                        stories_table.c.rule_version == item.rule_version,
                    )
                ).first()
                if seed_row is not None and seed_row.id != item.id:
                    raise ValueError("Story immutable identity conflicts with persisted seed")
                if row is not None:
                    existing = Story.model_validate_json(row["payload_json"])
                    if (
                        existing.identity_seed_document_id != item.identity_seed_document_id
                        or existing.identity_profile != item.identity_profile
                        or existing.rule_version != item.rule_version
                    ):
                        raise ValueError("Story immutable identity cannot be rewritten")
                    created_run_id = str(row["created_run_id"])
                else:
                    created_run_id = run_id
                self._upsert_connection(
                    connection,
                    stories_table,
                    item.id,
                    {
                        "id": item.id,
                        "created_run_id": created_run_id,
                        "last_run_id": run_id,
                        "identity_seed_document_id": item.identity_seed_document_id,
                        "presentation_document_id": item.presentation_document_id,
                        "rule_version": item.rule_version,
                        "event_type": item.event_type.value,
                        "earliest_trusted_published_at": (item.earliest_trusted_published_at),
                        "latest_published_at": item.latest_published_at,
                        "first_seen_at": item.first_seen_at,
                        "last_seen_at": item.last_seen_at,
                        "payload_json": item.model_dump_json(),
                    },
                )

    def save_c7_scores(self, run_id: str, items: Iterable[C7Score]) -> None:
        """Persist immutable C7 evaluations, retaining rows by rule version."""
        for item in items:
            row_id = stable_id(
                "c7-ranking",
                f"{run_id}|{item.event_id}|{item.view_id}|{item.rule_version}",
            )
            values = {
                "id": row_id,
                "run_id": run_id,
                "event_id": item.event_id,
                "story_id": item.story_id,
                "view_id": item.view_id,
                "rule_version": item.rule_version,
                "scored_at": item.scored_at,
                "priority_rank": item.priority_rank,
                "priority_band": item.priority_band.value,
                "importance_score": item.importance_score,
                "relevance_score": item.relevance_score,
                "payload_json": item.model_dump_json(),
            }
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(c7_rankings_table.c.payload_json).where(c7_rankings_table.c.id == row_id)
                ).scalar_one_or_none()
                if existing is not None:
                    if existing != item.model_dump_json():
                        connection.execute(
                            c7_rankings_table.update()
                            .where(c7_rankings_table.c.id == row_id)
                            .values(**values)
                        )
                    continue
                conflicting = connection.execute(
                    select(c7_rankings_table.c.id).where(
                        c7_rankings_table.c.run_id == run_id,
                        c7_rankings_table.c.event_id == item.event_id,
                        c7_rankings_table.c.view_id == item.view_id,
                        c7_rankings_table.c.rule_version == item.rule_version,
                    )
                ).first()
                if conflicting is not None and conflicting.id != row_id:
                    raise ValueError("C7 score identity is not deterministic")
                connection.execute(c7_rankings_table.insert().values(**values))

    def load_c7_scores(self, run_id: str, view_id: str, rule_version: str) -> list[C7Score]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(c7_rankings_table.c.payload_json)
                .where(
                    c7_rankings_table.c.run_id == run_id,
                    c7_rankings_table.c.view_id == view_id,
                    c7_rankings_table.c.rule_version == rule_version,
                )
                .order_by(c7_rankings_table.c.priority_rank, c7_rankings_table.c.event_id)
            ).scalars()
            return [C7Score.model_validate_json(payload) for payload in payloads]

    def save_market_impacts(self, run_id: str, items: Iterable[MarketImpactRecord]) -> None:
        """Persist versioned C8 evaluations; refresh only an exact evaluation identity."""
        for item in items:
            row_id = stable_id("c8-evaluation", f"{run_id}|{item.id}")
            payload = item.model_dump_json()
            values = {
                "id": row_id,
                "record_id": item.id,
                "run_id": run_id,
                "story_id": item.story_id,
                "event_id": item.event_id,
                "target_symbol": item.target.symbol,
                "reaction_window": item.reaction_window.value,
                "rule_version": item.rule_version,
                "evaluated_at": item.evaluated_at,
                "direction": item.direction.value,
                "magnitude": item.magnitude.value,
                "association_confidence": item.association_confidence.value,
                "epistemic_status": item.epistemic_status.value,
                "payload_json": payload,
            }
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(c8_market_impacts_table.c.payload_json).where(
                        c8_market_impacts_table.c.id == row_id
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    if existing != payload:
                        connection.execute(
                            c8_market_impacts_table.update()
                            .where(c8_market_impacts_table.c.id == row_id)
                            .values(**values)
                        )
                    continue
                conflicting = connection.execute(
                    select(c8_market_impacts_table.c.id).where(
                        c8_market_impacts_table.c.run_id == run_id,
                        c8_market_impacts_table.c.event_id == item.event_id,
                        c8_market_impacts_table.c.target_symbol == item.target.symbol,
                        c8_market_impacts_table.c.reaction_window == item.reaction_window.value,
                        c8_market_impacts_table.c.rule_version == item.rule_version,
                    )
                ).first()
                if conflicting is not None and conflicting.id != row_id:
                    raise ValueError("C8 market impact identity is not deterministic")
                connection.execute(c8_market_impacts_table.insert().values(**values))

    def load_market_impacts(self, run_id: str, rule_version: str) -> list[MarketImpactRecord]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(c8_market_impacts_table.c.payload_json)
                .where(
                    c8_market_impacts_table.c.run_id == run_id,
                    c8_market_impacts_table.c.rule_version == rule_version,
                )
                .order_by(
                    c8_market_impacts_table.c.event_id,
                    c8_market_impacts_table.c.target_symbol,
                    c8_market_impacts_table.c.reaction_window,
                )
            ).scalars()
            return [MarketImpactRecord.model_validate_json(payload) for payload in payloads]

    def save_daily_briefing(self, item: DailyIntelligenceBrief) -> None:
        """Persist one immutable C9 product snapshot for a run/view/rule identity."""
        payload = item.model_dump_json()
        payload_hash = content_hash(payload)
        values = {
            "id": item.id,
            "run_id": item.run_id,
            "report_date": item.report_date,
            "view_id": item.view_id,
            "rule_version": item.rule_version,
            "composed_at": item.composed_at,
            "primary_count": len(item.primary_stories),
            "payload_hash": payload_hash,
            "payload_json": payload,
        }
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(
                        c9_daily_briefings_table.c.id,
                        c9_daily_briefings_table.c.payload_hash,
                        c9_daily_briefings_table.c.payload_json,
                    ).where(
                        c9_daily_briefings_table.c.run_id == item.run_id,
                        c9_daily_briefings_table.c.view_id == item.view_id,
                        c9_daily_briefings_table.c.rule_version == item.rule_version,
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if (
                    existing["id"] != item.id
                    or existing["payload_hash"] != payload_hash
                    or existing["payload_json"] != payload
                ):
                    raise ValueError("immutable C9 briefing conflicts with persisted run/view/rule")
                return
            connection.execute(c9_daily_briefings_table.insert().values(**values))

    def load_daily_briefing(
        self, run_id: str, view_id: str, rule_version: str
    ) -> DailyIntelligenceBrief | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        c9_daily_briefings_table.c.payload_hash,
                        c9_daily_briefings_table.c.payload_json,
                    ).where(
                        c9_daily_briefings_table.c.run_id == run_id,
                        c9_daily_briefings_table.c.view_id == view_id,
                        c9_daily_briefings_table.c.rule_version == rule_version,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        payload = str(row["payload_json"])
        if content_hash(payload) != row["payload_hash"]:
            raise RuntimeError("persisted C9 briefing payload checksum is invalid")
        item = DailyIntelligenceBrief.model_validate_json(payload)
        if item.run_id != run_id or item.view_id != view_id or item.rule_version != rule_version:
            raise RuntimeError("persisted C9 briefing identity is inconsistent")
        return item

    def load_stories(self, rule_version: str) -> list[Story]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(stories_table.c.payload_json)
                .where(stories_table.c.rule_version == rule_version)
                .order_by(stories_table.c.id)
            ).scalars()
            return [Story.model_validate_json(payload) for payload in payloads]

    def save_story_memberships(self, items: Iterable[StoryDocumentMembership]) -> None:
        for item in items:
            with self.engine.begin() as connection:
                row = connection.execute(
                    select(story_documents_table.c.payload_json).where(
                        story_documents_table.c.source_document_id == item.source_document_id,
                        story_documents_table.c.rule_version == item.rule_version,
                    )
                ).first()
                if row is not None:
                    existing = StoryDocumentMembership.model_validate_json(row.payload_json)
                    if existing.story_id != item.story_id:
                        raise ValueError(
                            "SourceDocument cannot belong to multiple story-v1 Stories"
                        )
                    if existing.id != item.id:
                        raise ValueError("Story membership identity is not deterministic")
                    continue
                story_exists = connection.execute(
                    select(stories_table.c.id).where(stories_table.c.id == item.story_id)
                ).first()
                if story_exists is None:
                    raise ValueError(f"Story membership references missing Story {item.story_id}")
                document_exists = connection.execute(
                    select(source_documents.c.id).where(
                        source_documents.c.id == item.source_document_id
                    )
                ).first()
                if document_exists is None:
                    raise ValueError(
                        "Story membership references missing SourceDocument "
                        f"{item.source_document_id}"
                    )
                self._upsert_connection(
                    connection,
                    story_documents_table,
                    item.id,
                    {
                        "id": item.id,
                        "story_id": item.story_id,
                        "source_document_id": item.source_document_id,
                        "match_method": item.match_method,
                        "rule_version": item.rule_version,
                        "first_linked_at": item.first_linked_at,
                        "payload_json": item.model_dump_json(),
                    },
                )

    def load_story_memberships(self, rule_version: str) -> list[StoryDocumentMembership]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(story_documents_table.c.payload_json)
                .where(story_documents_table.c.rule_version == rule_version)
                .order_by(story_documents_table.c.source_document_id)
            ).scalars()
            return [StoryDocumentMembership.model_validate_json(payload) for payload in payloads]

    def save_evidence(self, run_id: str, items: Iterable[Evidence]) -> None:
        for item in items:
            self._upsert(
                evidence_table,
                item.id,
                self._evidence_values(run_id, item),
            )

    def load_evidence_for_source(self, source_kind: SourceKind, source_id: str) -> list[Evidence]:
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

    def save_driver_candidates(self, run_id: str, items: Iterable[DriverCandidate]) -> None:
        for item in items:
            self._upsert(
                driver_candidates_table,
                item.id,
                {
                    "id": item.id,
                    "run_id": run_id,
                    "target_event_id": item.target_event_id,
                    "candidate_event_id": item.candidate_event_id,
                    "interpretation_claim_id": item.interpretation_claim_id,
                    "confidence": item.confidence.value,
                    "payload_json": item.model_dump_json(),
                },
            )

    def load_driver_candidates_for_target(self, target_event_id: str) -> list[DriverCandidate]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(driver_candidates_table.c.payload_json).where(
                    driver_candidates_table.c.target_event_id == target_event_id
                )
            ).scalars()
            return rank_driver_candidates(
                DriverCandidate.model_validate_json(payload) for payload in rows
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
                if item.release_at is None or item.source_document_id is None:
                    continue
                event_bundle = build_macro_event(item, now)
                if event_bundle is None:
                    continue
                event, claim, evidence = event_bundle
                event_exists = connection.execute(
                    select(events_table.c.id).where(events_table.c.id == event.id)
                ).first()
                if event_exists:
                    continue
                self._save_macro_event_bundle(connection, run_id, event, claim, evidence)
                events.append(event)
                claims.append(claim)
                evidence_items.append(evidence)
        return events, claims, evidence_items

    def load_canonical_macro_observations(
        self, items: Iterable[MacroObservation]
    ) -> list[MacroObservation]:
        """Load the persisted first-seen payload for each requested series/period."""
        keys = sorted({(item.series_id, item.period) for item in items})
        result: list[MacroObservation] = []
        with self.engine.connect() as connection:
            for series_id, period in keys:
                payloads = connection.execute(
                    select(macro_table.c.payload_json).where(
                        macro_table.c.series_id == series_id,
                        macro_table.c.period == period,
                    )
                ).scalars()
                observations = [
                    MacroObservation.model_validate_json(payload) for payload in payloads
                ]
                if not observations:
                    raise RuntimeError("Persisted macro observation is missing after atomic save")
                result.append(min(observations, key=lambda item: (item.observed_at, item.id)))
        return result

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
