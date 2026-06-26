---
phase: 14
plan: 05
subsystem: contact-resolution
tags: [checker, flood-wait, throttle-detect, inline-degrade, suspect-rollback, gap-closure, wave-5]
requires:
  - "14-03 _apply_results probe_state suspect/clean branch (D-07 rollback) — reused verbatim, not modified"
  - "14-03 _flag_checker_degraded (record_restriction_event db= + senders UPDATE, no auth_status) — extended with optional raw_text"
  - "14-03 _degraded_this_tick singleton scratch set + RESV-05 JOIN-LATERAL selection gate"
  - "Phase 10 record_restriction_event + senders.restriction_status/restricted_until/lifecycle_status"
provides:
  - "_is_throttle_signal(summary): FloodWait OR anomalous all-empty live batch (>= ANOMALY_MIN_BATCH) detector"
  - "ContactCheckWorker._maybe_degrade_on_signal: inline flood/throttle degrade at the resolve tick, returns 'suspect'"
  - "ANOMALY_MIN_BATCH module constant (8) — min live batch size for the all-empty anomaly branch"
  - "Inline finalization guard: a flood/throttle batch never finalizes not_registered/high; checker leaves rotation immediately (not two probes later)"
affects:
  - "RESV-04 full re-check / re-activation (DEFERRED — gated follow-up) consumes this inline guard"
tech-stack:
  added: []
  patterns:
    - "Pure module-level signal predicate (_is_throttle_signal) — testable without a worker instance, fed by the resolve summary"
    - "Inline degrade short-circuits the decoupled >=2-miss control probe: signal at the tick → flag now + mark THIS batch suspect (recompute probe_state AFTER the signal)"
    - "Reuse, do not duplicate: the suspect rollback SQL (D-07) and the degrade transaction (D-06) are untouched; only WHAT marks a batch suspect changed"
key-files:
  created: []
  modified:
    - "app/services/contact_check_worker.py (_is_throttle_signal + ANOMALY_MIN_BATCH + _maybe_degrade_on_signal wired into both _tick branches; _flag_checker_degraded raw_text-overridable)"
    - "tests/test_checker_probe.py (6 RED-first inline-flood tests appended)"
decisions:
  - "ANOMALY_MIN_BATCH=8 — comfortably below the 14-04 observed 20-30 poisoned-batch size yet above noise; only LIVE (non from_cache) results count toward the all-empty anomaly (Pitfall 1) and tiny batches are excluded to avoid false-positive degradation"
  - "Inline degrade is idempotent within a tick (checker already in _degraded_this_tick → suspect verdict returned without re-emitting the event), so a phone+username split batch flags once"
  - "Recompute probe_state AFTER the signal check so the just-flagged checker's OWN batch finalizes as suspect — the whole point of inline (vs the decoupled probe which only protects the NEXT batch)"
metrics:
  tasks: 2
  files: 2
  commits: 3
  completed: 2026-06-26
---

# Phase 14 Plan 05: Inline Flood/Throttle-Aware Finalization Summary

Closes Gap A from the 14-04 live-smoke failure: a freshly-throttled checker (FloodWait, or the 14-04 all-empty signature `checked=20..30 reg=0`) can no longer finalize its empty resolves as `not_registered`/`high`/`clean`. The 14-04 smoke proved the decoupled control-probe (`_probe_cycle`, runs in the `_run` loop) flags a checker only AFTER an entire poisoned batch has already been finalized — it wrote ZERO `sender_restriction_events` and suspect-rollback never fired while both "healthy" checkers logged `checked=20..30 reg=0 flood=True`. This plan adds an INLINE flood/throttle trigger at the resolve tick itself, reusing the already-built D-07 suspect rollback and D-06 inline degrade, so a flood batch rolls its negatives back to `pending`, never carries high-confidence, and the checker leaves rotation immediately — without waiting for the separate probe.

## What Was Built

### Task 1 — RED tests (tests/test_checker_probe.py)
Six tests appended in the existing module style, driving `ContactCheckWorker()._tick()` DIRECTLY (so `_probe_cycle` never populates `_degraded_this_tick` — the inline trigger is the only thing that can flag the checker, isolating the gap). Two summary builders: `_flood_summary` (`flood_wait_hit=True`, all not_registered) and `_anomalous_empty_summary` (the 14-04 signature: `flood_wait_hit=False`, `checked=N`, `reg=0`, all live non-cache). Assertions: (a) `test_flood_batch_rolls_back_to_pending` — every contact `pending` + `tg_checked_at IS NULL` + not `high` + `tg_probe_state='suspect'`; (b) `test_flood_batch_writes_no_high_confidence` — `COUNT(tg_confidence='high')=0`; (c) `test_flood_batch_degrades_checker_inline` — checker `spam_limited`/`paused`/future `restricted_until` + a `spam_limited` `sender_restriction_events` row, `auth_status` UNCHANGED (Pitfall 2); (d) `test_flood_checker_left_out_of_next_selection` — re-run `_tick()` does NOT await `check_phones` (RESV-05 gate), contacts stay pending; (e) `test_no_healthy_checker_leaves_pending` — N=0-healthy safe-stop (D-04); (f) `test_anomalous_all_empty_batch_treated_as_throttle` — batch of 10 (> threshold 8) with `flood_wait_hit=False` also rolls back + degrades. RED proven: 5 failed against the unfixed worker (one — the N=0 safe-stop — was already GREEN because the RESV-05 gate from Wave 3 already excludes paused checkers; it ships as a regression guard); the 3 pre-existing probe tests stayed GREEN.

### Task 2 — Inline flood/throttle-aware finalization + inline degrade (app/services/contact_check_worker.py)
`_is_throttle_signal(summary)` — a pure predicate: True iff `flood_wait_hit` is set OR the batch is anomalously all-empty, defined as `len(live results) >= ANOMALY_MIN_BATCH` AND `registered==0` AND every LIVE (non-`from_cache`) result not_registered. Only live results count (an all-cache batch tests nothing — Pitfall 1) and tiny batches are excluded (legitimately-empty small batches must not degrade). `ANOMALY_MIN_BATCH=8` (module constant, matches the Task-1 test comment). `_maybe_degrade_on_signal(checker_id, summary, probe_state)` is called in BOTH `_tick` branches (phones + usernames) immediately after `check_phones`/`check_usernames` and BEFORE `_apply_results`: on a signal it adds the checker to `_degraded_this_tick` and calls `_flag_checker_degraded` (now `raw_text`-overridable so the audit row records `"resolve-tick: FloodWait"` / `"resolve-tick: anomalous empty-rate N/N"`), then returns `'suspect'` so the batch finalizes via the EXISTING D-07 rollback (not_registered → pending, no high-confidence). The degrade is idempotent within a tick (a phone+username split flags once). The D-07 suspect-branch SQL and the RESV-05 JOIN-LATERAL gate were NOT touched — only what marks a batch suspect changed. No new migration; the Phase-10 `record_restriction_event` (category `'restriction'`, event_type free-form) is reused.

## Verification Results

All runs ONLY via the test-overlay under `COMPOSE_PROJECT_NAME=wt14_05` with the ephemeral `db-test` (tmpfs) started explicitly + `api --no-deps` (the base `db` service pins `container_name: outreach-platform-db`, which would collide with the running prod container — `--no-deps` avoids recreating it). The prod `outreach-platform-db` was NEVER recreated or touched (confirmed `Up 2 days (healthy)` after teardown). Torn down with `down` (NEVER `down -v`). The gitignored prod `.env` was copied into the worktree purely for compose interpolation and removed after (never committed).

- Task 1 RED: `pytest tests/test_checker_probe.py -k "flood or anomalous or no_healthy"` against the unfixed worker → 5 failed, 1 passed (the N=0 safe-stop guard), 3 deselected. The pre-existing `two_misses`/`single_miss`/`suspect_rollback` → 3 passed (no regression from the additions).
- Task 2 GREEN: `pytest tests/test_checker_probe.py tests/test_contact_check_worker.py tests/test_checker_cap.py tests/test_checker_pool.py` → **28 passed** (the 6 new GREEN + worker/cap/pool regression set).
- `grep -n flood_wait_hit app/services/contact_check_worker.py` → feeds `_is_throttle_signal` → `_maybe_degrade_on_signal` (degrade/suspect decision), not only the logger line.
- No new migration (`git status migrations/` empty); degrade reuses `_flag_checker_degraded` (record_restriction_event + senders UPDATE, no `auth_status` change).
- **Full suite via overlay: 774 passed, 1 skipped, 0 failed (169.80s)** — exactly the 768 Wave-3 baseline + the 6 new tests, no regressions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree compose collisions + raw-DSN network dependency**
- **Found during:** every verification run.
- **Issue:** The base `docker-compose.yml` pins `container_name: outreach-platform-*` (collides with the running prod container) and needs a gitignored `.env` for interpolation. Separately, the conftest builds its schema against a `db-test`-hosted raw DSN, so `db-test` must be reachable on the compose network — a bare `run --rm --no-deps api` (no db-test) fails with a DNS resolution error.
- **Fix:** Ran under `COMPOSE_PROJECT_NAME=wt14_05`; started ONLY the ephemeral `db-test` (`up -d db-test`) then `run --rm --no-deps api pytest` so the prod-named `db` is never created while `db-test` is still on the network. Copied prod `.env` for interpolation, removed it after (gitignored, never committed). Same operational pattern as Plans 14-01/14-02/14-03.
- **Files modified:** none committed (`.env` not committed).
- **Commit:** n/a (operational).

### Scope notes (not deviations)
- `_flag_checker_degraded` gained an optional `raw_text` param (default preserves the prior `"control-probe: N consecutive misses"` text), so the probe path is byte-identical and the inline path records its distinct cause. Additive, no behaviour change to the existing probe.
- The N=0-healthy safe-stop test (`test_no_healthy_checker_leaves_pending`) was already GREEN against the unfixed worker — the RESV-05 gate from Wave 3 already excludes paused checkers. It ships as a regression guard, not a newly-fixed behaviour. Five of the six new tests were genuinely RED.

## Known Stubs
None. No production code stubs introduced.

## Out of Scope / Deferred (NOT claimed here)
- RESV-04 (re-check of the 14k/2110/699 already-finalized contacts) and full checker re-activation remain DEFERRED to a later gated follow-up. This plan is CODE-ONLY: it mutated NO prod data, re-activated NO parked checker, and added NO migration.

## Commits
- `a079130` test(14-05): RED — flood/throttle batch must roll back, not finalize
- `c5ef668` feat(14-05): inline flood/throttle-aware finalization + inline checker degrade

## Self-Check: PASSED
- app/services/contact_check_worker.py (_is_throttle_signal + ANOMALY_MIN_BATCH + _maybe_degrade_on_signal + raw_text param) — FOUND
- tests/test_checker_probe.py (6 inline-flood tests) — FOUND
- Commit a079130 (RED tests) — FOUND
- Commit c5ef668 (GREEN impl) — FOUND
- flood_wait_hit feeds _is_throttle_signal/_maybe_degrade_on_signal (not only logger) — OK
- No new migration; no auth_status change; D-07/RESV-05 paths untouched — OK
- Full suite 774 passed / 0 failed; 6 new GREEN, 768 Wave-3 baseline intact — OK
- No prod data mutation, no checker re-activation, prod db Up 2 days healthy after teardown — OK
