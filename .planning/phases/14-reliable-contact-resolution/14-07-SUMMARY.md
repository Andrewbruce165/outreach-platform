---
phase: 14
plan: 07
subsystem: contact-resolution
tags: [checker, post-batch-rest, throttle-prevention, rotation, gap-closure, q3, wave-7]
requires:
  - "14-05 inline flood/throttle-aware finalization (_is_throttle_signal / _maybe_degrade_on_signal) — untouched, the rest is orthogonal to it"
  - "14-03 _tick JOIN-LATERAL checker selection gate (RESV-05) — extended with one sibling clause, not rewritten"
  - "14-06 spike Q3 verdict (conditional GO) — this plan IS the prerequisite that verdict authorised"
  - "Phase 10 senders.restriction_status/restricted_until/lifecycle_status — explicitly NOT touched by the rest path"
provides:
  - "senders.checker_rest_until TIMESTAMPTZ — benign post-batch rest stamp (NOT a restriction)"
  - "config knob contact_check_rest_seconds (default 300s, env CONTACT_CHECK_REST_SECONDS)"
  - "ContactCheckWorker._rest_checker(checker_id) — stamps checker_rest_until = NOW()+rest in its own short TX"
  - "LATERAL selection gate clause: AND (checker_rest_until IS NULL OR checker_rest_until <= NOW())"
  - "Q3 prevention: a single checker can no longer chain consecutive batches past the ~45-50 burst onset"
affects:
  - "RESV-04 full re-check / checker re-activation + 14k drain (DEFERRED — separate user-gated ops step) consumes this rest gate as its safe-pacing prerequisite"
tech-stack:
  added: []
  patterns:
    - "Benign throughput knob as a SEPARATE column from the restriction machinery — checker_rest_until is orthogonal to restricted_until so a normal post-batch rest never looks like a throttle degrade in the audit log and never routes the checker through the recovery control-probe"
    - "Per-checker batch_applied flag in the groupby loop: only a non-raising batch rests (a raising branch is handled by its own error/degrade path); a clean EMPTY batch still rests"
    - "make_interval(secs => :rest) instead of (:rest || ' seconds')::interval — asyncpg binds the knob as int, the string-concat form raises DataError"
key-files:
  created:
    - "migrations/035_checker_post_batch_rest.sql (idempotent ADD COLUMN IF NOT EXISTS senders.checker_rest_until TIMESTAMPTZ)"
  modified:
    - "app/models/__init__.py (Sender.checker_rest_until column, benign-rest comment)"
    - "app/config.py (contact_check_rest_seconds knob, default 300s)"
    - "app/services/contact_check_worker.py (LATERAL gate clause + batch_applied flag + _rest_checker after-batch UPDATE)"
    - "tests/test_checker_probe.py (4 RED-first post-batch-rest tests appended)"
decisions:
  - "checker_rest_until is a SEPARATE column from restricted_until (not reuse) — restricted_until is restriction cooldown that the recovery probe keys on; the rest is a benign throughput pacer. Conflating them would make a rest look like a throttle degrade and run a rested checker through the recovery control-probe."
  - "default contact_check_rest_seconds=300 (5 min) — start value; one batch (≤30) + a 5-min rest keeps a single checker well under the ~45-50 burst onset across consecutive ticks, and with ≥2 healthy checkers the existing rotation alternates them for ≈2x throughput. Tunable via env without a redeploy of logic."
  - "A non-raising EMPTY/clean batch STILL rests (batch_applied=True after _apply_results); only a raising branch is skipped (its own degrade path owns it). This is what actually prevents the chained-burst — the rest must not depend on the batch having results."
metrics:
  tasks: 2
  files: 4
  commits: 2
  completed: 2026-06-26
---

# Phase 14 Plan 07: Checker Post-Batch Rest (Q3 Prevention Gap) Summary

Closes the last Phase-14 prevention gap surfaced by the 14-06 spike (Q3): one resolve batch (≤ burst_cap 30) is safe, but the worker chains batch-after-batch on the SAME checker with only a ~5s poll between them, so the cumulative burst crosses the ~45-50 throttle onset within ~2 batches. This plan adds a BENIGN per-checker post-batch REST — a new durable `senders.checker_rest_until` timestamp the worker stamps after every non-raising batch and gates the LATERAL checker-pick on. A single checker can no longer chain consecutive batches past the burst onset; the existing rotation naturally alternates a second healthy checker meanwhile (≈2x throughput, no parallel execution). The rest is the guarded-re-activation PREREQUISITE the 14-06 GO verdict authorised — and this plan is CODE-ONLY: it re-activated NO parked checker, mutated NO prod data, ran NO 14k drain (the migration is not even deployed to prod).

## What Was Built

### Task 1 — RED tests (tests/test_checker_probe.py)
Four tests appended in the existing module style, driving `ContactCheckWorker()._tick()` DIRECTLY (so `_probe_cycle` never runs and the rest contract is isolated from the degrade path). A new `_clean_registered_summary` builder produces a healthy batch (every phone registered, `flood_wait_hit=False`, no anomaly) so the ONLY state change attributable to the batch is the benign rest — the 14-05 inline degrade does not fire. Assertions: (a) `test_post_batch_rest_excludes_checker_until_elapsed` — after a clean batch `checker_rest_until` is set in the future and on the next tick the checker is NOT selected (`check_phones` not awaited, the un-resolved contact stays pending); (b) `test_second_healthy_checker_selected_while_first_rests` — with two checkers, while A rests B is selected on the next tick (both contacts resolve, two DISTINCT checker slugs used → rotation alternation proven); (c) `test_post_batch_rest_touches_only_rest_column` — after a clean batch `restriction_status` stays `'none'`, `lifecycle_status` stays `'active'`, `restricted_until` stays NULL, `auth_status` stays `'ok'`, and `COUNT(sender_restriction_events)=0`; (d) `test_rested_checker_reselected_without_recovery_probe` — once `checker_rest_until <= NOW()` the checker is re-selected, and `_recover_checkers()` is a no-op for it (no recovery control-probe — that path keys on `restriction_status='spam_limited'` + `restricted_until`, which the rest never sets). RED proven: all 4 failed against the unfixed worker (the column did not exist + no gate/UPDATE); the 9 pre-existing probe/flood tests stayed GREEN.

### Task 2 — checker_rest_until column, config knob, LATERAL gate, after-batch rest UPDATE
- **migrations/035_checker_post_batch_rest.sql**: idempotent `ALTER TABLE senders ADD COLUMN IF NOT EXISTS checker_rest_until TIMESTAMPTZ NULL`. No data backfill (NULL = "not resting"). Header documents why it is a SEPARATE column from `restricted_until`.
- **app/models/__init__.py**: `Sender.checker_rest_until = Column(DateTime(timezone=True), nullable=True)` next to `restricted_until`, commented as a benign post-batch rest (NOT a restriction). The test schema picks it up via ORM `create_all` (migration 035 is prod-only, like 034 — not in the conftest list).
- **app/config.py**: `contact_check_rest_seconds: int = Field(default=300, validation_alias="CONTACT_CHECK_REST_SECONDS")` beside the other `contact_check_*` knobs, with a docstring noting it is NOT a restriction cooldown.
- **app/services/contact_check_worker.py**: (1) the LATERAL checker subquery gained one sibling clause `AND (checker_rest_until IS NULL OR checker_rest_until <= NOW())`, so a resting checker is excluded just like a paused/restricted one. (2) The per-checker groupby loop tracks a `batch_applied` flag set True after each non-raising `_apply_results` (phone and/or username branch); at the end of the loop iteration, if `batch_applied`, it calls the new `_rest_checker(checker_id)`. (3) `_rest_checker` runs ONE benign `UPDATE senders SET checker_rest_until = NOW() + make_interval(secs => :rest)` in its own short transaction, touching ONLY `checker_rest_until` — never `restriction_status`/`lifecycle_status`/`restricted_until`, never `record_restriction_event`. `_flag_checker_degraded` and `_recover_checkers` were left unchanged.

## Verification Results

All runs ONLY via the test-overlay under `COMPOSE_PROJECT_NAME=wt14_07` with the ephemeral `db-test` (tmpfs) started explicitly + `api --no-deps` (the base `db` service pins `container_name: outreach-platform-db`, which would collide with the running prod container — `--no-deps` avoids recreating it). The prod `outreach-platform-db` was NEVER recreated or touched (confirmed `Up 2 days (healthy)` after teardown). Torn down with `down` (NEVER `down -v`). The gitignored prod `.env` was copied into the worktree purely for compose interpolation and removed after (never committed).

- **Task 1 RED**: `pytest tests/test_checker_probe.py -k "rest or second_healthy"` against the unfixed worker → 4 failed (3 with teardown errors from the same missing column), 9 deselected. The pre-existing probe/flood subset `-k "not rest and not second_healthy"` → 9 passed (no regression from the additions).
- **Task 2 GREEN (file)**: `pytest tests/test_checker_probe.py` → **13 passed** (9 prior + 4 new rest tests). First GREEN attempt hit `asyncpg.DataError: invalid input for query argument $1: 300 (expected str, got int)` from the `(:rest || ' seconds')::interval` form — fixed by `make_interval(secs => :rest)` (Rule 1 auto-fix), then GREEN.
- **Full suite via overlay: 778 passed, 1 skipped, 0 failed (169.49s)** — exactly the 774-pass Wave-5 baseline + the 4 new rest tests, no regressions.
- **Isolation invariant verified**: `_rest_checker` UPDATE is the only place `checker_rest_until` is written; it contains no `restriction_status`/`restricted_until`/`record_restriction_event`. Prod `senders` has NO `checker_rest_until` column yet (migration not deployed — code-only). Parked checkers `sender-7979031303` / `sender-8364639216` read-only confirmed `none`/`paused` (NOT re-activated).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Interval bind raised asyncpg DataError**
- **Found during:** Task 2 first GREEN run.
- **Issue:** `SET checker_rest_until = NOW() + (:rest || ' seconds')::interval` — asyncpg binds the `contact_check_rest_seconds` knob as an integer, and Postgres `||` (text concat) rejects an int operand: `invalid input for query argument $1: 300 (expected str, got int)`. This failed not only the new rest tests but cascaded into the other `_tick`-driven flood tests in the file (they share the after-batch path on registered results).
- **Fix:** Switched to `NOW() + make_interval(secs => :rest)`, which takes the integer knob directly with no text concat. No behavioural change to the rest semantics.
- **Files modified:** app/services/contact_check_worker.py (within the same Task-2 commit).
- **Commit:** `4f9c00c`.

### Operational note (not a deviation)
- Same worktree compose pattern as Plans 14-01..14-05: `COMPOSE_PROJECT_NAME=wt14_07`, `up -d db-test` then `run --rm --no-deps api pytest`, prod `.env` copied for interpolation and removed after (gitignored, never committed). The prod-named `db`/`api` were never recreated.

## Known Stubs
None. No production code stubs introduced.

## Out of Scope / Deferred (NOT claimed here)
- Checker re-activation (`sender-7979031303` / `sender-8364639216`), the landline pre-filter, and the 14k drain remain DEFERRED to a separate, explicitly user-gated ops step (the 14-06 GO verdict authorised the prerequisite — this plan — not the drain). This plan mutated NO prod data, re-activated NO parked checker, deployed NO migration to prod, and ran NO drain.
- Tuning `contact_check_rest_seconds` against live hit-rate (the 200-500 staged drain in the 14-06 GO conditions) is part of that deferred ops step.

## Commits
- `abb2616` test(14-07): RED — post-batch checker rest excludes from selection, no restriction side-effects
- `4f9c00c` feat(14-07): benign per-checker post-batch rest closes Q3 chained-burst gap

## Self-Check: PASSED
- migrations/035_checker_post_batch_rest.sql — FOUND
- app/models/__init__.py (Sender.checker_rest_until) — FOUND
- app/config.py (contact_check_rest_seconds) — FOUND
- app/services/contact_check_worker.py (LATERAL gate + _rest_checker + batch_applied) — FOUND
- tests/test_checker_probe.py (4 post-batch-rest tests) — FOUND
- Commit abb2616 (RED tests) — FOUND
- Commit 4f9c00c (GREEN impl) — FOUND
- _rest_checker touches ONLY checker_rest_until (no restriction_status/lifecycle/restricted_until, no event) — OK
- Full suite 778 passed / 0 failed; 4 new GREEN, 774 Wave-5 baseline intact — OK
- No prod data mutation, no checker re-activation, no 14k drain; prod db Up 2 days healthy after teardown; migration NOT deployed to prod — OK
