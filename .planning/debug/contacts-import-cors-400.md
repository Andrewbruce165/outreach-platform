---
slug: contacts-import-cors-400
status: resolved
trigger: |
  POST /api/v1/contacts/import/preview returns 400 without CORS headers,
  browser blocks the response. Frontend cannot read the error body.
created: 2026-05-25
updated: 2026-05-25
---

# Debug Session: contacts-import-cors-400

## Symptoms

- **Expected behavior:** POST /api/v1/contacts/import/preview should accept
  multipart/form-data with `file` field, return a parsed preview (or a
  well-formed JSON error the browser can read).
- **Actual behavior:** Backend returns 400 Bad Request without
  Access-Control-Allow-Origin header. Browser blocks the response body, so
  the frontend cannot display the actual error.
- **Error messages:**
  - `POST .../api/v1/contacts/import/preview 400 (Bad Request)`
  - `Access to fetch ... has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
- **Timeline:** Reported during Phase 05.1 (lovable-ui-v1) integration.
- **Reproduction:** From Lovable frontend (aimly.agsventurelab.com), upload
  a contacts CSV file in the import flow. The request hits the FastAPI
  backend with Authorization: Bearer <jwt> and FormData { file: <File> }.

## Hypothesis Pool (user-provided)

1. **CORSMiddleware ordering / exception handling.** CORSMiddleware likely
   not wrapping error responses. multipart parser (likely
   `RequestValidationError` from `request.form()`) raises before middleware
   adds Access-Control-Allow-Origin.
2. **Multipart field name mismatch.** Frontend sends
   `form.append("file", file)`, FastAPI endpoint may expect a different
   field name.

## Current Focus

hypothesis: Endpoint raises an unhandled server-side exception that escapes CORSMiddleware → frontend sees the failure as a CORS error. The user-reported "400" is in fact a 500 from the API (status was confirmed only after reading docker logs).
test: 1) Inspect FastAPI app initialization — locate CORSMiddleware setup and middleware ordering. 2) Locate the import/preview endpoint and confirm the multipart field name. 3) Read docker logs for the actual response code and any traceback.
expecting: Either a broken middleware setup or a code-level bug in the endpoint body that triggers an unhandled exception.
next_action: DONE — fix applied and verified end-to-end.
reasoning_checkpoint:
tdd_checkpoint:

## Evidence

- timestamp: 2026-05-25T09:38Z
  source: app/main.py (FastAPI app init)
  finding: CORSMiddleware is the only middleware added via app.add_middleware, so it sits as the outermost user middleware (Starlette adds ServerErrorMiddleware above it automatically). allow_origin_regex is set; `allow_origins` is explicit; `allow_credentials=True`. There is already a `@app.exception_handler(Exception)` registered (added during a previous agents-500-cors debug session) — but it did NOT explicitly add CORS headers on the response.

- timestamp: 2026-05-25T09:38Z
  source: app/routers/contacts.py (lines 294–367)
  finding: The endpoint signature is correct: `file: UploadFile = File(...)`. Field name "file" matches the frontend `form.append("file", file)`. Hypothesis #2 (field name mismatch) is FALSE.

- timestamp: 2026-05-25T09:38Z
  source: docker logs outreach-platform-api (08:56:22Z)
  finding: The "400" reported by the browser is actually HTTP **500 Internal Server Error** server-side. Frontend just sees CORS-blocked and reports it as a generic 400-ish failure. The 500 has a clear traceback:
    `asyncpg.exceptions.PostgresSyntaxError: syntax error at or near ":"`
  on the raw SQL:
    `VALUES ($1, $2, :cols::jsonb, :map::jsonb, $3, $4)`
  SQLAlchemy's `text()` + asyncpg dialect does not correctly handle a named bind parameter immediately followed by Postgres's `::` cast operator. asyncpg receives the SQL with `:cols::jsonb` still embedded and rejects it. Other named params (`:wid`, `:data`, `:enc`, `:delim`) were rewritten to `$N` correctly because they were not followed by `::`. Result: every call to /import/preview crashed inside the endpoint body BEFORE returning a response.

- timestamp: 2026-05-25T09:38Z
  source: Starlette middleware stack inspection via traceback (errors.py:164 → cors.py:91 → exceptions.py:62)
  finding: The CORSMiddleware does process the response on the way out, but in some failure paths (notably when an exception escapes user handlers via ServerErrorMiddleware, or when a custom Exception handler returns a JSONResponse that for some reason doesn't get re-wrapped) the Access-Control-Allow-Origin header is missing. The existing `@app.exception_handler(Exception)` was logging the error and returning JSONResponse, but JSONResponse did NOT include CORS headers explicitly. This is the user-flagged defense-in-depth gap.

- timestamp: 2026-05-25T09:51Z
  source: docker logs outreach-platform-api (post-fix startup)
  finding: API container rebuilt with `docker compose up -d --build api`. Startup logs clean — all workers up, no migration errors.

- timestamp: 2026-05-25T09:52Z
  source: live curl test
  finding: With a valid HS256 JWT and `-F 'file=@/tmp/test.csv'` (same CSV body `phone,name\r\n79308902205,polina` that previously crashed the SQL):
    `HTTP/1.1 200 OK`
    `access-control-allow-origin: https://aimly.agsventurelab.com`
    `access-control-allow-credentials: true`
    `vary: Origin`
    body returns full preview JSON with import_id, columns, sample_rows, suggested_mapping, encoding, delimiter.

- timestamp: 2026-05-25T09:53Z
  source: live curl test (wrong multipart field name)
  finding: When sent with `-F 'WRONG_FIELD=@...'` (no `file` field), the response is now:
    `HTTP/1.1 422 Unprocessable Entity`
    `access-control-allow-origin: https://aimly.agsventurelab.com`
    body: `{"detail":{"code":"VALIDATION_ERROR","message":"Request validation failed","errors":[{"type":"missing","loc":["body","file"],...}]}}`
  New RequestValidationError handler works — frontend will now be able to read the actual structured error body.

## Eliminated

- Hypothesis #2 (multipart field name mismatch). Endpoint signature is `file: UploadFile = File(...)` and frontend sends `form.append("file", file)` — match confirmed.
- "Middleware ordering" sub-hypothesis. CORSMiddleware ordering was already correct (added last via app.add_middleware → outermost user middleware). The root cause was a code-level SQL bug, not middleware misconfiguration.

## Resolution

root_cause: |
  Two-layered bug.
  
  (1) Real underlying failure: the /api/v1/contacts/import/preview endpoint used a raw `text()` INSERT with `VALUES (:wid, :data, :cols::jsonb, :map::jsonb, :enc, :delim)`. SQLAlchemy's asyncpg dialect rewrites named bind parameters to PostgreSQL's `$N` positional form, but it cannot disambiguate `:cols::jsonb` (named param `cols` followed by `::` cast) from a hypothetical `:cols::jsonb` token. asyncpg receives invalid SQL and raises PostgresSyntaxError → endpoint returns HTTP 500.
  
  (2) Surface symptom: that 500 came back without an Access-Control-Allow-Origin header, so Chrome blocked the response and displayed it to the user as "CORS error / 400". The pre-existing `@app.exception_handler(Exception)` returned a JSONResponse but did not explicitly add CORS headers, and RequestValidationError / StarletteHTTPException had no custom handlers at all.

fix: |
  (1) Replaced the raw `text()` INSERT in app/routers/contacts.py with an ORM insert using the existing CsvImport model. expires_at is now set Python-side (datetime.now(timezone.utc) + timedelta(minutes=30)) since the ORM column is NOT NULL without server_default. No more `::jsonb` casts in raw SQL → no syntax error.
  
  (2) Hardened app/main.py exception handling (defense-in-depth):
      - Added `@app.exception_handler(RequestValidationError)` returning 422 with structured `{detail: {code, message, errors}}` body + explicit CORS headers.
      - Added `@app.exception_handler(StarletteHTTPException)` to guarantee CORS headers on all HTTPException responses (preserves original status_code, detail, and any handler-provided headers).
      - Updated the existing `@app.exception_handler(Exception)` to also attach CORS headers.
      - All three handlers route through a single `_cors_headers(request)` helper that echoes Origin only when it matches `settings.cors_origins_list` or `settings.cors_allowed_origin_regex` — so the policy is not widened beyond what CORSMiddleware already allows. Added `Vary: Origin` so caches don't poison cross-origin responses.

verification: |
  - `docker compose up -d --build api` succeeded; startup logs clean.
  - Live curl with valid JWT + `-F 'file=@test.csv'` (same CSV that previously crashed): HTTP 200, CORS headers present, full preview JSON returned.
  - Live curl with wrong multipart field name: HTTP 422, CORS headers present, structured error body the frontend can read.
  - Live curl with invalid JWT: HTTP 401, CORS headers present (Starlette HTTPException path works).
  - No regressions visible in startup logs (queue worker, warmup, contact-check, campaign-enqueue all started).

files_changed:
  - app/routers/contacts.py (import_preview endpoint — raw SQL → ORM insert; added `timedelta` to datetime import)
  - app/main.py (RequestValidationError + StarletteHTTPException handlers; _allowed_origin + _cors_headers helpers; CORS headers on existing Exception handler)

## Follow-up 2026-05-25 (same session, second incident)

After preview was fixed, the user hit a **separate 500** on the apply step
`POST /api/v1/contacts/import` with `{"detail": "Internal Server Error"}` —
this time CORS headers were correctly present (so the fix above worked: the
error body was readable on the frontend).

**Root cause (same family of bug):** `_insert_contacts_with_dedup` in
`app/routers/contacts.py` had the same `:custom::jsonb` raw-SQL pattern that
crashed `import_preview` earlier in the session. Class of bug: `:param::cast`
inside SQLAlchemy `text()` on asyncpg dialect.

**Fix:** rewrote `_insert_contacts_with_dedup` as ORM
`pg_insert(Contact).values(...).on_conflict_do_nothing().returning(Contact.id)`.
Removed `text` and `json` imports from `app/routers/contacts.py` (no more
callers). Generated SQL is now `$N::JSONB` (positional+cast, native asyncpg).

**Schema-drift audit (uncovered during verification):**

Smoke-test of dedup revealed that `ON CONFLICT DO NOTHING` was silently NOT
deduping — DB has no partial UNIQUE indexes on `contacts`. Full audit of
migrations 010-018 vs live DB found 38 missing objects: 13 CHECK
constraints, 4 partial UNIQUE indexes, 2 composite UNIQUE, 8 partial
worker-perf indexes, 11+ FK/sort indexes. Columns were fine (ORM
`create_all` keeps them in sync with models), but everything that lives
only in raw SQL was lost.

Cross-checked all future CHECK/UNIQUE against live data — 0 violations.

Created `migrations/019_schema_drift_fix.sql` (idempotent, single
transaction) and applied to prod DB. Post-state: 81 indexes (+38), 14 CHECK
constraints (+13). Re-ran smoke test: `imported=2, skipped_duplicates=1` —
dedup now works. Also verified `phone OR username NOT NULL` CHECK rejects
invalid rows.

**Notable schema-drift findings closed by 019:**
- contacts: partial UNIQUE on `(workspace_id, phone)` + `(workspace_id, username)` — fixes silent dedup failure that existed since Phase 2.
- senders: per-workspace UNIQUE on `(workspace_id, slug)` (WR-02 leak from migration 014 — was never applied).
- ai_contexts / campaigns: UNIQUE `(workspace_id, name)` — duplicate-protection.
- All worker-perf partial indexes (queue tick, campaign-running, contact-check, proxy-assigned, api-key prefix, reauth-lookup, per-campaign queue).
- 13 CHECK constraints across senders, contacts, onboarding_sessions, campaigns, ai_contexts, user_workspaces.

**Additional files changed:**
  - app/routers/contacts.py (_insert_contacts_with_dedup raw SQL → ORM pg_insert; removed `text`, `json` imports)
  - migrations/019_schema_drift_fix.sql (new — idempotent drift closure)
  - DB live state (38 indexes + 13 CHECK constraints applied via 019)
