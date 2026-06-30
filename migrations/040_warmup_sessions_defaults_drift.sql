-- 040_warmup_sessions_defaults_drift.sql
--
-- Restore DB-level defaults on warmup_sessions.status / messages_sent.
--
-- Migration 005 created these columns as `NOT NULL DEFAULT 'active'` / `DEFAULT 0`,
-- but the table was later recreated by SQLAlchemy `Base.metadata.create_all`
-- (after the 2026-05-26 DROP SCHEMA incident). The ORM model used Python-side
-- `default=` (not `server_default=`), so create_all recreated the columns WITHOUT
-- a DB-level DEFAULT. The raw-SQL INSERT in WarmupScheduler._create_new_sessions
-- bypasses the ORM default, so a freshly-created session row inserted NULL into a
-- NOT NULL column → IntegrityError. (Surfaced once a spam_limited account joined
-- the pool and new mesh pairs needed creating.)
--
-- The code now sets status/messages_sent explicitly in the INSERT, and the ORM
-- model now declares server_default. This migration restores parity on the live
-- table so the schema matches migration 005's intent and no other path can hit
-- the same NULL again. Idempotent: SET DEFAULT is safe to re-run.

ALTER TABLE warmup_sessions ALTER COLUMN status        SET DEFAULT 'active';
ALTER TABLE warmup_sessions ALTER COLUMN messages_sent SET DEFAULT 0;
