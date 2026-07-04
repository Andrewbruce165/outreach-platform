-- 049: Account Profile Management (Phase 20 — PROF-01 / Plan 20-01).
--
-- Next free migration number is 049. NB: the Phase-20 PLAN was authored when 046 was
-- the latest file and assumed slot 047; quick-task 260703-ssv subsequently landed
-- 047_message_queue_priority_default.sql and 048_sender_long_pause_until.sql, so this
-- migration is renumbered to 049 to avoid a numbering collision. Auto-applied at api
-- start by app/database.py::_apply_migrations in lexical order; this file MUST be
-- idempotent (ADD COLUMN IF NOT EXISTS) — the applier re-runs it on any schema drift
-- and the api fail-fasts (does not start) if a migration raises.
--
-- Adds the cached Telegram-profile columns to senders. NO backfill:
--   NULL           on the four nullable columns = "not yet cached from Telegram"
--   '{}'::jsonb    on profile_field_changed_at   = "no profile field has ever changed"
--
-- profile_field_changed_at is per-field cooldown STATE (not an audit log):
--   {"username": iso8601, "photo": iso8601, "name": iso8601, "bio": iso8601}
-- It is NOT NULL DEFAULT '{}'::jsonb because the ORM Sender.profile_field_changed_at
-- carries a matching server_default — create_all (test/fresh DB) builds the schema from
-- the ORM, so a NOT NULL column WITHOUT server_default would break raw INSERTs that omit
-- it (memory project-orm-default-vs-server-default-drift; mig 040/042 precedent).
--
-- No BEGIN/COMMIT wrapper — plain ADD COLUMN IF NOT EXISTS is atomic per statement
-- (matches 035_checker_post_batch_rest.sql).
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_username        VARCHAR(32) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_bio             VARCHAR(140) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_photo           BYTEA NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_photo_mime      VARCHAR(32) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS profile_field_changed_at JSONB NOT NULL DEFAULT '{}'::jsonb;
