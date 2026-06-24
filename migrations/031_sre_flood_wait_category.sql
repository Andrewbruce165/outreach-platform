-- 031: extend sender_restriction_events.category CHECK to allow 'flood_wait' (WR-02).
--
-- Why: a FloodWait is Telegram's normal rate-limit backoff, NOT an account
-- restriction. The HARD-FloodWait queue path only PAUSES the queue — it does
-- NOT change senders.restriction_status, so pool_health is unaffected. Recording
-- it under category='restriction' polluted restriction analytics (the
-- `WHERE category='restriction'` filter from migration 030 counted it as a real
-- restriction) and triggered a pointless activity_slice scan for a non-restriction
-- event. queue.py now files flood_wait events under category='flood_wait'; this
-- migration widens the closed CHECK enum to accept that value.
--
-- Idempotent (drop+recreate the named constraint, mirrors 030). Migration 030 is
-- already committed and may be applied in prod, so this is a SEPARATE migration
-- rather than an edit to 030 (the applier never re-runs an already-recorded file).

ALTER TABLE sender_restriction_events DROP CONSTRAINT IF EXISTS sre_category_chk;
ALTER TABLE sender_restriction_events ADD CONSTRAINT sre_category_chk
    CHECK (category IN ('restriction', 'recipient_privacy', 'flood_wait'));
