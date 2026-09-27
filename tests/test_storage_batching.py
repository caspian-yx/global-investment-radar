from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select

from radar.domain import SourceCategory, SourceDocument, SourceRole, SourceTier, content_hash
from radar.storage import RadarStore, runs


def _document(document_id: str) -> SourceDocument:
    content = f"Primary source content for {document_id}."
    return SourceDocument(
        id=document_id,
        provider="batching-test",
        source_category=SourceCategory.OFFICIAL,
        source_role=SourceRole.PRIMARY,
        source_tier=SourceTier.A,
        publisher="Batching Test",
        original_url=f"https://example.test/{document_id}",
        canonical_url=f"https://example.test/{document_id}",
        title=f"Document {document_id}",
        content=content,
        region="US",
        published_at=datetime(2026, 9, 20, 8, tzinfo=UTC),
        collected_at=datetime(2026, 9, 20, 9, tzinfo=UTC),
        content_hash=content_hash(content),
    )


def test_save_documents_uses_one_transaction_for_the_collection(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'batch-documents.db').as_posix()}")
    store.initialize()
    transaction_count = 0

    def count_transaction(_connection) -> None:
        nonlocal transaction_count
        transaction_count += 1

    event.listen(store.engine, "begin", count_transaction)
    documents = [_document("document-a"), _document("document-b"), _document("document-c")]

    store.save_documents("run-1", documents)

    assert transaction_count == 1
    assert store.load_source_documents(item.id for item in documents) == documents


def test_retry_start_clears_stale_terminal_state(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'retry-state.db').as_posix()}")
    store.initialize()
    first_started_at = datetime(2026, 9, 20, 8, tzinfo=UTC)
    completed_at = datetime(2026, 9, 20, 8, 5, tzinfo=UTC)
    retry_started_at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    store.start_run(
        run_id="run-1",
        run_key="daily:2026-09-20:live:ai-deepseek:v0.1",
        started_at=first_started_at,
        data_mode="live",
        ai_status="enabled",
    )
    store.complete_run("run-1", completed_at, {"email_status": "sent"})

    store.start_run(
        run_id="run-1",
        run_key="daily:2026-09-20:live:ai-deepseek:v0.1",
        started_at=retry_started_at,
        data_mode="live",
        ai_status="enabled",
    )

    with store.engine.connect() as connection:
        row = connection.execute(select(runs).where(runs.c.id == "run-1")).mappings().one()
    assert row["status"] == "running"
    assert row["started_at"] == retry_started_at.replace(tzinfo=None)
    assert row["completed_at"] is None
    assert row["details_json"] == "{}"


def test_document_batch_rejects_conflicting_duplicate_id_atomically(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'duplicate-documents.db').as_posix()}")
    store.initialize()
    original = _document("document-a")
    conflicting = original.model_copy(update={"title": "Conflicting title"})

    with pytest.raises(ValueError, match="conflicting duplicate"):
        store.save_documents("run-1", [original, conflicting])

    assert store.load_source_documents([original.id]) == []


def test_document_retry_uses_set_oriented_upsert(tmp_path) -> None:
    store = RadarStore(f"sqlite:///{(tmp_path / 'retry-batch.db').as_posix()}")
    store.initialize()
    documents = [_document(f"document-{index:02d}") for index in range(20)]
    store.save_documents("run-1", documents)
    statements: list[tuple[str, bool]] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, executemany) -> None:
        statements.append((statement, executemany))

    event.listen(store.engine, "before_cursor_execute", capture_statement)
    store.save_documents("run-1", documents)

    assert len(statements) <= 2
    assert not any(executemany for _statement, executemany in statements)
    assert store.load_source_documents(item.id for item in documents) == documents
