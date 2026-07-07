-- 052: cache Telegram Premium flag on senders (surfaced as a badge in the UI).
-- Populated from get_me().premium at onboarding finalize, profile resync and
-- bulk account import. Default false = "not premium / not yet checked".
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_premium boolean NOT NULL DEFAULT false;
