-- 065: campaigns gains `opener_paraphrase_enabled` BOOLEAN (C&C mass-ban remediation,
-- H5). When true, the CampaignEnqueueWorker runs each rendered opener through an LLM
-- paraphrase (meaning-preserving) before it is written to message_queue, so every
-- recipient gets a VISIBLY distinct opener instead of 594 byte-identical ones — the
-- top clustering signal in the mass ban. Default false = opt-in; existing campaigns
-- and behavior are unchanged. campaigns HAS an ORM model, so create_all builds the
-- column on a fresh DB; this migration adds it for existing prod DBs. Idempotent.

BEGIN;

ALTER TABLE campaigns
  ADD COLUMN IF NOT EXISTS opener_paraphrase_enabled BOOLEAN NOT NULL DEFAULT false;

COMMIT;
