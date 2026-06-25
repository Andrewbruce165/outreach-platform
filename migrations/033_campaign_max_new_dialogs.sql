-- migrations/033_campaign_max_new_dialogs.sql
-- Phase 12 (NDLG-01): per-campaign daily new-dialog limit.
-- Adds: campaigns.max_new_dialogs_per_day INTEGER NOT NULL DEFAULT 50.
-- Purpose: explicit, configurable daily cap on NEW cold dialogs per campaign
--          (the queue filter reads it; the API schemas expose it).
-- Idempotent: ADD COLUMN IF NOT EXISTS — auto-applier re-runs safely on any drift.
-- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory lock).
-- Fail-fast: api does NOT start if this migration raises.
-- D-11: DEFAULT 50 applies to ALL existing campaigns INCLUDING running ones —
--       ADD COLUMN ... DEFAULT 50 sets every existing row to 50. Running campaigns
--       may slow under the new cap, which is the intended safety effect. NO backfill
--       to a higher value (no UPDATE statement here).
-- D-12: ge=1/le=100 bounds are enforced at the API layer (plan 12-03), consistent
--       with how sender rate caps are API-enforced. NO DB CHECK constraint here.

BEGIN;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_new_dialogs_per_day INTEGER NOT NULL DEFAULT 50;
COMMIT;
