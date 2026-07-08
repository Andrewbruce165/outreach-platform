-- migrations/059_drop_dead_limit_columns.sql
-- Phase 22 terminal cleanup (D-07 / D-04): drop the two superseded throttle columns.
--
--   * campaigns.max_new_dialogs_per_day (D-07) — the per-campaign daily new-dialog cap.
--     Superseded by the account-level grade budget resolved from the workspace ladder
--     (Phase 22). queue.py stopped reading it in 22-03; the campaign API surface is
--     removed in this plan (22-06).
--   * senders.rate_per_day (D-04) — the per-sender daily message cap. The daily throttle
--     is now the account grade new-chat budget (D-04); the sender API stopped exposing it
--     in 22-04 and queue.py's daily-message cap was removed in 22-03.
--
-- ORDERING (RESEARCH Pitfall 6): this migration runs LAST in Phase 22 (Wave 3). It MUST
-- land only after 22-03 (queue stopped reading both columns), 22-04 (sender API dropped
-- rate_per_day) and 22-05 (warmup uses the ladder). The migrations auto-apply at api boot
-- in lexical order; because no live code path reads either column after those plans, the
-- boot-time DROP cannot crash a still-referencing worker.
--
-- The ORM removal (app/models/__init__.py) ships in the SAME commit so create_all (the
-- test / fresh-DB schema builder) and the DB stay consistent — neither declares the columns.
--
-- Idempotent: DROP COLUMN IF EXISTS is a no-op on re-run / drift.
-- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory lock, fail-fast).
BEGIN;
ALTER TABLE campaigns DROP COLUMN IF EXISTS max_new_dialogs_per_day;
ALTER TABLE senders DROP COLUMN IF EXISTS rate_per_day;
COMMIT;
