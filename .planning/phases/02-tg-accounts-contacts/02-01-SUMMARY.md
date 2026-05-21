---
phase: 02-tg-accounts-contacts
plan: 01
subsystem: api
tags: [fastapi, telethon, postgres, sqlalchemy-async, async-worker, multi-tenant, periodic-reconcile]

# Dependency graph
requires:
  - phase: 01-workspace-foundation
    provides: AuthCtx / auth_dep dual-auth, workspace_id FK on senders, encryption.encrypt_session / decrypt_session, make_telegram_client factory
  - plan: 02-02
    provides: onboarding_sessions table (migration 013), OnboardingSession ORM, Sender.lifecycle_status / rate_per_*, is_active dropped, test_workspace + test_sender_factory + test_checker fixtures
provides:
  - app/services/onboarding_state.py — workspace-scoped CRUD helpers around onboarding_sessions (save_state, load_state, update_status, delete_session) + OnboardingCleanupWorker
  - app/routers/onboarding.py — full rewrite of 12 endpoints onto Depends(auth_dep) + persistent state + recovery (decrypt_session) + Telethon error mapping
  - app/services/listener.py — periodic reconcile loop (_reconcile_loop, _reconcile_tick, _disconnect_sender) so new senders are picked up without restarting the container
  - docker-compose.yml — host socket mount + user:root removed (D-18)
  - app/main.py — registers onboarding router and starts onboarding_cleanup_worker in lifespan
  - tests/test_onboarding_state.py — encryption, TTL expiry, cross-tenant isolation, status transitions, cleanup worker tick
  - tests/test_onboarding.py — Telethon-mocked integration tests covering start / verify-code / 2FA / QR / cancel / recovery / cross-workspace / role override
  - tests/test_listener_reconcile.py — diff logic unit tests (new / removed / proxy-change / cancel / SQL filter shape)
affects:
  - 02-04 (contacts + CSV) — uses the same auth_dep workspace-scoping pattern proved here for routers with persistent state
  - 02-05 (ContactCheckWorker) — reuses the AsyncSessionLocal + asyncio.Task worker pattern from OnboardingCleanupWorker
  - phase-04 (campaigns) — sender lifecycle gating already works through lifecycle_status + auth_status; reconcile picks up campaign-driven pauses automatically

# Tech tracking
tech-stack:
  added: [] # all libs already in requirements.txt (telethon, qrcode, cryptography, sqlalchemy[asyncio])
  patterns:
    - "Persistent state with in-process side-cache (D-17): durable phone_code_hash + encrypted session_string in DB, non-serialisable Telethon client in a process-local dict, transparent recovery via decrypt_session on cache miss"
    - "Module-level singleton worker registered in FastAPI lifespan (start after queue_worker / warmup_worker, stop before engine.dispose) — copy of QueueWorker / WarmupWorker shape, no new infra"
    - "Periodic reconcile loop replaces docker-restart anti-pattern: idempotent SELECT desired set, set-diff against currently-connected, dispatch start_client / disconnect / proxy-change as create_task'd actions"
    - "Defensive cleanup on auth/ban errors: start_client clears _connected_sender_ids + _proxy_snapshot when Telethon raises AUTH_ERRORS / UserDeactivatedBanError so a future reauth lands on the NEW branch of reconcile diff"

key-files:
  created:
    - app/services/onboarding_state.py
    - tests/test_onboarding_state.py
    - tests/test_onboarding.py
    - tests/test_listener_reconcile.py
  modified:
    - app/routers/onboarding.py (full rewrite — 778 → 605 lines, in-memory dict + subprocess + verify_api_key all gone)
    - app/services/listener.py (added reconcile loop, bookkeeping in start_client, graceful stop, signal-event)
    - app/main.py (import + register onboarding router; start/stop OnboardingCleanupWorker)
    - docker-compose.yml (removed /var/run/docker.sock volume mount and user:root from api service)

key-decisions:
  - "Telethon TelegramClient stays in in-process dict (D-17 confirmed): the client object embeds OS sockets and event handlers, so we accept the trade-off that ongoing flows in volatile memory may need 'start over' on container restart — but only IF the user is mid-verify. The recovery path via decrypt_session(StringSession) handles the common restart-after-/start case transparently."
  - "QR flow does NOT have a recovery path: the qr_login object is not persisted (Telethon private API), so api-container restart during the 120-second QR window forces the user to re-scan. CONTEXT.md and RESEARCH §QR-only-in-process already endorse this trade-off."
  - "Reconcile interval 30s (D-18 / C-06) is overridable via LISTENER_RECONCILE_INTERVAL env var. start_client is fired as asyncio.create_task from the reconcile tick (not awaited) so one slow connect doesn't stall the next tick."
  - "Initial connects in run() also switched from asyncio.gather to create_task + asyncio.Event() — so the reconcile loop keeps ticking even when initial_senders is empty (first-ever startup of a fresh workspace)."
  - "Slug for newly-onboarded sender = 'sender-{telegram_id}' (falls back to 'sender-{session_id[:8]}' on get_me failure). Globally unique by virtue of telegram_id; collision with legacy slugs returns 500 IntegrityError — acceptable in v1 (clean DB per Phase 1 D-01)."

patterns-established:
  - "Auth gate is the FIRST line of every router endpoint (Depends(auth_dep) before any other dep): tested by AUTH_REQUIRED 401 in test_onboarding.py — same shape as folders/senders tests"
  - "Cross-tenant SESSION_NOT_FOUND, not 403: load_state returns None when workspace_id mismatches, the router maps to 404 with code='SESSION_NOT_FOUND' — security pattern of 'not revealing existence' already used in senders/workspace routers"
  - "Inline Pydantic schemas for endpoint-local types (StartRequest, VerifyCodeRequest, etc.) — kept inside app/routers/onboarding.py instead of promoting to app/schemas/__init__.py because they're not consumed by any other module"
  - "Sub-second test idempotency: clear _in_process_clients at the top of each onboarding test so the monkeypatched make_telegram_client is the only factory in play"

requirements-completed: [ONBD-01, ONBD-02, ONBD-03, ONBD-04, ONBD-05]

# Metrics
duration: ~25 min (single session)
completed: 2026-05-21
---

# Phase 02 Plan 01: Onboarding Rewrite + Listener Reconcile Loop Summary

**Workspace-scoped onboarding (12 endpoints) on top of AuthCtx + persistent state in `onboarding_sessions`; periodic reconcile loop in `listener.py` so new senders connect automatically — `subprocess.run('docker restart')` and the host socket mount are gone.**

## Performance

- **Duration:** ~25 min (single session, 2026-05-21)
- **Started:** 2026-05-21T20:30Z
- **Completed:** 2026-05-21T20:55Z
- **Tasks:** 3 (all committed atomically)
- **Files modified:** 8 (4 created + 4 modified)
- **Commits:** 3 task commits + this docs commit

## Accomplishments

- **`app/services/onboarding_state.py`** (Task 1, D-16): Persistent state helpers + `OnboardingCleanupWorker`. Module-level singleton wired into `app/main.py` lifespan. TTL = 10 min, cleanup tick = 5 min (env override `ONBOARDING_CLEANUP_INTERVAL`). All session strings encrypted via `encrypt_session` before write.
- **`app/routers/onboarding.py` full rewrite** (Task 2, ONBD-01..05, D-16/D-17/D-18): All 12 endpoints under `Depends(auth_dep)`. `_in_process_clients: dict[str, TelegramClient]` is the only piece of in-process state (D-17). `_get_or_recover_client` rebuilds Telethon from `decrypt_session(StringSession)` when the dict is empty (api-container restart between `/start` and `/verify-code` is invisible to the user). Subprocess `docker restart`, `verify_api_key`, the legacy in-memory dict — all gone.
- **`app/services/listener.py` reconcile loop** (Task 3, D-18): `_reconcile_tick` diffs desired senders (from `get_active_senders`) against `_connected_sender_ids`, schedules `start_client` for new, `_disconnect_sender` for removed/paused/auth-errored, and disconnect-now / reconnect-next-tick on proxy change. `_reconcile_loop` runs every 30s (env override). `run()` switched to `create_task` + `Event` so the loop keeps ticking with zero initial senders. `stop()` cancels reconcile gracefully and clears all bookkeeping.
- **`docker-compose.yml`** (Task 2, D-18): removed the `/var/run/docker.sock:/var/run/docker.sock` volume mount and `user: root` from the api service — the API container no longer needs (or has) any way to talk to the Docker daemon.
- **Tests:**
  - `tests/test_onboarding_state.py` — 10 unit tests (encryption round-trip, cross-tenant isolation, TTL expiry, status transitions, cleanup worker tick, singleton check).
  - `tests/test_onboarding.py` — 14 integration tests with `monkeypatch.setattr(make_telegram_client)`, covering AUTH_REQUIRED, happy paths for start/verify-code/verify-2fa, error mapping (PhoneCodeInvalid → 400, SessionPasswordNeeded → 200 2fa_required, PasswordHashInvalid → 400), cross-workspace 404, QR start image, cancel idempotency, recovery after emptied dict, checker-role override at verify time, and a static-source guard against re-introduction of `subprocess` / `_onboarding_sessions` / `verify_api_key`.
  - `tests/test_listener_reconcile.py` — 7 reconcile-diff tests (new connect, removed disconnect, auth-flipped disconnect, proxy change, loop cancellation, SQL filter shape audit, attribute presence).

## Task Commits

Each task committed atomically:

1. **Task 1: onboarding_state service + cleanup worker** — `013c912` (feat)
2. **Task 2: onboarding router rewrite + docker-compose lockdown + integration tests** — `04e6921` (feat)
3. **Task 3: listener reconcile loop + unit tests** — `82966a8` (feat)
4. **Plan completion (this file + STATE.md + ROADMAP.md update)** — `<final-docs-commit>`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Auto-add missing critical functionality] `docker.sock` mention was forbidden by acceptance criteria, not just the mount**
- **Found during:** Task 2 acceptance check `grep -c "docker.sock" docker-compose.yml == 0`.
- **Issue:** After dropping the volume mount I left a comment that referenced `docker.sock` literally. The acceptance criterion was a literal grep-count, so the comment had to be reworded too.
- **Fix:** Reworded the comment to `# Phase 2 (D-18): host socket mount removed — listener auto-reconciles ...`. Behaviour identical; criterion now passes.
- **Files modified:** docker-compose.yml
- **Commit:** included in `04e6921` (same task amend was unnecessary — the rewording happened before commit)

**2. [Rule 2 — Auto-add missing critical functionality] `run()` would have spawned no reconcile loop when no initial senders exist**
- **Found during:** Task 3 design walkthrough.
- **Issue:** The pre-existing `run()` returned early with `logger.warning("⚠️ Нет активных отправителей")` if `get_active_senders` was empty. Without modification, a fresh workspace whose first sender is onboarded after the listener starts would never get connected — the reconcile loop would never start either.
- **Fix:** `run()` now schedules initial connects with `asyncio.create_task` (not `asyncio.gather` blocking), always starts the reconcile task, and blocks on a new `_stop_event` instead of the gather result. `stop()` flips the event to unblock `run()`.
- **Files modified:** app/services/listener.py
- **Commit:** included in `82966a8` (Task 3)

**3. [Rule 1 — Bug fix] start_client did not clear reconcile bookkeeping on auth-error / ban**
- **Found during:** Task 3 — designing reconcile diff invariants.
- **Issue:** When Telethon raises `AUTH_ERRORS` or `UserDeactivatedBanError`, `start_client` `return`s without reconnect — but it also failed to remove the sender from `self.clients`, `_connected_sender_ids`, and `_proxy_snapshot`. After a future reauth (`auth_status = 'ok'`) reconcile would compute `current ⊇ desired` for that sid and never spawn `start_client` again.
- **Fix:** In both AUTH_ERRORS and UserDeactivatedBanError branches now: `_connected_sender_ids.discard(sid)`, `_proxy_snapshot.pop(sid, None)`, `self.clients.pop(slug, None)`.
- **Files modified:** app/services/listener.py
- **Commit:** included in `82966a8` (Task 3)

### Notes

- One `is_active` reference (in `_auto_save_reauth`) was already swept by plan 02-02 (commit `f692821`). Re-verified during the onboarding rewrite — no leftovers (the whole `_auto_save_reauth` function is gone with the in-memory dict).
- The `app/utils/phone.py` E.164 normaliser (deferred to plan 02-04 per CONTEXT) is NOT introduced here; onboarding uses a local `_normalize_phone` helper that does strip + leading-plus only, and lets Telethon's `PhoneNumberInvalidError` surface the rest. This keeps plan 02-01 atomic; plan 02-04 will replace `_normalize_phone` with a shared util.

## Authentication Gates

None encountered (Telethon calls are fully mocked in tests; no live Telegram, no Supabase JWT signature work beyond conftest's HS256 secret).

## Files Created / Modified

- `app/services/onboarding_state.py` (created — 205 lines)
- `app/routers/onboarding.py` (rewritten — 605 lines, down from 778)
- `app/services/listener.py` (modified — +135 lines for reconcile loop and bookkeeping)
- `app/main.py` (modified — import + register router + start/stop worker)
- `docker-compose.yml` (modified — removed host socket mount + user:root)
- `tests/test_onboarding_state.py` (created — 286 lines, 10 tests)
- `tests/test_onboarding.py` (created — 326 lines, 14 tests)
- `tests/test_listener_reconcile.py` (created — 219 lines, 7 tests)

## Self-Check

Run after writing this file:

- [x] `app/services/onboarding_state.py` exists
- [x] `app/routers/onboarding.py` contains `router = APIRouter(prefix="/api/v1/onboarding"`
- [x] `app/routers/onboarding.py` contains `Depends(auth_dep)` ≥ 7 times (actual: 9)
- [x] `app/routers/onboarding.py` does NOT contain `subprocess` (excl. docstring mention of removal)
- [x] `app/routers/onboarding.py` does NOT contain `_onboarding_sessions: dict` declaration (excl. docstring mention of removal)
- [x] `app/routers/onboarding.py` does NOT contain `verify_api_key`
- [x] `app/routers/onboarding.py` contains `_in_process_clients: dict[str, TelegramClient]`
- [x] `app/routers/onboarding.py` contains `_get_or_recover_client` + `decrypt_session(session_row.encrypted_session_string)`
- [x] `app/routers/onboarding.py` imports from `app.services.onboarding_state`
- [x] `app/routers/onboarding.py` contains `SessionPasswordNeededError` handling
- [x] `app/routers/onboarding.py` defaults `lifecycle_status="active"` on the freshly-created sender
- [x] Workspace-isolation via `load_state(db, sid, ctx.workspace_id)` (and direct `.where(Sender.workspace_id == ctx.workspace_id)` for reauth)
- [x] `app/main.py` registers `onboarding.router` and starts `onboarding_cleanup_worker`
- [x] `grep -c "docker.sock" docker-compose.yml` == 0
- [x] `app/services/listener.py` has `_reconcile_loop`, `_reconcile_tick`, `_connected_sender_ids`, `_proxy_snapshot`, `LISTENER_RECONCILE_INTERVAL`
- [x] All 3 task commits exist in `git log`: 013c912, 04e6921, 82966a8

## Self-Check: PASSED
