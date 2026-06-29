-- migrations/038_warmup_settings.sql
-- Per-workspace warmup control + content settings (Phase 15, WARM-06 / WARM-10).
-- One row per workspace holds the master on/off switch plus the configurable
-- warmup-content object (topics / system_prompt / language / tone).
--
-- EXPLICIT OPT-IN (LOCKED DECISION — research Open Question 3): `enabled`
-- DEFAULT FALSE and this migration deliberately does NOT seed any existing
-- (live) workspace to TRUE — there is NO seed/insert statement here at all.
-- This is a behaviour change: warmup stays OFF until the user flips the master
-- toggle on in the new Warmup tab. Default-FALSE also means new tenants never
-- auto-warm (consistent with D-13: accounts are not auto-enrolled).
--
-- Content defaults (D-10): absence of a row OR empty `topics` / NULL
-- `system_prompt` resolves in code to the hard-coded WARMUP_TOPICS (24 RU topics)
-- + WARMUP_SYSTEM_PROMPT, so existing dialog content is byte-identical when
-- nothing is configured. The migration only stores overrides; it seeds nothing.
--
-- NOTE: numbered 038 (not 037) — slot 037 is taken by 037_campaign_prompt_presets.sql.
-- Idempotent: CREATE TABLE IF NOT EXISTS — auto-applier re-runs safely on drift.
-- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory lock).
-- Fail-fast: api does NOT start if this migration raises.

BEGIN;

CREATE TABLE IF NOT EXISTS warmup_settings (
    workspace_id   UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,           -- D-06 master switch (default OFF: explicit opt-in, no live-workspace seed)
    topics         JSONB   NOT NULL DEFAULT '[]'::jsonb,     -- D-10 empty = use code default WARMUP_TOPICS
    system_prompt  TEXT,                                      -- D-10 NULL = use code default WARMUP_SYSTEM_PROMPT
    language       TEXT    NOT NULL DEFAULT 'ru',             -- D-10 content language
    tone           TEXT,                                      -- D-10 optional tone override
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
