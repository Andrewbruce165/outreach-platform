-- migrations/027_folders_workspace_name_unique.sql
-- Schema-drift fix (folders): the UNIQUE (workspace_id, name) constraint is
-- declared inline in 013_phase2.sql's `CREATE TABLE IF NOT EXISTS folders`,
-- but at startup app/database.py::init_db runs Base.metadata.create_all FIRST
-- (creating `folders` from the ORM, which has no UniqueConstraint), so 013's
-- CREATE TABLE is a no-op and the inline constraint never lands. The 019
-- drift-fix batch covered contacts/ai_contexts/campaigns/senders uniqueness but
-- MISSED folders. Verified absent in prod (no unique constraint/index on
-- folders(workspace_id, name) as of 2026-06-19).
--
-- Without it:
--   * folders.get_or_create_by_name() does INSERT ... ON CONFLICT
--     (workspace_id, name) → InvalidColumnReferenceError at runtime (CSV import
--     and push-by-folder_name paths crash).
--   * Duplicate folder names per workspace are silently allowed.
--
-- Idempotent: DO block swallows duplicate_object/duplicate_table so the applier
-- can re-run safely. Prod has 0 duplicate (workspace_id, name) rows, so the
-- constraint installs cleanly; if dupes ever exist this fails fast (correct).

DO $$
BEGIN
    ALTER TABLE folders
      ADD CONSTRAINT folders_workspace_name_unique
      UNIQUE (workspace_id, name);
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'folders_workspace_name_unique already exists, skipping';
    WHEN duplicate_table THEN
        RAISE NOTICE 'folders_workspace_name_unique already exists, skipping';
END$$;
