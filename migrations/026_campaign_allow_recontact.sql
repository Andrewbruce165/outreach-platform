-- 026: per-campaign re-contact policy + honest conversation freshness.
--
-- Problem: campaign_enqueue cross-campaign dedup never re-touches a contact who
-- already has ANY conversation in the workspace. That is correct as a default,
-- but blocks legitimate re-engagement of contacts whose old dialog is closed
-- (finished) or long stale. We add an opt-in, per-campaign flag plus a staleness
-- threshold, and we make "freshness" trustworthy.
--
-- 1. campaigns.allow_recontact — opt-in. DEFAULT false → existing campaigns
--    keep the current "never re-touch" behavior unchanged.
-- 2. campaigns.recontact_min_age_days — a "protected" dialog older (more stale)
--    than this many days no longer blocks re-contact. DEFAULT 30.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS allow_recontact BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS recontact_min_age_days INTEGER NOT NULL DEFAULT 30;

-- 3. conversations.updated_at freshness trigger.
--    The ORM-level onupdate=func.now() on conversations.updated_at does NOT fire
--    for the raw-SQL (text()) writes this codebase uses, and there was no DB
--    trigger — so updated_at == created_at for practically every row and could
--    not be used as a "last activity" signal. The re-contact staleness check
--    needs a real last-activity timestamp, so bump conversations.updated_at on
--    every message insert (inbound or outbound).
CREATE OR REPLACE FUNCTION touch_conversation_on_message() RETURNS trigger AS $$
BEGIN
    UPDATE conversations SET updated_at = now() WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS messages_touch_conversation ON messages;
CREATE TRIGGER messages_touch_conversation
    AFTER INSERT ON messages
    FOR EACH ROW
    EXECUTE FUNCTION touch_conversation_on_message();
