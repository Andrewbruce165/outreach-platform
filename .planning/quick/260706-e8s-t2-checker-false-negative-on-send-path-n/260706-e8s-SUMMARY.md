---
phase: 260706-e8s
plan: 01
subsystem: api
tags: [queue, telegram, false-negative, suspect-rollback, contacts-cache, failover, rotation]

# Dependency graph
requires:
  - phase: 09-cold-contact-failover
    provides: "failover_cold_backlog template (session ownership, per-campaign healthy-pool query, _pick_least_loaded, FOR UPDATE OF mq SKIP LOCKED, PII-safe logging)"
  - phase: 14-reliable-contact-resolution
    provides: "checker suspect/rollback semantics being mirrored onto the send path"
  - phase: 17-sender-side-resolve-ladder
    provides: "suspect cache-poison handling (confidence-gated cache) that this parallels for the send path"
provides:
  - "app.services.send_suspect.rollback_suspect_resolve_fails — reactive send-path suspect rollback + cache purge, invoked from queue.py PEER_FLOOD/ACCOUNT_FROZEN"
  - "queue.py RECIPIENT_NOT_IN_TELEGRAM preventive reroute onto untried healthy senders (bounded), with a stable resolve-fail marker in message_queue.extra_data"
  - "PRIVACY_RESTRICTED now stamps the same clawback-able marker"
affects: [queue, send-path, checker-parity, pool-health]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Reactive suspect-rollback on the SEND path mirroring Phase 14/17 checker rollback"
    - "Stable code marker in message_queue.extra_data (never match localised RU error_message)"
    - "Bounded re-rotation via nr_tried_senders set (WR-15), running-campaign-only (WR-17)"

key-files:
  created:
    - "app/services/send_suspect.py"
    - "tests/test_send_suspect_rollback.py"
  modified:
    - "app/services/queue.py"

key-decisions:
  - "SUSPECT_RESOLVE_WINDOW_MINUTES=15 — suspect-attribution window (~15-40 sends at 20-55s), NOT a rate-limit constant, so the CLAUDE.md empirical-interval carve-out does not apply"
  - "Reactive rollback finalizes via _fail_item (not a direct fail); boundedness comes from nr_tried_senders exhaustion, not attempts"
  - "Reroute stamps resolve_fail_sender=new_sid so a later flag on the NEW sender is attributable; finalize stamps resolve_fail_sender=current sender"
  - "Defensive contacts rollback (tg_status→pending/suspect) is a forward-safe no-op today (send path sets no contacts.tg_resolved_by)"

patterns-established:
  - "Pattern: send-path false-negative is SUSPECT, never silently finalized — reroute onto untried healthy sender or claw back on later flag + purge poisoned cache"
  - "Pattern: preventive reroute (_reroute_resolve_fail) commits then returns True so the send loop short-circuits before finalize"

requirements-completed: [T2]

# Metrics
duration: 22min
completed: 2026-07-06
---

# Phase 260706-e8s Plan 01: Send-path Suspect-Resolve Rollback Summary

**Mirrors the Phase 14/17 checker suspect/rollback onto the message-send path: a NOT_REGISTERED/PRIVACY false-negative from a sliding-into-throttle sender is re-rotated onto an untried healthy sender (or clawed back when the sender is later flagged spam_limited/frozen) and the poisoned resolve cache is purged — the 07:31 incident now self-heals with no manual SQL.**

## Performance

- **Duration:** ~22 min
- **Started:** 2026-07-06T10:29Z
- **Completed:** 2026-07-06T10:51Z
- **Tasks:** 2
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments
- New `app/services/send_suspect.py::rollback_suspect_resolve_fails` — reactive claw-back of a just-flagged sender's windowed NOT_REGISTERED/PRIVACY failed rows onto a healthy UNTRIED pool sender (queue + sticky CCA in lock-step, `scheduled_at=NOW()`), plus unconditional purge of the flagged sender's fresh `is_registered=false` `contacts_cache` rows and a forward-safe defensive `contacts` rollback.
- `queue.py` PEER_FLOOD and ACCOUNT_FROZEN branches now invoke the reactive rollback right after `failover_cold_backlog(sender.id)`.
- New `RECIPIENT_NOT_IN_TELEGRAM` branch in the send loop: `_reroute_resolve_fail` re-rotates onto an untried healthy sender and finalizes (with a stable resolve-fail marker) only when the pool is exhausted — bounded (WR-15), running-campaign-only, best-effort when no healthy receiver.
- `PRIVACY_RESTRICTED` stamps the same clawback-able marker (no reroute — recipient-level).
- 11 acceptance tests (A–J) reproducing the 2026-07-06 07:31 incident end-to-end and all edge cases; full targeted regression (queue-lifecycle/failover/rotation/send) stays green.

## Task Commits

Each task followed TDD (test → feat):

1. **Task 1: Reactive suspect-rollback helper + tests A–F**
   - `32291a5` (test) — failing tests A–F
   - `95d93f2` (feat) — `app/services/send_suspect.py`
2. **Task 2: queue.py wiring + NOT_REGISTERED preventive reroute + marker stamping + tests G–J**
   - `65da3a2` (test) — failing tests G–J
   - `765ae22` (feat) — `app/services/queue.py`

**Plan metadata:** handled by the orchestrator (docs commit).

## Files Created/Modified
- `app/services/send_suspect.py` (created, 213 lines) — `rollback_suspect_resolve_fails(flagged_sender_id, db=None)` + `_rollback` core: windowed suspect-row select (running campaigns only), per-campaign healthy-pool resolution, bounded reroute to untried senders, unconditional cache purge, defensive contacts rollback. PII-safe logging.
- `app/services/queue.py` (modified) — `import json`; `_stamp_resolve_fail` + `_reroute_resolve_fail` helpers; new `RECIPIENT_NOT_IN_TELEGRAM` branch; marker stamp on `PRIVACY_RESTRICTED`; reactive-rollback wiring on `PEER_FLOOD` and `ACCOUNT_FROZEN`.
- `tests/test_send_suspect_rollback.py` (created) — tests A–J via the test-overlay (raw-SQL seeding + `_FakeTelegram` mock driving the real `QueueWorker._send_item`).

## Decisions Made
- **Window is a suspect-attribution window, not a rate control** — `SUSPECT_RESOLVE_WINDOW_MINUTES=15` touches none of `MIN/MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `FLOOD_HARD_THRESHOLD`, the 24h pause, or per-sender `rate_per_*` (confirmed unchanged by diff).
- **Finalize via `_fail_item`** (per plan) — boundedness is guaranteed by `nr_tried_senders` exhaustion, not by the attempts counter. Tests I/J seed `attempts=2` so the terminal `_fail_item` finalizes to `failed` in a single drive (deterministic).
- **Marker in `extra_data`, not on error text** — reactive rollback matches `extra_data->>'resolve_fail_code'`, never the localised RU `error_message` (MEMORY: `'ограничен'` substring-matched `'ограничений'`).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] asyncpg parameter type-inference conflict in the suspect-row SELECT**
- **Found during:** Task 1 (first GREEN run of tests A–F)
- **Issue:** `WHERE mq.sender_id = :sid ... AND mq.extra_data->>'resolve_fail_sender' = :sid` — asyncpg inferred `$1` as `uuid` from the `sender_id` comparison, then the text comparison failed with `operator does not exist: text = uuid`.
- **Fix:** Compare `mq.sender_id::text = :sid` so both usages of the single bound param are text.
- **Files modified:** `app/services/send_suspect.py`
- **Verification:** Tests A–F pass via the test-overlay.
- **Committed in:** `95d93f2` (Task 1 feat commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Cast-only correctness fix, no behavioural change, no scope creep.

## Issues Encountered
- **Test-overlay container-name collision on the prod server.** The base `docker-compose.yml` `db` service uses a fixed `container_name: outreach-platform-db`; the running prod db collides when `run` starts base deps. Resolved by starting the ephemeral `db-test` explicitly (`up -d db-test`) and running `api` with `--no-deps`, and passing `--env-file /root/apps/aimly/tg-outreach/.env` for `${VAR}` substitution (the worktree has no `.env`). The test overlay still overrides `DATABASE_URL` to `db-test`, so the conftest guard is satisfied and prod is never touched. No prod state was modified.

## User Setup Required
None - no external service configuration required. This is a pure code change (no migration — `message_queue.extra_data` JSONB already exists). Deploy is the standard `docker compose up -d --build api` (and `listener` shares the queue module import path but the behaviour lives in the API/queue worker).

## Next Phase Readiness
- Reactive + preventive send-path suspect handling is live behind the existing PEER_FLOOD/ACCOUNT_FROZEN and NOT_REGISTERED paths; no new worker or lifespan hook.
- Note for a follow-up (out of scope here): the reactive reroute deliberately does NOT rewrite `extra_data.resolve_fail_sender` when clawing a row onto a new sender B (per plan) — if B is later flagged, a row that was rerouted (still pending) is not re-clawed by resolve_fail_sender match; this is acceptable because pending rows are simply resent, and finalized rows always carry the finalizing sender's id.

---
*Phase: 260706-e8s*
*Completed: 2026-07-06*

## Self-Check: PASSED

- FOUND: app/services/send_suspect.py
- FOUND: tests/test_send_suspect_rollback.py
- FOUND: app/services/queue.py
- FOUND: .planning/quick/260706-e8s-t2-checker-false-negative-on-send-path-n/260706-e8s-SUMMARY.md
- Commits FOUND: 32291a5 (test A–F), 95d93f2 (feat send_suspect), 65da3a2 (test G–J), 765ae22 (feat queue wiring)
- Tests: 11/11 (A–J) GREEN; regression (queue-lifecycle/failover/rotation/send) 37/37 GREEN
- Rate-limit constants unchanged; no migration added
