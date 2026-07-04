---
phase: 20-account-profile-management
plan: 03
subsystem: api
tags: [telethon, fastapi, senders, profile, photo, bytea, resync, multipart, testing]

# Dependency graph
requires:
  - phase: 20-account-profile-management (plan 02)
    provides: "guardrail helper spine on senders.py (_check_profile_cooldown/_stamp_profile_change/_profile_advisory/_raise_profile_telegram_error), _sender_to_response with has_photo=bool(tg_photo), ProfileUpdateResponse; TelegramService per-op client skeleton (get_client/disconnect_client)"
  - phase: 20-account-profile-management (plan 01)
    provides: "cached-profile columns (mig 049: tg_photo BYTEA / tg_photo_mime / profile_field_changed_at), profile Pydantic schemas, RED test scaffold (test_photo/test_resync/test_photo_serve_auth)"
  - phase: 01-workspace-foundation
    provides: "workspace scoping + auth_dep + JWT test fixtures"
provides:
  - "TelegramService.set_profile_photo / delete_profile_photo / resync_profile (canonical per-op client methods) + router-facing aliases upload_profile_photo / delete_profile_photos / fetch_profile"
  - "POST /senders/{slug}/photo — multipart upload, size(413)/mime(422) validation, D-08 photo cooldown, caches Telegram's normalized avatar"
  - "DELETE /senders/{slug}/photo — removes photo + clears cache (de-escalation, stamps but no cooldown block)"
  - "GET /senders/{slug}/photo — auth-gated BYTEA serve via Response (D-11), 404 when none, workspace-scoped"
  - "POST /senders/{slug}/resync — pull live username/bio/photo into cache (D-12), no cooldown/stamp"
  - "PHOTO_TOO_SMALL / PHOTO_FORMAT_INVALID mappings in _raise_profile_telegram_error"
affects: [20-04, 20-05, account-profile-2fa, account-profile-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Photo bytes stay server-side in senders.tg_photo (BYTEA); served ONLY through an auth-gated GET endpoint via fastapi.Response(content=..., media_type=...) — never a raw blob URL, never base64 in the list (D-11)"
    - "Upload validates size/mime BEFORE the D-08 cooldown check so a bad input reports the input error (413/422), not a stale-cooldown 409"
    - "Photo cooldown is asymmetric: UPLOAD is D-08 hard-blocked (spam-risk direction); DELETE is always allowed (de-escalation) but still stamps so a rapid follow-up upload is throttled"
    - "resync is a READ-from-Telegram (fetch_profile) → no cooldown/stamp; response honours the service's authoritative has_photo even when it ships no raw bytes"

key-files:
  created: []
  modified:
    - app/services/telegram.py
    - app/routers/senders.py

key-decisions:
  - "Implemented to the authoritative RED test contract (like 20-02): the tests patch upload_profile_photo/delete_profile_photos/fetch_profile and mock a bare {'ok': True}, so canonical set_profile_photo/delete_profile_photo/resync_profile hold the per-op body (grep acceptance) with those alias names delegating; the router relies on raised exceptions for errors, NOT res.get('success')"
  - "DELETE /photo does NOT cooldown-check (plan said 'delete counts as a change → cooldown + stamp'); test_photo does upload→delete in one test and the upload's photo stamp would 409 the delete. Kept the stamp, dropped the delete-side cooldown — D-08 still guards the upload direction"
  - "Upload validation order = size/mime BEFORE _check_profile_cooldown so test_photo's 422/413 steps return the input error, not a 409 from the step-1 photo stamp (still 'cooldown before the Telegram call' per acceptance)"

patterns-established:
  - "Pattern: cached profile photo lifecycle = upload (cache Telegram normalized avatar) / delete (clear) / auth-gated serve, all reusing the 20-02 guardrail spine"
  - "Pattern: service methods raise; router maps via _raise_profile_telegram_error (now covers PHOTO_TOO_SMALL/PHOTO_FORMAT_INVALID/FLOOD_WAIT for photo ops)"

requirements-completed: [PROF-04, PROF-06, PROF-07]

# Metrics
duration: 9min
completed: 2026-07-04
---

# Phase 20 Plan 03: Photo and Resync Summary

**Profile-photo lifecycle shipped — POST/DELETE/GET /senders/{slug}/photo (multipart upload caching Telegram's normalized avatar, auth-gated BYTEA serve D-11, size/mime + D-08 cooldown guards) and POST /resync pulling live username/bio/photo into the cache (D-12); the 3 RED tests (test_photo, test_resync, test_photo_serve_auth) turned GREEN.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-07-04T09:07:42Z
- **Completed:** 2026-07-04T09:16:55Z
- **Tasks:** 2
- **Files modified:** 2 (0 created, 2 modified)

## Accomplishments
- `TelegramService` gained `set_profile_photo` (upload_file → `UploadProfilePhotoRequest` → re-download the normalized avatar, OQ3), `delete_profile_photo` (fresh `get_profile_photos('me', limit=1)` file_reference → `DeletePhotosRequest`, Pitfall 6), and `resync_profile` (`get_me`/`GetFullUserRequest`/`download_profile_photo('me', file=bytes)`), each a per-op client with `finally: disconnect_client` and errors propagating. Router-facing aliases `upload_profile_photo`/`delete_profile_photos`/`fetch_profile` delegate to the canonical impls (the names the endpoints call / the RED tests patch).
- 4 endpoints on the senders router: `POST /senders/{slug}/photo` (multipart, size→413 / mime→422 validated before any Telegram call, D-08 photo cooldown before the write, caches the normalized avatar with a raw-upload fallback, D-09 advisory), `DELETE /senders/{slug}/photo` (clears photo + cache, de-escalation), `GET /senders/{slug}/photo` (auth-gated `Response(content=sender.tg_photo, media_type=...)`, 404 when none, opaque cross-workspace 404 — D-11), and `POST /senders/{slug}/resync` (D-12, read-from-Telegram, no cooldown/stamp).
- `MAX_PHOTO_BYTES`/`ALLOWED_PHOTO_MIME` constants + `PHOTO_TOO_SMALL`/`PHOTO_FORMAT_INVALID` mappings added to `_raise_profile_telegram_error`.
- 3 target tests GREEN (`test_photo`, `test_resync`, `test_photo_serve_auth`); wave-merge regression sample GREEN — `test_account_profile.py` 8/9 (the 1 remaining RED is `test_2fa`, Wave-4/PROF-05 target, unchanged), `test_senders.py` 21/21, `test_onboarding.py` 15/15.

## Task Commits

Each task was committed atomically:

1. **Task 1: TelegramService photo + resync methods** - `337b683` (feat)
2. **Task 2: Photo endpoints (upload/delete/serve) + resync endpoint** - `bf712e8` (feat)

**Plan metadata:** (final docs commit — this SUMMARY + STATE + ROADMAP + REQUIREMENTS)

_Note: Task 1 is a `tdd="true"` task whose RED tests (`test_photo`/`test_resync`) also require the Task 2 endpoints; the tests turn GREEN at Task 2 (cross-task test dependency, same as 20-02). Verified collectively via the test-overlay after both edits landed._

## Files Created/Modified
- `app/services/telegram.py` - +6 methods on `TelegramService`: `set_profile_photo`/`delete_profile_photo`/`resync_profile` (canonical, per-op client, errors propagate) + `upload_profile_photo`/`delete_profile_photos`/`fetch_profile` aliases.
- `app/routers/senders.py` - `UploadFile`/`File`/`Response` added to the fastapi import; `MAX_PHOTO_BYTES`/`ALLOWED_PHOTO_MIME` constants; `PHOTO_TOO_SMALL`/`PHOTO_FORMAT_INVALID` in `_raise_profile_telegram_error`; 4 endpoints (POST/DELETE/GET `/photo` + POST `/resync`).

## Decisions Made
- **Implemented to the authoritative RED test contract, not the plan's literal suggested code (same reconciliation as 20-02).** The RED scaffold patches `telegram_service.upload_profile_photo` / `delete_profile_photos` / `fetch_profile` (NOT the plan's `set_profile_photo`/`delete_profile_photo`/`resync_profile`) and mocks a bare `{"ok": True}` success. So the canonical methods (named per the plan's grep acceptance) hold the per-op body and disconnect-in-finally, with those three alias names delegating to them — the endpoints call the aliases (test-patched), exactly mirroring 20-02's `set_username`/`update_username` split. The router relies on **raised** exceptions for the error path (SessionAuthError → 403, everything else → `_raise_profile_telegram_error`), never `res.get("success")`, because `{"ok": True}` has no `success` key.
- **DELETE /photo does NOT cooldown-check.** The plan said "delete counts as a change → cooldown + stamp", but `test_photo` performs a successful upload (which stamps `profile_field_changed_at["photo"]`) and then a DELETE **in the same test**; a delete-side `_check_profile_cooldown(sender, "photo")` would find the fresh <1h stamp and 409 (test expects 200). Resolution: DELETE still **stamps** (so a rapid follow-up upload is throttled) but is never itself cooldown-blocked — a coherent, defensible policy (removing a photo is de-escalation; rapid re-uploads are the anti-spam risk). D-08 remains enforced on the UPLOAD direction.
- **Upload validates size/mime BEFORE the cooldown check.** The plan snippet ran the cooldown first, but `test_photo`'s bad-mime (422) and oversized (413) steps run **after** a successful upload has already stamped the photo field; a cooldown-first order would return 409 instead of the input error. Validation-first keeps the acceptance criterion ("cooldown before the Telegram call") true while returning the correct input error.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's suggested service/router shape did not satisfy the authoritative RED tests**
- **Found during:** Task 1 + Task 2
- **Issue:** The plan named the methods `set_profile_photo`/`delete_profile_photo`/`resync_profile` and had the router check `res.get("success")`, but the RED scaffold patches `upload_profile_photo`/`delete_profile_photos`/`fetch_profile` and mocks `{"ok": True}` (no `success` key) — a `res.get("success")` guard would 400 a valid upload/delete.
- **Fix:** Canonical `set_profile_photo`/`delete_profile_photo`/`resync_profile` (per-op body, grep acceptance) + alias methods `upload_profile_photo`/`delete_profile_photos`/`fetch_profile` the endpoints call and the tests patch; service methods raise on failure; router maps errors via `_raise_profile_telegram_error` (extended with PHOTO_TOO_SMALL/PHOTO_FORMAT_INVALID) + SessionAuthError → 403, never checks `res.get("success")`.
- **Files modified:** app/services/telegram.py, app/routers/senders.py
- **Verification:** `test_photo`, `test_resync` GREEN.
- **Committed in:** `337b683` (Task 1), `bf712e8` (Task 2)

**2. [Rule 1 - Bug] Plan's DELETE cooldown + upload-before-validation ordering contradicts the RED `test_photo` sequence**
- **Found during:** Task 2
- **Issue:** `test_photo` does upload (stamps `photo`) → bad-mime → oversized → DELETE, all on one sender in one test against the shared test DB (each request commits). The plan's "DELETE checks cooldown" would 409 the delete (fresh <1h photo stamp), and the plan's "cooldown before validation" would 409 the bad-mime/oversized steps instead of returning 422/413.
- **Fix:** DELETE does not cooldown-check (still stamps); UPLOAD validates size/mime before the cooldown check. D-08 is preserved for the upload direction (the anti-spam-relevant one).
- **Files modified:** app/routers/senders.py
- **Verification:** `test_photo` all four sub-assertions GREEN (200 upload, 422 gif, 413 oversized, 200 delete).
- **Committed in:** `bf712e8` (Task 2)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both were necessary to satisfy the authoritative RED test contract (the plan's `<action>` snippets were non-binding illustrations, as 20-02 already flagged). No scope creep — the endpoints, D-08/D-09/D-11/D-12 semantics, and requirements (PROF-04/06/07) are exactly as scoped.

## Issues Encountered
- **Parallel agent in the repo (quick-tasks 260704-bty/buc/buq — queue dispatcher + campaigns router + router deletions).** Followed the parallel-agent commit rule: staged only my two files per commit (`git add app/services/telegram.py`, then `git add app/routers/senders.py`), never `git add -A`; left the other agent's untracked `.planning/quick/` dirs and `docs/db-schema-polina_gocrazy.md` untouched.
- `test_2fa` is RED in the wave-merge sample — this is the Wave-4 (PROF-05, Plan 20-04) 2FA target, documented as still-RED by 20-02; it is out of scope for this plan and was RED before these changes.

## Known Stubs
None introduced. Photo bytes are cached (`senders.tg_photo`) and served through the auth-gated endpoint; `has_photo` reflects the real cached/service value. The upload's `res.get("photo") or raw` fallback caches the raw upload only when the service returns no normalized avatar (test-mock path); in prod `set_profile_photo` always returns the normalized bytes.

## User Setup Required
None - no external service configuration required. No new migration (reuses mig 049 columns from 20-01). Not yet deployed to prod (api + listener rebuild pending).

## Next Phase Readiness
- Plan 20-04 (2FA + recovery email) is next: reuses the same `telegram.py`/`senders.py` spine; its RED target `test_2fa` (edit_2fa / set_recovery_email endpoints) is the remaining RED in `test_account_profile.py`.
- Plan 20-05 (frontend) can now wire the account avatar to `GET /senders/{slug}/photo` (auth-gated), the upload/delete to POST/DELETE `/photo`, and a "refresh from Telegram" action to POST `/resync`.
- No blockers. Concurrent parallel-agent work is isolated and does not touch Phase-20 files.

---
*Phase: 20-account-profile-management*
*Completed: 2026-07-04*

## Self-Check: PASSED
- Created/modified files present: app/services/telegram.py, app/routers/senders.py, 20-03-SUMMARY.md.
- All task commits present: `337b683` (Task 1), `bf712e8` (Task 2).
