---
slug: checker-false-spam-limited
status: resolved
trigger: |
  DATA_START
  проверь состояние аккаунта 79586008602 и +16185468137
  в ui висит что Spam-limited - Not sending · rechecks Jun 29, 04:00 PM
  хотя я сам проверит через spambot и там нет проблем
  DATA_END
created: 2026-06-29
updated: 2026-06-29
---

# Debug Session: checker-false-spam-limited

## Symptoms

- **Expected:** Accounts +79586008602 and +16185468137 should be usable / not restricted. User manually verified both via Telegram @SpamBot → no restriction reported by Telegram.
- **Actual:** UI shows "Spam-limited - Not sending · rechecks Jun 29, 04:00 PM" for these accounts.
- **Errors:** No user-facing error; restriction surfaced from `senders.restriction_status='spam_limited'`.
- **Timeline:** Active now (2026-06-29). Restriction events span 06:57–08:00 UTC today.
- **Reproduction:** View the accounts in UI; field reflects DB `restriction_status` + `restricted_until`.

## Initial Evidence (gathered by orchestrator, read-only)

Both accounts are **checker** role, both `lifecycle_status='paused'`, `restriction_status='spam_limited'`:

| slug | phone | restricted_until | checker_rest_until |
|---|---|---|---|
| sender-8364639216 | +79586008602 | 2026-06-29 14:00:18 UTC | (null) |
| sender-8349156575 | +16185468137 | 2026-06-29 08:06:45 UTC | 2026-06-29 07:56:45 UTC |

`sender_restriction_events` (newest first) — **all `source=antispam_signal`**, flapping:

```
sender-8364639216 | spam_limited | antispam_signal | until 14:00:18 | created 08:00:18   <- escalated to 6h
sender-8349156575 | spam_limited | antispam_signal | until 08:06:45 | created 07:51:45   <- 15min
sender-8349156575 | cleared      | antispam_signal |                | created 07:50:13
sender-8349156575 | spam_limited | antispam_signal | until 07:50:11 | created 07:35:11
sender-8349156575 | cleared      | antispam_signal |                | created 07:33:39
sender-8349156575 | spam_limited | antispam_signal | until 07:33:35 | created 07:18:35
sender-8364639216 | spam_limited | antispam_signal | until 07:30:55 | created 07:15:55
sender-8364639216 | cleared      | antispam_signal |                | created 07:13:00
sender-8364639216 | spam_limited | antispam_signal | until 07:12:44 | created 06:57:44
```

Note: 8364639216 created 08:00:18 → restricted_until 14:00:18 = exactly **6 hours** (escalated). Earlier trips were 15-min windows. `now = 2026-06-29 08:02 UTC`.

## Prior Art / Related Context

This strongly matches the **documented checker probe-burn root cause** (memory `project-phase14-checker-throttle-pool-wide.md` + `.planning/notes/checker-probe-burn-phase-brief.md`): the checker health-probe runs every ~5s, ignores `checker_rest_until`, and over-fires on 49 control contacts. Burst-throttle false-negatives (≥2 probe misses) trip the checker to `spam_limited` via `antispam_signal` even though Telegram has not actually restricted the account (@SpamBot clean). Repeated trips escalate the restriction window.

## Root Cause

There are **TWO distinct false-positive sources**, both confirmed by the `raw_text` of the events (read live from DB):

**(A) Self-inflicted contacts-API throttle from the checker resolve/probe loop (the early 15-min flaps).**
Events 06:57–07:51 carry `raw_text = "resolve-tick: anomalous empty-rate 30/30"`. These are written by `contact_check_worker.py::_maybe_degrade_on_signal` → `_flag_checker_degraded` (`app/services/contact_check_worker.py:618-629, 554-596`). A 30-phone batch returning `registered=0 not_registered=30` against a ~53%-registered base is statistically impossible by chance — it is a real but **reversible** Telegram contacts-API soft-throttle that our own pipeline induced. The throttle is self-burned because:
  - `_probe_cycle` (lines 450-474) runs **every `poll_interval` (~5s)** and its WHERE clause checks `restriction_status`, `lifecycle_status`, `restricted_until` but **NOT `checker_rest_until`** — so the Plan-14-07 post-batch rest is defeated for the probe path; a "resting" checker is still probed every 5s (≈36 live resolves/min of pure probe load). Burned checkers logged ~4,267 probe batches/day.
  - Trip → 15-min cooldown (`contact_check_cooldown_seconds=900`) → `_recover_checkers` re-probes → clears → resolve tick trips it again. Flap. This is NOT a Telegram ban: sessions are `auth_status='ok'`, @SpamBot is clean.

**(B) The newest, escalated 6h trip is a SEPARATE bug — a *clean* SpamBot reply misclassified as a restriction.**
The latest event for sender-8364639216 (created 08:00:18, `restricted_until` exactly **6h** later) carries `raw_text = "Good news, no limits are currently applied to your account. You're free as a bird!"` — the verbatim **@SpamBot "no limits" CLEAN reply**. It was recorded as `spam_limited` because:
  - `listener.py::_handle_antispam_signal` (lines 927-1019) flags `spam_limited` for **any** inbound message from a SpamBot ID (`ANTISPAM_BOT_IDS = {178220800, 777000}`, lines 648-657 / 668-679). It does **not** parse the SpamBot body — a "you're free as a bird" clean reply is treated identically to a real warning.
  - The only guard against this is `is_spambot_selfcheck(sender_slug)` (line 951), a 30s in-process TTL set by the **sender** reconcile/manual self-check path (`telegram.py:238-248, 326`). The checker pool does not arm this guard, so when the user (or any flow) pinged @SpamBot for these *checker* accounts and SpamBot replied "no limits", the listener saw an unsolicited SpamBot message and flagged the account.
  - `restricted_until` = `restriction_recheck_interval_seconds` default `6*60*60` (`config.py:79-84`) → the exact **6h** window the UI shows.

**Net:** the `spam_limited` state on both checker accounts is a **false positive** — there is no real Telegram restriction (@SpamBot confirms clean). (A) self-burns the checker via the every-5s probe that ignores `checker_rest_until`; (B) then a clean SpamBot reply got mislabeled as a fresh 6h restriction. The data layer stayed safe throughout (suspect batches roll back to `pending`).

## Eliminated

- **Real Telegram restriction / ban** — ELIMINATED. @SpamBot returns "no limits" (the verbatim clean reply is literally stored in the latest event's `raw_text`); sessions are `auth_status='ok'`; no `FROZEN`/`Unauthorized`/`FloodWait` in logs.
- **Genuine PeerFlood / FloodWait from real outbound sends** — ELIMINATED. The early flap events are `raw_text="resolve-tick: anomalous empty-rate 30/30"` (contacts-resolve throttle, not a send-path flood), and the 6h event is a clean SpamBot reply, not a flood signal.
- **Exponential/escalating-cooldown logic in the worker** — ELIMINATED as the 6h source. The worker only ever uses `contact_check_cooldown_seconds` (900s/15min); the 6h window comes entirely from `_handle_antispam_signal` using `restriction_recheck_interval_seconds` (6h).

## Current Focus

- **hypothesis:** CONFIRMED. `spam_limited` is a false positive with two compounding causes: (A) self-inflicted contacts-API throttle amplified by the every-5s probe that ignores `checker_rest_until`; (B) `_handle_antispam_signal` mislabeling a clean SpamBot reply as a restriction (6h window from `restriction_recheck_interval_seconds`).
- **next_action:** Remediation decision (see fix options). Immediate: manually clear the two stuck checkers. Code fix: (1) parse SpamBot body in `_handle_antispam_signal` so a clean "no limits" reply never flags; (2) make the probe path honor `checker_rest_until` + run far less than every 5s (the inline 14-05 anomaly detector already catches throttle for free).
- **reasoning_checkpoint:** root cause confirmed from event `raw_text` (live DB read) + code at `contact_check_worker.py:450-596` and `listener.py:927-1019` + config defaults (`config.py:79-84, 114-135`).

## Resolution

- **root_cause:** False-positive `spam_limited` on two checker accounts: (A) self-inflicted reversible contacts-API throttle, burned by an every-5s health-probe that ignores `checker_rest_until`; (B) a clean @SpamBot reply ("no limits…") misclassified as a 6h restriction by `listener._handle_antispam_signal`, which flags on SpamBot *sender id* without parsing the body.
- **fix (code, applied — targets root cause B, the user-reported 6h stuck state):**
  - `app/services/telegram.py`: extracted the SpamBot verdict classifier into a module-level `classify_spambot_text(text) -> 'free'|'limited'|'suspended'|'unknown'` (single source of truth, phrase tables incl. "free as a bird"); refactored `check_spambot` to use it (behavior preserved).
  - `app/services/listener.py::_handle_antispam_signal`: after the solicited-self-check guard, classify the inbound SpamBot body and **return without flagging** unless the verdict is `limited`/`suspended`. A `free`/`unknown` body (e.g. "Good news, no limits … free as a bird!", or a Telegram-service id-777000 message) can no longer pin the sender `spam_limited`. Restrictive replies still pause+flag exactly as before.
  - `tests/test_spambot_selfcheck.py`: added regression `test_antispam_guard_skips_clean_spambot_body_even_without_marker` — clean body + no marker ⇒ queue untouched, `restriction_status` stays `none`.
- **verification (self):** test-overlay green — `test_spambot_selfcheck` (5) + `test_sender_restriction` (10) + `test_restriction_audit` (13) + `test_phase5_bot_filter`/`test_checker*`/`test_contact_check_worker`/`test_check_contacts` (51) all pass; new regression passes; the two pre-existing antispam guard tests still pass (limited⇒flag, selfcheck⇒skip).
- **DONE (user-approved, 2026-06-29 08:13 UTC):** (1) cleared the stuck checker `sender-8364639216` (+79586008602) → `restriction_status='none'`, `restricted_until=NULL`, `checker_rest_until=NULL`, `lifecycle_status='active'`, with a manual `cleared`/`reconcile` audit row. `sender-8349156575` (+16185468137) had already self-recovered. (2) Deployed: `docker compose up -d --build api && … listener` — both containers Up & healthy (api serving 200s, migrations applied, listener reconnected sessions). Fix B is now live.
- **scope note:** root cause A (probe-burn every-5s, ignores `checker_rest_until`) is the documented Phase-brief work (`.planning/notes/checker-probe-burn-phase-brief.md`) — a separate phase, NOT changed here. This session fixed only the false-positive flag (B) that violated the safety invariant and matches the user's exact symptom (@SpamBot clean, UI spam-limited).
- **files_changed:** app/services/telegram.py, app/services/listener.py, tests/test_spambot_selfcheck.py

## Post-fix observation (2026-06-29 08:15 UTC) — bug A still re-flaps this checker

After the manual clear at 08:12:52, `sender-8364639216` was **re-flagged `spam_limited` at 08:15:02** with `raw_text="resolve-tick: anomalous empty-rate 30/30"`, `source=antispam_signal`, 15-min window. This is **root cause A (probe-burn / self-throttle), NOT B** — B's fix held (no 6h SpamBot mis-flag recurred), but the checker remains hostage to A: while A is unfixed and the account is `active`/`none`, the worker `_tick` immediately runs a resolve batch, the contacts-API soft-throttle returns registered=0/30, and the inline 14-05 detector degrades it 15min. Clearing does not stick.

**Remediation for A is staged but NOT executed:** `.planning/quick/260629-b7j-checker-probe-burn-fix/260629-b7j-PLAN.md` (migration 036 `checker_trip_count`, `contact_check_probe_interval_seconds` + `contact_check_max_backoff_seconds` knobs, rest-aware/budget-gated/interval-throttled probe gate, escalating cooldown). Awaiting user go-ahead (touches the probe/queue zone + a migration → user chose to treat A as a separate phase).

**Interim options for sender-8364639216:** (i) execute the staged probe-burn plan; (ii) `lifecycle_status='paused'` to stop the worker probing it so the reversible throttle recovers (park, like sender-8428118140); (iii) leave it flapping on 15-min cycles. No code/DB change made for A in this session.

**USER DECISION (2026-06-29):** chose (iii) **leave as is** — bug A deferred to its own phase (staged plan `260629-b7j`). +79586008602 will continue 15-min spam_limited↔cleared flaps until that phase runs; this is cosmetic (data safe, suspect batches roll back to `pending`). Session closed: reported symptom (bug B, the 6h clean-SpamBot mis-flag) is fixed + deployed; secondary cause A is tracked separately.
