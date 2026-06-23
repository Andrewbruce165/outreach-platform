---
phase: 08-pool-management-and-even-distribution
plan: 03
subsystem: backend-api
tags: [pool-management, attach, detach, campaigns, wave-3, sender-lock, min-pool-guard]
requires:
  - tests/test_pool_endpoints.py (Plan 08-01 RED harness — POOL-01..06b)
  - app/services/rebalance.py::rebalance_on_attach (Plan 08-02)
  - campaigns.py start/resume validation chain (_load_campaign, _validate_workspace_owns_senders, _check_sender_lock, _campaign_to_response)
  - message_queue / campaign_contact_assignments / conversations / campaign_senders schema (migration 016)
provides:
  - "POST /api/v1/campaigns/{id}/senders (attach) — POOL-01/02/03"
  - "DELETE /api/v1/campaigns/{id}/senders/{sid} (detach) — POOL-04/05/06/06b"
  - "CampaignSenderAttachRequest{sender_id: UUID} request schema"
  - "CampaignSenderAttach.id computed field (mirrors sender_id) in the attached_senders[] contract"
affects:
  - Phase 09 (cold-contact failover) — detach intentionally does NOT auto-reassign the cold backlog (D-06); that is Phase 9's job
  - frontend Senders panel (Phase 08 UI plan) — consumes these two endpoints + attached_senders[].id
tech-stack:
  added: []
  patterns:
    - "attach: insert CampaignSender → flush → _check_sender_lock → rollback+409 on conflict (incoming sender in scope, byte-identical /start 409)"
    - "attach allowed on draft/paused/running — no INVALID_TRANSITION block, only _load_campaign 404 (D-01)"
    - "rebalance_on_attach gated behind `if c.status == 'running'` (D-08)"
    - "detach guards: MIN_POOL_GUARD (running + cnt<=1) and DETACH_BLOCKED_PENDING (cold-pending EXISTS, NOT EXISTS sent + NOT EXISTS conversations)"
    - "per-test JWT sub to dodge user_workspaces UNIQUE(supabase_user_id) cross-test binding contamination (session-scoped test schema)"
key-files:
  created: []
  modified:
    - app/routers/campaigns.py
    - app/schemas/__init__.py
    - tests/test_pool_endpoints.py
decisions:
  - "CampaignSenderAttach gained a computed `id` field mirroring sender_id — the attached_senders[] contract the tests/UI read keys entries by `id`; sender_id retained for Phase 4 back-compat (no breaking change, 24 campaign_router + 5 sender_lock tests still green)"
  - "Pool tests given per-test JWT subs (pool-add/pool-lock/...) — the Wave-0 harness shared sub='pool-user' which collides with the UNIQUE(user_workspaces.supabase_user_id) binding across the session-scoped test schema, so tests after the first 404'd on their own campaign. Fix mirrors the established test_campaign_router.py convention (u-list/u-lock/...)"
  - "Attach is idempotent (pre-check then no-op 200) to avoid the (campaign_id, sender_id) PK violation on re-attach"
  - "No migration — migration 016 already covers campaign_senders / CCA / message_queue"
metrics:
  duration: 16min
  tasks: 3
  files: 3
  completed: 2026-06-23
---

# Phase 8 Plan 03: Attach/Detach Pool Endpoints Summary

Added the two pool-management endpoints to the existing campaigns router — `POST /campaigns/{id}/senders` (attach) and `DELETE /campaigns/{id}/senders/{sid}` (detach) — reusing the start/resume validation chain (`_load_campaign`, `_validate_workspace_owns_senders`, `_check_sender_lock`, `_campaign_to_response`) byte-for-byte and wiring Plan 08-02's `rebalance_on_attach` into the running-campaign attach path. The pool is now mutable on draft/paused/running with the same isolation, lock, and guard invariants the rest of the campaign lifecycle enforces. All 7 RED tests from Plan 08-01 (POOL-01..06b) are now GREEN.

## What Was Built

### Task 1 — `CampaignSenderAttachRequest` schema + `CampaignUpdate` docstring (app/schemas/__init__.py)
- New thin request body `class CampaignSenderAttachRequest(BaseModel): sender_id: UUID` (D-02) — keeps the OpenAPI attach body clean and distinct from the read-only `CampaignSenderAttach` response sub-object.
- Updated the stale `CampaignUpdate` docstring (was: sender_ids «удали → создай новую») to point at `POST/DELETE /campaigns/{id}/senders` and note PATCH still intentionally ignores `sender_ids` (D-12). No field added to `CampaignUpdate`.

### Task 2 — `POST /campaigns/{id}/senders` attach endpoint (app/routers/campaigns.py)
Validation order (RESEARCH §"Attach Validation Reuse"):
1. `_load_campaign` (404 CAMPAIGN_NOT_FOUND, workspace-scoped — closes IDOR T1).
2. `_validate_workspace_owns_senders([sender_id])` (404 SENDER_NOT_FOUND, no leak — POOL-03/T1).
3. Idempotency pre-check on `(campaign_id, sender_id)` → 200 no-op if already attached (avoids PK violation).
4. `db.add(CampaignSender(...))` + `db.flush()`.
5. `_check_sender_lock` → on conflict `db.rollback()` + `raise HTTPException(409, {"code":"SENDER_LOCK_CONFLICT","conflicts":conflicts})` — byte-identical to start_campaign:621-627 (POOL-02/T2/T5). Insert-then-check-then-rollback so the incoming sender is in scope.
6. `if c.status == "running": await rebalance_on_attach(c.id, sender_id, db)` (D-08) — skipped for draft/paused.
7. `commit` + `refresh` + `_campaign_to_response`.

No status-transition block — attach is allowed on draft/paused/running (D-01); only `_load_campaign` 404 gates it. `rebalance_on_attach` imported at module top.

### Task 3 — `DELETE /campaigns/{id}/senders/{sid}` detach endpoint (app/routers/campaigns.py)
1. `_load_campaign`.
2. Min-pool guard (D-03): `count(CampaignSender)` via the same idiom start uses; `status=='running' and cnt<=1` → 409 MIN_POOL_GUARD. draft/paused may reach 0 senders.
3. Cold-pending guard (D-04/D-05): raw `text()` `EXISTS` scoped to the detached sender — pending row with `NOT EXISTS sent` (never delivered) AND `NOT EXISTS conversations` (not engaged) → 409 DETACH_BLOCKED_PENDING. The `NOT EXISTS conversations` clause is exactly what lets engaged dialogs through (D-05/POOL-06b).
4. `delete(CampaignSender)` of the row; `commit` + `refresh` + `_campaign_to_response`. No `UPDATE message_queue`, no rebalance — no auto-reassign of the cold backlog (D-06, deferred to Phase 9).

## Verification Results

- `pytest tests/test_pool_endpoints.py` (overlay): **7 passed** (POOL-01..06b GREEN; was 7 RED 404).
- Regression sample (overlay): `test_pool_endpoints + test_sender_lock + test_campaign_router + test_rotation_campaign + test_rebalance` → **46 passed**, no regressions. Confirms the `CampaignSenderAttach.id` computed field did not break the 24 campaign_router / 5 sender_lock tests that read `attached_senders[]`.
- Acceptance grep-gates — all pass: attach uses `Depends(auth_dep)` like start; 409 body is `{"code":"SENDER_LOCK_CONFLICT","conflicts":[...]}` (no new code); `rebalance_on_attach` only inside `if c.status == "running":`; detach has both `NOT EXISTS` clauses and contains no `UPDATE message_queue` / rebalance call.

All commands ran through the mandatory test-overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`), never the bare invocation (CLAUDE.md / 2026-05-26 DROP SCHEMA guard).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `attached_senders[]` entries lacked the `id` key the contract reads**
- **Found during:** Task 2 (test_attach_adds_sender).
- **Issue:** The RED test (and the UI) read `attached_senders[].id`, but `CampaignSenderAttach` only exposed `sender_id`. `KeyError: 'id'`.
- **Fix:** Added a Pydantic `@computed_field` `id` to `CampaignSenderAttach` that mirrors `sender_id`; kept `sender_id` for Phase 4 back-compat. Non-breaking — confirmed by the 46-test regression sample.
- **Files modified:** app/schemas/__init__.py
- **Commit:** 294c383

**2. [Rule 1 - Bug] Wave-0 pool tests not isolated — shared JWT sub collided with the workspace-binding UNIQUE constraint**
- **Found during:** Task 2 (test_attach_locked_sender_409 / test_attach_foreign_sender_404 404'd only when run after test_attach_adds_sender).
- **Issue:** `tests/test_pool_endpoints.py` used a single `sub="pool-user"` for all 7 tests. `user_workspaces` has `UNIQUE(supabase_user_id)` (migration 023) and the test schema is created once per session, so `pool-user` stayed bound to the first test's workspace; later tests' freshly-created campaigns lived in a different `test_workspace` → `_load_campaign` 404. Endpoints were correct; the harness was flaky.
- **Fix:** Gave each test a distinct sub (pool-add / pool-lock / pool-foreign / pool-detach / pool-min / pool-cold / pool-engaged), mirroring the established `test_campaign_router.py` convention (u-list / u-lock / ...). No assertion changed. Added an explanatory comment near `_auth_headers`.
- **Files modified:** tests/test_pool_endpoints.py
- **Commit:** 294c383

## Deferred Issues

**Pre-existing full-suite failures (NOT caused by this plan).** Per Plan 08-02's summary, the full suite has ~20 setup-time asyncpg errors in `test_migration_014.py` / `test_onboarding_reauth.py` (fixture-level, pre-existing). This plan touched only the campaigns router, the campaign schemas, and the pool test file; the targeted 46-test regression sample (every file that exercises `attached_senders` / sender-lock / rotation / rebalance) is fully green. Out of scope, already logged in the phase `deferred-items.md`.

## Known Stubs

None. Both endpoints are fully wired and exercised by the GREEN POOL-01..06b tests; `rebalance_on_attach` is now called by its documented consumer (the running-campaign attach path).

## Self-Check: PASSED

- `app/routers/campaigns.py` contains `attach_sender` and `detach_sender` — verified.
- `app/schemas/__init__.py` contains `CampaignSenderAttachRequest` — verified.
- Task commits 291139e, 294c383, ead79ec present in git history — verified.
