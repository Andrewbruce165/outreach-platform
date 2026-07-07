-- Migration 055 — messages media columns (bridges the Phase 23 mig-053 gap).
--
-- Phase 24 Plan 24-06 (inbox fidelity) needs the `messages` table to carry the
-- concrete media metadata for a campaign file-opener so the inbox renders a media
-- bubble instead of a plain-text line. These columns were specced by Phase 23
-- (migration 053: message_type / file_name / mime_type / size_bytes) but Phase 23
-- was never executed (migrations jump 052 -> 054), so the columns are absent.
--
-- This bridge migration adds them idempotently so Plan 24-06's media INSERT works.
-- Fully guarded (ADD COLUMN IF NOT EXISTS + a DO-block constraint guard) so it is a
-- no-op if a later Phase 23 migration adds the same columns/constraint.
--
-- message_type CHECK set mirrors the Phase 23 spec: text|photo|video|voice|document.
-- Campaign attachments only ever classify to photo|video|document (never 'voice'),
-- all within the set. file_name/mime_type/size_bytes are nullable (text rows leave
-- them NULL; message_type falls back to the 'text' DEFAULT).

ALTER TABLE messages ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) NOT NULL DEFAULT 'text';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name VARCHAR(255);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS mime_type VARCHAR(255);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS size_bytes BIGINT;

DO $$
BEGIN
    ALTER TABLE messages ADD CONSTRAINT messages_message_type_check
        CHECK (message_type IN ('text','photo','video','voice','document'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
