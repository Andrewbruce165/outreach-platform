-- 062: conversations.status gains 'spambot' — a per-sender live chat with the
-- official Telegram @SpamBot (id 178220800), launched from the account card.
-- This conversation is populated in both directions (manager sends via the reused
-- send endpoint; @SpamBot replies persisted by the listener) but is EXCLUDED from
-- the normal Inbox list, so it never pollutes real contact conversations.
-- Idempotent: DROP CONSTRAINT IF EXISTS makes the auto-applier safe on re-run.
-- Preserves every existing value (incl. 'lead_pending' from mig 061 and
-- 'telegram_service' from mig 046).

BEGIN;

DO $$ BEGIN
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','lead_pending','handoff','finished','bot_ignored','no_reply','telegram_service','spambot'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
