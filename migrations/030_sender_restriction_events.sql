-- 030: durable append-only restriction event-log (HLTH-01 / HLTH-02).
--
-- Why: senders.restriction_status holds only the CURRENT state; message_queue.error_message
-- is overwritten on every reschedule; telemetry_events never records restriction changes;
-- and container logs live only ~18h (see .planning/notes/account-restriction-audit-gap.md).
-- So "how often did this account hit PEER_FLOOD this month / what was it doing right before
-- it got limited" is unrecoverable today.
--
-- This table is the durable, append-only record: exactly one row per restriction
-- state-change (none→spam_limited, →frozen, →cleared, →banned) OR per genuine forward
-- shift of restricted_until (D-01). Ordinary "still limited" reconcile ticks WITHOUT a
-- date shift produce NO row (the D-01 gate lives at the listener call-site). Each
-- restriction-category row carries a snapshot of the sender's preceding activity
-- (activity_slice) plus the proxy in effect at event time — computed at write time
-- because the source data (messages_log) is the only durable trace and the configured-
-- vs-actual rate at that instant cannot be reconstructed later (D-05).
--
-- category is the discriminator (D-03/D-04): 'restriction' = account-level audit;
-- 'recipient_privacy' = recipient-level error (account healthy, restriction_status
-- untouched) — EXCLUDED from restriction analytics by a single WHERE category='restriction'.

CREATE TABLE IF NOT EXISTS sender_restriction_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sender_id        UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    -- D-04: one discriminator column. 'restriction' = account-level audit;
    -- 'recipient_privacy' = recipient-level error, account healthy.
    category         VARCHAR(20)  NOT NULL DEFAULT 'restriction',
    -- spam_limited|frozen|flood_wait|cleared|banned|extension (restriction)
    --   | privacy_restricted (recipient_privacy)
    event_type       VARCHAR(20)  NOT NULL,
    -- queue_error | spambot_reconcile | antispam_signal  (free-form, NO CHECK — OQ#2)
    source           VARCHAR(20)  NOT NULL,
    restricted_until TIMESTAMPTZ  NULL,      -- value at the moment of the event (D-02); NULL for cleared/recipient
    raw_text         TEXT         NULL,      -- raw send-error message or @SpamBot reply (D-02)
    activity_slice   JSONB        NULL,      -- HLTH-02 snapshot (D-05/D-06); NULL for recipient_privacy
    proxy            JSONB        NULL,      -- senders.proxy at event time (D-06.3)
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()  -- server timestamp (D-02)
);

-- Per-account history, newest-first (HLTH-03 endpoint reads this order).
CREATE INDEX IF NOT EXISTS idx_sre_sender_created
    ON sender_restriction_events (sender_id, created_at DESC);
-- Future analytics + the category split filter (D-03), workspace-scoped.
CREATE INDEX IF NOT EXISTS idx_sre_workspace_category
    ON sender_restriction_events (workspace_id, category, created_at DESC);

-- Guard against typos from raw-SQL writers (idempotent — drop+recreate, mirrors 028).
-- Only `category` is CHECK-constrained; `source` is intentionally free-form so the
-- listener antispam path can record source='antispam_signal' (OQ#2) without a migration.
ALTER TABLE sender_restriction_events DROP CONSTRAINT IF EXISTS sre_category_chk;
ALTER TABLE sender_restriction_events ADD CONSTRAINT sre_category_chk
    CHECK (category IN ('restriction', 'recipient_privacy'));
