-- Phase 16 fix: KB table `id` columns need a DB-level DEFAULT.
--
-- Migration 041 declared `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, but
-- init_db() runs Base.metadata.create_all BEFORE _apply_migrations(). create_all
-- built these tables from the ORM models, which carried only a client-side
-- `default=uuid.uuid4` (no server_default), so the physical columns got NO DB
-- default — and 041's `CREATE TABLE IF NOT EXISTS` was then a no-op.
--
-- Consequence: the KnowledgeIngestWorker inserts kb_chunks via raw text() SQL
-- that omits `id`, hitting `null value in column "id" ... violates not-null`
-- (NotNullViolation) and sending the document to status='failed'. The ORM models
-- now also carry server_default=gen_random_uuid() (fresh-DB path); this migration
-- repairs already-created tables. Same drift/fix pattern as sender_restriction_events.
--
-- Idempotent: ALTER COLUMN ... SET DEFAULT is naturally repeatable; IF EXISTS
-- guards a not-yet-created table (create_all makes these before migrations run).

ALTER TABLE IF EXISTS knowledge_bases ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE IF EXISTS kb_documents     ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE IF EXISTS kb_chunks        ALTER COLUMN id SET DEFAULT gen_random_uuid();
