-- migrations/023_user_workspaces_unique.sql
-- 2026-05-26 hotfix (ui-data-missing incident).
--
-- Race condition fix: prior `_resolve_or_create_workspace` in app/utils/auth.py used a
-- "post-commit re-SELECT" pattern that did NOT prevent duplicates. The race surfaced
-- as 4 Workspace+UserWorkspace rows created within 5 milliseconds for one
-- supabase_user_id when Lovable fronted 4 parallel fetches against /api/v1/* on first
-- load. Result: JWT requests randomly land in one of 4 workspaces, UI sees empty data.
--
-- This migration adds the DB-level guarantee. The application code in auth.py must
-- also be rewritten to use INSERT ... ON CONFLICT (supabase_user_id) DO NOTHING +
-- re-SELECT so the race becomes a no-op at the DB layer instead of a crash.
--
-- Cleanup of pre-existing duplicates MUST run BEFORE this migration (DELETE FROM
-- workspaces WHERE id IN (...dupes)) — FK CASCADE handles user_workspaces.
--
-- Idempotent via DO block + duplicate_object exception swallow.

DO $$
BEGIN
    ALTER TABLE user_workspaces
      ADD CONSTRAINT user_workspaces_supabase_user_id_key
      UNIQUE (supabase_user_id);
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'user_workspaces_supabase_user_id_key already exists, skipping';
    WHEN duplicate_table THEN
        RAISE NOTICE 'user_workspaces_supabase_user_id_key already exists, skipping';
END$$;
