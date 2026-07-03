-- 048_sender_long_pause_until.sql
-- WR-04: durable per-sender long-pause marker. Replaces the inline
-- asyncio.sleep(long_pause) that stalled the whole shared queue tick. Survives
-- process restart; also acts as the "already paused, don't re-trigger" guard
-- against the modulo double-fire on a static 30-min count.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS long_pause_until TIMESTAMPTZ;
