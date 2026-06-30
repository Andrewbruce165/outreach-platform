"""Knowledge Bases router (Phase 16 — KB-01..KB-04).

Workspace-scoped KB API: CRUD, document upload (multipart, 202) + paste-text
(202), per-document list (D-10), re-index, delete, the D-09 aggregate
(DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE), a manual ``search_knowledge_base``
endpoint (the Search tab), and agent↔KB attach/detach + reverse list.

The upload/paste endpoints mirror the proven contacts.py 202-accepted idiom: they
record a ``pending`` kb_documents row and let the Wave-2 KnowledgeIngestWorker do
the extract/chunk/embed asynchronously (NEVER parse in the request handler).

All endpoints are under ``Depends(auth_dep)`` and filter by ``ctx.workspace_id``;
``_load_kb`` 404s on cross-workspace access.
"""

import logging
import os
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AIContext, KbDocument, KnowledgeBase
from app.schemas.knowledge_bases import (
    AgentForKbResponse,
    AgentKbAttachRequest,
    KbDocumentResponse,
    KbPasteTextRequest,
    KbSearchHit,
    KbSearchRequest,
    KbSearchResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge-bases"])

# Max upload size for a single KB document (20 MB). Documents bigger than this
# are rejected at the handler before any DB write.
MAX_DOC_BYTES = 20 * 1024 * 1024

# Supported upload file extensions → kb_documents.source_kind (matches the
# kb_ingest.extract_text dispatch: pdf|docx|txt|md|csv|text).
_EXT_TO_SOURCE_KIND = {
    "pdf": "pdf",
    "docx": "docx",
    "txt": "txt",
    "md": "md",
    "csv": "csv",
}

_ZERO_AGG = {"documents": 0, "indexed": 0, "processing": 0, "failed": 0, "storage_bytes": 0}


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _load_kb(db: AsyncSession, ctx: AuthCtx, kb_id: UUID) -> KnowledgeBase:
    """Workspace-scoped SELECT by id. 404 (KB_NOT_FOUND) if cross-tenant or missing."""
    result = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.workspace_id == ctx.workspace_id,
        )
    )
    kb = result.scalars().first()
    if kb is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_NOT_FOUND", "message": "Knowledge base not found"},
        )
    return kb


async def _load_doc(
    db: AsyncSession, ctx: AuthCtx, kb_id: UUID, doc_id: UUID
) -> KbDocument:
    """Workspace + KB-scoped document SELECT. 404 if cross-tenant / wrong KB / missing."""
    result = await db.execute(
        select(KbDocument).where(
            KbDocument.id == doc_id,
            KbDocument.kb_id == kb_id,
            KbDocument.workspace_id == ctx.workspace_id,
        )
    )
    doc = result.scalars().first()
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "KB_DOCUMENT_NOT_FOUND", "message": "Document not found"},
        )
    return doc


async def _aggregates_for_kbs(
    db: AsyncSession, workspace_id: UUID, kb_ids: list[UUID]
) -> dict[UUID, dict]:
    """One-pass COUNT(*) FILTER aggregate over kb_documents per KB.

    Mirrors the campaigns.py pool_health FILTER aggregate. Returns
    {kb_id: {documents, indexed, processing, failed, storage_bytes}}; KBs with
    no documents are absent and default to zeros at the call site.
    """
    if not kb_ids:
        return {}
    rows = (await db.execute(text("""
        SELECT kb_id,
               COUNT(*)                                              AS documents,
               COUNT(*) FILTER (WHERE status = 'indexed')            AS indexed,
               COUNT(*) FILTER (WHERE status = 'processing')         AS processing,
               COUNT(*) FILTER (WHERE status = 'failed')             AS failed,
               COALESCE(SUM(size_bytes), 0)                          AS storage_bytes
          FROM kb_documents
         WHERE workspace_id = :wid
           AND kb_id = ANY(:kb_ids)
         GROUP BY kb_id
    """), {"wid": str(workspace_id), "kb_ids": [str(k) for k in kb_ids]})).fetchall()
    return {
        row[0]: {
            "documents": int(row[1]),
            "indexed": int(row[2]),
            "processing": int(row[3]),
            "failed": int(row[4]),
            "storage_bytes": int(row[5]),
        }
        for row in rows
    }


def _derive_status(agg: dict) -> str:
    """D-09 KB status: failed > processing > indexed > empty."""
    if agg["failed"] > 0:
        return "failed"
    if agg["processing"] > 0:
        return "processing"
    if agg["indexed"] > 0:
        return "indexed"
    return "empty"


def _kb_to_response(kb: KnowledgeBase, agg: dict) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        source_kind=kb.source_kind,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        documents=agg["documents"],
        indexed=agg["indexed"],
        processing=agg["processing"],
        failed=agg["failed"],
        storage_bytes=agg["storage_bytes"],
        status=_derive_status(agg),
    )


def _doc_to_response(doc: KbDocument) -> KbDocumentResponse:
    return KbDocumentResponse(
        id=doc.id,
        kb_id=doc.kb_id,
        name=doc.name,
        source_kind=doc.source_kind,
        size_bytes=doc.size_bytes,
        status=doc.status,
        error=doc.error,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lstrip(".").lower()


# ─── KB CRUD ─────────────────────────────────────────────────────────────────


@router.get("")
async def list_knowledge_bases(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeBaseResponse]:
    """List workspace KBs with the D-09 aggregate per KB (newest-first)."""
    result = await db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.workspace_id == ctx.workspace_id)
        .order_by(KnowledgeBase.created_at.desc())
    )
    kbs = result.scalars().all()
    aggs = await _aggregates_for_kbs(db, ctx.workspace_id, [kb.id for kb in kbs])
    return [_kb_to_response(kb, aggs.get(kb.id, dict(_ZERO_AGG))) for kb in kbs]


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create a KB (source_kind='files'). 409 KB_NAME_CONFLICT on duplicate name."""
    name = payload.name.strip()
    existing = await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.workspace_id == ctx.workspace_id,
            KnowledgeBase.name == name,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail={"code": "KB_NAME_CONFLICT", "message": f"KB '{name}' already exists"},
        )
    kb = KnowledgeBase(
        workspace_id=ctx.workspace_id,
        name=name,
        description=payload.description,
        source_kind="files",
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    logger.info(f"[kb] created workspace={ctx.workspace_id} name='{name}' id={kb.id}")
    return _kb_to_response(kb, dict(_ZERO_AGG))


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """KB detail incl. the D-09 aggregate. 404 if cross-workspace."""
    kb = await _load_kb(db, ctx, kb_id)
    aggs = await _aggregates_for_kbs(db, ctx.workspace_id, [kb.id])
    return _kb_to_response(kb, aggs.get(kb.id, dict(_ZERO_AGG)))


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: UUID,
    payload: KnowledgeBaseUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Rename / edit description (Settings tab). 409 on duplicate name."""
    kb = await _load_kb(db, ctx, kb_id)
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name != kb.name:
            dup = await db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.workspace_id == ctx.workspace_id,
                    KnowledgeBase.name == new_name,
                )
            )
            if dup.scalars().first():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "KB_NAME_CONFLICT",
                            "message": f"KB '{new_name}' already exists"},
                )
        kb.name = new_name
    if payload.description is not None:
        kb.description = payload.description
    await db.commit()
    await db.refresh(kb)
    aggs = await _aggregates_for_kbs(db, ctx.workspace_id, [kb.id])
    return _kb_to_response(kb, aggs.get(kb.id, dict(_ZERO_AGG)))


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Delete a KB. FK cascade drops its documents, chunks and agent links."""
    kb = await _load_kb(db, ctx, kb_id)
    await db.delete(kb)
    await db.commit()
    logger.info(f"[kb] deleted workspace={ctx.workspace_id} id={kb_id}")
    return None


# ─── Documents (D-10) ────────────────────────────────────────────────────────


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> list[KbDocumentResponse]:
    """Per-document list (D-10), newest-first."""
    await _load_kb(db, ctx, kb_id)
    result = await db.execute(
        select(KbDocument)
        .where(
            KbDocument.kb_id == kb_id,
            KbDocument.workspace_id == ctx.workspace_id,
        )
        .order_by(KbDocument.created_at.desc())
    )
    return [_doc_to_response(d) for d in result.scalars().all()]


@router.post("/{kb_id}/documents", response_model=KbDocumentResponse, status_code=202)
async def upload_document(
    kb_id: UUID,
    file: UploadFile = File(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Multipart upload → records a pending kb_documents row (202 Accepted).

    The KnowledgeIngestWorker (Wave 2) claims status='pending' rows and does the
    extract/chunk/embed asynchronously — never parse in this handler (Pitfall).
    """
    await _load_kb(db, ctx, kb_id)
    raw = await file.read()
    if len(raw) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "FILE_TOO_LARGE", "message": f"Max {MAX_DOC_BYTES} bytes"},
        )
    ext = _ext_of(file.filename)
    source_kind = _EXT_TO_SOURCE_KIND.get(ext)
    if source_kind is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_FILE_TYPE",
                "message": "Supported: pdf, docx, txt, md, csv",
            },
        )
    doc = KbDocument(
        workspace_id=ctx.workspace_id,
        kb_id=kb_id,
        name=file.filename or f"document.{ext}",
        source_kind=source_kind,
        size_bytes=len(raw),
        status="pending",
        raw_content=raw,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info(
        f"[kb] upload workspace={ctx.workspace_id} kb={kb_id} doc={doc.id} "
        f"kind={source_kind} size={len(raw)}"
    )
    return _doc_to_response(doc)


@router.post("/{kb_id}/documents/paste", response_model=KbDocumentResponse, status_code=202)
async def paste_document(
    kb_id: UUID,
    payload: KbPasteTextRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Paste raw text → records a pending kb_documents row (source_kind='text', 202)."""
    await _load_kb(db, ctx, kb_id)
    encoded = payload.content.encode("utf-8")
    doc = KbDocument(
        workspace_id=ctx.workspace_id,
        kb_id=kb_id,
        name=payload.name.strip(),
        source_kind="text",
        size_bytes=len(encoded),
        status="pending",
        raw_content=encoded,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info(
        f"[kb] paste workspace={ctx.workspace_id} kb={kb_id} doc={doc.id} size={len(encoded)}"
    )
    return _doc_to_response(doc)


@router.post(
    "/{kb_id}/documents/{doc_id}/reindex",
    response_model=KbDocumentResponse,
    status_code=202,
)
async def reindex_document(
    kb_id: UUID,
    doc_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Set a doc back to pending so the worker re-runs (delete-then-insert is idempotent)."""
    doc = await _load_doc(db, ctx, kb_id, doc_id)
    doc.status = "pending"
    doc.error = None
    await db.commit()
    await db.refresh(doc)
    logger.info(f"[kb] reindex workspace={ctx.workspace_id} kb={kb_id} doc={doc_id}")
    return _doc_to_response(doc)


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    kb_id: UUID,
    doc_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document. FK cascade drops its chunks."""
    doc = await _load_doc(db, ctx, kb_id, doc_id)
    await db.delete(doc)
    await db.commit()
    logger.info(f"[kb] delete-doc workspace={ctx.workspace_id} kb={kb_id} doc={doc_id}")
    return None


# ─── Manual search (Search tab, KB-05 surface) ───────────────────────────────


@router.post("/{kb_id}/search", response_model=KbSearchResponse)
async def search_knowledge_base(
    kb_id: UUID,
    payload: KbSearchRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Manual test retrieval over this KB's chunks (cosine distance, Pitfall 4).

    Embeds the query, runs ``ORDER BY embedding <=> :qvec`` filtered by workspace
    + this KB, keeps hits within ``kb_search_max_distance``. Prefers the shared
    ``app.services.kb_search.kb_search`` helper when plan 16-04 has landed it;
    otherwise runs a thin self-contained vector query so this surface works now.
    """
    await _load_kb(db, ctx, kb_id)
    top_k = payload.top_k or settings.kb_search_top_k

    # Prefer the canonical shared helper (16-04 source of truth) when present.
    try:
        from app.services.kb_search import kb_search as _shared_kb_search
    except ImportError:
        _shared_kb_search = None

    if _shared_kb_search is not None:
        hits = await _shared_kb_search(
            db=db,
            workspace_id=ctx.workspace_id,
            kb_ids=[kb_id],
            query=payload.query,
            top_k=top_k,
            max_distance=settings.kb_search_max_distance,
        )
        return KbSearchResponse(results=[
            KbSearchHit(
                content=h["content"],
                document_id=h["document_id"],
                document_name=h.get("document_name"),
                distance=float(h["distance"]),
            )
            for h in hits
        ])

    # Self-contained fallback (16-04 not yet landed). Embed the query then cosine-rank.
    from app.services.kb_ingest import embed_texts

    qvecs = await embed_texts([payload.query], settings.openai_embedding_model)
    if not qvecs:
        return KbSearchResponse(results=[])
    qvec_literal = "[" + ",".join(repr(float(x)) for x in qvecs[0]) + "]"

    rows = (await db.execute(text("""
        SELECT c.content,
               c.document_id,
               d.name AS document_name,
               (c.embedding <=> CAST(:qvec AS vector)) AS distance
          FROM kb_chunks c
          JOIN kb_documents d ON d.id = c.document_id
         WHERE c.workspace_id = :wid
           AND c.kb_id = :kb
         ORDER BY c.embedding <=> CAST(:qvec AS vector)
         LIMIT :k
    """), {
        "qvec": qvec_literal,
        "wid": str(ctx.workspace_id),
        "kb": str(kb_id),
        "k": top_k,
    })).fetchall()

    results = [
        KbSearchHit(
            content=row[0],
            document_id=row[1],
            document_name=row[2],
            distance=float(row[3]),
        )
        for row in rows
        if float(row[3]) <= settings.kb_search_max_distance
    ]
    return KbSearchResponse(results=results)


# ─── Agent ↔ KB (M:N, KB-04) ─────────────────────────────────────────────────


async def _load_agent(db: AsyncSession, ctx: AuthCtx, agent_id: UUID) -> AIContext:
    """Workspace-scoped agent SELECT. 404 if cross-tenant / missing."""
    result = await db.execute(
        select(AIContext).where(
            AIContext.id == agent_id,
            AIContext.workspace_id == ctx.workspace_id,
        )
    )
    agent = result.scalars().first()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND", "message": "Agent not found"},
        )
    return agent


@router.get("/{kb_id}/agents")
async def list_agents_for_kb(
    kb_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> list[AgentForKbResponse]:
    """Reverse M:N: agents this KB is attached to (Agents tab)."""
    await _load_kb(db, ctx, kb_id)
    rows = (await db.execute(text("""
        SELECT a.id, a.name
          FROM agent_knowledge_bases akb
          JOIN ai_contexts a ON a.id = akb.agent_id
         WHERE akb.kb_id = :kb
           AND akb.workspace_id = :wid
         ORDER BY a.name
    """), {"kb": str(kb_id), "wid": str(ctx.workspace_id)})).fetchall()
    return [
        AgentForKbResponse(id=row[0], agent_id=row[0], agent_name=row[1])
        for row in rows
    ]


@router.post("/{kb_id}/agents", status_code=201)
async def attach_agent(
    kb_id: UUID,
    payload: AgentKbAttachRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Attach a workspace-owned agent to this KB. INSERT ... ON CONFLICT DO NOTHING."""
    await _load_kb(db, ctx, kb_id)
    await _load_agent(db, ctx, payload.agent_id)  # 404 if agent not in workspace
    await db.execute(text("""
        INSERT INTO agent_knowledge_bases (agent_id, kb_id, workspace_id)
        VALUES (:aid, :kb, :wid)
        ON CONFLICT (agent_id, kb_id) DO NOTHING
    """), {"aid": str(payload.agent_id), "kb": str(kb_id), "wid": str(ctx.workspace_id)})
    await db.commit()
    logger.info(
        f"[kb] attach workspace={ctx.workspace_id} kb={kb_id} agent={payload.agent_id}"
    )
    return {"attached": True, "kb_id": str(kb_id), "agent_id": str(payload.agent_id)}


@router.delete("/{kb_id}/agents/{agent_id}", status_code=204)
async def detach_agent(
    kb_id: UUID,
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Detach an agent from this KB (DELETE the M:N row)."""
    await _load_kb(db, ctx, kb_id)
    await db.execute(text("""
        DELETE FROM agent_knowledge_bases
         WHERE agent_id = :aid AND kb_id = :kb AND workspace_id = :wid
    """), {"aid": str(agent_id), "kb": str(kb_id), "wid": str(ctx.workspace_id)})
    await db.commit()
    logger.info(
        f"[kb] detach workspace={ctx.workspace_id} kb={kb_id} agent={agent_id}"
    )
    return None
