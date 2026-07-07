---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 05
subsystem: api
tags: [fastapi, background-worker, async-job, account-import, skip-locked, status-poll, multitenancy]

# Dependency graph
requires:
  - phase: 21-01-schema-foundation-and-test-scaffold
    provides: "AccountImportStaging/Job/Item ORM (mig 051) + the RED contract test_worker_drives_items_and_status"
  - phase: 21-03-preview-unzip-pair-stage
    provides: "POST /import/preview staging + unpack_and_pair(zip_bytes) -> {matched, unpaired, malformed} — re-read at confirm"
  - phase: 21-04-per-account-import-routine
    provides: "import_one_account(db, item) -> result_str — the per-file routine the worker calls by module ref"
provides:
  - "POST /api/v1/accounts/import/{import_id}/confirm — re-reads the staged ZIP, creates ONE running AccountImportJob (batch role, D-16) + N pending AccountImportItem rows (one per matched pair, each carrying its own session bytes + parsed vendor JSON), returns job_id + total (202)"
  - "GET /api/v1/accounts/import/{job_id}/status — workspace-scoped processed/total + a secrets-free per-item list (basename/status/result/reason only)"
  - "app/services/account_import_worker.py — AccountImportWorker: claim ONE pending item FOR UPDATE SKIP LOCKED → processing (committed) → import_one_account off the claim TX → terminal ok/failed + result code + session_blob NULLed + job.processed bump + job→done at total; never dies on a per-item error (D-10/IMPT-07)"
  - "app/config.py — ACCOUNT_IMPORT_POLL_INTERVAL knob (default 3s); lifespan registration in app/main.py next to the other 7 workers"
affects: [21-06-frontend-and-handoff]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-step async import (D-09): a fast confirm returns job_id (202) so the ~1-3s/account connect+get_me never hits the nginx/HTTP timeout on a large batch; a background worker drains items and the UI polls a status endpoint"
    - "Claim/commit/never-die worker mirrors KnowledgeIngestWorker: claim ONE pending row FOR UPDATE SKIP LOCKED + committed processing flip (UI sees progress) → network work in a FRESH session OFF the claim TX → terminal status + job progress in a third committed TX; loop logs+continues on any per-item error"
    - "Per-account routine called by MODULE REFERENCE (account_import.import_one_account) so tests monkeypatch the module attribute — same seam as the KB worker patching embed_texts"
    - "Result-code STRING → terminal status mapping: {imported, already_connected} → ok, everything else → failed; session_blob (live auth_key) NULLed once the item is terminal (security)"
    - "Confirm/status are workspace-scoped and secrets-free by construction: the status ImportStatusItem model exposes ONLY basename/status/result/reason — never session_blob, vendor_json (holds twoFA), or any secret"

key-files:
  created:
    - app/services/account_import_worker.py
  modified:
    - app/routers/account_import.py
    - app/main.py
    - app/config.py
    - tests/test_account_import_worker.py

key-decisions:
  - "Worker calls the AUTHORITATIVE 21-04 signature import_one_account(db, item) -> result_str (NOT the plan's illustrative (db, ws, role, basename, bytes, vendor) -> dict). The worker builds an item dict {id, job_id, workspace_id, basename, session_blob(bytes), vendor_json} and maps the returned code string to a terminal status; import_one_account resolves role from the item's job itself."
  - "Result-code → terminal status: {imported, already_connected} → 'ok' (already_connected is a successful dedup per D-14/IMPT-06, not an error); all failure codes (auth_failed/convert_failed/banned/not_authorized/connect_failed/malformed_json/failed) → 'failed'. Matches the RED test (imported→ok, auth_failed→failed)."
  - "reason column = the result-code for a failed item (a UI-visible reason), NULL for an ok item. sender_id is left NULL — the 21-04 routine returns a bare string, not the created sender id; populating it would need a fragile re-query and no test/must-have requires it."
  - "Double-submit / re-confirm allowed (a fresh job each time) — the worker dedups per phone (pre-connect, D-14) and telegram_id (post-connect, IMPT-06), so a re-confirm never creates duplicate senders. Simplest choice, documented per the plan's DISCRETION note."
  - "Role validated via Literal['sender', 'checker'] (Pydantic → structured 422 on an invalid/missing role, handled by main.py's RequestValidationError handler) rather than a hand-rolled INVALID_ROLE check."

patterns-established:
  - "Async confirm → background worker → status poll is the standard shape for any slow-per-item batch (mirrors csv contact import's 202 + ContactCheckWorker, and KB upload's 202 + KnowledgeIngestWorker)"

requirements-completed: [IMPT-02, IMPT-07]

# Metrics
duration: 23min
completed: 2026-07-07
---

# Phase 21 Plan 05: Async Confirm + Worker + Status Summary

**`POST /import/{import_id}/confirm` turns a staged preview into a background job (202 + job_id), `AccountImportWorker` drains each `account_import_items` row through the 21-04 `import_one_account` routine (claim FOR UPDATE SKIP LOCKED → processing → terminal ok/failed, session bytes NULLed, job.processed→done, never dying on a per-item error), and `GET /import/{job_id}/status` polls a secrets-free processed/total + per-file report.**

## Performance

- **Duration:** 23 min
- **Started:** 2026-07-07T08:32:44Z
- **Completed:** 2026-07-07T08:56:39Z
- **Tasks:** 2
- **Files modified:** 5 (1 created, 4 modified)

## Accomplishments

- `POST /api/v1/accounts/import/{import_id}/confirm` (202): loads the workspace-scoped `AccountImportStaging` (404 `IMPORT_NOT_FOUND` / 410 `IMPORT_EXPIRED` / structured 4xx on a corrupt re-read), re-runs `unpack_and_pair`, creates ONE running `AccountImportJob` (batch role D-16, `total`=matched count, `processed`=0) + one pending `AccountImportItem` per matched pair (carrying its own `session_blob` bytes + parsed `vendor_json`), and returns `{job_id, total}`.
- `GET /api/v1/accounts/import/{job_id}/status`: workspace-scoped `{job_id, status, total, processed, items:[{basename, status, result, reason}]}` — the item model is secrets-free by construction (no `session_blob`/`vendor_json`/`twoFA`); unknown job → 404 `JOB_NOT_FOUND`.
- `app/services/account_import_worker.py`: `AccountImportWorker` mirroring `KnowledgeIngestWorker` — `start()`/`stop()`/`_run()` (never-die loop) + `_tick()`: claim ONE pending item `FOR UPDATE SKIP LOCKED`, commit a `processing` flip (UI sees progress), call `account_import.import_one_account(db, item)` in a fresh session OFF the claim TX (module-ref so tests patch it), then in a third committed TX write terminal `ok`/`failed` + result code + `reason`, NULL the live `session_blob`, bump `account_import_jobs.processed`, and flip the job to `done` once `processed >= total`.
- Lifespan wiring in `app/main.py` (`account_import_worker.start()`/`.stop()` next to `kb_ingest_worker` — now 8 background workers) + `ACCOUNT_IMPORT_POLL_INTERVAL` config knob (default 3s).

## Task Commits

Each task was committed atomically:

1. **Task 1: POST confirm + GET status endpoints** — `17e4759` (feat)
2. **Task 2: AccountImportWorker + lifespan + config knob** — `549f7c4` (feat)

**Plan metadata:** _(this commit)_ (docs: complete plan)

## Files Created/Modified

- `app/services/account_import_worker.py` — `AccountImportWorker` (claim→processing→ok/failed, never-die) + module-scope singleton `account_import_worker`.
- `app/routers/account_import.py` — `ImportConfirmRequest`/`ImportConfirmResponse`/`ImportStatusItem`/`ImportStatusResponse` + `POST /import/{import_id}/confirm` (202) + `GET /import/{job_id}/status`.
- `app/main.py` — import + `start()`/`stop()` of `account_import_worker` in the lifespan.
- `app/config.py` — `account_import_poll_interval` (`ACCOUNT_IMPORT_POLL_INTERVAL`, default 3s).
- `tests/test_account_import_worker.py` — added 3 endpoint integration tests (`test_confirm_endpoint_creates_job_and_items`, `test_confirm_expired_staging_returns_410`, `test_status_endpoint_reports_progress`) alongside the RED worker contract (now green).

## Decisions Made

- **Worker calls `import_one_account(db, item) -> str`, the 21-04 authoritative signature** — not the plan's illustrative `(db, ws, role, basename, bytes, vendor) -> dict`. See Deviations.
- **Result-code → status:** `{imported, already_connected}` → `ok`; every failure code → `failed`. `already_connected` is a successful dedup (D-14/IMPT-06).
- **`reason` = result-code on failure, NULL on ok; `sender_id` left NULL** (the routine returns a bare string, not the sender id — no test/must-have needs it).
- **Double-submit allowed** (fresh job per confirm) — the worker dedups per phone + telegram_id, so a re-confirm never duplicates senders.
- **Role validated via `Literal['sender', 'checker']`** — Pydantic emits a structured 422 for an invalid/missing role.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Worker wired to the authoritative `import_one_account(db, item) -> str`, not the plan's dict signature**
- **Found during:** Task 2 (reading the 21-04 SUMMARY + `tests/test_account_import_worker.py::test_worker_drives_items_and_status`)
- **Issue:** The plan's Task 2 action told the worker to call `import_one_account(db, workspace_id, role, basename, bytes(session_blob), vendor_json)` and consume a dict (`result['status']`/`result['result']`/`result['reason']`/`result['sender_id']`). But 21-04 (per its own documented deviation, following the 21-01 RED contract) shipped `import_one_account(db, item) -> result_str` — a single item-row dict argument returning a bare result-code string. The RED worker test confirms it: the stub is `async def _fake_import(db, item)` returning `"imported"`/`"auth_failed"`, and the test asserts `imported→ok`, `auth_failed→failed`. Implementing the plan's dict call would fail collection + the assertions.
- **Fix:** The worker builds an item dict `{id, job_id, workspace_id, basename, session_blob(bytes), vendor_json}`, calls `account_import.import_one_account(db, item)`, and maps the returned code string to a terminal `ok`/`failed` status (`_OK_RESULTS = {"imported", "already_connected"}`). `role` is resolved inside the routine from the item's job (21-04 behaviour), so it is not passed separately.
- **Files modified:** app/services/account_import_worker.py
- **Verification:** `tests/test_account_import_worker.py::test_worker_drives_items_and_status` green (items pending→processing→ok/failed, job.processed→2, job→done).
- **Committed in:** `549f7c4` (Task 2 commit)

**2. [Rule 2 - Missing Critical] Added confirm/status endpoint integration tests (the RED scaffold had none)**
- **Found during:** Task 1 (the plan's Task-1 verify `-k "confirm or status"` and acceptance #7 "tests pass" — but the RED file `test_account_import_worker.py` held only the worker test, whose name coincidentally matches `status`; there were NO endpoint-level confirm/status tests)
- **Issue:** Without endpoint tests the confirm job/item creation, the 410-on-expiry / 404-on-unknown mapping, and the secrets-free status payload were only grep-verified, not exercised. Mirrors the 21-03 precedent (which added `test_preview_endpoint_stages_and_returns` for the same reason).
- **Fix:** Added `test_confirm_endpoint_creates_job_and_items` (preview→confirm→202+job_id+total, one pending item per matched pair carrying session bytes, orphan NOT imported, twoFA absent), `test_confirm_expired_staging_returns_410` (expired staging → 410 `IMPORT_EXPIRED`, unknown id → 404 `IMPORT_NOT_FOUND`), and `test_status_endpoint_reports_progress` (processed/total + per-item shape, `leak-me`/`session_blob`/`vendor_json` absent, unknown job → 404 `JOB_NOT_FOUND`). Named with `confirm`/`status_endpoint` so the plan's `-k` picks them up without dragging in the worker test before Task 2.
- **Files modified:** tests/test_account_import_worker.py
- **Verification:** the 3 endpoint tests pass in isolation (`-k "confirm or status_endpoint"` → 3 passed); the full worker file → 4/4 passed.
- **Committed in:** `17e4759` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 bug reconciling the worker to the authoritative 21-04 contract, 1 missing critical test — both follow the 21-03/21-04 "the RED test is the contract" precedent).
**Impact on plan:** Behaviour delivered is exactly the plan's intent (async confirm → job+items → never-die worker → status poll). Only the worker's call signature/return-mapping changed to match the shipped 21-04 routine, and endpoint coverage was added. No product-scope creep.

## Issues Encountered

- **Full-suite shared-DB test-ordering pollution (pre-existing, out of scope).** A full `pytest -q` via test-overlay reports **89 failed + 115 errors** (`sqlalchemy.exc` setup errors) across unrelated files (`test_send*`, `test_rotation_campaign`, `test_sender_lock`, `test_restriction_audit`, …). Each passes in isolation (`test_sender_lock.py` → 5/5 alone). **Verified pre-existing:** the FULL suite at the parent commit `f9f718f` (21-04 completion, before any 21-05 code) reports the identical **89 failed / 115 errors / 853 passed**; my HEAD `549f7c4` reports **89 failed / 115 errors / 856 passed** — the only delta is my 3 new endpoint tests passing. 21-05 cannot be the cause: the `async_client` conftest fixture uses `ASGITransport(app=app)` with no `LifespanManager`, so the lifespan (and thus `account_import_worker.start()`, my only cross-cutting change) never runs in tests. Logged to `deferred-items.md` (item 3). Matches the Phase-20 SUMMARY note. Belongs to a test-isolation/conftest hardening task.
- 21-05's own targeted files are fully green: `tests/test_account_import_worker.py` 4/4.

## Known Stubs

None. The confirm endpoint reads the real staged ZIP and creates real job/item rows carrying real session bytes; the status endpoint reads live job/item rows; the worker calls the real `import_one_account` (stubbed only in `test_worker_drives_items_and_status` via the module-ref monkeypatch, exactly as intended). `sender_id` on the item is intentionally left NULL (the 21-04 routine returns a result-code string, not the sender id) — documented, not a stub.

## User Setup Required

None — no external service configuration required. `ACCOUNT_IMPORT_POLL_INTERVAL` has a safe default (3s) and is env-overridable without a redeploy. Not yet deployed to prod (api rebuild pending for the phase — the new worker starts in the api container's lifespan).

## Next Phase Readiness

- IMPT-02 delivered end-to-end (confirm → async job → worker → status poll); IMPT-07 per-file report surfaced in the status endpoint.
- **21-06** (frontend + handoff) can now wire the two-step UI: `POST /import/preview` → show matched/unpaired/malformed → pick role → `POST /import/{import_id}/confirm` → poll `GET /import/{job_id}/status` for processed/total + per-file results. openapi.json regeneration for the sibling repo is a 21-06 task.
- Deploy note: on the next api rebuild the `AccountImportWorker` starts alongside the other 7 workers; the `idx_aii_pending` partial index (mig 051) keeps the claim SELECT cheap.

---
*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Completed: 2026-07-07*

## Self-Check: PASSED

- Created file `app/services/account_import_worker.py` + `21-05-SUMMARY.md` exist on disk.
- Task commits `17e4759` (feat, confirm+status) and `549f7c4` (feat, worker+lifespan+knob) present in git log.
- Targeted tests green via test-overlay: `tests/test_account_import_worker.py` 4/4 (worker contract + 3 new endpoint tests). All Task-1 and Task-2 acceptance greps pass. Full-suite failures are pre-existing shared-DB ordering pollution (89 failed/115 errors identical at parent `f9f718f`), documented in Issues Encountered + deferred-items.md.
