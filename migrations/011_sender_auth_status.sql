-- migrations/011_sender_auth_status.sql
-- Add auth_status field to senders for tracking Telegram session health.
-- Values: ok, session_expired, session_revoked, deactivated, banned
BEGIN;

ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS auth_status VARCHAR(30) NOT NULL DEFAULT 'ok';

COMMIT;
