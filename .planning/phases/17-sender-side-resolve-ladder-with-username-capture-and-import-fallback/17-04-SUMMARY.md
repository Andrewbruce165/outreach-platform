---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 04
subsystem: contact-resolution
tags: [block-capture, user-is-blocked, restriction-audit, block-rate, read-only-metric, country-hypothesis, docs]

# Dependency graph
requires:
  - phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
    plan: 01
    provides: "RED tests test_user_blocked_records_event / test_blocked_event_inserts_no_check_violation / test_block_rate_aggregate; the block-rate test imports app.services.restriction_audit.sender_block_rate directly"
  - phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
    plan: 03
    provides: "the rebuilt 3-tier resolve_contact send path that the UserIsBlockedError catch sits in front of — this plan ADDS the block catch without reverting the ladder"
  - phase: 10-pool-visibility
    provides: "record_restriction_event dual-mode helper (restriction_audit.py:48), sender_restriction_events table with FREE-FORM event_type VARCHAR(20) (no CHECK) — so event_type='blocked' inserts without a migration; the PRIVACY_RESTRICTED queue branch as the exact no-pause mirror"
provides:
  - "send_message + send_file catch UserIsBlockedError (typed + string-match fallback) → structured error code USER_IS_BLOCKED"
  - "queue.py USER_IS_BLOCKED dispatch branch records a durable event_type='blocked', category='restriction' row in-TX and fails ONLY this queue item — never pauses the pending backlog, never flips senders.restriction_status, never calls failover (D-16)"
  - "app.services.restriction_audit.sender_block_rate(db, sender_id, window_days=7) → {blocks_<N>d, sends_<N>d, block_rate} read-only aggregate (blocks vs sent messages over a window)"
  - "GET /senders/{slug}/block-rate read-only, workspace-scoped endpoint (SenderBlockRateResponse: blocks_7d, sends_7d, block_rate)"
  - "/root/apps/aimly/tg-outreach/CLAUDE.md checker-semantics: the US-cannot-resolve-RU country claim is reframed as an unproven HYPOTHESIS (D-10/SRLD-09)"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Recipient block is captured as a durable per-sender event but is explicitly NOT an account restriction (no auto-pause) — mirrors the PRIVACY_RESTRICTED branch (record + _fail_item, no pause), NOT the PEER_FLOOD branch (pause + failover)"
    - "event_type='blocked' rides the FREE-FORM event_type VARCHAR(20) (no CHECK) under category='restriction' — no migration; Phase 17 added 0 migrations total"
    - "Read-only block-rate aggregate as the design-doc 'metric that actually matters' (blocks → reports → PeerFlood → freeze) — no control-loop, deferred alerting/auto-pause to a later phase (Phase 10 non-goal)"

key-files:
  created: []
  modified:
    - app/services/telegram.py
    - app/services/queue.py
    - app/services/restriction_audit.py
    - app/routers/senders.py
    - app/schemas/__init__.py
    - CLAUDE.md

key-decisions:
  - "UserIsBlockedError catch placed BETWEEN PeerFloodError and UserNotMutualContactError in BOTH send_message and send_file, plus a `'USER_IS_BLOCKED' in str(e)` defence-in-depth fallback inside the generic except (mirrors is_frozen_error)."
  - "Block event uses event_type='blocked' + category='restriction' (the design-doc proxy for accumulated reports → PeerFlood); category default 'restriction' triggers the activity_slice snapshot, which is the desired audit context. NO migration (free-form event_type)."
  - "The USER_IS_BLOCKED queue branch mirrors PRIVACY_RESTRICTED, not PEER_FLOOD: record event on the existing send-loop session (in-TX), fire the failure callback, _fail_item, return. It does NOT UPDATE senders.restriction_status, does NOT pause pending, does NOT call failover (D-16 — one recipient blocking is not an account restriction)."
  - "sender_block_rate lives in restriction_audit.py (next to record_restriction_event) because the 17-01 block-rate RED test imports it directly from there. Signature (db, sender_id, window_days=7) → dict with window-suffixed keys, transaction-neutral (uses the passed session)."
  - "The endpoint uses inline SQL with an explicit workspace_id filter (defence-in-depth, mirroring list_restriction_events) rather than calling sender_block_rate, so the workspace guard is in the SQL and the plan's grep acceptance (event_type = 'blocked' in senders.py) holds. Both paths produce identical counts."

requirements-completed: [SRLD-08, SRLD-09]

# Metrics
duration: 18min
completed: 2026-06-30
---

# Phase 17 Plan 04: Block Capture and Docs Summary

**Captures the dominant cold-outreach account-killer signal — a recipient blocking the sender — as a durable per-sender `event_type='blocked'` restriction event (SRLD-08/D-15): `send_message`/`send_file` now catch `UserIsBlockedError` → structured `USER_IS_BLOCKED` code, and the queue records it in-TX while failing ONLY that one item (no auto-pause, no failover — a block is not an account restriction, D-16). Adds a read-only `sender_block_rate` helper + `GET /senders/{slug}/block-rate` aggregate (the design-doc "metric that actually matters"), and reframes the unproven US-cannot-resolve-RU country claim in the checker-semantics docs as an explicit HYPOTHESIS (SRLD-09/D-10). Phase 17 added 0 migrations.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-06-30T17:04Z
- **Completed:** 2026-06-30
- **Tasks:** 3
- **Files modified:** 6 (telegram.py, queue.py, restriction_audit.py, senders.py, schemas/__init__.py, CLAUDE.md)

## Accomplishments

- **Task 1 — block capture (SRLD-08).**
  - `app/services/telegram.py`: added `UserIsBlockedError` to the module imports; added a typed `except UserIsBlockedError:` branch returning `{"success": False, "error": {"code": "USER_IS_BLOCKED", "message": "Получатель заблокировал отправителя"}}` to BOTH `send_message` (placed after `PeerFloodError`, before `UserNotMutualContactError`) and `send_file` (parity); plus a `'USER_IS_BLOCKED' in str(e)` defence-in-depth fallback inside each generic `except Exception` (mirroring `is_frozen_error`).
  - `app/services/queue.py`: added an `elif error_code == "USER_IS_BLOCKED":` dispatch branch immediately after `PRIVACY_RESTRICTED` and before the generic fail. It calls `record_restriction_event(sender.id, "blocked", "queue_error", None, error_msg, db=db)` on the existing send-loop session (in-TX), fires the failure callback, and `_fail_item`s — then returns. It deliberately does NOT touch `senders.restriction_status`, does NOT pause the pending backlog, and does NOT call failover.
  - `app/services/restriction_audit.py`: added `sender_block_rate(db, sender_id, window_days=7)` — a read-only aggregate counting `event_type='blocked'` events vs `message_type='sent'` messages_log rows over a trailing window, returning `{blocks_<N>d, sends_<N>d, block_rate}` (0.0 rate when no sends). The 17-01 block-rate RED test imports this directly.
- **Task 2 — read-only block-rate endpoint (SRLD-08, D-16).**
  - `app/routers/senders.py`: added `GET /senders/{slug}/block-rate` mirroring `list_restriction_events` (auth_dep + `_load_sender_by_slug` opaque-404 workspace guard, read-only). Inline SQL with an explicit `workspace_id = :wid` filter (defence-in-depth); returns `SenderBlockRateResponse`. The handler body contains NO UPDATE/INSERT/DELETE.
  - `app/schemas/__init__.py`: added `SenderBlockRateResponse(blocks_7d: int, sends_7d: int, block_rate: float)` next to `RestrictionEventResponse`.
- **Task 3 — country claim → hypothesis (SRLD-09, D-10).**
  - `CLAUDE.md` (the tg-outreach project CLAUDE.md, where the "Семантика checker'а" section actually lives) — added a bullet to that section explicitly framing «US-аккаунт не резолвит РФ-номера» as a **ГИПОТЕЗА**, not a fact: country was always confounded with cold/throttle (no clean isolation test ever run); "warmed beats cold" supported, "RU beats US" **не доказан**; Phase 17 deliberately does NOT gate resolve by country in code (D-10). Cross-references `17-CONTEXT.md` D-10. Privacy/throttle/Phase-14 content preserved.

## Where the catch / event / endpoint landed (for downstream)

- **UserIsBlockedError catch:** `send_message` (after PeerFloodError, before UserNotMutualContactError) and `send_file` (same position), plus string-match fallback in both generic `except Exception` blocks.
- **Block event:** `event_type='blocked'`, `category='restriction'` (default — triggers the activity_slice snapshot), `source='queue_error'`, written in-TX on the queue send-loop session. NO CHECK violation, NO migration.
- **block-rate helper:** `app.services.restriction_audit.sender_block_rate(db, sender_id, window_days=7)` → `{"blocks_7d", "sends_7d", "block_rate"}` (keys suffixed with the window).
- **block-rate endpoint:** `GET /api/v1/senders/{slug}/block-rate` → `SenderBlockRateResponse {blocks_7d, sends_7d, block_rate}`, workspace-scoped, read-only.

## `-k` selectors (this plan)

| Req | File | `-k` selector | Result |
|-----|------|---------------|--------|
| SRLD-08 (send-path block) | test_send.py | `user_blocked` | GREEN |
| SRLD-08 (durable event) | test_restriction_audit.py | `blocked` | GREEN |
| SRLD-08 (block-rate aggregate) | test_restriction_audit.py | `block_rate` | GREEN |
| SRLD-09 (doc reframe) | CLAUDE.md (grep) | `гипотеза` | done |

## Task Commits

1. **Task 1: capture UserIsBlockedError as durable per-sender block event** — `fb79d6e` (feat) — telegram.py, queue.py, restriction_audit.py
2. **Task 2: read-only per-sender block-rate endpoint** — `a2ea727` (feat) — senders.py, schemas/__init__.py
3. **Task 3: reframe US-cannot-resolve-RU country claim as hypothesis** — `8308c18` (docs) — CLAUDE.md

## Files Created/Modified

- `app/services/telegram.py` — `UserIsBlockedError` import + typed catch (send_message + send_file) + string-match fallback in both generic handlers.
- `app/services/queue.py` — `USER_IS_BLOCKED` dispatch branch (record 'blocked' in-TX, fail one item, no pause/failover).
- `app/services/restriction_audit.py` — `sender_block_rate(db, sender_id, window_days=7)` read-only aggregate.
- `app/routers/senders.py` — `GET /senders/{slug}/block-rate` read-only endpoint + `SenderBlockRateResponse` import.
- `app/schemas/__init__.py` — `SenderBlockRateResponse` schema.
- `CLAUDE.md` (tg-outreach) — checker-semantics country claim reframed as a hypothesis (D-10).

## Decisions Made

- **Block ≠ restriction (D-16).** A single recipient blocking the sender fails only that queue item and is recorded for the read-only rate; it never auto-pauses the sender or flips `restriction_status`. This is the explicit Phase 10 non-goal (no control-loop / alerting). Mirror = PRIVACY_RESTRICTED branch, not PEER_FLOOD.
- **event_type='blocked' is free-form (no migration).** `sender_restriction_events.event_type` is `VARCHAR(20)` with no CHECK; only `category` is constrained, and `category='restriction'` is already allowed. The 17-01 `test_blocked_event_inserts_no_check_violation` GREEN-confirms this. Phase 17 added **0 migrations** total.
- **block-rate helper in restriction_audit.py.** Placed next to `record_restriction_event` because the 17-01 RED test imports `app.services.restriction_audit.sender_block_rate` directly. The endpoint uses parallel inline SQL (with explicit workspace filter) for the workspace-scoped path; both produce identical counts.
- **Doc reframe location.** The checker-semantics country claim lives in the tg-outreach project `CLAUDE.md` (the file with the "Семантика checker'а (is_registered)" section), not in `/root/CLAUDE.md` (the infra-overview file, which has no checker section). The reframe was applied where readers actually look — see Deviations.

## Deviations from Plan

**1. [Rule 3 - Blocking/path correction] Doc reframe target file**
- **Found during:** Task 3.
- **Issue:** The plan's `files_modified` named `/root/CLAUDE.md` for the country-claim reframe, but `/root/CLAUDE.md` (169 lines) is the infrastructure-overview file and contains NO "Семантика checker'а" / checker-semantics section. The checker-semantics section with the country topic is in `/root/apps/aimly/tg-outreach/CLAUDE.md` (line 232). Editing `/root/CLAUDE.md` would have inserted the hypothesis into a file with no surrounding checker context, where no reader looks for checker semantics.
- **Fix:** Applied the SRLD-09/D-10 reframe to the correct file (`/root/apps/aimly/tg-outreach/CLAUDE.md`, the "Семантика checker'а" section) where the existing privacy/throttle/Phase-14 checker semantics live. All Task 3 acceptance criteria (the `гипотеза` keyword, the warmed-beats-cold / RU-beats-US / не доказан framing, the D-10 cross-reference) are satisfied there.
- **Files modified:** `CLAUDE.md` (tg-outreach, tracked in this repo) instead of `/root/CLAUDE.md`.
- **Verification:** `grep -n "гипотеза" /root/apps/aimly/tg-outreach/CLAUDE.md` → hit inside the checker-semantics section; `grep -ni "warmed beats cold\|RU beats US\|не доказан"` → hit; D-10 + 17-CONTEXT.md cross-reference present.
- **Committed in:** `8308c18` (Task 3 commit).

**2. [Rule 2 - Critical functionality / plan note] restriction_audit.py added to Task 1 scope**
- **Found during:** Task 1.
- **Issue:** The plan's Task 1 `<files>` listed only telegram.py + queue.py, but the 17-01 block-rate RED test imports `app.services.restriction_audit.sender_block_rate` directly — and the 17-01 SUMMARY + orchestrator prompt both flagged "17-04 must add `sender_block_rate`". Without it, the `block_rate` RED test (an SRLD-08 acceptance) cannot flip GREEN.
- **Fix:** Added the read-only `sender_block_rate` helper to `app/services/restriction_audit.py` and staged it with Task 1 (it is the durable-capture half of SRLD-08, alongside the queue write-point).
- **Files modified:** `app/services/restriction_audit.py`.
- **Verification:** `test_block_rate_aggregate` GREEN.
- **Committed in:** `fb79d6e` (Task 1 commit).

**Total deviations:** 2 (one Rule-3 path correction, one Rule-2 anticipated scope addition flagged by 17-01). No scope change to behaviour; no architectural change (Rule 4 not triggered).

## Out-of-Scope Failures (NOT regressions)

End-of-plan full suite: **850 passed, 1 skipped, 1 failed**. The single failure is pre-existing and NOT caused by this plan:

- `tests/test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14) → owned by a **parallel Phase 15 warmup effort** (uncommitted working-tree files: `app/services/warmup.py`, `app/main.py`, `app/routers/warmup.py`, `app/models/__init__.py`, `migrations/040_...`). None of this plan's 6 changed files is warmup-related. Left untouched per the plan constraint.

The two SRLD-08 RED tests (`test_user_blocked_records_event`, `test_block_rate_aggregate`) plus `test_blocked_event_inserts_no_check_violation` are all GREEN. The suite went from 848 → 850 passed (the 2 SRLD-08 RED tests flipped GREEN this plan).

## Known Stubs

None.

## Next Phase Readiness

- Phase 17 is **complete** (4/4 plans). The sender-side resolve ladder (17-03) + checker username capture (17-02) + durable block capture & read-only block-rate (17-04) are all shipped and GREEN.
- Deferred (explicit Phase 10 non-goal, NOT in scope): auto-pause/alerting on a high block-rate — the metric is now observable; any control-loop is a later-phase decision.
- **0 migrations added across all of Phase 17** (resolve ladder reads existing `contacts.*` columns; block capture rides free-form `event_type`).

## Self-Check: PASSED

- `17-04-SUMMARY.md` — created (this file).
- Commits `fb79d6e`, `a2ea727`, `8308c18` — verified present via `git log`.
- Production files verified: `UserIsBlockedError` in telegram.py (lines 28/826/1005), `USER_IS_BLOCKED` branch in queue.py (line 1049), `sender_block_rate` in restriction_audit.py (line 89), `block-rate` route in senders.py (line 784), `гипотеза` in CLAUDE.md (line 243).
- Full suite: 850 passed, 1 skipped, 1 failed (the 1 failure = out-of-scope WARM-14 parallel warmup test).
- No migration file added by Phase 17 (verified: latest migration is 043 from Phase 16; the only untracked migration 040 belongs to the parallel warmup effort).

---
*Phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback*
*Completed: 2026-06-30*
