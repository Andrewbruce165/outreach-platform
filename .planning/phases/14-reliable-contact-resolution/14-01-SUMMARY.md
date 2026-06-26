---
phase: 14
plan: 01
subsystem: contact-resolution
tags: [migration, config, test-scaffold, brownfield, wave-0]
requires:
  - "migrations/028 idempotent ADD-COLUMN template"
  - "Phase 10 sender_restriction_events + senders.restriction_status/restricted_until"
  - "ContactCheckWorker._tick claim-queue (Phase 2)"
provides:
  - "contacts.tg_confidence / tg_resolved_by / tg_probe_state (migration 034 + ORM mirror)"
  - "Settings.contact_check_burst_cap / pace_low / pace_high / cooldown_seconds / daily_cap"
  - "Wave-0 RED test scaffold (probe/cap/pool/selection/confidence/import-fallback)"
  - "conftest mock_telethon_client fixture"
affects:
  - "Wave 2 (14-02): selection gate, burst/daily cap, mobile-first, probe miss-counting, rotation turn RED→GREEN"
  - "Wave 3 (14-03): confidence/source write, suspect rollback, importContacts fallback+cleanup turn RED→GREEN"
tech-stack:
  added: []
  patterns:
    - "Idempotent raw-SQL migration (ADD COLUMN IF NOT EXISTS + DROP/ADD CONSTRAINT) mirroring 028"
    - "pydantic Settings Field with validation_alias for CONTACT_CHECK_* env-knobs"
    - "Deferred in-body imports for genuinely-RED test stubs (collect-only stays clean)"
    - "Module-autouse cleanup fixture for global-_tick test isolation"
key-files:
  created:
    - "migrations/034_contact_resolution_confidence.sql"
    - "tests/test_checker_probe.py"
    - "tests/test_checker_cap.py"
    - "tests/test_checker_pool.py"
    - "tests/test_checker.py"
  modified:
    - "app/models/__init__.py (Contact ORM mirror)"
    - "app/config.py (5 CONTACT_CHECK_* knobs)"
    - "tests/conftest.py (mock_telethon_client fixture)"
    - "tests/test_contact_check_worker.py (4 new RED tests + autouse cleanup)"
    - ".planning/phases/14-reliable-contact-resolution/14-VALIDATION.md (Per-Task Verification Map)"
decisions:
  - "tg_resolved_by is a NEW column (resolver-provenance, D-09), kept distinct from import-provenance contacts.source"
  - "No CHECK on tg_probe_state (kept free-form for forward-compat); CHECK only on tg_confidence (NULL|high|low)"
  - "CONTACT_CHECK_* use Settings Field pattern (typed/documented) not module-level os.environ; pace defaults 2.0/3.5 match checker.py:259"
  - "Verify command corrected from `from app.config import settings` to `get_settings()` — no module-level singleton exists (Rule 3)"
  - "Module-autouse cleanup added to keep RED stubs from leaking pending contacts into the globally-scoped _tick worker tests"
metrics:
  tasks: 3
  files: 9
  commits: 4
  completed: 2026-06-26
---

# Phase 14 Plan 01: Contact-Resolution Foundation Summary

Schema + config + Wave-0 RED scaffold for reliable contact resolution: migration 034 gives `not_registered` a confidence/source (tg_confidence/tg_resolved_by/tg_probe_state), five conservative `CONTACT_CHECK_*` Settings knobs cap the resolve volume, and 12 genuinely-RED test stubs lock the contracts that Waves 2-3 turn GREEN.

## What Was Built

### Task 1 — Migration 034 + ORM mirror (RESV-06/D-09)
`migrations/034_contact_resolution_confidence.sql` adds three nullable columns to `contacts` (`tg_confidence` TEXT, `tg_resolved_by` UUID, `tg_probe_state` TEXT) using the idempotent 028 template (`ADD COLUMN IF NOT EXISTS` + `DROP CONSTRAINT IF EXISTS` / `ADD CONSTRAINT`). A CHECK guards `tg_confidence ∈ {NULL, 'high', 'low'}`; `tg_probe_state` is left free-form for forward-compat. The columns are mirrored into the `Contact` ORM model so the test-overlay's `create_all` schema matches prod. `tg_resolved_by` is resolver-provenance (which checker produced the result) — deliberately separate from the existing import-provenance `contacts.source`.

### Task 2 — CONTACT_CHECK_* env-knobs (RESV-02/D-10)
Five typed `Settings` fields with `validation_alias` + descriptions, conservative defaults under the ~45-50 empirical throttle onset: `contact_check_burst_cap=30`, `contact_check_pace_low=2.0`, `contact_check_pace_high=3.5` (matching `random.uniform(2.0,3.5)` at checker.py:259), `contact_check_cooldown_seconds=900`, `contact_check_daily_cap=400`. Documented as a SEPARATE knob set from the empirical `queue.py` send constants (CLAUDE.md guard — not unified, queue.py untouched).

### Task 3 — Wave-0 RED scaffold (RESV-01/02/03/05/06)
12 genuinely-failing test stubs (real assertions, not `pass`/`xfail`) across four files plus a new conftest fixture:
- `tests/test_checker_probe.py`: `test_two_misses_flags`, `test_single_miss_no_flag`, `test_suspect_rollback_keeps_registered` (RESV-01/D-05/D-07)
- `tests/test_checker_cap.py`: `test_burst_cap` (cap is the active per-batch budget), `test_daily_cap_durable` (survives a fresh worker — Pitfall 5)
- `tests/test_checker_pool.py`: `test_rotation_picks_eligible`, `test_rotation_n1_pauses` (RESV-03/D-04)
- `tests/test_checker.py`: `test_import_fallback_and_cleanup` (RESV-01/D-02 — importContacts fallback + DeleteContacts cleanup)
- `tests/test_contact_check_worker.py` (+4): `test_selection_skips_restricted`, `test_selection_skips_paused`, `test_mobile_first_order`, `test_confidence_written`
- `tests/conftest.py`: `mock_telethon_client` fixture (AsyncMock client dispatching on ResolvePhone/ImportContacts/DeleteContacts request types, with an ordered `.calls` log)

Deferred in-body imports keep `--collect-only` clean (769 collected, exit 0); the helpers/SQL/columns the bodies assert do not exist yet, so all 12 fail RED. The 14-VALIDATION.md Per-Task Verification Map is filled and `wave_0_complete: true`.

## Verification Results

- Task 1: `Contact` ORM exposes `tg_confidence`/`tg_resolved_by`/`tg_probe_state` — PASS (test-overlay import).
- Task 2: `get_settings()` returns all five knobs with documented defaults — PASS.
- Task 3: `--collect-only` exits 0 (769 collected); `tests/test_checker.py` collects cleanly with `mock_telethon_client`; the 12 stubs FAIL when run (genuinely RED).
- Full suite via test-overlay: **12 failed (intentional Phase-14 RED stubs), 756 passed, 1 skipped** — the 756 matches the pre-Phase-14 baseline (no regression).
- All tests run ONLY via `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` (CLAUDE.md test-overlay safety rule). The isolated `db-test` ran under a dedicated compose project (`wt_aef7`) to avoid colliding with the running prod `outreach-platform-db`; torn down with `down` (never `down -v`, never on prod).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Verify command referenced a non-existent `settings` singleton**
- **Found during:** Task 2 verification.
- **Issue:** The plan's `<automated>` verify used `from app.config import settings`, but this repo has no module-level `settings` — every call-site uses the `get_settings()` accessor.
- **Fix:** Ran the identical assertions via `get_settings()`. Acceptance criteria (config.py content) are unchanged. Documented the correction in 14-VALIDATION.md.
- **Files modified:** none (verification-only); note added to 14-VALIDATION.md.
- **Commit:** 708d672 (Task 2), e467241 (VALIDATION note).

**2. [Rule 1 - Bug] RED stubs leaked pending contacts into globally-scoped worker tests**
- **Found during:** Task 3 full-suite run.
- **Issue:** `ContactCheckWorker._tick()` resolves pending contacts across ALL workspaces, and the session-scoped test DB never rolls back committed rows (conftest `async_db_session` only rolls back, but the factories `commit`). The new selection/cap/probe/pool stubs committed pending contacts in a checker-equipped workspace that then leaked into legacy worker tests later in the same session, breaking 10 previously-green tests (e.g. `_tick` picking up leaked pending and crashing on a bare `AsyncMock`).
- **Fix:** Added a module-autouse cleanup fixture (delete `contacts_cache` + pending `contacts` post-test) to the four worker/checker test modules. Restores full-suite isolation: 756 baseline preserved.
- **Files modified:** tests/test_checker_cap.py, tests/test_checker_probe.py, tests/test_checker_pool.py, tests/test_contact_check_worker.py.
- **Commit:** c84ebe0.

**3. [Rule 3 - Blocking] Worktree compose collided with running prod containers**
- **Found during:** Task 1 verification.
- **Issue:** The base `docker-compose.yml` pins `container_name: outreach-platform-db`/`-api` and `api` `depends_on: db`; the worktree compose tried to create a second `outreach-platform-db`, conflicting with the live prod container. Also no `.env` exists in the worktree for compose `${VAR}` interpolation.
- **Fix:** Ran the test-overlay under a dedicated compose project name (`COMPOSE_PROJECT_NAME=wt_aef7`), brought up only `db-test`, and ran `api` with `--no-deps` so the prod `db` is never (re)created. Copied the gitignored prod `.env` into the worktree purely for compose interpolation (the overlay forces `DATABASE_URL=…/outreach_test`, so the conftest guard and the ephemeral tmpfs db keep prod safe). `.env` is gitignored and was never committed.
- **Files modified:** none committed (`.env` is gitignored).
- **Commit:** n/a (operational).

## Known Stubs

The 12 RED test stubs are intentional Wave-0 scaffold, not product stubs — they assert behaviors that Waves 2-3 implement and are tracked in 14-VALIDATION.md (status ❌ red, flip to ✅ as the worker/checker logic lands). No production code stubs were introduced: migration 034 columns are nullable-by-design (backfill not needed) and the `CONTACT_CHECK_*` knobs ship with working conservative defaults.

## Commits

- `b712a10` feat(14-01): migration 034 + ORM mirror for contact resolution confidence/source
- `708d672` feat(14-01): add CONTACT_CHECK_* resolve knobs to Settings
- `e467241` test(14-01): Wave-0 RED scaffold for probe/cap/pool/selection/confidence/import
- `c84ebe0` test(14-01): add autouse cleanup so RED stubs don't leak pending contacts

## Self-Check: PASSED
- migrations/034_contact_resolution_confidence.sql — FOUND
- tests/test_checker_probe.py / test_checker_cap.py / test_checker_pool.py / test_checker.py — FOUND
- Commits b712a10, 708d672, e467241, c84ebe0 — FOUND
- Migration contains `ADD COLUMN IF NOT EXISTS tg_confidence` + `DROP CONSTRAINT IF EXISTS contacts_tg_confidence_chk` — OK
- app/config.py contains `CONTACT_CHECK_BURST_CAP` (+4 more aliases) — OK
