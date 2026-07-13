-- 061: conversations gains 'lead_pending' status + pre_lead_status column.
-- AI auto-detection (mark_as_lead tool) and the manual "Mark lead" button now
-- land in 'lead_pending' instead of 'lead' directly; a human then Confirms
-- (-> 'lead') or Dismisses (-> pre_lead_status, the status that was active
-- right before detection) from the inbox "Lead detected" banner.
-- Idempotent: safe to re-run via the auto-applier.

BEGIN;

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pre_lead_status VARCHAR(20);

DO $$ BEGIN
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','lead_pending','handoff','finished','bot_ignored','no_reply','telegram_service'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
