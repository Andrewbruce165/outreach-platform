"""KB retrieval — cosine-distance search over the chunks of attached KBs.

Phase 16 plan 16-04 (KB-05 / KB-06). This is the single canonical retrieval
helper shared by two callers:

  1. ``app/routers/knowledge_bases.py`` — the manual ``POST /{kb_id}/search``
     test surface (passes a single-element ``kb_ids=[kb_id]``).
  2. ``app/services/ai_engine.py`` — the ``search_knowledge_base`` data-tool
     (passes the union of KBs attached to the conversation's agent).

Design notes
------------
* Pitfall 4: pgvector ``<=>`` is COSINE DISTANCE — lower = better. We ORDER BY
  ascending and keep hits with ``distance <= max_distance``. Never invert it to
  a similarity score.
* KB-06 (workspace isolation): the WHERE clause filters BOTH ``workspace_id``
  AND ``kb_id IN (...)`` (defence-in-depth) — a workspace can never see another
  workspace's chunks even if a foreign ``kb_id`` is passed in by accident.
* ``embed_query`` is the single-query embedder. It is a *module-level* function
  so tests monkeypatch ``app.services.kb_search.embed_query`` with a fixed
  vector and no OpenAI call is made. It wraps ``kb_ingest.embed_texts`` so prod
  uses the same batched AsyncOpenAI client / embedding model as ingest (one
  pool, one model — symmetry guarantees query and chunk vectors are comparable).
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AgentKnowledgeBase, KbChunk, KbDocument
from app.services import kb_ingest

logger = logging.getLogger(__name__)

settings = get_settings()


async def embed_query(query: str) -> list[float]:
    """Embed a single search query into a 1536-dim vector.

    Delegates to :func:`app.services.kb_ingest.embed_texts` (same AsyncOpenAI
    client + embedding model as ingest, so the query vector is comparable to the
    stored chunk vectors). Returns the first (only) vector.

    NB: monkeypatch THIS symbol in tests (``app.services.kb_search.embed_query``)
    to inject a deterministic query vector without hitting OpenAI.
    """
    vecs = await kb_ingest.embed_texts([query], settings.openai_embedding_model)
    return vecs[0]


async def attached_kb_ids(
    db: AsyncSession,
    agent_id,
) -> tuple[UUID | None, list[UUID]]:
    """Resolve the workspace_id + the list of KB ids attached to ``agent_id``.

    Reads ``agent_knowledge_bases`` (the D-07 M:N through-table). The workspace
    is derived from the attach rows themselves, so callers that don't already
    know the agent's workspace (e.g. the ai_engine legacy ``get_context`` path)
    still get a workspace-isolated search.

    Returns ``(workspace_id, kb_ids)``. When the agent has zero attached KBs:
    ``(None, [])``.
    """
    if not agent_id:
        return None, []

    rows = (
        await db.execute(
            select(
                AgentKnowledgeBase.kb_id,
                AgentKnowledgeBase.workspace_id,
            ).where(AgentKnowledgeBase.agent_id == agent_id)
        )
    ).all()

    if not rows:
        return None, []

    workspace_id = rows[0].workspace_id
    kb_ids = [r.kb_id for r in rows]
    return workspace_id, kb_ids


async def kb_search(
    db: AsyncSession,
    workspace_id,
    kb_ids: list,
    query: str,
    top_k: int | None = None,
    max_distance: float | None = None,
) -> list[dict]:
    """Cosine-distance search over the chunks of ``kb_ids`` within ``workspace_id``.

    Embeds ``query`` (via the patchable :func:`embed_query`), runs the pgvector
    cosine-distance query ordered nearest-first, caps at ``top_k`` and drops any
    hit beyond ``max_distance``.

    Args:
        db: async session.
        workspace_id: the calling workspace — chunks are filtered to it (KB-06).
        kb_ids: the KBs to search (typically the agent's attached set, or a single
            KB for the manual-search endpoint). Empty → returns ``[]`` with NO
            embed / DB call.
        query: natural-language query.
        top_k: max hits (defaults to ``settings.kb_search_top_k``).
        max_distance: cosine-distance ceiling, lower = closer (defaults to
            ``settings.kb_search_max_distance``). Pitfall 4: this is a DISTANCE,
            not a similarity — keep hits with ``distance <= max_distance``.

    Returns:
        ``[{"content", "document_id", "document_name", "distance"}]`` ordered
        nearest-first, length ≤ top_k.
    """
    if not kb_ids:
        # Zero attached KBs — no embed, no query (KB-05 fast path).
        return []

    limit = top_k or settings.kb_search_top_k
    ceiling = max_distance if max_distance is not None else settings.kb_search_max_distance

    query_vec = await embed_query(query)

    distance = KbChunk.embedding.cosine_distance(query_vec)
    stmt = (
        select(
            KbChunk.content,
            KbChunk.document_id,
            KbDocument.name.label("document_name"),
            distance.label("distance"),
        )
        # LEFT JOIN so a chunk whose document row is gone still surfaces (name=None).
        .outerjoin(KbDocument, KbDocument.id == KbChunk.document_id)
        .where(
            KbChunk.workspace_id == workspace_id,   # KB-06 isolation (defence-in-depth)
            KbChunk.kb_id.in_(kb_ids),
        )
        .order_by(distance)                          # ascending — nearest first
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()

    # Pitfall 4: distance, lower = better. Keep hits within the ceiling.
    return [
        {
            "content": r.content,
            "document_id": str(r.document_id),
            "document_name": r.document_name,
            "distance": float(r.distance),
        }
        for r in rows
        if float(r.distance) <= ceiling
    ]
