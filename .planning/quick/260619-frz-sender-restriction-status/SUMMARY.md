---
slug: sender-restriction-status
status: complete
date: 2026-06-19
commits: 0f84870..5f4f944
---

# Sender write-restriction (spam-limit / freeze) — SUMMARY

## What & why

A sender's status never reflected Telegram write-restrictions. The incident that
triggered this: `ru-account-1` hit **PEER_FLOOD** (spam-limit) mid-campaign, the queue
paused its pending messages 24h, but the sender kept showing **"active"** in the UI —
because `_derive_status` only knew `auth_status != ok → error`. A spam-limited or frozen
account still authenticates (Telegram only blocks the *write* path), so `auth_status`
stayed `ok`. Freeze (`FROZEN_*` RPC errors) was even more invisible — it fell into the
generic `SEND_FAILED` retry path.

## Decisions

- **New columns** `restriction_status` (`none|spam_limited|frozen`) + `restricted_until`,
  orthogonal to `auth_status` (which stays purely about session validity).
- **Auto-reconcile** via a background SpamBot sweep in the listener, not manual-only.
- `check_spambot` does not expose `limit_until` → fixed recheck delay
  (`RESTRICTION_RECHECK_INTERVAL`, default 6h) rather than parsing a date.

## Changes (6 atomic commits)

1. `0f84870` — migration 028 + `Sender` ORM: `restriction_status` + `restricted_until`
   (+ CHECK constraint + partial index for the sweep filter).
2. `44c7aa7` — `_derive_status` precedence error > frozen > limited > lifecycle;
   `SenderResponse` gains the new fields; **fixed spambot-check bug** that wrote a bogus
   `auth_status='limited'` — now writes `restriction_status`.
3. `e495a21` — `telegram.is_frozen_error()`; send_message/send_file surface `FROZEN_*`
   as a distinct `ACCOUNT_FROZEN` code.
4. `2428d6a` — queue: PEER_FLOOD → `spam_limited`, new ACCOUNT_FROZEN branch → `frozen`
   (both stamp `restricted_until`, keep the empirical 24h queue pause); `_check_rate_limits`
   skips senders with an active restriction so we stop burning sends.
5. `f3e8c0c` — listener `_restriction_reconcile_tick`/`_loop`: re-checks restricted senders
   via SpamBot (reusing the live client) once `restricted_until` elapses — `free` clears +
   un-pauses the queue, `limited`/`unknown` extends, `suspended` → `auth_status=banned`.
6. `5f4f944` — 8 tests (derive matrix, is_frozen_error, reconcile tick clear/extend/ban/skip,
   queue source-shape guards).

Config: `RESTRICTION_RECHECK_INTERVAL` (6h), `RESTRICTION_RECONCILE_INTERVAL` (15min) in
`app/config.py`.

## Verification

- Migration 028 auto-applied on prod (`schema_migrations` has `028_sender_restriction`);
  both columns present, all 3 senders default to `restriction_status='none'`.
- api healthy (`/api/v1/health` → 200), no errors in logs.
- listener log: `🔁 Restriction reconcile loop started (interval=900s)`.
- New tests: 8/8 pass via test-overlay. Related suites (senders, queue, listener reconcile,
  lifecycle, orm widening): 43 pass.

## Known issue (pre-existing, NOT from this task)

`tests/test_listener_reconcile.py::test_get_active_senders_query_shape` fails on
`assert "is_active" not in src` — the `get_active_senders` docstring comment literally
contains "is_active dropped". Confirmed present on `main` (I did not touch that function).
Brittle substring assertion, out of scope here.

## Follow-ups (not done)

- UI: surface `limited`/`frozen` badges + `restricted_until` (frontend repo).
- External alert on PEER_FLOOD/FROZEN (TODO already in queue.py — needs monitoring infra).
- Fix the brittle `test_get_active_senders_query_shape` assertion separately.
