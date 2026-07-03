-- 046: conversations.status gains 'telegram_service' — Telegram login/auth-code
-- notifications from the service account (id 777000 / +42777) get their own inbox tab.
-- Idempotent: DROP CONSTRAINT IF EXISTS makes the auto-applier safe on re-run.
-- Preserves every existing value (including 'no_reply' from mig 045 and 'bot_ignored').

BEGIN;

DO $$ BEGIN
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply','telegram_service'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
