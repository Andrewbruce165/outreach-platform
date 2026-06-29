-- migrations/037_campaign_prompt_presets.sql
-- Prompt template v2: preset-driven core_directive (multi-tenant).
-- Adds 4 nullable TEXT columns to campaigns so the system prompt can render
-- objective / disclosure-policy / agent-authority from preset libraries
-- (resolved in app/services/ai_engine.py like _TONE_LINES), plus an optional
-- per-campaign few-shot override.
--   objective_preset   — which _OBJECTIVE_LINES entry (book_call, qualify, …; NULL→primary_goal)
--   disclosure_preset  — which _DISCLOSURE_LINES entry (NULL→reveal_nothing default in engine)
--   authority_preset   — which _AUTHORITY_LINES entry (NULL→handoff_only default in engine)
--   style_examples     — optional campaign-language few-shot block; NULL→static both-language fallback
-- All nullable, no DEFAULT: existing campaigns stay NULL and the engine applies
-- the safe defaults, reproducing the prior call-booking text. Allowed values are
-- enforced at the API layer (Literal enums in schemas), NOT via DB CHECK — mirrors
-- how primary_goal / max_new_dialogs are validated.
-- Idempotent: ADD COLUMN IF NOT EXISTS — auto-applier re-runs safely on any drift.
-- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory lock).
-- Fail-fast: api does NOT start if this migration raises.

BEGIN;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS objective_preset  TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS disclosure_preset TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS authority_preset  TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS style_examples    TEXT;
COMMIT;
