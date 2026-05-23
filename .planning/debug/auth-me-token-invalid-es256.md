---
slug: auth-me-token-invalid-es256
status: resolved
trigger: bug-report
created_at: 2026-05-23
tdd_mode: false
goal: find_and_fix
---

# Debug session: `/api/v1/auth/me` returns 401 TOKEN_INVALID after Supabase magic-link login

## Symptoms (user-reported)

1. User opens magic link from email.
2. Lovable frontend `/auth/callback` exchanges `code` for a Supabase session (succeeds — Supabase JS client gets valid `access_token`).
3. Frontend calls backend `POST /api/v1/auth/me` with `Authorization: Bearer <jwt>`.
4. Backend responds **401 Unauthorized** with `{"code":"TOKEN_INVALID","message":"Invalid JWT"}`.
5. Previously the frontend treated this as expired session → auto-logout → redirect to `/login?redirect=%2F` (masking the bug as "Your session expired"). Frontend has been patched to stop the auto-logout.

User's leading hypothesis: backend hardcoded to HS256 but Supabase project publishes ES256.

## Current Focus

- **hypothesis:** Algorithm mismatch — Supabase publishes ES256 (asymmetric), backend `_decode_supabase_jwt` hardcoded to `algorithms=["HS256"]` using legacy `SUPABASE_JWT_SECRET`. JWT signature verification fails → `JWTError` → 401 `TOKEN_INVALID`.
- **next_action:** confirm with live JWKS probe + code inspection, then apply backend ES256/JWKS fix.

## Evidence

- timestamp: 2026-05-23T18:43Z — `curl https://qhxkyzmwnehnrfndpxxo.supabase.co/auth/v1/.well-known/jwks.json` returns:
  ```
  {"keys":[{"alg":"ES256","crv":"P-256","ext":true,"key_ops":["verify"],
    "kid":"494eb22c-6d90-42ab-a75c-65bca0e1d268","kty":"EC","use":"sig",
    "x":"wCt-GU-8Kno1Dh1sJnZXslGwpDM5fLc1fY9fbnAdN2Y",
    "y":"nkp30fIIvHyf2dfwobFEHVEZfXv0RWGnVLLzO_5dF5s"}]}
  ```
  → **Project signs JWTs with ES256, single EC P-256 key, kid `494eb22c-…`.**

- timestamp: 2026-05-23T18:42Z — `app/utils/auth.py:148-155` shows:
  ```python
  claims = jwt.decode(
      token,
      settings.supabase_jwt_secret,
      algorithms=["HS256"],
      audience="authenticated",
      options={"require": ["sub", "exp"]},
  )
  ```
  → Backend rejects everything that isn't HS256.

- timestamp: 2026-05-23T18:42Z — In-file comments (lines 13-23) acknowledge this exact pitfall:
  > "TODO(v2): migrate JWT validation from HS256 to ES256/JWKS (Supabase default since Oct 2025 — RESEARCH Pitfall 1)"
  > "Lovable's auto-bootstrapped Supabase project MUST be pinned to HS256 in Supabase Dashboard → Settings → API → JWT Settings → Algorithm = HS256."
  → The "pin to HS256" workaround was never applied (or was reverted by Supabase auto-migration).

- timestamp: 2026-05-23T18:43Z — Live API logs show repeating `POST /api/v1/auth/me HTTP/1.1 401 Unauthorized` over the last few hours — symptom is current, not historical.

- timestamp: 2026-05-23T18:43Z — `.env` (current) and `.env.bak.20260523-141400` (4 hours ago) both contain the SAME `SUPABASE_JWT_SECRET` and `SUPABASE_URL`. The only diff is CORS_ALLOWED_ORIGINS (added prod domain + Lovable). So today's backup is from a CORS edit, not a JWT change.

- timestamp: 2026-05-23T18:43Z — Container deps: `python-jose[cryptography]==3.3.0` + `cryptography==42.0.0` already installed. ES256 verification via `jose.jwt.decode` with a JWK already works (verified inside running container).

## Alternative hypotheses considered

| # | Hypothesis | Verdict |
|---|---|---|
| 1 | Wrong `SUPABASE_URL` | NO — URL matches the JWKS endpoint that returned a key. |
| 2 | Expired `SUPABASE_JWT_SECRET` rotation | NO — even if rotated, ES256 token won't validate with ANY HS256 secret (different algorithm class). |
| 3 | Audience/issuer mismatch | UNLIKELY — Supabase tokens still carry `aud=authenticated`; this would surface as `TOKEN_INVALID_CLAIMS`, not `TOKEN_INVALID`. |
| 4 | Clock skew | NO — would surface as `TOKEN_EXPIRED`. |
| 5 | Missing `aud` claim | NO — would surface as `TOKEN_INVALID_CLAIMS`. |
| 6 | Algorithm mismatch (ES256 vs HS256) | **YES — confirmed by JWKS probe + code inspection.** |

## Root cause

**Confirmed.** Supabase project `qhxkyzmwnehnrfndpxxo` signs all JWTs with **ES256** (asymmetric EC P-256, key `kid=494eb22c-6d90-42ab-a75c-65bca0e1d268`). Backend `_decode_supabase_jwt` calls `jwt.decode(..., algorithms=["HS256"])` with the legacy HMAC secret — so the signature can never validate. `python-jose` raises `JWTError` → handler returns `401 TOKEN_INVALID`.

Two reasonable fix directions:

A. **Backend → ES256/JWKS** (forward-compatible; matches Supabase's current default since Oct 2025; explicitly listed as the v2 direction in the source).
B. **Supabase → pin HS256** (downgrade workaround; the Phase 05.1 plan; deprecated path).

**Choosing A.** Reasons:
- Supabase moved all new + auto-bootstrapped projects to ES256 by default. Pinning back to HS256 is reversal of a migration that already happened — fragile, may be undone again automatically.
- Lovable handoff customers will hit this on every new project they spin up; permanent fix is better than per-customer dashboard ritual.
- All dependencies (`jose[cryptography]`) already installed; change is ~30 LOC, no env rotation required.

## Plan: fix

1. Add JWKS fetch + cache helper in `app/utils/auth.py` (cache the JWKS for ~1h, refetch on `kid` miss).
2. Rewrite `_decode_supabase_jwt` to:
   - Read header → `kid`.
   - Look up key in cached JWKS, refresh once on miss.
   - Decode with `algorithms=["ES256"]`, using the JWK dict directly (`python-jose` accepts JWK).
   - Keep audience + `require=["sub","exp"]` + same exception → HTTPException mapping.
3. Keep `SUPABASE_JWT_SECRET` env tolerated (no breakage) but stop using it for verification. Add deprecation comment.
4. Add `SUPABASE_URL`-derived JWKS URL: `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`.
5. Update in-file Phase 05.1 comments to reflect "fixed in this session".
6. Add unit test that verifies ES256 path with a synthetic key + the audience/sub/exp options.
7. Rebuild + recreate container (per global CLAUDE.md memory: never just `restart`).
8. Verify with live token from frontend OR by minting a synthetic ES256 token signed with a test key and pointing JWKS_URL at a local fixture.

## Resolution

**Root cause:** confirmed (algorithm mismatch — Supabase ES256 vs backend hardcoded HS256). See Evidence + Alternative hypotheses tables.

**Fix applied (commit-ready, not yet committed):**

- `app/utils/auth.py` — rewrote `_decode_supabase_jwt` as async with two paths routed by the JWT `alg` header:
  - `ES256` → look up JWK by `kid` in the in-process JWKS cache (1h TTL, refetched on miss). JWKS is fetched from `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` via httpx (already in requirements). Verified with `algorithms=["ES256"]` using the JWK dict directly (python-jose accepts JWK).
  - `HS256` → legacy fallback against `settings.supabase_jwt_secret` (preserves any customer who pinned their project to HS256).
  - Anything else → 401 `TOKEN_INVALID` with diagnostic message (`unsupported alg=...`).
- `app/utils/auth.py` — added `_fetch_jwks` + `_get_jwk_for_kid` with asyncio lock to prevent thundering-herd JWKS refetch under concurrent first requests.
- `app/utils/auth.py` — made `auth_dep` await the now-async `_decode_supabase_jwt`.
- `app/config.py` — relaxed `supabase_jwt_secret` to `Optional[str] = None` and updated docstring to mark it as legacy/fallback. `supabase_url` remains required (used for JWKS endpoint).
- `tests/test_auth_dep.py` — converted JWT decode tests to async; added three new tests:
  - `test_decode_es256_jwt_via_jwks` — happy path
  - `test_decode_es256_jwt_unknown_kid` — unknown kid → 401 TOKEN_INVALID
  - `test_decode_jwt_unsupported_alg` — HS512 → 401 TOKEN_INVALID
- `tests/conftest.py` — added ES256 fixtures (`_es256_keypair`, `_seed_jwks_cache`, `es256_supabase_jwt`, `es256_supabase_jwt_unknown_kid`, `unsupported_alg_jwt`) that generate an ephemeral EC P-256 keypair and inject the matching JWK into `_JWKS_CACHE` so tests never make a real HTTP call.

**No env-level changes.** `SUPABASE_JWT_SECRET` left in `.env` (untouched — now an unused legacy field; safe to remove later). No secret rotation. No migration.

**Container rebuilt** with `docker compose up -d --build api` (and recreated, per global CLAUDE.md memory). `outreach-platform-api` came up cleanly.

**Verification:**

1. **Unit tests** — 7/7 JWT decode tests pass inside the rebuilt container:
   - 4 HS256 legacy path tests (still work via fallback)
   - 2 new ES256/JWKS path tests
   - 1 unsupported-alg rejection test

   3 pre-existing failures in `test_lazy_workspace_create_*` / `test_repeated_request_finds_existing` are unrelated to this fix (`SQLAlchemy InvalidRequestError: A transaction is already begun on this Session` — fixture issue introduced before this debug session, verified by re-running against unmodified `main` baseline).

2. **Live JWKS fetch** — `_fetch_jwks()` run inside the running api container against `https://qhxkyzmwnehnrfndpxxo.supabase.co/auth/v1/.well-known/jwks.json` returned kid `494eb22c-6d90-42ab-a75c-65bca0e1d268` (matches the curl probe from earlier).

3. **Live endpoint smoke** — `POST /api/v1/auth/me`:
   - No auth → `401 AUTH_REQUIRED` (unchanged, good).
   - Garbage token → `401 TOKEN_INVALID` with new diagnostic "Invalid JWT (malformed header)".
   - ES256 token signed by an attacker key with random kid → `401 TOKEN_INVALID` "Invalid JWT (unknown kid)" — JWKS refreshed exactly once (logged), no signature-bypass possible.

4. **Container logs** show `HTTP/1.1 200 OK` from the Supabase JWKS endpoint + `[auth] JWKS refreshed: 1 key(s) — kids=['494eb22c-6d90-42ab-a75c-65bca0e1d268']`.

**End-to-end live magic-link flow not exercised** because that requires a real Lovable login (browser + email round-trip). The user can confirm by retrying the broken flow in the browser — expected outcome: `/auth/callback` exchange succeeds, frontend calls `POST /auth/me`, backend returns 200 with workspace, dashboard loads (no redirect to /login).

**Follow-ups for frontend repo:** None blocking. The frontend logout-on-TOKEN_INVALID patch is already deployed — keep it. New backend now distinguishes `TOKEN_EXPIRED` from `TOKEN_INVALID`; if the frontend wants to differentiate "force re-login" (expired) from "hard error" (invalid signature / unsupported alg / unknown kid), it can branch on `detail.code`.

**Follow-ups for backend (not blocking, for next session):**
- Remove unused `SUPABASE_JWT_SECRET` from `.env` + `docker-compose.yml` once we are confident no customer project remains on HS256 (could be Phase 6 cleanup).
- Migrate from `python-jose` to `PyJWT` per existing TODO (`python-jose` is unmaintained since 2024).
- Fix the 3 pre-existing `_resolve_or_create_workspace` test failures (SQLAlchemy session/transaction interleaving in the fixture).
