"""Phase 16 — KnowledgeIngestWorker tick + re-index idempotency (Wave 0 RED).

Asserts the documented ingest-worker behaviour Plan 16-02 will implement:
claim a pending kb_documents row → extract → chunk → embed (deterministic stub)
→ store kb_chunks with non-null embeddings → flip status to 'indexed'. Re-index
must be idempotent (delete-then-insert; chunk_count stable, no duplicates —
Pitfall 8).

Until `app.services.kb_ingest_worker` lands, the deferred in-body import raises
ImportError → RED. Expected Wave-0 state.

The embedder is stubbed deterministically (patch the worker's embed function to
return fixed 1536-dim vectors) so the tick runs without OpenAI network calls.
NB Wave 2/3: confirm the patched symbol name matches the real embedder
(`app.services.kb_ingest.embed_texts` is the planned name) when the module lands.

Test → requirement map:
- test_tick_indexes_pending_doc → KB-03
- test_reindex_is_idempotent    → KB-03 / Pitfall 8
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

# Planned production symbol for the embedder — patched so the worker indexes
# deterministically without hitting OpenAI. If Wave 2 names it differently,
# update this single constant.
_EMBED_TARGET = "app.services.kb_ingest.embed_texts"


def _fixed_vec(seed: float) -> list[float]:
    """A deterministic unit-ish 1536-dim vector (seed in slot 0, zeros elsewhere)."""
    v = [0.0] * 1536
    v[0] = seed
    return v


async def _seed_pending_doc(db, ws_id, kb_id=None, body=b"chunk one. chunk two. chunk three.", name="seed.txt"):
    """Insert a KB + a pending kb_documents row with a known TXT blob. Returns (kb_id, doc_id)."""
    import uuid as _uuid
    if kb_id is None:
        kb_id = str(_uuid.uuid4())
        await db.execute(text("""
            INSERT INTO knowledge_bases (id, workspace_id, name)
            VALUES (:id, :ws, 'Worker KB')
        """), {"id": kb_id, "ws": str(ws_id)})
    doc_id = str(_uuid.uuid4())
    await db.execute(text("""
        INSERT INTO kb_documents
            (id, workspace_id, kb_id, name, source_kind, size_bytes, status, raw_content)
        VALUES (:id, :ws, :kb, :name, 'txt', :size, 'pending', :raw)
    """), {
        "id": doc_id, "ws": str(ws_id), "kb": kb_id,
        "name": name, "size": len(body), "raw": body,
    })
    await db.commit()
    return kb_id, doc_id


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_kb_state(async_db_session):
    """Worker indexes ANY workspace's pending docs globally; clean committed KB
    rows after each test so they don't leak into a later worker test."""
    yield
    await async_db_session.execute(text("DELETE FROM kb_chunks"))
    await async_db_session.execute(text("DELETE FROM kb_documents"))
    await async_db_session.execute(text("DELETE FROM agent_knowledge_bases"))
    await async_db_session.execute(text("DELETE FROM knowledge_bases"))
    await async_db_session.commit()


# ─── KB-03: worker tick indexes a pending doc ──────────────────────────────────

async def test_tick_indexes_pending_doc(async_db_session, test_workspace):
    """KB-03: pending doc with a known TXT blob → one _tick → status 'indexed',
    chunk_count > 0, kb_chunks rows with non-null embedding.
    """
    from app.services.kb_ingest_worker import KnowledgeIngestWorker  # RED until 16-02

    kb_id, doc_id = await _seed_pending_doc(async_db_session, test_workspace.id)

    worker = KnowledgeIngestWorker()
    with patch(_EMBED_TARGET, new=AsyncMock(side_effect=lambda texts, *a, **k: [_fixed_vec(float(i + 1)) for i in range(len(texts))])):
        await worker._tick()

    row = (await async_db_session.execute(text("""
        SELECT status, chunk_count FROM kb_documents WHERE id = :id
    """), {"id": doc_id})).first()
    assert row is not None
    status, chunk_count = row
    assert status == "indexed", f"expected indexed, got {status}"
    assert int(chunk_count) > 0

    chunk_rows = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM kb_chunks
        WHERE document_id = :id AND embedding IS NOT NULL
    """), {"id": doc_id})).scalar_one()
    assert int(chunk_rows) == int(chunk_count)
    assert int(chunk_rows) > 0


# ─── KB-03 / Pitfall 8: re-index idempotency ───────────────────────────────────

async def test_reindex_is_idempotent(async_db_session, test_workspace):
    """KB-03 / Pitfall 8: indexing a doc, then re-indexing it, keeps chunk_count
    stable (delete-then-insert) — no doubled / duplicate chunks.
    """
    from app.services.kb_ingest_worker import KnowledgeIngestWorker  # RED until 16-02

    kb_id, doc_id = await _seed_pending_doc(async_db_session, test_workspace.id)

    embed = AsyncMock(side_effect=lambda texts, *a, **k: [_fixed_vec(float(i + 1)) for i in range(len(texts))])
    worker = KnowledgeIngestWorker()

    with patch(_EMBED_TARGET, new=embed):
        await worker._tick()

    first_count = (await async_db_session.execute(text(
        "SELECT chunk_count FROM kb_documents WHERE id = :id"
    ), {"id": doc_id})).scalar_one()
    first_chunks = (await async_db_session.execute(text(
        "SELECT COUNT(*) FROM kb_chunks WHERE document_id = :id"
    ), {"id": doc_id})).scalar_one()
    assert int(first_count) > 0
    assert int(first_chunks) == int(first_count)

    # Flip back to pending (the re-index trigger) and tick again.
    await async_db_session.execute(text(
        "UPDATE kb_documents SET status = 'pending' WHERE id = :id"
    ), {"id": doc_id})
    await async_db_session.commit()

    with patch(_EMBED_TARGET, new=embed):
        await worker._tick()

    second_count = (await async_db_session.execute(text(
        "SELECT chunk_count FROM kb_documents WHERE id = :id"
    ), {"id": doc_id})).scalar_one()
    second_chunks = (await async_db_session.execute(text(
        "SELECT COUNT(*) FROM kb_chunks WHERE document_id = :id"
    ), {"id": doc_id})).scalar_one()

    assert int(second_count) == int(first_count), "chunk_count must stay stable on re-index"
    assert int(second_chunks) == int(first_chunks), "re-index must not duplicate chunks"
