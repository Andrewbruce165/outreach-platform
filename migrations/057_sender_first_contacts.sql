-- migrations/057_sender_first_contacts.sql
-- Phase 22 (account-level new-chat limit grades) — new-warmup-pair registry.
--
-- Records the FIRST time two sender accounts contacted each other so the Wave-2
-- warmup-budget feature (22-05) does NOT charge an already-warmed pair as a NEW
-- chat (D-08). One row per unordered pair.
--
-- CANONICAL ORDER INVARIANT: the pair is stored with sender_a_id < sender_b_id
-- (enforced via LEAST/GREATEST). An unordered {X,Y} therefore maps to exactly one
-- row regardless of who initiated, and the composite PK dedups it. Any writer of
-- this table MUST canonicalise with LEAST(a,b)/GREATEST(a,b) before insert.
--
-- Idempotent backfill (D-08): seed the registry from prior warmup activity so
-- pairs that already warmed before this phase are recorded (and thus excluded
-- from the new-chat budget later). Two sources — warmup_sessions (sender_a_id,
-- sender_b_id) and warmup_messages (from_sender_id, to_sender_id) — both
-- canonicalised and ON CONFLICT DO NOTHING so re-runs and overlap are no-ops.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING — auto-applier
-- re-runs safely on drift. Auto-applied via app/database.py::_apply_migrations
-- (lexical order, advisory lock). Fail-fast: api does NOT start if this raises.

BEGIN;

CREATE TABLE IF NOT EXISTS sender_first_contacts (
    sender_a_id      UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    sender_b_id      UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    first_contact_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sender_a_id, sender_b_id)  -- canonical: sender_a_id < sender_b_id
);

-- Backfill from warmup_sessions (canonicalised pair, earliest session start).
INSERT INTO sender_first_contacts (sender_a_id, sender_b_id, first_contact_at)
SELECT LEAST(sender_a_id, sender_b_id),
       GREATEST(sender_a_id, sender_b_id),
       MIN(created_at)
  FROM warmup_sessions
 WHERE sender_a_id IS NOT NULL
   AND sender_b_id IS NOT NULL
   AND sender_a_id <> sender_b_id
 GROUP BY LEAST(sender_a_id, sender_b_id), GREATEST(sender_a_id, sender_b_id)
ON CONFLICT DO NOTHING;

-- Backfill from warmup_messages (canonicalised pair, earliest message).
INSERT INTO sender_first_contacts (sender_a_id, sender_b_id, first_contact_at)
SELECT LEAST(from_sender_id, to_sender_id),
       GREATEST(from_sender_id, to_sender_id),
       MIN(sent_at)
  FROM warmup_messages
 WHERE from_sender_id IS NOT NULL
   AND to_sender_id IS NOT NULL
   AND from_sender_id <> to_sender_id
 GROUP BY LEAST(from_sender_id, to_sender_id), GREATEST(from_sender_id, to_sender_id)
ON CONFLICT DO NOTHING;

COMMIT;
