-- 064: proxy_pool gains a nullable `quarantined_until` TIMESTAMPTZ (C&C mass-ban
-- remediation). When a port is reclaimed from a DEAD sender (banned/session_expired)
-- it must not be handed straight to a live sender: a static residential IP that
-- Telegram already associated with a banned account carries that reputation, so the
-- proxy-reclaim sweep parks the freed port for a cooldown window and every free-pool
-- selection (import, manual create, assign) skips a port whose quarantine has not
-- elapsed. NULL = never quarantined / available now.
-- proxy_pool HAS an ORM model (ProxyPool), so create_all builds the table on a fresh
-- DB; this migration adds the new column for existing prod DBs. Idempotent.

BEGIN;

ALTER TABLE proxy_pool ADD COLUMN IF NOT EXISTS quarantined_until TIMESTAMPTZ;

COMMIT;
