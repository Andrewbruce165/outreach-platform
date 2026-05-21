---
phase: 02-tg-accounts-contacts
plan: 05
subsystem: api
tags: [fastapi, telethon, asyncio, sqlalchemy, postgres, workspace-isolation, background-worker, pydantic-v2]

requires:
  - phase: 02-tg-accounts-contacts (plan 02)
    provides: migration 013 + Contact/Sender ORM (tg_status, role='checker', auth_status, lifecycle_status)
  - phase: 02-tg-accounts-contacts (plan 04)
    provides: contacts router with _has_checker helper + tg_status='unchecked' D-20 fallback
  - phase: 02-tg-accounts-contacts (plan 01)
    provides: workspace-scoped onboarding (creates senders with role='checker' via verify-code role toggle)
  - phase: 01-workspace-foundation
    provides: AuthDep / AuthCtx (workspace_id on every request)
provides:
  - ContactCheckWorker async background task in API lifespan
  - JOIN LATERAL workspace-isolated SQL for pending contact pickup
  - POST /api/v1/contacts/recheck endpoint (contact_ids or folder_id)
  - has_checker bool field on GET /api/v1/workspace (D-20 UI banner data)
  - Workspace-scoped UPDATE pattern that silently no-ops cross-tenant ids
affects: [phase-03-agents, phase-04-campaigns]

tech-stack:
  added: []
  patterns:
    - "Singleton async worker via module-level instance + lifespan start/stop"
    - "JOIN LATERAL for workspace-isolated cross-table picks"
    - "Reuse of CheckerService.check_phones (no duplication of FloodWait / polite-delay logic)"
    - "Cross-tenant UPDATE filter: WHERE id = ANY(:ids) AND workspace_id = :wid"

key-files:
  created:
    - app/services/contact_check_worker.py
    - tests/test_contact_check_worker.py
    - tests/test_check_contacts.py
  modified:
    - app/routers/check_contacts.py (full rewrite — workspace-scoped recheck)
    - app/routers/workspace.py (added has_checker field + EXISTS query)
    - app/main.py (register contact_check_worker + check_contacts router)

key-decisions:
  - "Worker config env vars: CONTACT_CHECK_BATCH_SIZE=5, CONTACT_CHECK_POLL_INTERVAL=5 (RESEARCH §rate-limit — ~30 phones/min per checker, safe FloodWait margin)"
  - "JOIN LATERAL over plain JOIN — guarantees one checker per workspace match without GROUP BY noise; if multiple checkers exist later, LIMIT 1 keeps round-robin out-of-scope"
  - "POST /recheck returns 202 Accepted (not 200) — operation is asynchronous; UI polls GET /contacts to observe progress"
  - "Cross-tenant contact_ids → silent zero (marked_pending=0) rather than 404 — security by obscurity, mirrors workspace.py api-keys delete pattern"
  - "FloodWait partial: phones absent from results stay 'pending' (no explicit pending re-set) — worker picks them up on next tick automatically"

patterns-established:
  - "ContactCheckWorker — third async background worker in lifespan (after QueueWorker + WarmupWorker + OnboardingCleanupWorker). Future Phase 3 agent-scheduler will follow the same shape."
  - "WorkspaceResponse extension: when a new derived field is added (has_checker), helper function lives in workspace.py and is called from both GET and PATCH endpoints — single source of truth for the field."

requirements-completed: [CONT-04]

duration: 35min
completed: 2026-05-21
---

# Phase 2 Plan 05: ContactCheckWorker + recheck endpoint + has_checker exposure Summary

**Workspace-isolated async TG-presence pipeline: ContactCheckWorker drains contacts.tg_status='pending' via the workspace's role='checker' sender, POST /contacts/recheck rolls contacts back to pending, GET /workspace exposes has_checker for the UI banner — Phase 2 fully closed.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-05-21T18:00:00Z
- **Completed:** 2026-05-21T18:35:01Z
- **Tasks:** 2
- **Files modified:** 6 (3 created + 3 modified)

## Accomplishments

- **ContactCheckWorker async pipeline** — singleton background asyncio task wired into `app/main.py` lifespan. Polls `contacts.tg_status='pending'` every 5s in batches of 5; resolves phones via existing `checker_service.check_phones` (lock-per-checker_slug, FloodWait, polite delay reused — no duplication); writes back `tg_status='registered'|'not_registered'|'error'` + `tg_telegram_id`, `tg_username_resolved`, `tg_error`, `tg_checked_at`.
- **Workspace isolation via JOIN LATERAL** — single SQL pass picks pending contacts together with their workspace's active checker. Workspace A's checker can never resolve workspace B's contacts; D-20 `unchecked` contacts (created when no checker exists) are skipped automatically because the JOIN finds no match.
- **POST /api/v1/contacts/recheck** — workspace-scoped batch UPDATE to `tg_status='pending'` (with `tg_error` cleared). Supports both `contact_ids` and `folder_id` payloads via the existing `RecheckRequest` Pydantic schema. Returns `202 Accepted + {marked_pending: N}`. Cross-tenant ids silently no-op (filtered by `WHERE workspace_id = :wid`); nonexistent / cross-tenant folder_ids return 404 without leaking existence.
- **has_checker on GET /api/v1/workspace** — boolean field surfaced in `WorkspaceResponse` (and on PATCH response, since it shares the schema). Computed via single `COUNT(*) WHERE role='checker' AND auth_status='ok'`. UI uses it to render the "Add a dedicated checker account" banner per D-20.
- **22 new automated tests** — 11 unit (worker `_tick` lifecycle, no-pending, no-checker skip, registered/not_registered/error mapping, FloodWait partial, batched single call, cross-tenant isolation, `auth_status` gate) + 8 integration (recheck happy paths, missing-target 422, nonexistent-folder 404, cross-tenant zero, has_checker false/true/false-on-expired) + idempotent start/stop coverage.

## Task Commits

1. **Task 1: ContactCheckWorker service + lifespan registration** — `1cfc731` (feat)
2. **Task 2: check_contacts router rewrite (recheck) + has_checker exposure + integration tests** — `683c86b` (feat)

**Plan metadata:** _to be appended after metadata commit_

## Files Created/Modified

### Created

- **`app/services/contact_check_worker.py`** — `ContactCheckWorker` singleton class with `start() / stop() / _run() / _tick() / _apply_results()`. SQL uses `JOIN LATERAL (SELECT … FROM senders WHERE workspace_id = c.workspace_id AND role='checker' AND auth_status='ok' LIMIT 1) s ON TRUE` for workspace-isolated checker lookup. Reuses `checker_service.check_phones(...)`. Module-level singleton `contact_check_worker = ContactCheckWorker()`.
- **`tests/test_contact_check_worker.py`** — 11 unit tests with `monkeypatch.patch("app.services.contact_check_worker.checker_service.check_phones", new=AsyncMock(...))`. Uses existing `test_workspace / test_checker / test_contacts_factory` fixtures from conftest.
- **`tests/test_check_contacts.py`** — 8 integration tests using `async_client + valid_supabase_jwt + async_db_session` pattern from `tests/test_contacts.py`.

### Modified

- **`app/routers/check_contacts.py`** — full rewrite. Old version imported the removed `app.routers.auth.verify_api_key` shim (broken since Phase 1 D-14). New version uses `Depends(auth_dep)`, supports both `contact_ids` and `folder_id` via `RecheckRequest`, returns 202 + `{marked_pending: N}`. Folder ownership validated with workspace-scoped pre-SELECT (404 without leaking existence). Logs `[recheck] workspace=… source=… marked_pending=…`.
- **`app/routers/workspace.py`** — added `has_checker: bool = False` to `WorkspaceResponse`; added `_workspace_has_checker(db, workspace_id)` helper using `select(func.count(Sender.id))` with `role='checker' AND auth_status='ok'` filter; both GET and PATCH endpoints call the helper before constructing the response.
- **`app/main.py`** — imports `contact_check_worker` + `check_contacts` router; starts worker after `onboarding_cleanup_worker` in lifespan, stops it first in shutdown (LIFO with siblings); registers `check_contacts.router` after `contacts.router`.

## Decisions Made

- **Reused existing `CheckerService.check_phones`** rather than re-implementing ResolvePhone calls in the worker. `CheckerService` already encapsulates `asyncio.Lock` per `checker_slug`, FloodWait handling with partial result, `contacts_cache` cross-sender cache, and polite delay 2–3.5s. The worker is a thin orchestrator on top.
- **`JOIN LATERAL ... LIMIT 1`** rather than `JOIN ... GROUP BY` — keeps one row per pending contact with exactly its workspace's checker; if multiple checkers exist later, picking "any one" is good enough for v1 (round-robin / least-recently-used is Phase 3 territory).
- **Worker writes commits in a fresh `AsyncSessionLocal()`** — separated from the SELECT session so tests using a rollback `async_db_session` fixture can still observe UPDATEs (writes commit through the engine, then tests re-SELECT through their own session). Mirrors the queue / warmup worker pattern.
- **`tg_error = NULL` on recheck** — clears stale error messages so the UI doesn't display "PHONE_NOT_OCCUPIED" after a user retries.
- **Cross-tenant `contact_ids` → silent `marked_pending=0`** rather than 404 — matches `app/routers/workspace.py::revoke_api_key` security pattern (don't differentiate "not found" from "not yours").

## Deviations from Plan

None — plan executed exactly as written. The plan's `<action>` SQL skeleton, Pydantic shapes, and test outlines were followed verbatim. Added one extra unit test (`test_tick_skips_when_checker_auth_status_not_ok`) and one extra integration test (`test_workspace_has_checker_false_when_checker_auth_expired`) to cover the explicit `auth_status='ok'` predicate — both were implied by the plan's acceptance criteria but not enumerated.

## Issues Encountered

- **No working Python venv on macOS host** — `/private/tmp/check-venv` has SQLAlchemy 1.x incompatible with Python 3.14; `/Users/andrewbruce/myenv` lacks Telethon/FastAPI. Code therefore validated via `python3 -m py_compile` only on the macOS host; the project's actual test environment is the Docker `api` container (project deploys with `docker compose up -d --build api`). All file paths, SQL, Pydantic schemas, and imports follow the patterns already present in tests/conftest.py and tests/test_contacts.py, which the existing test suite executes against the Postgres test DB. CI / Docker run will execute the new tests.

## Phase 2 Closure

This was the last plan in Phase 2. All 16 Phase 2 requirements are now satisfied:

| Group | Requirements | Status |
|-------|--------------|--------|
| ONBD-01..05 | onboarding (phone/SMS/2FA/QR/workspace-bind/list) | closed (plan 02-01) |
| SNDR-01..03 | per-sender rate limits / proxy / derived status | closed (plan 02-02) |
| CONT-01..03, CONT-05 | CSV import / workspace contacts / push API / fields | closed (plan 02-04) |
| **CONT-04** | **TG presence check via checker (async pipeline)** | **closed (this plan)** |
| FLDR-01..03 | folder CRUD / move / auto-create-on-import | closed (plan 02-03) |

## User Setup Required

None — no external service configuration is required. The worker activates automatically when the API container starts. The `CONTACT_CHECK_BATCH_SIZE` and `CONTACT_CHECK_POLL_INTERVAL` env vars have safe defaults (5 / 5s) and only need overriding if Phase 3 tuning demands it.

## Next Phase Readiness

- **Phase 3 (Agents)** can start. AI contexts → agent abstraction is independent of contact-presence; the `contacts` table now reliably reports `tg_status` so Phase 4 campaigns can filter `WHERE tg_status='registered'` when picking targets.
- **D-20 follow-up deferred to v2**: auto-recheck of `unchecked` contacts when a workspace first adds a checker. Today the user dials `POST /contacts/recheck {folder_id|contact_ids}` manually. The endpoint is in place; UI can wire a "Recheck all unchecked" button against `GET /contacts?tg_status=unchecked` for now.
- **No blockers** for Phase 3.

## Self-Check: PASSED

Verified after writing SUMMARY.md:

- `app/services/contact_check_worker.py` — FOUND
- `app/routers/check_contacts.py` — FOUND (rewritten)
- `app/routers/workspace.py` — FOUND (extended)
- `tests/test_contact_check_worker.py` — FOUND
- `tests/test_check_contacts.py` — FOUND
- `app/main.py` — FOUND (contact_check_worker + check_contacts.router registered)
- Commit `1cfc731` — FOUND (Task 1)
- Commit `683c86b` — FOUND (Task 2)

---
*Phase: 02-tg-accounts-contacts*
*Completed: 2026-05-21*
