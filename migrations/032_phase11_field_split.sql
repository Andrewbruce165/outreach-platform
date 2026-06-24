-- migrations/032_phase11_field_split.sql
-- Phase 11: Agent/Campaign Field Split — single-source-of-truth per field.
-- Adds: tone_preset, response_speed, response_delay_seconds on ai_contexts.
--       dialogue_flow, arguments_facts, campaign_rules on campaigns.
-- Backfills: voice_baseline → tone_preset (Playful → Casual) before DROP.
--            success_criteria → lead_trigger_hint (concat, no data loss) before DROP.
-- Drops legacy: tone (JSONB slider), tone_of_voice (TEXT), voice_baseline (VARCHAR)
--               on ai_contexts. success_criteria on campaigns.
-- Idempotent: ADD COLUMN IF NOT EXISTS, DROP COLUMN IF EXISTS, DROP CONSTRAINT IF EXISTS.
--             UPDATE backfills guarded by WHERE … IS NULL / position() = 0.
-- Fail-fast: api does NOT start if this migration raises (auto-applier design).
-- D-02 note: tone slider (JSONB formal/warm/brief values) and tone_of_voice free-text
--            are intentionally discarded — no mapping exists for the new typed enum.
--            Only voice_baseline→tone_preset is losslessly backfilled.

BEGIN;

-- ── 1. ADD new columns on ai_contexts ─────────────────────────────────────────

ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS tone_preset VARCHAR(20);
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS response_speed VARCHAR(20);
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS response_delay_seconds INTEGER;

-- ── 2. ADD CHECK constraints (idempotent: DROP IF EXISTS before ADD) ───────────

ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_tone_preset_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_tone_preset_check
    CHECK (tone_preset IS NULL OR tone_preset IN ('Friendly','Professional','Direct','Casual'));

ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_response_speed_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_response_speed_check
    CHECK (response_speed IS NULL OR response_speed IN ('instant','human','slow','manual'));

-- ── 3. ADD new columns on campaigns ──────────────────────────────────────────

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS dialogue_flow JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS arguments_facts TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS campaign_rules TEXT;

-- ── 4. DATA-MIGRATE BEFORE DROP (strict Pitfall 4 order) ─────────────────────

-- 4a. Backfill tone_preset from voice_baseline (before dropping voice_baseline).
--     Playful → Casual (new enum has no Playful); Professional/Friendly map 1-to-1.
--     Guard: only rows where tone_preset IS NULL and voice_baseline IS NOT NULL.
--     Re-run safe: guard prevents double-application.
UPDATE ai_contexts
   SET tone_preset = CASE voice_baseline
       WHEN 'Professional' THEN 'Professional'
       WHEN 'Friendly'     THEN 'Friendly'
       WHEN 'Playful'      THEN 'Casual'
       ELSE NULL
   END
 WHERE tone_preset IS NULL
   AND voice_baseline IS NOT NULL;

-- 4b. Merge success_criteria into lead_trigger_hint before dropping success_criteria.
--     Three sub-cases:
--       i.  lead_trigger_hint IS NULL/empty → set to success_criteria directly.
--       ii. Both present and success_criteria not already in lead_trigger_hint →
--           append with newline (no data loss, no double-append on re-run).
--       iii. success_criteria already present in lead_trigger_hint → no-op (re-run guard).
--     Guard: only runs when success_criteria IS NOT NULL AND <> '' (no phantom newlines).

-- Sub-case i: lead_trigger_hint is empty/NULL → set directly
UPDATE campaigns
   SET lead_trigger_hint = success_criteria
 WHERE success_criteria IS NOT NULL
   AND success_criteria <> ''
   AND (lead_trigger_hint IS NULL OR lead_trigger_hint = '');

-- Sub-case ii: both present, not yet merged → concat with newline
UPDATE campaigns
   SET lead_trigger_hint = lead_trigger_hint || E'\n' || success_criteria
 WHERE success_criteria IS NOT NULL
   AND success_criteria <> ''
   AND lead_trigger_hint IS NOT NULL
   AND lead_trigger_hint <> ''
   AND position(success_criteria IN lead_trigger_hint) = 0;

-- ── 5. DROP legacy columns (AFTER data migration) ────────────────────────────

-- Drop the voice_baseline CHECK before dropping the column (defensive, some PG versions
-- keep the constraint alive until DROP; explicitly named from migration 018).
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_voice_baseline_check;

-- D-02 note: tone_of_voice (TEXT free-text) and tone (JSONB slider) values are
-- intentionally discarded — no typed-enum mapping exists for slider values.
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS voice_baseline;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS tone;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS tone_of_voice;

-- D-13 note: success_criteria content safely merged into lead_trigger_hint above.
ALTER TABLE campaigns DROP COLUMN IF EXISTS success_criteria;

COMMIT;
