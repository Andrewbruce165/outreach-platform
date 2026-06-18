-- 025: outreach by @username.
-- The identity key for the outreach pipeline (rotation assignment, queue item,
-- conversation, contacts_cache) is stored in the *_phone columns. For contacts
-- that have only a Telegram username (no phone), we store '@username' there as
-- an opaque key. A username is up to 32 chars + '@' = 33; the columns are
-- VARCHAR(20) — too short. Widen all five key columns to VARCHAR(40).
--
-- ALTER ... TYPE VARCHAR(40) is a widening: lossless, fast (no rewrite needed
-- for varchar length increase in PG), and idempotent (re-running on an already-
-- widened column is a no-op that PG accepts).
ALTER TABLE message_queue                ALTER COLUMN recipient_phone TYPE VARCHAR(40);
ALTER TABLE campaign_contact_assignments ALTER COLUMN contact_phone   TYPE VARCHAR(40);
ALTER TABLE conversations                ALTER COLUMN contact_phone   TYPE VARCHAR(40);
ALTER TABLE contacts_cache               ALTER COLUMN phone           TYPE VARCHAR(40);
ALTER TABLE messages_log                 ALTER COLUMN recipient_phone TYPE VARCHAR(40);
