---
slug: spambot-selfcheck-antispam-guard
created: 2026-06-22
status: complete
---

# SpamBot self-check antispam guard

## Problem

`check_spambot` sends `/start` to @SpamBot (id 178220800). The reply lands in the
listener's update stream → `_handle_antispam_signal` (listener.py:881) fires, which
cancels all pending/processing queue items for that sender **and** disables AI in all
its conversations. SpamBot's id is in `ANTISPAM_BOT_IDS` (listener.py:118).

So the restriction reconcile sweep (pings SpamBot every ~15 min on restricted accounts,
listener.py:1377) and the manual `/senders/{slug}/spambot-check` endpoint both kill the
sender's own queue. We must distinguish a **solicited** reply (we asked) from an
**unsolicited** warning (account genuinely flagged — cancel is correct there).

## Decision (confirmed with user 2026-06-22)

In-memory suppression only (no DB marker, no migration).

Topology note: `api` and `listener` are **separate containers** — `telegram_service`
is a per-process singleton, so the in-memory registry is only visible within the
process that set it.

- **Sweep** runs in the **listener** process, same process as `_handle_antispam_signal`
  → in-memory guard is fully effective. ✅
- **Manual endpoint** runs in the **api** process; the SpamBot reply is handled by the
  listener's persistent client → the api-set flag is **not** visible to the listener's
  guard. This case stays a documented foot-gun (rare, deliberate manual action). We still
  pass `selfcheck_key` from the endpoint for intent/forward-compat, with a comment noting
  the cross-process limitation.

## Tasks (atomic commits)

1. **TelegramService self-check registry** — `self._spambot_selfcheck: dict[str, float]`
   in `__init__` + methods `mark_spambot_selfcheck(slug, ttl=30)` and
   `is_spambot_selfcheck(slug)` (prunes expired). `check_spambot(client, selfcheck_key=None)`:
   if key given, `mark_spambot_selfcheck(key)` **before** sending `/start`. TTL ~30s covers
   reply delivery; not cleared in `finally` to avoid racing the update stream.
2. **Wire both call sites** — pass `selfcheck_key=slug` at the sweep (listener.py:1377) and
   `selfcheck_key=sender.slug` at the endpoint (senders.py:615), the latter with a
   cross-process-limitation comment.
3. **Guard in `_handle_antispam_signal`** — at the top: if
   `telegram_service.is_spambot_selfcheck(sender_slug)`: log "solicited SpamBot reply —
   skip auto-cancel" and `return`. Covers both detect branches (id + keyword).
4. **Tests (overlay)** — mark/is + TTL expiry; guard skips cancellation when flag active and
   still cancels when not.

## Deploy

`docker compose up -d --build api listener`

## Risk

The window globally suppresses antispam for that sender for ~30s — acceptable: the only
expected message in that window is SpamBot's reply. Unsolicited warnings outside the
window behave exactly as before.
