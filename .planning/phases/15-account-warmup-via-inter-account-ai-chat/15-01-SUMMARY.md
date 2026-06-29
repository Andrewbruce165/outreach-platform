---
phase: 15-account-warmup-via-inter-account-ai-chat
plan: 01
subsystem: database
tags: [postgres, sqlalchemy, pytest, warmup, migrations, tdd]

# Dependency graph
requires:
  - phase: 12-workspace (mig 012)
    provides: workspace_id on warmup_pool/sessions/messages + workspaces table
  - phase: 10-account-health
    provides: senders.restriction_status / restricted_until (the WARM-14 gate inputs)
provides:
  - warmup_settings table (mig 038) + WarmupSettings ORM — per-workspace master toggle + content
  - conftest wiring so the ephemeral test DB builds warmup_settings
  - three RED test files (isolation/router/worker) that Plans 02/03/04 turn green
  - WARM-01..15 requirements block in REQUIREMENTS.md
affects: [15-02-isolation, 15-03-worker, 15-04-router]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-workspace settings stored as a row keyed by workspace_id (mig 038), default-OFF explicit opt-in"
    - "Wave-0 RED test scaffold with deferred-in-body imports of not-yet-existing symbols (clean --collect-only, genuine RED)"
    - "Source-introspection guard for the isolation short-circuit (Phase 13 getsource pattern)"

key-files:
  created:
    - migrations/038_warmup_settings.sql
    - tests/test_warmup_isolation.py
    - tests/test_warmup_router.py
    - tests/test_warmup_worker.py
  modified:
    - app/models/__init__.py
    - tests/conftest.py
    - .planning/REQUIREMENTS.md

key-decisions:
  - "Migration numbered 038 (not the plan's 037) — slot 037 was taken by 037_campaign_prompt_presets.sql (commit 4022984)"
  - "warmup_settings.enabled DEFAULT FALSE, NO live-workspace seed — explicit opt-in (research Open Question 3)"
  - "Content defaults (D-10): absence of row / empty topics / NULL system_prompt resolves in code to WARMUP_TOPICS + WARMUP_SYSTEM_PROMPT"

patterns-established:
  - "Wave-0 RED scaffold: 8 tests fail for missing behaviour (not import errors); deferred body imports keep collection clean"
  - "Per-workspace warmup_settings table is the storage anchor for WARM-06 / WARM-10"

requirements-completed: [WARM-01, WARM-02, WARM-04, WARM-05, WARM-06, WARM-10, WARM-14]

# Metrics
duration: 25min
completed: 2026-06-29
---

# Phase 15 Plan 01: Warmup Settings Foundation + RED Test Scaffold Summary

**warmup_settings table (mig 038, default-OFF explicit opt-in) + WarmupSettings ORM, conftest wiring, and 8 RED isolation/router/worker tests that anchor the Phase-15 isolation guarantee before any implementation.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-06-29T~12:00Z
- **Completed:** 2026-06-29T~12:25Z
- **Tasks:** 4
- **Files modified:** 7 (4 created, 3 modified)

## Accomplishments

- `migrations/038_warmup_settings.sql` — idempotent per-workspace warmup control + content table; `enabled DEFAULT FALSE`; NO live-workspace seed (explicit opt-in); verified it re-applies idempotently and creates all 8 columns in the test DB.
- `WarmupSettings` ORM model in `app/models/__init__.py` (workspace_id PK, enabled/topics/system_prompt/language/tone/timestamps); imports cleanly (`app.models.WarmupSettings.__tablename__ == "warmup_settings"`).
- conftest `_build_outreach_schema` now exists-guard-applies mig 038 so the ephemeral test DB has `warmup_settings`.
- Three RED test files with the canonical names from 15-VALIDATION.md — **8 tests, all RED for the right reason** (missing behaviour / missing helper), collection clean.
- WARM-01..15 derived block + 15 Traceability rows + dated footer in REQUIREMENTS.md.
- Full suite (excluding the 3 RED files) **798 passed, 1 skipped** — no regression from the migration/ORM/conftest changes.

## Task Commits

1. **Task 1: Migration 038 + WarmupSettings ORM** — `1e84f18` (feat)
2. **Task 2: Wire migration 038 into conftest schema builder** — `7e7510d` (test)
3. **Task 3: RED test stubs — isolation, router, worker** — `410ceac` (test)
4. **Task 4: WARM-01..15 derived block in REQUIREMENTS.md** — `f69f9f7` (docs)

## Files Created/Modified

- `migrations/038_warmup_settings.sql` — warmup_settings table (workspace_id PK, enabled DEFAULT FALSE, topics JSONB, system_prompt/tone nullable, language default ru, timestamps); idempotent, no seed.
- `app/models/__init__.py` — `class WarmupSettings(Base)` added after WarmupMessage.
- `tests/conftest.py` — exists-guarded apply of mig 038 in `_build_outreach_schema`.
- `tests/test_warmup_isolation.py` — WARM-01/02/04 RED guards (workspace internal-sender set, no-DB-write/no-AI on internal inbound, source-introspection guard for both handlers).
- `tests/test_warmup_router.py` — WARM-05 RED guards (workspace scoping + /pool response-shape incl. new restriction_status/restricted_until, is_active no longer required).
- `tests/test_warmup_worker.py` — WARM-06/10/14 RED guards (disabled-workspace skip, content-defaults resolver, restricted-sender exclusion).
- `.planning/REQUIREMENTS.md` — WARM-01..15 block + Traceability rows + footer.

## RED Test Count

**8 tests, all RED** at end of plan (expected — go green in Plans 02/03/04):

| File | Tests | Why RED |
|------|-------|---------|
| test_warmup_isolation.py | 3 | `_get_workspace_sender_tg_ids` helper + short-circuit not implemented (Plan 02) |
| test_warmup_router.py | 2 | workspace-scoped `/api/v1/warmup/pool` (AuthDep) not mounted/rewritten (Plan 04) |
| test_warmup_worker.py | 3 | enabled gate + `_get_warmup_content` resolver + restriction clause not added (Plan 03) |

## Decisions Made

- **Migration numbered 038, not 037** — slot 037 was already taken by `037_campaign_prompt_presets.sql` (commit 4022984). 038 used in filename, internal comment, ORM/conftest wiring, REQUIREMENTS references.
- **`enabled DEFAULT FALSE`, no live-workspace seed** — explicit opt-in (research Open Question 3). Behaviour change documented in the migration comment: warmup stays OFF until the user flips the master toggle in the new tab.
- **Content defaults in code, not DB** — empty `topics` / NULL `system_prompt` resolve to the 24 RU `WARMUP_TOPICS` + `WARMUP_SYSTEM_PROMPT` (asserted by `test_content_defaults_when_empty`).

## Deviations from Plan

### Mandated override (not a discovered deviation)

**1. Migration 037 → 038 (per the MIGRATION_NUMBER_OVERRIDE instruction)**
- **Reason:** Plan frontmatter and tasks specified `migrations/037_warmup_settings.sql`, but commit 4022984 had already created `migrations/037_campaign_prompt_presets.sql` for a parallel quick task.
- **Fix:** Created `migrations/038_warmup_settings.sql`; updated the conftest key-link (`038_warmup_settings`) and all internal references. The plan's must_haves `artifacts.path` of `migrations/037_warmup_settings.sql` is satisfied at `migrations/038_warmup_settings.sql` with byte-identical table content (table name, columns, default-FALSE, idempotency, no seed all per plan).
- **Files:** migrations/038_warmup_settings.sql, tests/conftest.py.
- **Committed in:** 1e84f18 (Task 1), 7e7510d (Task 2).

---

**Total deviations:** 1 mandated migration-number override (037→038). No auto-fixed bugs/blocking issues. No scope creep.
**Impact on plan:** None functional — only the migration number differs from the plan text; everything else is exactly as specified.

## Issues Encountered

- **Legacy warmup router is unmounted and broken-import.** `app/routers/warmup.py` imports `from app.routers.auth import verify_api_key` but `app.routers.auth` does not exist, and the router is NOT included in `app/main.py`. This is the legacy state the D-05 rewrite (Plan 04) replaces. The router RED tests therefore get 404 on `GET /api/v1/warmup/pool` — which is a correct RED signal (the workspace-scoped, AuthDep-mounted endpoint does not exist yet). Recorded here so Plan 04 knows to mount the router and fix the import as part of the rewrite.

## Known Stubs

None. This plan is Wave-0 scaffolding by design: the migration + ORM are production-real, and the three test files are intentionally-failing RED guards (documented), not stubbed product code.

## Next Phase Readiness

- `warmup_settings` table + ORM ready for Plan 03 (`_get_active_pool` enabled gate, `_get_warmup_content` resolver, restriction clause) and Plan 04 (router `PUT/GET /settings`).
- 8 RED tests pin the exact behaviours; Plans 02/03/04 turn them green.
- Plan 04 must also mount the warmup router in `app/main.py` and fix the broken `app.routers.auth` import as part of the D-05 AuthDep rewrite.

---
*Phase: 15-account-warmup-via-inter-account-ai-chat*
*Completed: 2026-06-29*
