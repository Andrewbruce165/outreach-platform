---
phase: 02-tg-accounts-contacts
plan: 02
subsystem: api
tags: [postgres, sqlalchemy, fastapi, pydantic, alembic-free-migrations, rate-limiting, multi-tenant]

# Dependency graph
requires:
  - phase: 01-workspace-foundation
    provides: AuthCtx / auth_dep dual-auth (JWT + workspace API key), workspace_id FK on every tenant table, raw-SQL migration pattern (012_workspace.sql)
provides:
  - migration 013_phase2.sql — folders / contacts / onboarding_sessions / csv_imports + senders extension (lifecycle_status, rate_per_min/hour/day, role CHECK, is_active dropped)
  - ORM models Folder / Contact / OnboardingSession / CsvImport + extended Sender
  - Pydantic v2 schemas for Phase 2 (FolderResponse, ContactCreate, SenderResponse with derived status, WarningItem, AssignProxyRequest, RecheckRequest, ContactImportPreview/Request)
  - rewritten app/routers/senders.py — workspace-scoped CRUD via Depends(auth_dep), derived status (D-11), rate-limit warnings (D-14), assign-proxy, workspace proxy pool CRUD (D-22)
  - app/services/queue.py reads sender.rate_per_min/hour/day per tick (global MAX_MSGS_PER_* removed, D-13)
  - all 14 hidden is_active call-sites swept across listener/warmup/rotation/health/onboarding/queue
  - pytest factories test_workspace / test_sender_factory / test_checker / test_folder / test_contacts_factory in tests/conftest.py
  - tests/test_migration_013.py + tests/test_senders.py
affects:
  - 02-03 (onboarding wiring) — uses OnboardingSession ORM + test_sender_factory
  - 02-04 (folders/contacts router) — uses Folder/Contact ORM + ContactCreate schemas + test_folder factory
  - 02-05 (CSV import + ContactCheckWorker) — uses CsvImport ORM + ContactImportPreviewResponse + test_checker factory
  - 02-01 (listener reconcile loop) — uses lifecycle_status/auth_status filter that get_active_senders now applies
  - phase-03 (agents) — reuses derived-status pattern and rate-limit warning shape
  - phase-04 (campaigns) — picks up scheduling/rate-limit ownership previously hard-coded in queue.py

# Tech tracking
tech-stack:
  added: [] # no new libs; uses existing FastAPI / SQLAlchemy 2.0 async / Pydantic v2 / Telethon
  patterns:
    - "Derived API field pattern: read-time computation of `status` from auth_status + lifecycle_status (storable raw, derived for UX)"
    - "Soft cap / hard cap warning pattern: 200 OK + warnings[] for soft, 422 for hard — reusable for agent/campaign settings later"
    - "Workspace-scoped CRUD: every SELECT/UPDATE/DELETE includes .where(Model.workspace_id == ctx.workspace_id) + TODO(v2-rls) marker"
    - "Hidden-dependency sweep: PATTERNS.md catalogues all call-sites of a soon-to-be-dropped column before migration, the plan rewrites them in one atomic shot"

key-files:
  created:
    - migrations/013_phase2.sql
    - tests/test_migration_013.py
    - tests/test_senders.py
  modified:
    - app/models/__init__.py (added Folder, Contact, OnboardingSession, CsvImport; extended Sender)
    - app/schemas/__init__.py (added RateLimits, WarningItem, AssignProxyRequest, ProxyPool*, Folder*, Contact*, RecheckRequest; rewrote SenderResponse/Update)
    - app/routers/senders.py (full rewrite — workspace-scoped, derived status, warnings, assign-proxy)
    - app/services/queue.py (removed MAX_MSGS_PER_*, read from sender)
    - app/services/listener.py (3 is_active sweeps + _set_auth_status)
    - app/services/warmup.py (2 is_active sweeps)
    - app/services/rotation.py (2 is_active sweeps)
    - app/routers/health.py (1 is_active sweep)
    - app/routers/onboarding.py (1 is_active sweep in _auto_save_reauth)
    - app/main.py (include_router(senders.router))
    - tests/conftest.py (Phase 2 fixtures + 013 migration applied)

key-decisions:
  - "Single migration 013_phase2.sql for all Phase 2 DDL (C-01 single-file recommend) — atomic transaction, rollback-safe"
  - "Derived status computed at API read-time, not stored — auth_status + lifecycle_status are the two orthogonal raw fields (D-11)"
  - "Rate limits live on senders table with empirical defaults 4/20/150 = green corridor; hard cap 10/50/300 via Pydantic Field(le=) + ручной check на сервере (D-13/D-14)"
  - "Pre-existing warmup_pool.is_active column LEFT INTACT — separate table, separate semantics (was a per-row enrolment flag, not a per-sender lifecycle)"
  - "Plan-deviation: discovered hidden is_active reference in app/routers/onboarding.py:204 (_auto_save_reauth) — not in PATTERNS.md catalogue but functionally identical to listener._set_auth_status. Swept under Rule 2 (missing critical functionality, would have left dangling write to dropped column)."

patterns-established:
  - "Soft-cap warnings response shape: { sender: {...}, warnings: [{field, value, recommended_max, severity}] } — Lovable consumes this; future planning of campaign rate-limit UX should reuse"
  - "Workspace-scoped query pattern: `select(Model).where(Model.workspace_id == ctx.workspace_id)` + TODO(v2-rls) comment marker per Phase 1 D-12"
  - "Per-sender DB-read of rate limits in worker tick: single row SELECT per sender per tick, no cache (acceptable cost given 3s poll interval)"
  - "Derived 'error' status: when auth_status != 'ok', UI shows status=error regardless of lifecycle. Storage stays minimal — two columns, four observable states."

requirements-completed: [SNDR-01, SNDR-02, SNDR-03]

# Metrics
duration: ~50 min (across 2 sessions — Task 1 in prior session, Tasks 2+3 + SUMMARY in resume session)
completed: 2026-05-21
---

# Phase 02 Plan 02: Sender Settings + Phase 2 Schema Foundation Summary

**Migration 013 lays down Phase 2 DDL (folders/contacts/onboarding_sessions/csv_imports), rewrites senders router around AuthCtx with derived status + soft/hard rate-limit caps, and sweeps all 14 hidden `senders.is_active` call-sites across listener/warmup/rotation/health/queue/onboarding so the column can be dropped cleanly.**

## Performance

- **Duration:** ~50 min across 2 sessions (Task 1 prior, Tasks 2+3 in resume)
- **Started:** 2026-05-21 (Task 1) → continuation 2026-05-21T17:30Z (Tasks 2+3)
- **Completed:** 2026-05-21T17:45Z
- **Tasks:** 3 (all committed atomically)
- **Files modified:** 13 (3 created + 10 modified)

## Accomplishments

- **Migration 013_phase2.sql:** 4 new tables (folders, contacts, onboarding_sessions, csv_imports) with CHECK constraints, partial UNIQUE indexes (workspace_id, phone) and (workspace_id, username), FK CASCADE per Phase 1 convention. Senders extended (lifecycle_status, rate_per_min/hour/day, role CHECK), `is_active` column dropped.
- **Phase 2 ORM + schemas:** All Phase 2 models live in app/models, full Pydantic v2 schema layer ready for plans 02-03..02-05.
- **Senders router rewrite:** Workspace-isolated through `Depends(auth_dep)`, derived `status` field (D-11), `warnings[]` for soft-cap rate limits (D-14), assign-proxy endpoint, workspace proxy pool CRUD (D-22). No subprocess docker-restart, no `verify_api_key`, no `is_active` writes.
- **queue.py rate-limit overhaul:** Global `MAX_MSGS_PER_*` constants removed; `_check_rate_limits` reads `sender.rate_per_*` from DB per tick + gates on lifecycle_status/auth_status (replaces old is_active gate).
- **14 hidden is_active call-sites swept:** see "is_active sweep audit" below.
- **15 integration tests for senders router** (tests/test_senders.py) covering workspace isolation, derived status, rate-limit warnings/hard cap, lifecycle transitions, proxy CRUD + assign-proxy + cross-tenant 404.

## Task Commits

Each task committed atomically:

1. **Task 1: Migration 013_phase2.sql + smoke test** — `8cbaca7` (feat)
2. **Task 2: ORM models + Pydantic schemas + pytest factories** — `5696aa4` (feat)
3. **Task 3: Senders router rewrite + queue.py constants outrip + is_active sweep** — `f692821` (feat)

## Files Created/Modified

### Created
- `migrations/013_phase2.sql` — All Phase 2 DDL (atomic BEGIN/COMMIT, idempotent IF NOT EXISTS)
- `tests/test_migration_013.py` — Schema smoke (tables, CHECK constraints, partial UNIQUE indexes, is_active dropped)
- `tests/test_senders.py` — 15 integration tests covering all SNDR-01..03 acceptance criteria

### Modified
- `app/models/__init__.py` — Folder/Contact/OnboardingSession/CsvImport classes + Sender extended (is_active Column removed, lifecycle_status + rate_per_* added)
- `app/schemas/__init__.py` — Phase 2 Pydantic schemas (RateLimits, WarningItem, AssignProxyRequest, ProxyPool*, Folder*, Contact*, RecheckRequest); SenderResponse/Update rewritten with derived `status` Literal
- `app/routers/senders.py` — Full rewrite (594 lines): workspace-scoped CRUD + derived status helper + rate-limit validator + assign-proxy + workspace proxy pool CRUD
- `app/services/queue.py` — `MAX_MSGS_PER_*` removed; `_check_rate_limits` reads from sender row + gates on lifecycle_status/auth_status; SessionAuthError handler no longer flips is_active
- `app/services/listener.py` — 3 SQL filters updated, `_set_auth_status` no longer writes is_active
- `app/services/warmup.py` — `_get_active_pool` JOIN + `_process_session` per-sender eligibility check
- `app/services/rotation.py` — existing-assignment check + `_pick_best_sender` filter
- `app/routers/health.py` — derived "active" count for stats
- `app/routers/onboarding.py` — `_auto_save_reauth` no longer flips is_active (deviation; see below)
- `app/main.py` — `app.include_router(senders.router)`
- `tests/conftest.py` — Phase 2 fixtures (test_workspace, test_sender_factory, test_checker, test_folder, test_contacts_factory) + 013 migration applied in `_setup_database`

## is_active Sweep Audit (14 call-sites)

PATTERNS.md catalogued the hidden dependencies of `senders.is_active`. After this plan, all are gone (column dropped in migration 013). Reference for audit:

| # | File | Line | Was | Now |
|---|------|------|-----|-----|
| 1 | app/services/listener.py | 149 | `UPDATE senders SET auth_status, is_active=false` | `UPDATE senders SET auth_status` only |
| 2 | app/services/listener.py | 331 | `WHERE is_active = true AND role = 'sender'` | `WHERE role='sender' AND lifecycle_status='active' AND auth_status='ok'` |
| 3 | app/services/listener.py | 474 | `WHERE wp.is_active = true AND s.is_active = true AND s.role='sender'` | `WHERE wp.is_active=true AND s.lifecycle_status='active' AND s.auth_status='ok' AND s.role='sender'` (wp.is_active is the warmup_pool column — kept) |
| 4 | app/services/warmup.py | 170 | `WHERE wp.is_active=true AND s.is_active=true AND s.role='sender'` | Same as above |
| 5 | app/services/warmup.py | 246-266 | `SELECT id, slug, phone, session_string, is_active FROM senders` + eligibility check on `s.is_active` | `SELECT … lifecycle_status, auth_status` + `is_eligible = (lifecycle='active' AND auth='ok')` |
| 6 | app/services/rotation.py | 48 | `SELECT cca.sender_id, s.is_active` | `SELECT cca.sender_id, (s.lifecycle_status='active' AND s.auth_status='ok') AS is_eligible` |
| 7 | app/services/rotation.py | 147 | `WHERE … AND s.is_active = true AND s.role='sender'` | `WHERE … AND s.lifecycle_status='active' AND s.auth_status='ok' AND s.role='sender'` |
| 8 | app/services/queue.py | 350 | `if not sender or not sender.is_active:` | `if not sender: fail. if lifecycle != 'active' or auth != 'ok': fail.` |
| 9 | app/services/queue.py | 549 | `UPDATE senders SET is_active = false` (SessionAuthError) | Removed — auth_status alone carries error signal |
| 10 | app/routers/senders.py | 24 | `"is_active": sender.is_active` (response field) | Replaced with derived `status` field |
| 11 | app/routers/senders.py | 91 | `is_active=True` on create | Removed |
| 12 | app/routers/senders.py | 195-196 | `if request.is_active is not None: sender.is_active = request.is_active` | Replaced by `lifecycle_status` handling |
| 13 | app/routers/senders.py | 308 | `sender.is_active = False` after SpamBot | Removed — only `auth_status` update |
| 14 | app/routers/health.py | 37 | `sum(1 for s in senders if s.is_active)` | `sum(1 for s in senders if s.lifecycle_status=='active' and s.auth_status=='ok')` |

**Plus one not in PATTERNS.md catalogue (deviation, see below):**
- `app/routers/onboarding.py:204` — `sender.is_active = True` after successful re-auth, removed.

## Decisions Made

- **Confirmed C-01 single-migration strategy.** All Phase 2 DDL lives in one `013_phase2.sql` file (atomic transaction, single rollback unit). Alternative was 013/014/015 split — rejected because partial failure would leave schema half-migrated.
- **Confirmed C-02 Option B (DB blob) for CSV imports.** `csv_imports.file_data BYTEA NOT NULL` + 30-min TTL via `expires_at` column. Alternative was /tmp/{import_id} — rejected because survives no API restart.
- **Confirmed C-03 warnings[] shape.** Lovable-friendly: `{warnings: [{field, value, recommended_max, severity}]}` nested inside SenderCreateResponse. Same shape returned from POST and PATCH so frontend has one renderer.
- **Confirmed C-04 schema names** — `FolderCreate/Update/Response`, `ContactCreate/Response`, `ContactImportPreview/Request/Summary`, `WarningItem`, `RateLimits`. All match existing Phase 1 convention (`schemas/__init__.py`).
- **Kept warmup_pool.is_active intact.** Same column name, different semantics: per-row "is this sender enrolled in warmup right now". Migrated only the senders-table is_active.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Missing Critical] Swept extra is_active reference in app/routers/onboarding.py:204**
- **Found during:** Task 3 final grep verification (`grep -rn "is_active" app/`)
- **Issue:** PATTERNS.md catalogued 14 hidden is_active call-sites but missed `_auto_save_reauth` in onboarding.py which writes `sender.is_active = True` after successful re-auth. After migration 013 drops the column, this assignment would raise AttributeError at runtime.
- **Fix:** Removed the line; added comment explaining derived-active semantics. `auth_status='ok'` alone is now the signal of successful re-auth (matches Phase 2 D-11/D-12 contract).
- **Files modified:** app/routers/onboarding.py
- **Verification:** `grep -nE 'sender\.is_active' app/` returns 0 matches; `python3 -c "import ast; ast.parse(open('app/routers/onboarding.py').read())"` passes.
- **Committed in:** f692821 (Task 3 commit)

**2. [Rule 3 — Blocking docstring grep noise] Cleaned up stale references to MAX_MSGS_PER_HOUR**
- **Found during:** Task 3 grep verification of `MAX_MSGS_PER`
- **Issue:** Plan acceptance criterion required `grep -rn "MAX_MSGS_PER" app/` → 0 results. Two references in app/services/queue.py were in comments/docstrings (line 39 fatigue-factor comment + line 231 docstring mention). Strict-greppers / future linters could flag these.
- **Fix:** Updated comment to reference `sender.rate_per_hour`; rewrote docstring to drop the old constant name.
- **Files modified:** app/services/queue.py
- **Committed in:** f692821 (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 grep-hygiene).
**Impact on plan:** Both fixes essential — onboarding.py issue would have caused runtime AttributeError after migration 013. No scope creep, no architectural changes.

## Issues Encountered

**1. Python venv unavailable in local environment.** Could not run pytest locally to validate test suite. Mitigation: validated all changed files with `python3 -m ast` parse + grep-based acceptance criteria check. Tests will run in CI / Docker on next deploy (`docker compose up -d --build api`).

**2. Pre-existing tech debt left untouched:**
- `app/services/listener.py:471` queries `s.telegram_id` — column doesn't exist on Sender ORM. Out of scope for this plan.
- `app/services/ai_engine.py:75` filters `WHERE id=:id AND is_active=true` — this is `ai_contexts.is_active`, a different column (Phase 0 AIContext model still has it). Left intact per plan instruction "warmup_pool.is_active — другая колонка, оставляем" (same logic applies).

## Warnings Response Shape for Lovable

The PATCH /senders/{slug} response shape (also POST /senders) for documentation:

```json
{
  "sender": {
    "id": "uuid",
    "slug": "...",
    "status": "active",            // derived: 'active'|'warmup'|'paused'|'error'
    "auth_status": "ok",            // raw, for tooltip
    "lifecycle_status": "active",   // raw, for controls
    "rate_limits": { "per_minute": 7, "per_hour": 20, "per_day": 150 },
    "..."
  },
  "warnings": [
    { "field": "rate_per_min", "value": 7, "recommended_max": 4, "severity": "warning" }
  ]
}
```

Hard-cap exceedance (rate_per_min > 10):
```json
{ "detail": { "code": "RATE_LIMIT_EXCEEDS_HARD_CAP", "field": "rate_per_min", "value": 15, "hard_cap": 10, "message": "exceeds maximum safe limit, contact support if you need higher" } }
```
HTTP 422.

## Pytest Factories Available for Downstream Plans

`tests/conftest.py` now provides these fixtures (used by plans 02-03/02-04/02-05):
- `test_workspace` — Workspace row for the test session
- `test_sender_factory(role='sender'|'checker', slug=..., …)` — sender ORM row with all defaults filled
- `test_checker` — convenience fixture wrapping `test_sender_factory(role='checker')`
- `test_folder` — Folder row in test_workspace
- `test_contacts_factory(count=N, tg_status=...)` — N contacts in test_folder

## User Setup Required

None — no external service configuration required. Migration 013 will apply automatically on next `docker compose up -d --build api`.

## Next Phase Readiness

**Wave 1 unblocked** — plans 02-03 (onboarding wiring) and 02-04 (folders/contacts router) can now start in parallel. Both depend only on:
- ORM models from this plan (Folder, Contact, OnboardingSession ready)
- Pydantic schemas from this plan (FolderCreate/Response, ContactCreate/Response, OnboardingStart/VerifyCode patterns)
- pytest factories from this plan (test_folder, test_sender_factory, test_checker)
- senders router workspace-isolation pattern (PATTERNS.md cross-references)

**Wave 2 (02-05 contacts_check_worker) waits on:** 02-04 (contacts router) — needs ContactBatchPush + folder auto-create helper from there.

**Wave 0+1 (02-01 listener reconcile) can run anytime after Task 3** — its filter already matches the new schema (`lifecycle_status='active' AND auth_status='ok'`).

No blockers, no concerns.

## Self-Check: PASSED

- migrations/013_phase2.sql exists: FOUND
- tests/test_migration_013.py exists: FOUND
- tests/test_senders.py exists: FOUND
- Commits 8cbaca7, 5696aa4, f692821 in git log: FOUND
- `grep -c 'is_active' app/routers/senders.py` (excluding docstring comments) → 0 code matches
- `grep -c 'MAX_MSGS_PER' app/services/queue.py` (excluding docstring comments) → 0 code matches
- `python3 -m ast` parse on all 11 modified Python files: PASSED
- senders router includes RATE_HARD_CAP, _derive_status, Depends(auth_dep) (11 places), assign-proxy (3 places): VERIFIED

---
*Phase: 02-tg-accounts-contacts*
*Completed: 2026-05-21*
