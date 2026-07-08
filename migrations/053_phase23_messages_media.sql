-- Phase 23 (D-20/D-21): extend messages for edit/delete/file-send + incoming media.
-- messages has NO ORM model (raw-SQL only, created by 017) — the DB DEFAULT below is the
-- SOLE source of message_type's default. Do NOT add a Message ORM model (would re-introduce
-- the ORM default= vs server_default= drift the codebase fought in migs 040/042).

-- 1. message_type: NOT NULL DEFAULT 'text' backfills every existing row to 'text'.
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) NOT NULL DEFAULT 'text';

-- 2. CHECK constraint (idempotent via duplicate_object guard — ADD CONSTRAINT has no IF NOT EXISTS).
--    Value set locked by planner (research OQ1): text | photo | video | voice | document.
--    NO generic 'file' — the listener's voice branch maps to 'voice'.
DO $$ BEGIN
    ALTER TABLE messages
        ADD CONSTRAINT messages_message_type_check
        CHECK (message_type IN ('text','photo','video','voice','document'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 3. Media metadata (nullable — only set for file/media bubbles).
ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name  VARCHAR(255);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS mime_type  VARCHAR(255);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS size_bytes BIGINT;

-- 4. Edit marker (D-07). NULL = never edited.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;

-- 5. Relax message_text NOT NULL for file bubbles without text (D-20).
--    Idempotent: DROP NOT NULL on an already-nullable column is a harmless no-op.
--    Safe: every existing INSERT path (send, listener, warmup) always writes text.
ALTER TABLE messages ALTER COLUMN message_text DROP NOT NULL;
