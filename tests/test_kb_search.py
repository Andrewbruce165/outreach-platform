"""Phase 16 — kb_search vector query (Wave 0 RED).

Asserts the documented cosine-distance search behaviour Plan 16-04 will
implement: `search_knowledge_base` / `kb_search` embeds the query, runs the
pgvector cosine-distance query over the agent's attached KBs, returns the
nearest chunks ordered by distance, and respects top-K + a distance threshold.
Workspace isolation: it never returns chunks from another workspace.

Until `app.services.kb_search` lands, the deferred in-body import raises
ImportError → RED. Expected Wave-0 state.

Chunks are inserted with hand-crafted unit vectors of KNOWN ordering and the
query embedder is stubbed to a fixed query vector, so the expected cosine
ordering is deterministic without OpenAI. NB Wave 3: confirm the patched
embedder symbol + the kb_search signature when the module lands.

Test → requirement map:
- test_cosine_search_orders_by_distance → KB-05 (ordering + top-K + threshold)
- test_search_workspace_isolated        → KB-06 (no cross-workspace leak)
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

# Planned production symbol for the single-query embedder used by kb_search.
_EMBED_QUERY_TARGET = "app.services.kb_search.embed_query"


def _unit_vec(angle_slot1: float) -> list[float]:
    """2-D vector embedded in slot 0/1 of a 1536-dim vector (rest zero).

    angle_slot1 in [0,1]: higher = more aligned with slot 1. We use simple axis
    vectors so cosine distance ordering is trivially predictable.
    """
    v = [0.0] * 1536
    v[0] = 1.0 - angle_slot1
    v[1] = angle_slot1
    return v


def _vec_literal(v: list[float]) -> str:
    """pgvector text literal '[a,b,...]' for a raw-SQL INSERT."""
    return "[" + ",".join(str(x) for x in v) + "]"


async def _seed_kb_with_chunks(db, ws_id, chunks):
    """Create a KB + a document + chunks (content, vector). Returns kb_id."""
    kb_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO knowledge_bases (id, workspace_id, name)
        VALUES (:id, :ws, 'Search KB')
    """), {"id": kb_id, "ws": str(ws_id)})
    doc_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO kb_documents (id, workspace_id, kb_id, name, source_kind, status)
        VALUES (:id, :ws, :kb, 'searchdoc.txt', 'txt', 'indexed')
    """), {"id": doc_id, "ws": str(ws_id), "kb": kb_id})
    for i, (content, vec) in enumerate(chunks):
        await db.execute(text("""
            INSERT INTO kb_chunks
                (workspace_id, kb_id, document_id, chunk_index, content, embedding)
            VALUES (:ws, :kb, :doc, :idx, :content, CAST(:emb AS vector))
        """), {
            "ws": str(ws_id), "kb": kb_id, "doc": doc_id,
            "idx": i, "content": content, "emb": _vec_literal(vec),
        })
    await db.commit()
    return kb_id


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_kb_state(async_db_session):
    yield
    await async_db_session.execute(text("DELETE FROM kb_chunks"))
    await async_db_session.execute(text("DELETE FROM kb_documents"))
    await async_db_session.execute(text("DELETE FROM agent_knowledge_bases"))
    await async_db_session.execute(text("DELETE FROM knowledge_bases"))
    await async_db_session.commit()


# ─── KB-05: cosine ordering + top-K + threshold ───────────────────────────────

async def test_cosine_search_orders_by_distance(async_db_session, test_workspace):
    """KB-05: kb_search returns chunks ordered by cosine distance to the query,
    honours top_k, and drops chunks beyond the distance threshold.
    """
    from app.services.kb_search import kb_search  # RED until 16-04

    # Query vector points fully at slot 1 (angle=1.0). Closest chunk should be
    # the one with the highest slot-1 alignment.
    query_vec = _unit_vec(1.0)
    chunks = [
        ("near",   _unit_vec(1.0)),    # distance ~0 (identical direction)
        ("mid",    _unit_vec(0.6)),    # moderate
        ("far",    _unit_vec(0.0)),    # orthogonal → distance ~1.0 (beyond threshold)
    ]
    kb_id = await _seed_kb_with_chunks(async_db_session, test_workspace.id, chunks)

    with patch(_EMBED_QUERY_TARGET, new=AsyncMock(return_value=query_vec)):
        hits = await kb_search(
            db=async_db_session,
            workspace_id=test_workspace.id,
            kb_ids=[uuid.UUID(kb_id)],
            query="anything",
            top_k=2,
            max_distance=0.55,
        )

    contents = [h["content"] for h in hits]
    # top_k=2 + threshold drops 'far' (orthogonal, distance ~1.0 > 0.55).
    assert "near" in contents
    assert "far" not in contents
    # Ordered nearest-first.
    assert contents[0] == "near"
    assert len(hits) <= 2


# ─── KB-06: workspace isolation ────────────────────────────────────────────────

async def test_search_workspace_isolated(async_db_session, test_workspace):
    """KB-06: searching with workspace A's id returns zero chunks from a KB that
    lives in workspace B, even with identical vectors.
    """
    from app.services.kb_search import kb_search  # RED until 16-04

    query_vec = _unit_vec(1.0)

    # Workspace A KB.
    kb_a = await _seed_kb_with_chunks(
        async_db_session, test_workspace.id,
        [("a-secret", _unit_vec(1.0))],
    )

    # Workspace B + its KB with an identical-direction chunk.
    ws_b = uuid.uuid4()
    await async_db_session.execute(text(
        "INSERT INTO workspaces (id, name) VALUES (:id, 'WS B search')"
    ), {"id": str(ws_b)})
    await async_db_session.commit()
    kb_b = await _seed_kb_with_chunks(
        async_db_session, ws_b,
        [("b-secret", _unit_vec(1.0))],
    )

    with patch(_EMBED_QUERY_TARGET, new=AsyncMock(return_value=query_vec)):
        hits = await kb_search(
            db=async_db_session,
            workspace_id=test_workspace.id,           # scope to workspace A
            kb_ids=[uuid.UUID(kb_a), uuid.UUID(kb_b)],  # even if B's id is passed
            query="anything",
            top_k=10,
            max_distance=0.55,
        )

    contents = [h["content"] for h in hits]
    assert "a-secret" in contents
    assert "b-secret" not in contents, "search must never leak another workspace's chunks"
