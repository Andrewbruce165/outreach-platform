-- 062_sender_proxy_switch_pending.sql
-- proxy-switch-listener-lag fix (Approach A): durable per-sender marker set by
-- POST /senders/{slug}/assign-proxy the moment the new proxy is committed. While
-- this timestamp is set (and younger than proxy_switch_pending_ttl_seconds), the
-- send/warmup/checker selection paths SKIP the sender so it never opens a temp
-- connection on the NEW proxy while the listener may still hold the OLD IP
-- (double-IP → Telegram auth_key kill). The listener clears it (SET NULL) on a
-- confirmed reconnect to the new proxy; a TTL sweep in the reconcile loop lifts a
-- stale flag so a sender is never blocked forever if the listener never comes up.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS proxy_switch_pending_at TIMESTAMPTZ;
