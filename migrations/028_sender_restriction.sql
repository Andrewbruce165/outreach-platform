-- 028: sender write-restriction state (spam-limit / freeze).
--
-- Problem: a sender's status never reflected Telegram write-restrictions. A
-- spam-limited (PEER_FLOOD) or frozen (FROZEN_*) account keeps auth_status='ok'
-- (its session is valid — Telegram only blocks the WRITE path), so _derive_status
-- returned 'active' and the UI showed a restricted account as healthy.
--
-- auth_status answers "is the session valid / can we authenticate". Restriction is
-- orthogonal (a restricted account authenticates fine), so it gets its own column
-- instead of overloading auth_status.
--
-- restriction_status:
--   none         — no known restriction (default)
--   spam_limited — PEER_FLOOD; usually auto-lifts within hours/a day
--   frozen       — FROZEN_* RPC error; harder, needs appeal
-- restricted_until — when the background reconcile sweep should re-check via SpamBot.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS restriction_status TEXT NOT NULL DEFAULT 'none';
ALTER TABLE senders ADD COLUMN IF NOT EXISTS restricted_until TIMESTAMPTZ NULL;

-- Guard against typos from raw-SQL writers (idempotent — drop+recreate).
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_restriction_status_chk;
ALTER TABLE senders ADD CONSTRAINT senders_restriction_status_chk
    CHECK (restriction_status IN ('none', 'spam_limited', 'frozen'));

-- Reconcile sweep filters on (restriction_status != 'none' AND restricted_until <= now()).
CREATE INDEX IF NOT EXISTS idx_senders_restriction
    ON senders (restricted_until)
    WHERE restriction_status <> 'none';
