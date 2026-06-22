---
slug: spambot-selfcheck-antispam-guard
created: 2026-06-22
status: complete
commits: 7da5f6e..f796893
---

# SpamBot self-check antispam guard — SUMMARY

## What shipped

A solicited SpamBot reply (our own ping via the reconcile sweep or the manual
`/spambot-check` endpoint) no longer triggers `_handle_antispam_signal`'s
auto-cancel of the sender's queue + AI disable. Unsolicited warnings still cancel.

## Changes (4 atomic commits)

1. **`7da5f6e` telegram.py** — `TelegramService._spambot_selfcheck: dict[str, float]`
   registry + `mark_spambot_selfcheck(slug, ttl=30)` / `is_spambot_selfcheck(slug)`
   (monotonic clock, prunes expired). `check_spambot(client, selfcheck_key=None)`
   marks the window **before** sending `/start`; not cleared in `finally` (TTL lapse
   avoids racing the asynchronously-delivered reply). Added `import time`.
2. **`bbf9744` listener.py + senders.py** — pass `selfcheck_key=slug` at the sweep
   (listener.py:1377) and `selfcheck_key=sender.slug` at the endpoint
   (senders.py:615, with cross-process-limitation comment).
3. **`1036f97` listener.py** — guard at the top of `_handle_antispam_signal`: if
   `telegram_service.is_spambot_selfcheck(sender_slug)` → log + `return`. Covers both
   detect branches (id at :636 and keyword at :660 funnel here).
4. **`f796893` tests** — `tests/test_spambot_selfcheck.py` (4 tests): registry mark/is
   + TTL expiry/prune (fake clock); guard skips cancellation when marked, still cancels
   when not.

## Key decision (with user, 2026-06-22)

In-memory suppression only, **no DB marker / migration**. `api` and `listener` are
separate containers → the in-memory registry is per-process:

- **Sweep** runs in the listener process (same as `_handle_antispam_signal`) → fully covered ✅
- **Manual endpoint** runs in the api process; the SpamBot reply is handled by the
  listener's persistent client → the api-set flag is invisible there → **not covered**
  (documented foot-gun; rare deliberate action). `selfcheck_key` is still passed for
  intent/forward-compat.

## Verification

- `pytest tests/test_spambot_selfcheck.py` → **4 passed**.
- Related suites: `test_phase5_bot_filter.py` + `test_sender_restriction.py` → all pass.
  - `test_listener_reconcile.py::test_get_active_senders_query_shape` fails, but this is
    **pre-existing** (the brittle `assert "is_active" not in src` matches an unrelated
    comment at listener.py:382, present at base commit `d63d7c1` — untouched by this task).
- Deployed: `docker compose up -d --build api listener` — both started clean, api serving
  200s, listener connected to Telegram.

## Follow-up (optional, not done)

If the manual endpoint's foot-gun ever matters, promote to a DB marker
(`senders.spambot_selfcheck_until`) so suppression works cross-process.
