---
phase: 15-account-warmup-via-inter-account-ai-chat
plan: 04
subsystem: warmup-api
tags: [warmup, fastapi, auth, multitenancy, openapi, restriction, tdd]

# Dependency graph
requires:
  - phase: 15 (plan 01)
    provides: warmup_settings table (mig 038) + WARM-05 RED router tests
  - phase: 15 (plan 03)
    provides: enabled-gated pool + content resolver + WARMUP_TOPICS/WARMUP_SYSTEM_PROMPT defaults
  - phase: 12-workspace (mig 012)
    provides: workspace_id on warmup_pool/sessions/messages + auth_dep/AuthCtx
  - phase: 10-account-health
    provides: senders.restriction_status / restricted_until (D-11 inputs)
provides:
  - workspace-scoped /api/v1/warmup router (8 endpoints on auth_dep) — mounted in app/main.py
  - GET/PUT /api/v1/warmup/settings — master toggle (D-06) + per-workspace content (D-10) with resolved defaults
  - D-11 enriched /pool — restriction_status / restricted_until / derived warmup_reason (no new error column)
  - regenerated lovable-handoff openapi.json + types/api.ts
affects: [15-05-frontend-tab (sibling repo, manual UAT)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "All warmup endpoints scoped by WHERE workspace_id = :wid from AuthCtx (D-05, Phase 3/4/5 pattern)"
    - "Cross-workspace mutation guard via _assert_workspace_owns_sender (mirror senders.py _validate_workspace_owns_*)"
    - "D-11 warmup_reason DERIVED from restriction_status/restricted_until in the response (no schema/error-column change)"
    - "Settings upsert via INSERT ... ON CONFLICT (workspace_id) DO UPDATE; GET resolves empty topics/NULL prompt to code defaults"

key-files:
  created:
    - .planning/phases/15-account-warmup-via-inter-account-ai-chat/15-04-SUMMARY.md
  modified:
    - app/routers/warmup.py
    - app/main.py
    - tests/test_warmup_router.py
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts

key-decisions:
  - "warmup_reason text computed in /pool from restriction-fields only (D-11 locked: no error column, no _send_via_telethon change)"
  - "PUT /settings missing fields reset to defaults (enabled=false, topics=[], prompt=NULL) — full-object PUT semantics; GET resolves emptiness to code defaults"
  - "Router mounted in app/main.py — the legacy router was unmounted with a broken app.routers.auth import (closed as part of the D-05 rewrite)"

requirements-completed: [WARM-05, WARM-06, WARM-07, WARM-08, WARM-09, WARM-10, WARM-11]

# Metrics
duration: ~18min
completed: 2026-06-29
---

# Phase 15 Plan 04: Warmup API — Workspace-Scoping + Settings + Enriched Status Summary

**All 8 `/api/v1/warmup` endpoints rewritten from legacy `verify_api_key` onto `auth_dep`/`AuthCtx` workspace-scoping, the latent dropped-column `senders.is_active` bug removed, per-account status enriched with restriction reason (D-11, derived — no new column), and master-toggle + per-workspace content `GET/PUT /settings` added (D-06/D-10). Router mounted in `app/main.py`; openapi handoff regenerated. WARM-05 router tests + new settings tests GREEN; full suite green except the 3 plan-15-02 isolation tests.**

## Performance

- **Duration:** ~18 min
- **Tasks:** 3 (1 TDD turn-green + 1 feature + 1 handoff regen)
- **Files modified:** 5 (1 created, 4 modified)

## Accomplishments

- **Task 1 (D-05 + is_active fix + D-11):** every endpoint now takes `ctx: AuthCtx = Depends(auth_dep)` and binds `{"wid": str(ctx.workspace_id)}`; every query carries `WHERE ... workspace_id = :wid` (senders/pool/sessions/messages each filter on their own `workspace_id`). POST/DELETE/toggle gate on `_assert_workspace_owns_sender` → 404 cross-workspace. GET `/pool` dropped `s.is_active` (column removed in mig 013) while keeping `wp.is_active AS warmup_active`; added `s.restriction_status`, `s.restricted_until`, and a derived `warmup_reason` (frozen / spam_limited / other → RU human text incl. restricted_until; else null) per the locked D-11 decision — no `_send_via_telethon` change, no new error column. `sent_today` subquery also workspace-scoped.
- **Task 2 (D-06/D-10):** `GET /settings` returns `{enabled, topics, system_prompt, language, tone}` with resolved defaults (empty topics → 24 `WARMUP_TOPICS`, NULL prompt → `WARMUP_SYSTEM_PROMPT`); no row → defaults with `enabled=false` (explicit opt-in). `PUT /settings` (pydantic `WarmupSettingsUpdate`) upserts via `INSERT ... ON CONFLICT (workspace_id) DO UPDATE`, returns `{status: "saved", settings: <resolved>}`. Added `test_settings_get_resolves_defaults` + `test_settings_put_persists_master_toggle` (incl. idempotent second PUT).
- **Task 3:** rebuilt the api container and regenerated `lovable-handoff/openapi.json` + `types/api.ts` via `scripts/export-handoff.sh` (not hand-edited). All 8 warmup paths incl. `/warmup/settings` present; `/pool` doc carries restriction_status/warmup_reason. UI-SPEC drift check OK (39/39).
- Mounted the warmup router in `app/main.py` (was unmounted with a broken `app.routers.auth` import — fixed by the auth_dep rewrite, no `app.routers.auth` reference remains).

## OpenAPI regeneration command

```bash
docker compose up -d --build api
bash scripts/export-handoff.sh
```

(`export-handoff.sh` scrapes `/openapi.json` from the running api container, validates the project title, regenerates `types/api.ts` via `openapi-typescript@7`, and runs the UI-SPEC drift check.)

## Task Commits

1. **Task 1: workspace-scope router + fix is_active + D-11 status (+ mount)** — `c0e8ae4` (feat)
2. **Task 2: cover /settings master toggle + content defaults** — `692675e` (test)
3. **Task 3: regenerate openapi handoff** — `e47d49d` (chore)

(The `/settings` GET/PUT endpoint code lives in `app/routers/warmup.py` and was committed in Task 1's file write; Task 2 adds the tests proving it.)

## Test Results

- `tests/test_warmup_router.py`: **4 passed** (2 WARM-05 scoping/shape + 2 new settings) via test-overlay.
- Full suite: **805 passed, 1 skipped, 3 failed** — the 3 failures are exactly `tests/test_warmup_isolation.py` (plan 15-02, runs after this plan, documented as deferred-RED). No regression (passing count 801 → 805).

## Deviations from Plan

None — plan executed as written. The `/settings` endpoint code was authored in the Task-1 file write (single-file router) and validated by Task-2 tests; commits are split per task as required.

## Known Stubs

None. All endpoints are production-real, workspace-scoped, and backed by live queries. The `warmup_reason` derivation and settings default-resolution are intended behaviour (D-11/D-10), not stubs.

## Manual UAT note

The warmup frontend tab lives in the sibling repo `aimly-tg-outreach` and is generated/built + human-verified separately per the 15-VALIDATION manual-only checklist. This plan did NOT touch the sibling repo.

## Self-Check: PASSED

- `app/routers/warmup.py` exists; contains `auth_dep`, `ON CONFLICT (workspace_id)`, `restriction_status`, `warmup_reason`; NO `verify_api_key`, NO `s.is_active`.
- Router mounted in `app/main.py` (`warmup.router`).
- `lovable-handoff/openapi.json` contains `/warmup/settings` (grep -c == 1) and the 8 warmup paths.
- Commits `c0e8ae4`, `692675e`, `e47d49d` exist in git history.

---
*Phase: 15-account-warmup-via-inter-account-ai-chat*
*Completed: 2026-06-29*
