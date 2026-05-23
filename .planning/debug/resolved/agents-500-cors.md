---
slug: agents-500-cors
status: resolved
created_at: 2026-05-23T18:58:32Z
trigger: GET /api/v1/agents from Lovable frontend returns 500 with no CORS header
goal: find_and_fix
tdd_mode: false
---

# Debug Session: agents-500-cors

## Symptoms

Frontend at `https://aimly-tg-outreach.lovable.app` cannot reach backend at `https://aimly.agsventurelab.com/api/v1/agents`.

**Browser console:**
```
Access to fetch at 'https://aimly.agsventurelab.com/api/v1/agents' from origin 'https://aimly-tg-outreach.lovable.app' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.
GET https://aimly.agsventurelab.com/api/v1/agents net::ERR_FAILED 500 (Internal Server Error)
```

## Hypotheses (with verdicts)

- **H1 (CORS allowlist):** REJECTED — `.env` includes `https://aimly-tg-outreach.lovable.app` and the regex default also matches `.lovable.app`. CORSMiddleware config is correct. Verified by hitting endpoint without auth → 401 *with* proper CORS headers attached.
- **H2 (middleware order):** PARTIALLY CONFIRMED — there is only one app-added middleware (CORS), but Starlette's built-in `ServerErrorMiddleware` sits **outside** CORS. So when an unhandled exception bubbles past CORS, the synthetic 500 response from `ServerErrorMiddleware` never traverses CORS → no headers. Not a config bug per se, but a Starlette architectural fact.
- **H3 (500 in app):** **CONFIRMED — primary root cause.** `sqlalchemy.exc.InvalidRequestError: A transaction is already begun on this Session.` inside `_resolve_or_create_workspace()` at `auth.py:329`.
- **H4 (nginx):** REJECTED — nginx is transparent; the 500 comes from FastAPI.

## Evidence

- timestamp: 2026-05-23T18:59Z — `diff .env.bak.20260523-141400 .env` shows CORS env CONTAINS the lovable.app origin; the regex was removed in favor of code default `^https://[a-z0-9-]+\.(lovableproject\.com|lovable\.app)$` (config.py:43-47).
- timestamp: 2026-05-23T19:00Z — `curl -H 'Origin: https://aimly-tg-outreach.lovable.app' https://aimly.agsventurelab.com/api/v1/agents` → 401 with `access-control-allow-origin: https://aimly-tg-outreach.lovable.app` present. Confirms CORS works on 4xx responses.
- timestamp: 2026-05-23T19:00Z — `docker compose logs api` shows repeated `sqlalchemy.exc.InvalidRequestError: A transaction is already begun on this Session.` at `/app/app/utils/auth.py:329` (the `async with db.begin():` line). Affects `/api/v1/agents`, `/api/v1/folders`, `/api/v1/campaigns`, `/api/v1/analytics/*`, `/api/v1/telemetry/events`, and even `/api/v1/auth/me`.
- timestamp: 2026-05-23T19:00Z — `SELECT * FROM user_workspaces` returns 0 rows. So EVERY JWT request hits the lazy-create branch, which is exactly the broken code path.

## Root Cause

Two separate but interlocking causes (clearly distinguishable):

### Cause A — the 500 itself
`_resolve_or_create_workspace()` in `app/utils/auth.py` does:

```python
# Line 306 — implicit transaction starts here (autobegin)
result = await db.execute(select(UserWorkspace).where(...))
uw = result.scalars().first()

if uw is not None:
    return AuthCtx(...)  # fast path — works fine

# Slow path (new user):
async with db.begin():   # ← line 329: TRIES TO START A SECOND TRANSACTION ⇒ raises
    ...
```

`AsyncSession` in SQLAlchemy 2.x has implicit `autobegin=True`. The prior `await db.execute(select(...))` opens an implicit transaction. Then `async with db.begin()` raises `InvalidRequestError: A transaction is already begun on this Session.` This is a coding bug in the lazy auto-create branch (Phase 1 D-08 TENT-02). It has been latent — only fired now because no one had ever logged in yet (`user_workspaces` is empty).

### Cause B — missing CORS header on the 500
Unhandled exceptions bubble past Starlette's `CORSMiddleware` (added via `app.add_middleware`) and are caught by the built-in `ServerErrorMiddleware`, which is **outside** the user middleware stack. The synthetic 500 it generates never goes through CORS → no `Access-Control-Allow-Origin` header. This is independent of CORS config; it's a Starlette layering reality. Defensive fix would be a global `Exception` handler returning a proper `JSONResponse` (which does get CORS headers via the standard exception path). But fixing Cause A removes the symptom for this specific bug.

## Fix Applied

**File:** `app/utils/auth.py` — replace `async with db.begin():` with the project's standard `db.flush()` + `db.commit()` pattern (matches conventions in routers/agents.py, routers/campaigns.py, etc.). No explicit nested transaction, no conflict with the autobegin session.

## Resolution

- root_cause: SQLAlchemy `InvalidRequestError: A transaction is already begun on this Session` in `_resolve_or_create_workspace` lazy auto-create branch — `async with db.begin()` opened a nested transaction on a session that already had an implicit transaction from the preceding `db.execute(select(...))`. Triggered for every JWT-authenticated request because `user_workspaces` was empty (everyone hits the lazy-create path).
- fix: Rewrote the lazy auto-create block in `app/utils/auth.py::_resolve_or_create_workspace` to use the project's standard `db.flush() / db.commit() / db.rollback()` pattern instead of a nested `async with db.begin()`. Container rebuilt via `docker compose up -d --build api`.
