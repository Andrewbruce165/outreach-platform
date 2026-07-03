-- migrations/045_follow_up.sql
-- Phase 19 (No Reply Follow-Up and Auto-Finish): schema foundation.
--
-- D-01: conversations.status gains the value 'no_reply' — hung on every contact
--       we messaged and are still waiting on. Extends the CHECK set (does NOT drop
--       'bot_ignored', added in migration 017). New legal set is EXACTLY:
--       ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply').
-- D-03/D-04/D-08: conversations.pings_sent INTEGER NOT NULL DEFAULT 0 — follow-up
--       state counter (how many pings this conversation has already received).
-- D-08: campaigns.follow_up_max_pings INTEGER NOT NULL DEFAULT 2 — max follow-up pings.
-- D-12: campaigns gains follow_up_enabled (default false), follow_up_interval_hours
--       (default 24), auto_finish_hours (default 72). All NOT NULL with DEFAULT so
--       existing rows — INCLUDING running campaigns — get safe values with no backfill
--       (mirrors max_new_dialogs_per_day, D-11/D-15).
--
-- Bounds (interval 4–168h, max_pings 1–5, auto_finish 24–720h) are enforced at the
-- API layer (Pydantic), NOT via a DB CHECK — matches the recontact_min_age_days /
-- max_new_dialogs_per_day precedent.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT inside a DO $$ EXCEPTION
-- WHEN duplicate_object $$ block + idempotent column adds. Transaction-safe — the
-- CHECK is rebuilt via constraint drop/add (an enum value-add cannot run inside a
-- transaction, so a VARCHAR+CHECK is used instead). Auto-applied via
-- app/database.py::_apply_migrations (lexical order, advisory lock). Fail-fast: api
-- does NOT start if this migration raises.

BEGIN;

-- 1. Extend conversations.status CHECK to add 'no_reply' (preserving 'bot_ignored').
DO $$ BEGIN
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 2. Follow-up state counter on conversations.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pings_sent INTEGER NOT NULL DEFAULT 0;

-- 3. Campaign follow-up / auto-finish columns (all NOT NULL + DEFAULT, no backfill).
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS follow_up_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS follow_up_interval_hours INTEGER NOT NULL DEFAULT 24;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS follow_up_max_pings INTEGER NOT NULL DEFAULT 2;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS auto_finish_hours INTEGER NOT NULL DEFAULT 72;

COMMIT;
