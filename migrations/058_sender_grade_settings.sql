-- migrations/058_sender_grade_settings.sql
-- Phase 22 (account-level new-chat limit grades) — per-workspace grade ladder.
--
-- One row per workspace holds the configurable 3-level new-chat ladder (D-16):
-- level 1 → level1_chats_per_day new chats/day, promote after level1_step_days;
-- level 2 → level2_chats_per_day, promote after level2_step_days; level 3 →
-- level3_chats_per_day and is PERMANENT (D-17: no level-3 step, top of ladder).
--
-- Code-defaults (D-16): the ABSENCE of a row resolves in app/services/grade_ladder.py
-- to LADDER_DEFAULTS = 5/30, 9/30, 13. This migration therefore seeds NOTHING —
-- an unconfigured workspace uses the code-default ladder, byte-identical behaviour.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS — auto-applier re-runs safely on drift.
-- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory
-- lock). Fail-fast: api does NOT start if this migration raises.

BEGIN;

CREATE TABLE IF NOT EXISTS sender_grade_settings (
    workspace_id        UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    level1_chats_per_day INT NOT NULL DEFAULT 5,
    level1_step_days     INT NOT NULL DEFAULT 30,
    level2_chats_per_day INT NOT NULL DEFAULT 9,
    level2_step_days     INT NOT NULL DEFAULT 30,
    level3_chats_per_day INT NOT NULL DEFAULT 13,   -- D-17: level 3 permanent, no step
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
