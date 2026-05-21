# Phase 3 — Deferred Items

## From plan 03-01

### app/routers/contexts.py — legacy AI contexts CRUD router

**Resolved in plan 03-02 Task 4:** file deleted via `git rm`. Replaced by
new workspace-scoped `app/routers/agents.py` under `/api/v1/agents` (D-02).
No follow-up needed.

**Found during:** Plan 03-01 final verification (Task 7 post-check)
**Status:** UNREGISTERED in app/main.py — dead code, no runtime impact
**Issue:** Router still references dropped columns (`is_active`, `max_message_length`, `response_delay_seconds`, `webhook_functions`, `document_webhook_url`) + dropped FK (`senders.ai_context_id`) in SELECT/INSERT/UPDATE statements + legacy `UPDATE senders SET ai_context_id = NULL` in DELETE handler.

**Why deferred to plan 03-02:**
- Plan 03-02 creates a new `/api/v1/agents` router (workspace-scoped CRUD via AuthDep) which entirely supersedes this legacy `/api/v1/contexts` endpoint (global, X-API-Key based).
- Since this router is not mounted in `main.py`, migration 015 does NOT crash any live endpoint — runtime is safe.
- Plan 03-02 should:
  1. Create new `/api/v1/agents` router with Phase 3 schema (D-02 fields only)
  2. Delete `app/routers/contexts.py` entirely (replaced, not adapted)

**Lines with dead references** (for awareness — do not touch in plan 03-01):
- L24-26, L37-41, L52-56: Pydantic schemas with dropped fields
- L75-77, L84-90, L113-117, L142-147, L161-163, L173-177: SELECT/INSERT/RETURNING with dropped columns
- L194-196: UPDATE field list includes dropped columns
- L242-243: `UPDATE senders SET ai_context_id = NULL WHERE ai_context_id = :id` — would fail with `column "ai_context_id" does not exist`

This is recorded per scope boundary of GSD deviation rules — out-of-scope discovery logged, not fixed.

### app/routers/send.py — legacy send router

**Resolved in plan 03-02 Task 4:** file fully rewritten under AuthDep with
explicit `ai_context_id` parameter (D-06). `AIContext.is_active` filter dropped,
sender check uses `lifecycle_status=='active' AND auth_status=='ok'`.
Registered in `app/main.py` in Task 5.

**Found during:** Plan 03-01 final verification
**Status:** UNREGISTERED in app/main.py + not imported anywhere — dead code, no runtime impact
**Issues:**
- L53, L175, L286: `sender.is_active` — Phase 2 dropped this column (pre-existing tech debt, not Phase 3)
- L68, L190: `AIContext.is_active == True` — Phase 3 Task 2 dropped this attribute. Would crash if router were registered.
- L67, L87, L189, L207: still passes `context_id=request.ai_context_id` to `get_or_assign_sender` — this is the correct new pattern (we kept the parameter in rotation.py for D-05), so this remains valid for plan 03-02 rewrite.

**Why deferred to plan 03-02:**
Per plan 03-02 scope: "новый /api/v1/agents router (CRUD + duplicate) + рерайт /api/v1/send под AuthDep с explicit ai_context_id". The send.py rewrite is plan 03-02's main deliverable — `is_active` references will be naturally dropped during that rewrite.

Plan 03-02 should:
1. Rewrite `app/routers/send.py` under AuthDep with explicit ai_context_id parameter
2. Drop `AIContext.is_active` ORM filter (column gone)
3. Replace `sender.is_active` with `lifecycle_status='active' AND auth_status='ok'`
4. Mount `app/routers/send.router` + `app/routers/agents.router` in `app/main.py`
