"""Phase 16 — Knowledge Base API schemas (KB-01..KB-04).

Pydantic v2 request/response models for the workspace-scoped KB surface:
KB CRUD + the D-09 aggregate (DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE),
the D-10 per-document list, document upload/paste, manual search, and the
agent↔KB M:N attach/detach + reverse list.

No business logic lives here — the router computes the aggregate and derives
``status``. ``model_config = ConfigDict(from_attributes=True)`` mirrors the
existing schema modules (e.g. SenderResponse) so response models can be built
straight from ORM rows when convenient.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─── KB CRUD ─────────────────────────────────────────────────────────────────


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = None


class KnowledgeBaseResponse(BaseModel):
    """KB list/detail row. Carries the D-09 aggregate inline.

    ``status`` is derived by the router: ``failed`` when any doc failed, else
    ``processing`` when any doc is processing, else ``indexed`` when ≥1 doc is
    indexed, else ``empty``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: Optional[str] = None
    source_kind: str
    created_at: datetime
    updated_at: datetime
    # D-09 aggregate over kb_documents.
    documents: int = 0
    indexed: int = 0
    processing: int = 0
    failed: int = 0
    storage_bytes: int = 0
    status: str = "empty"


class KnowledgeBaseListResponse(BaseModel):
    items: List[KnowledgeBaseResponse]


# ─── Documents (D-10 per-doc) ────────────────────────────────────────────────


class KbDocumentResponse(BaseModel):
    """D-10 per-document list row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    name: str
    source_kind: str
    size_bytes: int
    status: str
    error: Optional[str] = None
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime


class KbDocumentListResponse(BaseModel):
    items: List[KbDocumentResponse]


class KbPasteTextRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


# ─── Manual search (Search tab) ──────────────────────────────────────────────


class KbSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: Optional[int] = None


class KbSearchHit(BaseModel):
    content: str
    document_id: UUID
    document_name: Optional[str] = None
    distance: float


class KbSearchResponse(BaseModel):
    results: List[KbSearchHit]


# ─── Agent ↔ KB (M:N, KB-04) ─────────────────────────────────────────────────


class AgentKbAttachRequest(BaseModel):
    agent_id: UUID


class AgentForKbResponse(BaseModel):
    """Reverse M:N row — an agent this KB is attached to (Agents tab).

    The test consumes ``id`` (the agent id) directly; ``agent_id`` is kept as an
    explicit alias-free duplicate for clarity, both carry the same value.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    agent_name: str


class AgentForKbListResponse(BaseModel):
    items: List[AgentForKbResponse]
