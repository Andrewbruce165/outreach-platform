---
slug: sender-restriction-status
created: 2026-06-19
status: complete
---

# Sender restriction status (spam-limit / freeze) — detect, surface, auto-reconcile

## Problem

Sender status never reflects Telegram write-restrictions. Two distinct restrictions
are conflated and both leave the sender showing "active":

- **PEER_FLOOD** (spam-limit) — `PeerFloodError` → code `PEER_FLOOD`. queue.py:720
  pauses all pending 24h + fails the item but does **not** touch sender status.
- **Freeze** — RPC `FROZEN_*` (e.g. `FROZEN_METHOD_INVALID`). Falls into the generic
  `except Exception` in telegram.py → code `SEND_FAILED` → ordinary retry, no status change.

`_derive_status` only knows `auth_status != ok → error`, else `lifecycle_status`.
`spambot-check` endpoint has a bug: maps `"limited" → "limited"` into `auth_status`,
which is not a valid enum value.

## Decisions (confirmed with user)

- **Storage**: new columns `restriction_status` (`none|spam_limited|frozen`) +
  `restricted_until`, NOT overloading `auth_status` (auth = session validity only).
- **Reconcile**: background sweep in listener, re-checks restricted senders via SpamBot
  when `restricted_until <= now`, lifts/extends/escalates automatically.
- `check_spambot` does NOT extract `limit_until` (docstring lies) → use fixed re-check
  delay `RESTRICTION_RECHECK_INTERVAL` (default 6h) for spam_limited; +24h on first PEER_FLOOD.

## Tasks (atomic commits)

1. **migration 028 + model** — add `restriction_status TEXT NOT NULL DEFAULT 'none'` +
   `restricted_until TIMESTAMPTZ NULL` to `senders` (idempotent). Add to `Sender` ORM.
2. **derive_status + schema + spambot-check fix** — `_derive_status`: error > frozen >
   limited > lifecycle. Add fields to `SenderResponse`. Fix `spambot-check` to write
   `restriction_status` instead of bogus `auth_status`.
3. **freeze detection in telegram.py** — catch `FROZEN_*` prefix in send_message/send_file
   → distinct code `ACCOUNT_FROZEN`.
4. **queue.py** — PEER_FLOOD branch sets `spam_limited` + `restricted_until=+24h`; new
   `ACCOUNT_FROZEN` branch sets `frozen` + pause. Pre-send skip in `_check_rate_limits`
   for senders with active restriction.
5. **listener restriction reconcile sweep** — `_restriction_reconcile_tick()` + loop:
   senders where `restriction_status != none AND restricted_until <= now` → check_spambot
   → free=clear, limited=extend, suspended=auth_status banned.
6. **tests** (test-overlay) — derive_status matrix, FROZEN detection, PEER_FLOOD writes
   restriction, reconcile sweep clears/extends.

## Deploy

`docker compose up -d --build api listener`
