-- migrations/056_sender_grade_columns.sql
-- Phase 22 (account-level new-chat limit grades) — grade storage on senders.
--
-- Adds the durable per-account grade level (1..3) and the timer that the ladder
-- uses to decide when an account may step up (D-14). No behaviour change here:
-- nothing reads these columns until the Wave-2 feature plans (queue rewrite /
-- warmup budget / sender API). rate_per_day is intentionally LEFT IN PLACE — its
-- removal ships in plan 22-06 after all readers are rewritten (RESEARCH Pitfall 6).
--
-- Backfill (D-10/D-14): the grade timer must start at ACCOUNT CREATION, not at
-- migration time. The new column DEFAULT NOW() stamps every existing row with the
-- migration instant, so we pull level_updated_at back to created_at for rows the
-- default overshot. Re-run-safe: the WHERE guard is a no-op on the second pass.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + DO$$ duplicate_object CHECK + guarded
-- UPDATE — the auto-applier re-runs safely on drift. Auto-applied via
-- app/database.py::_apply_migrations (lexical order, advisory lock).
-- Fail-fast: api does NOT start if this migration raises.

BEGIN;

ALTER TABLE senders ADD COLUMN IF NOT EXISTS current_level INT NOT NULL DEFAULT 1;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS level_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- D-10/D-14: start the grade timer at account creation for pre-existing rows.
-- Only touches rows where the new DEFAULT NOW() overshot created_at; idempotent.
UPDATE senders
   SET level_updated_at = created_at
 WHERE created_at IS NOT NULL
   AND level_updated_at > created_at;

-- Guarded CHECK for the 1..3 grade range (swallow duplicate on re-run).
DO $$
BEGIN
    ALTER TABLE senders ADD CONSTRAINT senders_current_level_range CHECK (current_level BETWEEN 1 AND 3);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMIT;
