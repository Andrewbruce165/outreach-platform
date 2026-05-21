-- migrations/014_phase2_1_hardening.sql
-- Phase 02.1: Multi-tenant Worker Hardening
-- Closes: CR-05 (reauth flow IntegrityError) + WR-02 (sender.slug global UNIQUE leak)
-- Changes:
--   1. DROP global UNIQUE on senders.slug → CREATE UNIQUE INDEX (workspace_id, slug)
--   2. ADD onboarding_sessions.original_sender_id (nullable FK on senders)
-- БД должна быть совместима с idempotent изменениями (Phase 1 D-01).
-- Все операторы идемпотентны (IF NOT EXISTS / IF EXISTS).

BEGIN;

-- ── 1. Sender.slug: global UNIQUE → per-workspace UNIQUE (WR-02) ────────────
-- Legacy constraint name from initial schema: senders_slug_key (Postgres auto-name
-- от `slug VARCHAR(50) UNIQUE` в исходной таблице senders).
-- В тестовой/чистой БД constraint может отсутствовать — IF EXISTS защищает.
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_slug_key;

-- Также убираем возможный legacy unique index (на случай если schema создавалась
-- через SQLAlchemy Base.metadata.create_all с unique=True — там имя индекса другое).
DROP INDEX IF EXISTS senders_slug_key;
DROP INDEX IF EXISTS ix_senders_slug;

-- Создаём per-workspace UNIQUE — теперь sender-john может существовать в каждом workspace.
-- Это И UNIQUE constraint в пределах workspace, И обычный index на slug для быстрого lookup.
CREATE UNIQUE INDEX IF NOT EXISTS idx_senders_workspace_slug
    ON senders(workspace_id, slug);

-- ── 2. onboarding_sessions.original_sender_id (CR-05) ──────────────────────
-- Маркер reauth-flow: если NOT NULL → verify-code/verify-2fa/_wait_for_qr должны
-- UPDATE'ить existing sender, не INSERT'ить нового. ON DELETE CASCADE — если
-- sender удалён пока шёл reauth, onboarding-сессия исчезнет вместе с ним.
ALTER TABLE onboarding_sessions
    ADD COLUMN IF NOT EXISTS original_sender_id UUID
        REFERENCES senders(id) ON DELETE CASCADE;

-- Partial index для быстрых reauth-lookup'ов (большинство сессий NULL — это обычный onboarding).
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_original_sender_id
    ON onboarding_sessions(original_sender_id)
    WHERE original_sender_id IS NOT NULL;

COMMIT;
