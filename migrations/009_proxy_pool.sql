-- migrations/009_proxy_pool.sql
-- Proxy pool for automated Decodo proxy allocation.
-- Each row = one static residential proxy (host + port = unique IP).
-- assigned_to_sender_id: NULL = free, non-NULL = in use by that sender.
-- ON DELETE SET NULL: deleting a sender automatically frees its proxy.

BEGIN;

CREATE TABLE IF NOT EXISTS proxy_pool (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    host                  VARCHAR(255) NOT NULL,
    port                  INTEGER      NOT NULL,
    username              VARCHAR(100) NOT NULL,
    password              VARCHAR(100),
    assigned_to_sender_id UUID         REFERENCES senders(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (host, port)
);

COMMIT;
