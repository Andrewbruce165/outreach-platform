---
slug: checker-fn-igor-base
status: parked_awaiting_healthy_pool
trigger: "Barter_база Игоря_первые 200 контактов — много контактов в not_registered хотя они есть в Telegram; обнулить not_found для перепроверки"
created: 2026-06-30
updated: 2026-06-30
---

# Checker false-negatives — Barter база Игоря (first 200)

## Symptoms
- Folder `Barter_база Игоря_первые 200 контактов` (id `a1e04b86-73bc-4ff1-8e2a-e2f1bc19a9d1`, created 2026-06-30 09:59) fully checked: **22 registered / 176 not_registered** (198 total).
- All 206 not_registered platform-wide are mobile `+79…` numbers. Igor folder: 11% registered vs documented calibration ~50% live among RU mobiles → not_registered bucket is heavily polluted with false-negatives. User confirms specific not_registered contacts are reachable in Telegram.

## Root cause (FOUND)
The folder was resolved by accounts that systematically return false-negatives on RU `+79` numbers. `tg_resolved_by` breakdown for the folder:

| resolver | account | role | region | outcome in folder |
|---|---|---|---|---|
| sender-8537405794 | +16184955130 | **sender** | **US (+1)** | 116 not_reg + 22 reg |
| sender-7979031303 | +16166369072 | checker | **US (+1)** | 30 not_reg |
| sender-8364639216 | +79586008602 | checker | RU | 30 not_reg (then tripped → spam_limited/paused) |

- **2 of 3 resolvers are US (+1) accounts** → documented failure mode (`project-us-senders-cannot-resolve-ru-phones`): US/cold accounts give ~100% ResolvePhone false-negatives on `+79`. They produced 146 of the 176 not_registered.
- The one RU checker (sender-8364639216) **tripped into `spam_limited`** mid-run (burst throttle) → its 30 not_registered are also suspect.
- US `sender` accounts (role=`sender` now) resolved because they were temporarily flagged `role='checker'` when the operator set up smoke-test checkers, then reverted. Worker selection itself is correct (`contact_check_worker.py:257` filters `role='checker'`).

All 176 not_registered are written with `tg_confidence='high'`, `tg_probe_state='clean'` — finalized as trustworthy. They are not.

## Why a plain reset is NOT enough (two compounding traps)
1. **Poisoned cache.** `checker._lookup_cache` (checker.py:175) reads `contacts_cache` **workspace-wide, cross-sender**, and is consulted BEFORE Telegram (checker.py:344). The 176 phones each have an `is_registered=false` cache row. A re-check — even by a healthy RU checker — returns the cached false-negative without calling Telegram. → must DELETE the 176 poisoned cache rows.
2. **US checker still in the pool.** sender-7979031303 (+1) is currently `role='checker'`, `restriction_status='none'`, active → worker will re-select it and re-poison. → pull it out of the checker pool.

## Healthy checker available
- **sender-8017533134** | +79584148809 | RU mobile | ok/none/active, trip_count=0 — clean. Its cache: 22 rows, all `is_registered=true` (never wrote a false negative). This is the good RU checker to re-run with.
- sender-8364639216 (RU) is resting (`restricted_until` 12:48) — will recover and rejoin rotation; fine, it's RU.

## Fix plan (3 parts)
1. Pull US account out of checker pool: sender-7979031303 `role` checker→sender (no RU-resolve capability).
2. Purge 176 poisoned `contacts_cache` rows (`is_registered=false` for Igor-folder phones). Keep the 22 `true` rows.
3. Reset 176 `not_registered` → `pending`, clearing tg_telegram_id/tg_error/tg_checked_at/tg_confidence/tg_resolved_by/tg_probe_state. Worker re-resolves via sender-8017533134.

Exact scope verified: 176 contacts to reset, 176 cache rows to purge (1:1).

## Outcome (2026-06-30 ~13:00)
- Reset + cache purge applied (176→pending, 176 poisoned rows deleted) — the authorized scope.
- BUT re-check immediately reproduced false-negatives: first finalized batch **0/30 registered** (sender-8364639216) — statistically conclusive throttle, not genuine. Confirms the WHOLE pool is throttled from the day's load, not just the US account.
- Worker's own throttle-detection then self-parked 2/3 checkers (sender-7979031303, sender-8017533134 → spam_limited/paused). sender-8364639216 stayed none/active (its batch was largely cache-served, so the ≥8-live-result anomaly gate didn't trip → CODE GAP: cache cross-contamination defeats inline throttle finalization).
- Braked all checkers via checker_rest_until +4h (reversible knob), purged the re-written false cache (62 rows), reset the 30 freshly-poisoned → pending.
- **Frozen safe state: 176 pending / 22 registered / 0 not_registered, 0 false cache rows, 0 eligible checkers, worker idle.**

## Blocker / next step
No healthy checker pool exists right now → re-checking is futile (re-poisons). Per playbook (`barter-bases-paused-unchecked`): keep parked until a VERIFIED-healthy RU checker pool exists. Durable park of the throttled pool (restriction_status/lifecycle) was attempted but DENIED by scope guard — needs explicit user authorization. Awaiting user decision: park base as 'unchecked' / add fresh warmed RU checkers / authorize durable pool park.

## Related
- `.planning/notes/checker-false-negatives.md`, `.planning/notes/checker-pool-throttle-spike.md`
- memory: project-us-senders-cannot-resolve-ru-phones, project-barter-bases-paused-unchecked, project-checker-trip-count-reset-defeats-escalation
