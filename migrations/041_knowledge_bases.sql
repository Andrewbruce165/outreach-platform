-- migrations/041_knowledge_bases.sql — Phase 16 RAG KBs (D-05/D-06/D-07).
-- Idempotent: CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS — auto-applier re-runs safely.
-- Fail-fast: api does NOT start if this raises. Auto-applied via _apply_migrations.
--
-- NOTE: numbered 041 — slot 040 is taken by 040_warmup_sessions_defaults_drift.sql.
-- HNSW chosen over IVFFlat because it builds on an EMPTY table (this CREATE INDEX
-- runs before any chunk exists); IVFFlat needs training rows and cannot.
-- Open Q 1: pasted text stored in raw_content BYTEA (utf-8), discriminated by
-- source_kind='text'. Open Q 2: STORAGE = SUM(kb_documents.size_bytes).
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name          VARCHAR(150) NOT NULL,
    description   TEXT,
    source_kind   VARCHAR(20) NOT NULL DEFAULT 'files',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_workspace ON knowledge_bases (workspace_id);

CREATE TABLE IF NOT EXISTS kb_documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name          VARCHAR(255) NOT NULL,
    source_kind   VARCHAR(20) NOT NULL,        -- 'pdf'|'docx'|'txt'|'md'|'csv'|'text'
    size_bytes    BIGINT NOT NULL DEFAULT 0,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|processing|indexed|failed
    error         TEXT,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    raw_content   BYTEA,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kbdoc_kb_status ON kb_documents (kb_id, status);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id   UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    embedding     VECTOR(1536) NOT NULL,       -- D-06 text-embedding-3-small
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kbchunk_embedding_hnsw
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_kbchunk_ws_kb ON kb_chunks (workspace_id, kb_id);

CREATE TABLE IF NOT EXISTS agent_knowledge_bases (
    agent_id      UUID NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, kb_id)
);
CREATE INDEX IF NOT EXISTS idx_akb_kb ON agent_knowledge_bases (kb_id);

COMMIT;
