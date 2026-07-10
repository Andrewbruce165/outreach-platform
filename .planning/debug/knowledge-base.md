# GSD Debug Knowledge Base

Resolved debug sessions. Used by `gsd-debugger` to surface known-pattern hypotheses at the start of new investigations.

---

## tdata-archive-import-fails — bulk import silently recognizes 0 accounts when vendor ships Telegram Desktop tdata instead of .session+.json
- **Date:** 2026-07-10
- **Error patterns:** bulk import, account import, archive, zip, tdata, key_datas, D877F783D5D3EF8C, TDF$, matched=0, unpack_and_pair, "не могу загрузить архив", preview empty, Telegram Desktop
- **Root cause:** `app/services/account_import.py::unpack_and_pair` recognizes accounts ONLY as `<phone>.json` + `<phone>.session` pairs (grouping strictly by file extension). A vendor tdata export (folders `+<phone>/tdata/` with `key_datas` / `D877F783D5D3EF8C…` / magic `TDF$`, no .session/.json) yields an all-empty recognized set with no error — user sees "can't upload archive". Not a regression; importer never supported tdata.
- **Fix:** (1) One-off out-of-product converter `scripts/tdata_to_session.py` (opentele in an isolated py3.11 container — opentele pulls its own Telethon and breaks on 3.13, needs libglib2.0-0 for PyQt5; `TDesktop(tdata).ToTelethon(UseCurrentSession)` is OFFLINE) → .session + minimal .json → normal importer. (2) UX: `UnsupportedArchiveError` (422 UNSUPPORTED_ARCHIVE) + `_looks_like_tdata`; `unpack_and_pair` now raises a clear message (tdata-specific when detected) instead of a mute empty result. Imported 10/10 as senders (job ecc46772), each got a distinct proxy_pool row.
- **Files changed:** app/services/account_import.py, tests/test_account_import.py, scripts/tdata_to_session.py
---

## campaign-pending-not-on-idle-senders — healthy attached senders idle at 0 pending while the campaign backlog stays stuck on the rest of the pool
- **Date:** 2026-07-10
- **Error patterns:** pending, message_queue, idle senders, zero pending, backlog, distribution, redistribute, rebalance, campaign_senders, attached after enqueue, sender not picking up, uneven load, cold-pending, campaign_contact_assignments
- **Root cause:** No continuous rebalance existed — redistribution was edge-triggered only. `rebalance_on_attach` (attach / restriction-clear) back-fills the ONE newly-eligible sender to ceil(cold_pending_NOW / P), so a sender attached/eligible after most of the batch is sent gets a tiny share; the per-tick sweep (`_sweep_stranded_cold_backlog`/failover) only evacuates rows OFF ineligible senders; enqueue rotation only assigns not-yet-assigned contacts and never revisits existing pending rows. Standing backlog therefore never flows to idle eligible senders (rebalance.py docstring documented this as the intended v1 scope limit).
- **Fix:** New `rebalance_campaign_even(campaign_id, db)` in rebalance.py — continuous even-split of cold-pending rows across ALL eligible senders (idle seeded as receivers; minimal-move targets → idempotent; same SKIP LOCKED + queue-row/CCA lock-step; BATCH_CAP/pass; NO scheduled_at reset). Called per running campaign from `CampaignEnqueueWorker._tick` after the sweep. Verified live: first tick moved 27 rows across 50 eligible senders, spread ±1.
- **Files changed:** app/services/rebalance.py, app/services/campaign_enqueue.py, tests/test_rebalance.py, tests/test_campaign_enqueue_worker.py
---

## frozen-spambot-check-error — frozen sender flips to status=error and demands reauth after a manual @SpamBot check, despite an intact session
- **Date:** 2026-07-10
- **Error patterns:** frozen, spambot, spambot-check, «заблокированы», «заблокирован», blocked, status error, reauth, reauthorization, auth_status banned, restriction_status frozen, suspended verdict, classify_spambot_text, _derive_status, read-only freeze, session intact
- **Root cause:** The @SpamBot verdict pipeline conflated Telegram's 2025 reversible read-only FREEZE with a permanent BAN. `classify_spambot_text` had no `frozen` bucket, so a frozen account's "your account was blocked / заблокирован" reply fell into `suspended` (the word "blocked" is a suspended-phrase). Both the manual `/spambot-check` endpoint (senders.py) and the reconcile sweep (listener.py) then set `auth_status='banned'` on `suspended`. `_derive_status` maps any `auth_status != 'ok'` to `error` (error > frozen precedence), flipping the sender frozen→error, dropping it from processing and prompting reauth — even though the freeze is read-only and the session is fully valid (a real hard ban surfaces on the AUTH path, not via SpamBot text). Not a regression; behaved this way since the frozen+spambot logic existed.
- **Fix:** (1) telegram.py: new `_SPAMBOT_FROZEN_PHRASES` + `frozen` verdict in `classify_spambot_text`, checked BEFORE limited/suspended so freeze wording wins over a generic "blocked". (2) senders.py manual /spambot-check: handle `frozen` verdict (keep restriction_status='frozen', bump recheck window, auth_status untouched) AND guard — a `suspended` text verdict must NOT escalate an already-frozen sender to banned. (3) listener.py reconcile sweep: same suspended-on-frozen guard + `frozen`-verdict handling; unsolicited-SpamBot net flags `frozen` instead of mislabeling `spam_limited`. Live-verified on prod: real SpamBot "blocked" reply (no explicit freeze wording) kept the sender status='frozen' via the router guard — auth_status stayed 'ok', no escalation, no reauth. 24 targeted tests green.
- **Files changed:** app/services/telegram.py, app/routers/senders.py, app/services/listener.py, tests/test_spambot_selfcheck.py, tests/test_sender_restriction.py
---
