-- migrations/_schema_migrations.sql
-- Bootstrap migration for the auto-applier (Task C of anti-drift hotfix).
-- Filename starts with `_` so it sorts BEFORE all numbered migrations and
-- always runs first.
--
-- Idempotent — safe to apply on every api start.
--
-- This file is NOT tracked in schema_migrations itself (it bootstraps the
-- tracking table; recording itself would be a chicken-and-egg).

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sha256      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON schema_migrations(applied_at);
