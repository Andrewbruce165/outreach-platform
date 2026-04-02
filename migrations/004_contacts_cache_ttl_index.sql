-- Migration 004: Add index to support contacts_cache TTL queries (7-day expiry)
--
-- The application now filters contacts_cache with:
--   AND updated_at > NOW() - INTERVAL '7 days'
-- This index makes those queries efficient.

CREATE INDEX IF NOT EXISTS idx_contacts_cache_ttl
    ON contacts_cache (sender_id, phone, updated_at DESC);
