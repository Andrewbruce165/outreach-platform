---
phase: quick-260713-h7z
plan: 01
subsystem: account-import
tags: [telegram, telethon, account-import, session-hardening]
status: complete
requires:
  - app/services/account_import.py::import_one_account
  - tests/conftest.py::stub_import_telethon
provides:
  - "Best-effort auth.ResetAuthorizations on every successful account import"
affects:
  - app/services/account_import.py
  - tests/conftest.py
  - tests/test_account_import.py
tech-stack:
  added: []
  patterns:
    - "Best-effort side-effect wrapped in its own try/except that swallows everything so it can never change the caller's result code"
key-files:
  created: []
  modified:
    - app/services/account_import.py
    - tests/conftest.py
    - tests/test_account_import.py
decisions:
  - "Reset is always-on: no settings flag, no role gate — runs for sender and checker imports identically."
  - "Reset failure never changes the import result — logged as a warning with masked phone only, per the module Security invariant."
metrics:
  duration: ~15m
  completed: 2026-07-13
  tasks: 2
  files: 3
---

# Quick 260713-h7z: Terminate Other Active Telegram Sessions Summary

Every successfully-imported Telegram account now issues a best-effort `auth.ResetAuthorizations` immediately after `get_me()` succeeds in `import_one_account`, killing any other live session (e.g. a vendor still logged into the sold account from a different IP) so Telegram's concurrent-use auth_key revocation can no longer silently kill the imported account.

## What Was Built

- **Task 1** — Extended the `stub_import_telethon` test fixture (`tests/conftest.py`) so the fake client is directly callable/awaitable (`await client(request)`), delegating to a new `reset_authorizations` AsyncMock exposed on the fixture namespace. Tests can assert `await_count` / inspect the passed request, or set `side_effect` to simulate a failing reset. All pre-existing AsyncMock attributes and `.install(module)` are preserved.
- **Task 2** — Added `from telethon.tl.functions.auth import ResetAuthorizationsRequest` and wired a self-contained `await client(ResetAuthorizationsRequest())` call inside `import_one_account`, placed right after `me = await client.get_me()` and before the `finally:` disconnect, still inside the same inner `try`. The call is wrapped in its own `try/except Exception` that swallows all failures (FloodWait / RPCError / "already reset within 24h") — on success it logs an info line, on failure a `logger.warning`, both with the masked phone only. No settings flag, no role gate. Added two unit tests (success path: reset invoked once, result `imported`; best-effort path: reset raises `FloodWaitError`, result still `imported`).

## Verification

- Task 1: `pytest tests/test_account_import.py -k "dedup_skip or partial_success"` → 2 passed.
- Task 2: `pytest tests/test_account_import.py -k "reset"` → 2 passed.
- Full suite: `pytest tests/test_account_import.py` → **21 passed**.

All runs via the test overlay (`docker-compose.test.yml`, ephemeral tmpfs `db-test`), never against prod.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test-overlay invocation adapted for the worktree environment**
- **Found during:** Task 1 verification
- **Issue:** The exact command in the plan (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest ...`) failed in this git worktree. The base `api.depends_on` merges `db` into the test overlay, and the base `db` service has a fixed `container_name: outreach-platform-db` that collides with the already-running prod db container (the worktree's compose project name differs from the main checkout, so compose does not recognise the running prod container as its own). Separately, the worktree has no `.env` (gitignored), so `Settings()` failed on `telegram_api_id` int-parsing.
- **Fix:** Brought up only the ephemeral `db-test` (`up -d db-test`) and ran `api` with `--no-deps` (which is all the tests need — `DATABASE_URL` points at `db-test`), plus `--env-file /root/apps/aimly/tg-outreach/.env` to supply the (gitignored) config from the main checkout. This keeps the safety guarantee intact: DATABASE_URL still targets `outreach_test`, never prod. The prod `db` container was never touched; `db-test` was torn down after the run.
- **Files modified:** none (test-invocation only)
- **Commit:** n/a

## Self-Check: PASSED

- `app/services/account_import.py` — FOUND (contains `ResetAuthorizationsRequest` import + guarded call)
- `tests/conftest.py` — FOUND (contains `reset_authorizations` AsyncMock)
- `tests/test_account_import.py` — FOUND (contains the two `reset` tests)
- Commit `2f82c27` (Task 1) — FOUND
- Commit `3048ffc` (Task 2) — FOUND

## Known Stubs

None.
