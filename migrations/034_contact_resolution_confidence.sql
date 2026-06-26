-- 034: contact resolution confidence / source (Phase 14 — RESV-06 / D-09).
--
-- Next free migration number is 034 (033_campaign_max_new_dialogs.sql is the
-- previous one). Auto-applied at api start by app/database.py::_apply_migrations
-- in lexical order; this file MUST be idempotent (ADD COLUMN IF NOT EXISTS +
-- DROP/ADD CONSTRAINT) — the applier re-runs it on any schema drift and the api
-- fail-fasts (does not start) if a migration raises.
--
-- Why a NEW column instead of reusing contacts.source:
--   contacts.source is IMPORT-provenance ("where did this contact come from" —
--   CSV / API push / etc.). tg_resolved_by is RESOLVER-provenance (D-09): WHICH
--   checker account produced the tg_status result, so a suspect/low-confidence
--   checker's not_registered can be distinguished from a clean-probe high-
--   confidence one and re-checked instead of silently dropped. The two are
--   orthogonal and must not be conflated.
--
-- Columns (all nullable — backfill not needed; new resolves populate them):
--   tg_confidence   'high' | 'low' | NULL  — high = result from a clean-probe checker.
--   tg_resolved_by  UUID (checker sender_id) | NULL — resolver identity (D-09).
--   tg_probe_state  'clean' | 'suspect' | NULL — checker's probe health at resolve time.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_confidence  TEXT NULL;   -- 'high'|'low'|NULL
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_resolved_by UUID NULL;   -- checker sender_id (resolver-provenance, D-09)
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_probe_state TEXT NULL;   -- 'clean'|'suspect'|NULL (kept free-form, NO CHECK)

-- Guard tg_confidence against typos from raw-SQL writers (idempotent — drop+recreate).
-- NOTE: no CHECK on tg_probe_state — kept free for forward-compat (future probe states).
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_tg_confidence_chk;
ALTER TABLE contacts ADD CONSTRAINT contacts_tg_confidence_chk
    CHECK (tg_confidence IS NULL OR tg_confidence IN ('high', 'low'));
