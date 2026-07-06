---
phase: quick-260706-fcq
plan: 01
subsystem: api
tags: [fastapi, pydantic, pool-management, checker, sender-restriction-events, openapi]

# Dependency graph
requires:
  - phase: 08-pool-management-and-even-distribution
    provides: "attach_sender / detach_sender endpoints, _check_sender_lock, _campaign_to_response"
  - phase: 10
    provides: "sender_restriction_events append-only log + restriction_status per sender"
  - phase: 17
    provides: "restriction-gated checker selection (why a burned checker is dangerous)"
provides:
  - "attach_sender pre-flight: RECENT_RESTRICTION advisory when the sender hit a non-cleared restriction event in the last 7 days (warning, not block)"
  - "attach_sender checker force-guard: role='checker' → 409 CHECKER_ROLE_CONFLICT unless force=true; CHECKER_FORCE_ATTACHED advisory on override"
  - "PATCH /senders/{slug} reverse guard: flipping an in-running-campaign sender to role='checker' → 409 CHECKER_ROLE_CONFLICT unless force=true"
  - "SenderAttachWarning schema + attach_warnings[] on CampaignResponse (default [], backward-compatible)"
  - "force field on CampaignSenderAttachRequest and SenderUpdate"
  - "openapi handoff + error-codes.md documenting the new contract for the Lovable frontend"
affects: [pool-management, checker-pool-health, lovable-frontend, campaign-launch-safety]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Advisory (non-blocking) warnings ride in a 200 response via an optional default-[] list field — backward-compatible for every other endpoint that shares the response model"
    - "Role-transition guard with explicit force=true escape hatch + distinct error code, reusing an existing running-campaign SELECT shape without reusing its raise"
    - "Offline openapi.json regeneration via app.openapi() in the ephemeral test container (no prod boot) — worktree-safe alternative to export-handoff.sh's docker compose up"

key-files:
  created:
    - "tests/test_pool_preflight.py — 9 tests (PFH-01/02 attach, PFH-03 reverse guard)"
  modified:
    - "app/schemas/__init__.py — SenderAttachWarning, attach_warnings[], force fields"
    - "app/routers/campaigns.py — _recent_restriction_warnings helper + attach_sender pre-flight"
    - "app/routers/senders.py — update_sender reverse-direction checker guard"
    - "lovable-handoff/openapi.json — regenerated (additive)"
    - "lovable-handoff/error-codes.md — CHECKER_ROLE_CONFLICT + advisory-warning section"

key-decisions:
  - "attach_warnings is an OPTIONAL default-[] field on the shared CampaignResponse so get/patch/start/pause/detach stay byte-identical (backward-compatible)"
  - "Checker role captured into a boolean BEFORE db.commit() to avoid a post-commit lazy-load (MissingGreenlet) when reading sender.role for the CHECKER_FORCE_ATTACHED warning"
  - "'cleared' recovery events excluded from RECENT_RESTRICTION via event_type <> 'cleared' (a clear is good news, not a warning)"
  - "Reverse guard uses an inline EXISTS with a distinct CHECKER_ROLE_CONFLICT code + force bypass rather than reusing _check_sender_not_in_running_campaign (that helper raises SENDER_USED_BY_RUNNING_CAMPAIGN and has no escape hatch)"
  - "Idempotency short-circuit (already-attached) left as-is → returns attach_warnings defaulting to []"

patterns-established:
  - "Green-corridor pre-flight: read sender_restriction_events (7-day window) and surface an advisory, never block the attach"
  - "Checker/sender role separation is enforced symmetrically on both mutation paths (attach + role PATCH) with the same code + force semantics"

requirements-completed: [PFH-01, PFH-02, PFH-03]

# Metrics
duration: ~35min
completed: 2026-07-06
---

# Quick Task 260706-fcq: Pre-flight Health Check on Sender Attach Summary

**attach_sender now surfaces a RECENT_RESTRICTION advisory (7-day green-corridor) and blocks silently burning a checker account (409 CHECKER_ROLE_CONFLICT unless force=true), with the reverse role→checker flip on PATCH /senders guarded the same way.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 3
- **Files modified:** 6 (1 created, 5 modified)
- **Tests:** 9 new (test_pool_preflight.py), all GREEN; pool_endpoints + pool_health regression 10/10 GREEN

## Accomplishments
- Pre-flight restriction warning on attach: a sender with a non-'cleared' restriction event in the last 7 days attaches successfully (200) but carries a `RECENT_RESTRICTION` advisory in `attach_warnings[]` — the "зелёный коридор" pre-flight the incident lacked.
- Checker force-guard on attach: attaching a `role='checker'` account returns `409 CHECKER_ROLE_CONFLICT` unless `force=true`; with force it attaches and carries a `CHECKER_FORCE_ATTACHED` advisory. This directly closes the gap that let a campaign launch on the only working checker (ca-account-1) and PEER_FLOOD it out of both sending and contact-checking.
- Symmetric reverse guard: flipping an in-running-campaign sender to `role='checker'` via `PATCH /senders/{slug}` is blocked the same way (409 + force bypass); idle senders flip freely.
- Frontend contract wired: openapi.json regenerated (attach_warnings + SenderAttachWarning + force fields) and error-codes.md documents the block + the two advisory warning codes for the amber banner.

## Task Commits

Each task was committed atomically:

1. **Task 1: Schemas + attach-sender pre-flight (restriction warning + checker force-guard)** - `4253505` (feat)
2. **Task 2: Reverse-direction role→checker force-guard on PATCH /senders** - `659f222` (feat)
3. **Task 3: Regenerate openapi handoff + document CHECKER_ROLE_CONFLICT** - `04c9897` (chore)

_Note: implementation and tests were committed together per task (both tests and code verified GREEN before each commit)._

## Files Created/Modified
- `tests/test_pool_preflight.py` - 9 tests: 6 attach (PFH-01/02) + 3 reverse-guard (PFH-03)
- `app/schemas/__init__.py` - `SenderAttachWarning` model, `attach_warnings[]` on `CampaignResponse`, `force` on `CampaignSenderAttachRequest` and `SenderUpdate`
- `app/routers/campaigns.py` - `_recent_restriction_warnings` helper + attach_sender pre-flight (checker 409 + RECENT_RESTRICTION/CHECKER_FORCE_ATTACHED advisories)
- `app/routers/senders.py` - `update_sender` reverse-direction checker guard
- `lovable-handoff/openapi.json` - regenerated offline via `app.openapi()` (additive: 78 lines added, 1 replaced docstring)
- `lovable-handoff/error-codes.md` - `CHECKER_ROLE_CONFLICT` row + non-blocking advisory-warnings section

## Decisions Made
- **attach_warnings is optional/default-[]** on the shared `CampaignResponse` → every other endpoint (get/patch/start/pause/detach) is unchanged and backward-compatible.
- **Role captured to a boolean before commit** to avoid an async lazy-load after `db.commit()` expires the ORM object.
- **'cleared' events excluded** from the warning (recovery, not restriction).
- **Inline EXISTS + distinct code** for the reverse guard rather than reusing `_check_sender_not_in_running_campaign` (different code, no force bypass).

## Deviations from Plan

None - plan executed exactly as written. No new migration (highest remains 049), empirical rate-limit/queue intervals in queue.py untouched.

## Issues Encountered
- **Worktree compose isolation:** the worktree is a distinct docker-compose project, so `docker compose ... run --rm api pytest` tried to create a fresh `db` container with the hardcoded `container_name: outreach-platform-db`, conflicting with the running prod DB. Resolved by (a) starting only the ephemeral `db-test` (`up -d db-test`), (b) running pytest with `--no-deps` so the prod `db` service is never touched, and (c) passing `--env-file /root/apps/aimly/tg-outreach/.env` for `${VAR}` interpolation (the worktree has no `.env`). The test overlay still overrides `DATABASE_URL` to the ephemeral `outreach_test`, so the conftest guard was satisfied and prod was never at risk. Prod containers confirmed still healthy after the run.
- **export-handoff.sh not runnable in the worktree** (it does `docker compose up -d db api`, same conflict). Used the established offline `app.openapi()` export inside the ephemeral api container instead (per prior-phase precedent in STATE.md), then pretty-printed with `jq` exactly as the script's line 36 does. Diff against the committed spec confirmed additive-only. `types/api.ts` regeneration (npx openapi-typescript) was skipped — it is a sibling-repo concern and not in this plan's file list.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Backend contract is ready for the Lovable frontend to render the amber "зелёный коридор" banner (RECENT_RESTRICTION / CHECKER_FORCE_ATTACHED) and the `force: true` confirmation dialog on the CHECKER_ROLE_CONFLICT 409. Building that React UI lives in the sibling `aimly-tg-outreach` repo and is the explicit follow-up.
- **Documented follow-up:** live inline @SpamBot pre-flight on attach (deliberately out of scope here — FloodWait/latency risk in the synchronous path). `GET /senders/{slug}/spambot-check` remains the manual on-demand verification.

---
*Phase: quick-260706-fcq*
*Completed: 2026-07-06*

## Self-Check: PASSED

All created/modified files present on disk; all three task commits (`4253505`, `659f222`, `04c9897`) present in git history. SUMMARY.md is untracked (docs commit handled by the orchestrator per constraints).
