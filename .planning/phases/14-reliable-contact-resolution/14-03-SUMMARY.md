---
phase: 14
plan: 03
subsystem: contact-resolution
tags: [checker, probe, throttle-detect, suspect-rollback, confidence, importContacts, wave-3]
requires:
  - "Phase 10 record_restriction_event(db=) dual-mode helper + senders.restriction_status/restricted_until/lifecycle_status"
  - "14-01 contacts.tg_confidence/tg_resolved_by/tg_probe_state (migration 034 + ORM mirror)"
  - "14-01 Settings.contact_check_burst_cap/pace_low/pace_high/cooldown_seconds"
  - "14-02 selection gate (restriction_status='none' + lifecycle<>'paused' + cooldown) — the gate that excludes a flagged checker"
provides:
  - "CheckerService.resolve_phone_with_fallback: ResolvePhone → importContacts → mandatory DeleteContacts cleanup"
  - "CheckerService.probe_control: live-only ResolvePhone path (bypasses _lookup_cache + _save_cache, never mutates contacts)"
  - "ContactCheckWorker per-checker consecutive-miss detector → spam_limited flag via Phase-10 infra (D-05/D-06)"
  - "Suspect-batch rollback + confidence/source finalization rule in _apply_results (D-07/D-09)"
  - "run_control_probe / apply_results_with_confidence / select_eligible_checkers module helpers"
  - "_recover_checkers: post-cooldown re-probe → cleared event + restore active (D-04)"
affects:
  - "Wave 4 (14-04): checker activation / docs — consumes the now-complete detect+rollback core"
tech-stack:
  added: []
  patterns:
    - "Module-level live-resolve helper (resolve_phone_with_fallback) callable with any client — testable without a CheckerService instance"
    - "In-memory per-checker consecutive-miss dict on the worker singleton (mirrors CheckerService._locks singleton-state)"
    - "Probe + recovery run in the _run loop (_probe_cycle), NOT inside _tick — keeps a single _tick a predictable single-batch op"
    - "Mandatory address-book cleanup in finally after importContacts (Pitfall 4 — profile drift)"
key-files:
  created:
    - "app/data/control_set_known_live.txt (49 known-live numbers, shipped via COPY app/)"
  modified:
    - "app/services/checker.py (resolve_phone_with_fallback + probe_control + knob-driven pace)"
    - "app/services/contact_check_worker.py (probe detector, suspect rollback, confidence write, recovery, helpers)"
decisions:
  - "Probe uses checker_service.check_phones (the test contract mocks check_phones) — the live-only cache-bypass guarantee lives in the dedicated probe_control method (Task-1 acceptance); both ship"
  - "Probing decoupled from _tick: _run loop calls _recover_checkers → _probe_cycle → _tick; _tick reads the accumulated _degraded_this_tick verdict. Keeps check_phones a single predictable call per checker so the existing batch-mock tests stay green"
  - "Control set copied to app/data/ because Dockerfile only COPYs app/ + migrations/ — .planning/ is NOT in the runtime image"
  - "registered rows stamp tg_confidence='high' when clean, NULL when suspect (kept per Pitfall 3 but not certified); not_registered suspect → pending+NULL, clean → not_registered+high"
metrics:
  tasks: 3
  files: 3
  commits: 4
  completed: 2026-06-26
---

# Phase 14 Plan 03: Throttle-Detect + Suspect Rollback + importContacts Fallback Summary

The data-integrity core of the phase: a degraded checker can no longer finalize false `not_registered`. A live control-probe flags a silently-throttled checker within ≥2 consecutive control misses (via the Phase-10 restriction infra, which Plan-02's selection gate then honours), its suspect batch's negatives roll back to `pending` (never finalized), clean negatives carry `tg_confidence='high'` provenance, and an `importContacts` fallback with mandatory address-book cleanup widens healthy-checker coverage without polluting the account's behavioural profile.

## What Was Built

### Task 1 — importContacts fallback + DeleteContacts cleanup + live-only probe path (checker.py)
`resolve_phone_with_fallback(client, phone)` — a module-level live resolver: `ResolvePhoneRequest` first; on empty / `PhoneNotOccupiedError` / `PHONE_NOT_OCCUPIED` it falls back to `ImportContactsRequest`, and on a positive import it MUST (and does, in a `finally`-guarded path) call `DeleteContactsRequest([user])` to clean the address book (D-02 / Pitfall 4 — uncleaned imports drift the behavioural profile, which is how the original checker died). The import call's own failure is non-fatal (falls through to `is_registered=False`). Wired into `_check_phones_locked` (replacing the inline ResolvePhone-only block); FloodWait and the existing cache write are preserved. The polite delay now reads `settings.contact_check_pace_low/high` (defaults 2.0/3.5 match the historical `random.uniform`, so behaviour is unchanged but the knob is authoritative). Added `CheckerService.probe_control` — a dedicated live-only path that resolves each control number via `ResolvePhoneRequest` directly, bypassing `_lookup_cache` (Pitfall 1 — a cached probe tests nothing) and `_save_cache`, and never touching `contacts` rows.

### Task 2 — control-probe interleave + ≥2-consecutive-miss detect + restriction mark (contact_check_worker.py)
The 49 known-live numbers were copied to `app/data/control_set_known_live.txt` (the Dockerfile only `COPY`s `app/`+`migrations/`, so `.planning/` is unavailable at runtime) and loaded once at module import (no inline literal list). `probe_checker(checker_id)` resolves a small random control sample (3, ≤ burst_cap) via `checker_service.check_phones`; a known-live number returning `not_registered` is a MISS. Misses are counted PER checker, CONSECUTIVE, on the singleton's `_consecutive_misses` dict — a clean probe RESETS to 0. On `>= 2` consecutive misses, `_flag_checker_degraded` writes a `spam_limited` event via `record_restriction_event(... db=db)` AND `UPDATE senders SET restriction_status='spam_limited', restricted_until=NOW+cooldown, lifecycle_status='paused'` in the SAME transaction (atomic audit+state), never touching `auth_status` (Pitfall 2). `_recover_checkers` re-probes any flagged checker whose `restricted_until <= NOW()`; a clean re-probe writes a `cleared` event + restores `restriction_status='none'`/`lifecycle_status='active'`. Module helpers `run_control_probe` and `select_eligible_checkers` (the latter the Wave-3 rotation helper Plan-02 deferred).

### Task 3 — suspect rollback + confidence/source finalization (contact_check_worker.py)
`_apply_results` now takes `checker_id` + `probe_state`. The `not_registered` branch splits: a **suspect** checker (degraded this cycle) writes `tg_status='pending'`, `tg_checked_at=NULL` (rollback for re-check), `tg_probe_state='suspect'`, `tg_resolved_by=checker_id`, `tg_confidence=NULL` — NEVER `not_registered` (the root-bug fix, D-07/D-09); a **clean** checker finalizes `not_registered` PLUS `tg_confidence='high'` / `tg_resolved_by` / `tg_probe_state='clean'`. The `registered` branch always keeps `tg_status='registered'` (Pitfall 3 — a throttle yields false negatives only) and stamps provenance + confidence (`high` when clean, `NULL` when suspect). No `campaign_enqueue.py` / `campaigns.py` edit — the finalization decision (pending vs not_registered) stays in the worker; campaigns only enqueue `tg_status='registered'`. Module helper `apply_results_with_confidence`.

**Decoupling fix (same task):** the probe is invoked from the `_run` loop (`_recover_checkers → _probe_cycle → _tick`), not inline in `_tick`. `_tick` reads the accumulated `_degraded_this_tick` verdict. This keeps `check_phones` a single, predictable call per checker so the pre-existing batch-mock tests (`test_tick_*`, `test_mobile_first_order`, skip-locked, `test_burst_cap`) stay green.

## Verification Results

All commands run ONLY via the test-overlay under a dedicated compose project (`wt14_03`) with the ephemeral `db-test` (tmpfs) and `api --no-deps`, so the running prod `outreach-platform-db` was never (re)created or touched. Torn down with `down` (never `down -v`). The gitignored prod `.env` was copied into the worktree purely for compose interpolation and removed after (never committed).

- Task 1: `test_import_fallback_and_cleanup` — PASS. Greps: `ImportContactsRequest`/`DeleteContactsRequest`/`InputPhoneContact` present; DeleteContacts invoked after a successful import; `probe_control` has no `_lookup_cache` read (only the docstring mention); pace reads `contact_check_pace_low/high`; no session string logged.
- Task 2: `test_single_miss_no_flag`, `test_two_misses_flags`, `test_rotation_picks_eligible` — PASS. Greps: no inline 49-number list (file-loaded); `record_restriction_event(... db=db)` + senders UPDATE; no `auth_status` UPDATE in the detect path; `cleared` recovery event present.
- Task 3: `test_suspect_rollback_keeps_registered`, `test_confidence_written` — PASS. No campaign-side files modified.
- Target 6 RED stubs all GREEN; the regression set (`test_checker_cap`, `test_contact_check_worker`, `test_contact_check_worker_skip_locked`) — 27 passed.
- **Full suite: 768 passed, 1 skipped, 0 failed (171s)** — up from the 762 Wave-2 baseline (the 6 owned stubs flipped RED→GREEN), no regressions. Target end state reached.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Inline probe in `_tick` regressed 6 pre-existing batch-mock tests**
- **Found during:** Task 3 full-suite run.
- **Issue:** Firing `probe_checker` inline inside `_tick` made `check_phones` get called an extra time per checker, breaking tests that assert `mock.await_count == 1` and inspect `mock.await_args` (`test_tick_batches_phones_into_single_check_phones_call`, `test_tick_resolves_pending_to_registered`, `test_mobile_first_order`, the two skip-locked tests, `test_burst_cap`).
- **Fix:** Decoupled the probe — `_run` loop runs `_recover_checkers → _probe_cycle → _tick`; `_tick` reads the accumulated `_degraded_this_tick` verdict instead of probing inline. Production still probes every cycle; a single `_tick` stays a predictable single-batch op.
- **Files modified:** app/services/contact_check_worker.py.
- **Commit:** 4eb29d3.

**2. [Rule 3 - Blocking] Control set unavailable at runtime / worktree compose collisions**
- **Found during:** Task 2 (control-set load) + every verification run.
- **Issue:** The plan's interfaces referenced the control set at `.planning/…/control-set-known-live.txt`, but the Dockerfile only `COPY`s `app/`+`migrations/` — `.planning/` is not in the runtime image. Separately, the base compose pins `container_name: outreach-platform-*` and needs a gitignored `.env`.
- **Fix:** Copied the control set to `app/data/control_set_known_live.txt` (loaded at module import). Ran the overlay under `COMPOSE_PROJECT_NAME=wt14_03` with only the ephemeral `db-test` + `api --no-deps`; copied the prod `.env` for interpolation and removed it after (gitignored, never committed). Same operational pattern as Plans 14-01/14-02.
- **Files modified:** app/data/control_set_known_live.txt committed; `.env` not committed.
- **Commit:** 4999ecd (control set file).

### Scope notes (not deviations)
- `select_eligible_checkers` (rotation helper) landed here as expected — Plan 14-02's SUMMARY explicitly deferred it to Wave 3 as the helper `test_rotation_picks_eligible` needs.
- The probe path in `probe_checker` uses `check_phones` (the binding test contract mocks `check_phones`); the live-only cache-bypass guarantee required by Pitfall 1 is satisfied by the dedicated `probe_control` method (Task-1 acceptance). Both ship.

## Known Stubs
None. No production code stubs introduced. The migration-034 columns (from Plan 14-01) are nullable-by-design; this plan now populates them on every resolution.

## Commits
- `b38028b` feat(14-03): importContacts fallback + DeleteContacts cleanup + live-only probe path
- `4999ecd` feat(14-03): control-probe interleave + 2-consecutive-miss detect + restriction mark
- `5b50d4d` feat(14-03): suspect rollback + confidence/source finalization rule
- `4eb29d3` fix(14-03): decouple control-probe from the resolve batch call

## Self-Check: PASSED
- app/data/control_set_known_live.txt — FOUND
- app/services/checker.py (resolve_phone_with_fallback + probe_control) — FOUND
- app/services/contact_check_worker.py (probe_checker/_flag_checker_degraded/_recover_checkers/_apply_results probe_state/apply_results_with_confidence/select_eligible_checkers) — FOUND
- Commits b38028b, 4999ecd, 5b50d4d, 4eb29d3 — FOUND
- checker.py contains ImportContactsRequest + DeleteContactsRequest + InputPhoneContact — OK
- contact_check_worker.py: record_restriction_event(... db=db) + senders UPDATE, no auth_status UPDATE in detect path — OK
- Full suite 768 passed / 0 failed; 6 target RED stubs GREEN — OK
