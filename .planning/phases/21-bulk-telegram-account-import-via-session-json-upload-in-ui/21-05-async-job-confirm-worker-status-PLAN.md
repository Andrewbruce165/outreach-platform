---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 05
type: execute
wave: 4
depends_on: ["21-04"]
files_modified:
  - app/services/account_import_worker.py
  - app/routers/account_import.py
  - app/main.py
  - app/config.py
  - tests/test_account_import_worker.py
autonomous: true
requirements: [IMPT-02, IMPT-07]
must_haves:
  truths:
    - "Confirm creates a job + N pending item rows (one per matched pair) with the batch role and returns job_id immediately (202)"
    - "AccountImportWorker claims one pending item at a time, runs import_one_account, and writes a terminal ok/failed status — never dying on a per-item error"
    - "The status endpoint reports processed/total + per-item result rows so the UI can poll progress"
    - "A duplicate confirm / expired staging returns a clean 4xx, not a 500; expired staging → 410"
    - "Item session_blob (live auth_key) is cleared once the item reaches a terminal state"
  artifacts:
    - path: "app/services/account_import_worker.py"
      provides: "AccountImportWorker mirroring KnowledgeIngestWorker (claim→processing→ok/failed)"
      contains: "class AccountImportWorker"
    - path: "app/routers/account_import.py"
      provides: "confirm + status endpoints"
      contains: "/confirm"
  key_links:
    - from: "AccountImportWorker._tick"
      to: "app.services.account_import.import_one_account"
      via: "module-ref call so tests can monkeypatch it"
      pattern: "account_import.import_one_account"
    - from: "app/main.py lifespan"
      to: "account_import_worker"
      via: "start()/stop() next to kb_ingest_worker"
      pattern: "account_import_worker"
    - from: "confirm endpoint"
      to: "account_import_items"
      via: "one row per matched pair with session bytes + parsed JSON"
      pattern: "AccountImportItem"
---

<objective>
Deliver step 2 of the two-step flow (D-09): an asynchronous confirm endpoint that turns a staged preview into a background import job (returns `job_id` immediately, 202), a never-dying worker that drives each per-file item through `import_one_account`, and a status-poll endpoint reporting per-file progress. Role is chosen once for the whole batch (D-16); every item is independent (D-10/IMPT-07).

Purpose: connect+get_me is ~1-3s/account, so a synchronous confirm would hit the nginx/HTTP timeout on a large batch. This plan wires the 21-04 per-account routine into a claim/commit worker (mirroring `KnowledgeIngestWorker`) fed by confirm-created item rows and polled via a status endpoint.
Output: `AccountImportWorker` + lifespan registration + config knob; `POST /accounts/import/{import_id}/confirm` + `GET /accounts/import/{job_id}/status`.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md

<interfaces>
<!-- Extracted from the running codebase. -->

Worker template to MIRROR (app/services/kb_ingest_worker.py, full file): claim ONE pending row
`WHERE status='pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`, flip to 'processing'
in its own COMMITTED transaction (so a UI poll sees progress), do the network work OUTSIDE the claim TX,
write terminal status in a fresh TX; loop never dies on a per-item error (log + continue). Module-scope
singleton `account_import_worker = AccountImportWorker()`, poll interval from config.

Lifespan registration (app/main.py):
  imports (line ~19): from app.services.kb_ingest_worker import kb_ingest_worker
  start (line ~68):   kb_ingest_worker.start()
  stop  (line ~78):   await kb_ingest_worker.stop()
  → add account_import_worker start/stop alongside these.

Config knob pattern (app/config.py ~line 68):
  kb_ingest_poll_interval: int = Field(default=5, validation_alias="KB_INGEST_POLL_INTERVAL", description="...")

Per-account routine (21-04): `import_one_account(db, workspace_id, role, basename, session_bytes, vendor_dict) -> dict`
  returns {status:'ok'|'failed', result:..., reason:..., sender_id:...}; never raises for a per-account failure.
Call it by MODULE REFERENCE (`from app.services import account_import; account_import.import_one_account(...)`)
so tests monkeypatch `app.services.account_import.import_one_account` (mirrors kb worker patching embed_texts).

ORM (21-01): AccountImportStaging(id, workspace_id, zip_data, summary, expires_at),
AccountImportJob(id, workspace_id, staging_id, role, status, total, processed),
AccountImportItem(id, job_id, workspace_id, basename, session_blob, vendor_json, status, result, reason, sender_id).

Preview pairing helper (21-03): `unpack_and_pair(zip_bytes) -> {matched:[{basename,json,session_bytes}], unpaired, malformed}`.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: POST confirm (create job + items) + GET status endpoints</name>
  <read_first>
    - app/routers/account_import.py (the preview endpoint + response models from 21-03)
    - app/routers/contacts.py lines 368-420 (import apply — how a staged blob is re-read + expiry checked)
    - app/services/account_import.py::unpack_and_pair (21-03)
    - app/models/__init__.py AccountImportJob / AccountImportItem / AccountImportStaging (21-01)
  </read_first>
  <action>
    In `app/routers/account_import.py` add two endpoints on the same `/api/v1/accounts` router:

    1. `POST /import/{import_id}/confirm` (async, returns 202):
    - Body model `ImportConfirmRequest { role: Literal['sender','checker'] }` (validate role — reject others with 422 `INVALID_ROLE`).
    - Load `AccountImportStaging` WHERE id==import_id AND workspace_id==ctx.workspace_id; not found → 404 `IMPORT_NOT_FOUND`; `expires_at < now` → 410 `IMPORT_EXPIRED`.
    - Re-read `unpack_and_pair(staging.zip_data)`; create ONE `AccountImportJob(workspace_id, staging_id=import_id, role=role, status='running', total=len(matched), processed=0)`; then for each `matched` entry create an `AccountImportItem(job_id, workspace_id, basename, session_blob=<session bytes>, vendor_json=<parsed dict>, status='pending')`. Do NOT create items for unpaired/malformed (they were reported at preview; optionally record them as pre-failed items for the report — DISCRETION, but keep `total` = number of items actually created).
    - Commit; return `{ job_id: job.id, total: job.total }` with HTTP 202.
    - Idempotency/double-submit: if the same staging already has a job, DISCRETION — simplest is to allow a new job (worker dedups by slug anyway); document the choice in the SUMMARY.

    2. `GET /import/{job_id}/status` (async):
    - Load `AccountImportJob` scoped to workspace (404 `JOB_NOT_FOUND` otherwise).
    - Load its `AccountImportItem` rows; return `{ job_id, status, total, processed, items: [ { basename, status, result, reason } ] }`. NEVER include session_blob, vendor_json.twoFA, or any secret in the items payload — only basename/status/result/reason.
    - `processed` = count of items in a terminal state (ok/failed); when processed==total, the worker will have flipped job.status to 'done' (Task 2) — return whatever the row says.
    Define `ImportConfirmRequest`, `ImportConfirmResponse`, `ImportStatusResponse`, `ImportStatusItem` co-located at the top of the router file.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import_worker.py -k "confirm or status" -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "/import/{import_id}/confirm" app/routers/account_import.py` succeeds
    - `grep -q "/import/{job_id}/status" app/routers/account_import.py` succeeds
    - `grep -q "IMPORT_EXPIRED" app/routers/account_import.py` succeeds (410 on expired staging)
    - `grep -q "Literal\['sender', 'checker'\]\|Literal\[\"sender\", \"checker\"\]" app/routers/account_import.py` succeeds (role validation)
    - `grep -q "AccountImportItem(" app/routers/account_import.py` succeeds (one row per matched pair)
    - The status response model exposes ONLY basename/status/result/reason per item (grep: no `session_blob`/`vendor_json`/`twoFA` in the status response model)
    - confirm returns 202 with job_id; status returns processed/total + per-item rows (tests pass)
  </acceptance_criteria>
  <done>Confirm validates role + staging (410 on expiry), creates a job + N pending items carrying the session bytes/JSON, returns job_id (202); status reports processed/total + per-item results with no secrets in the payload.</done>
</task>

<task type="auto">
  <name>Task 2: AccountImportWorker (claim→processing→ok/failed) + lifespan + config knob</name>
  <read_first>
    - app/services/kb_ingest_worker.py (full file — the claim/commit/never-die structure to mirror)
    - app/services/account_import.py::import_one_account (21-04 — called by module ref)
    - app/main.py lines 14-83 (worker imports + lifespan start/stop block)
    - app/config.py lines 60-80 (Field(...) knob pattern)
    - tests/test_account_import_worker.py::test_worker_drives_items_and_status (RED contract)
  </read_first>
  <action>
    Create `app/services/account_import_worker.py` mirroring `KnowledgeIngestWorker`:
    - `class AccountImportWorker` with `start()` (idempotent), `stop()` (cancel+await, swallow CancelledError), `_run()` (loop: `await self._tick()`; on `CancelledError` break; on any Exception log + continue — NEVER die; `await asyncio.sleep(self.poll_interval)`), and `_tick() -> int`.
    - `_tick`:
      1. Claim ONE `account_import_items` row `WHERE status='pending' ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED`; if none → return 0. Read `id, job_id, workspace_id, role(from its job), basename, session_blob, vendor_json`. Flip it to `status='processing'`, COMMIT (so the UI poll sees progress).
      2. OUTSIDE the claim TX, in a fresh `AsyncSessionLocal()`: call `result = await account_import.import_one_account(db, workspace_id, role, basename, bytes(session_blob), vendor_json)` (module-ref call so tests can patch it; normalise memoryview→bytes like the KB worker does for raw_content).
      3. In a fresh committed TX: UPDATE the item `status = 'ok' if result['status']=='ok' else 'failed'`, `result = result['result']`, `reason = result.get('reason')`, `sender_id = result.get('sender_id')`, `session_blob = NULL` (clear the live auth_key once terminal — security), `updated_at=NOW()`; and `UPDATE account_import_jobs SET processed = processed + 1, updated_at=NOW() WHERE id=:job_id`; then `UPDATE account_import_jobs SET status='done' WHERE id=:job_id AND processed >= total`. Return 1.
      - The `role` for the item is `account_import_jobs.role` — read it via a JOIN in the claim SELECT or a small extra SELECT.
      - A crash mid-item leaves the item in 'processing' (acceptable for v1, mirrors the KB worker note); never let a per-item exception kill the loop.
    - Module-scope singleton: `account_import_worker = AccountImportWorker()`.
    - Add config knob `account_import_poll_interval: int = Field(default=3, validation_alias="ACCOUNT_IMPORT_POLL_INTERVAL", description="Polling interval (seconds) for the AccountImportWorker loop.")` in `app/config.py`; read it in `__init__` (`self.poll_interval = get_settings().account_import_poll_interval`).
    - Register in `app/main.py`: import `account_import_worker`, `account_import_worker.start()` next to `kb_ingest_worker.start()` (~line 68), and `await account_import_worker.stop()` in the shutdown block (~line 78).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import_worker.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "class AccountImportWorker" app/services/account_import_worker.py` succeeds
    - `grep -q "FOR UPDATE SKIP LOCKED" app/services/account_import_worker.py` succeeds
    - `grep -q "account_import.import_one_account" app/services/account_import_worker.py` succeeds (module-ref call)
    - `grep -q "session_blob = NULL\|SET session_blob = NULL\|session_blob=NULL" app/services/account_import_worker.py` succeeds (auth_key cleared on terminal)
    - `grep -q "processed = processed + 1\|processed + 1" app/services/account_import_worker.py` succeeds
    - `grep -q "account_import_worker.start()" app/main.py` AND `grep -q "await account_import_worker.stop()" app/main.py` succeed
    - `grep -q "account_import_poll_interval" app/config.py` succeeds
    - test_worker_drives_items_and_status passes: items go pending→processing→ok/failed, job.processed increments, job flips to done at total; the worker never raises out of the loop
  </acceptance_criteria>
  <done>AccountImportWorker claims one pending item at a time, runs import_one_account off the claim TX, writes terminal ok/failed + increments job progress + flips job to done, clears the item's live session bytes, never dies, and is registered in the lifespan next to the other 7 workers.</done>
</task>

</tasks>

<verification>
- Confirm → 202 job_id; worker drains items to ok/failed; status reports processed/total.
- One failing item does not stall the batch or kill the worker.
- Expired staging → 410; unknown job → 404.
- Full suite green via test-overlay before phase gate.
</verification>

<success_criteria>
- IMPT-02 delivered end-to-end (confirm → async job → worker → status poll); IMPT-07 per-file report surfaced in status.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-05-SUMMARY.md`
</output>
