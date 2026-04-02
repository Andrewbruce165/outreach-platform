-- migrations/008_sender_proxy.sql
-- Add per-account proxy configuration to senders table.
-- nullable — backward compatible with existing accounts (they continue without proxy).

BEGIN;

ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS proxy JSONB;

COMMIT;
