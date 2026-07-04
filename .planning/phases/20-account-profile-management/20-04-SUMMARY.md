---
phase: 20-account-profile-management
plan: 04
subsystem: api
tags: [telethon, fastapi, senders, 2fa, edit_2fa, recovery-email, security, testing]

# Dependency graph
requires:
  - phase: 20-account-profile-management (plan 03)
    provides: "guardrail helper spine on senders.py (_raise_profile_telegram_error, _load_sender_by_slug, SessionAuthError→403 shape), TelegramService per-op client skeleton (get_client/disconnect_client) + alias-method pattern (canonical + router-facing alias)"
  - phase: 20-account-profile-management (plan 01)
    provides: "Phase-20 Pydantic 2FA schemas (TwoFAPasswordUpdate/RecoveryEmailStart/RecoveryEmailConfirm), RED test scaffold (test_2fa)"
  - phase: 02-tg-accounts-contacts
    provides: "senders table + Sender ORM + onboarding verify-2fa path (PasswordHashInvalidError→PASSWORD_INVALID precedent)"
  - phase: 01-workspace-foundation
    provides: "workspace scoping + auth_dep + JWT test fixtures"
provides:
  - "TelegramService.change_2fa_password / start_recovery_email / confirm_recovery_email (canonical per-op client) + router-facing aliases edit_2fa / set_recovery_email"
  - "POST /senders/{slug}/2fa — set (no current pw) or change (with current pw, D-04) 2FA password via one stateless edit_2fa; wrong pw → 400 PASSWORD_INVALID; password NEVER persisted (D-03)"
  - "POST /senders/{slug}/2fa/recovery-email — step 1, raw two-request flow pivoting on EmailUnconfirmedError → 200 {code: EMAIL_CONFIRMATION_SENT, code_length}"
  - "POST /senders/{slug}/2fa/recovery-email/confirm — step 2, ConfirmPasswordEmailRequest → 400 EMAIL_CODE_INVALID on bad/expired code"
  - "_status_for_profile_error (TOO_FRESH→409, FLOOD_WAIT→429, else 400) now the single source of truth for _raise_profile_telegram_error's HTTP status, extended with the 2FA taxonomy"
affects: [20-05, account-profile-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Password set/change goes through the high-level client.edit_2fa in ONE stateless request (no email kwarg → no email_code_callback → completes synchronously, Pitfall 2)"
    - "Recovery-email change is a TWO-request raw flow: GetPasswordRequest + compute_check + UpdatePasswordSettingsRequest → pivot on EmailUnconfirmedError → later ConfirmPasswordEmailRequest on a FRESH per-op client (pending-email state lives account-side)"
    - "2FA password is a transient request field only (D-03): no DB column written, no db.commit, never logged"
    - "Canonical method (grep acceptance, holds per-op body) + router-facing alias (test-patched name) split — identical to 20-02 set_username/update_username and 20-03 set_profile_photo/upload_profile_photo"
    - "_raise_profile_telegram_error derives HTTP status from _status_for_profile_error(code) instead of a hardcoded per-row status — single source of truth"

key-files:
  created: []
  modified:
    - app/services/telegram.py
    - app/routers/senders.py

key-decisions:
  - "Implemented to the authoritative RED test contract over the plan's suggested code (same reconciliation as 20-02/20-03): router calls telegram_service.edit_2fa / set_recovery_email (the test-patched names) which delegate to canonical change_2fa_password / start_recovery_email; router is raise-based (mocks return bare {ok:True}/{code_length:6} with no success key), never res.get('success')"
  - "_raise_profile_telegram_error refactored to derive status via _status_for_profile_error(code) (equivalent for every existing code — all 400 except FLOOD_WAIT 429 — and adds TOO_FRESH→409); the plan's _status_for_profile_error thus becomes load-bearing rather than dead code"
  - "confirm endpoint calls canonical confirm_recovery_email directly (test does not patch it); TOO_FRESH + FLOOD_WAIT both carry retry_after when Telethon exposes e.seconds"

patterns-established:
  - "Pattern: security-sensitive transient fields (2FA password) flow through the request → Telethon only, with an explicit no-DB-write invariant asserted by the plan's grep acceptance"

requirements-completed: [PROF-05]

# Metrics
duration: ~22min
completed: 2026-07-04
---

# Phase 20 Plan 04: 2FA and Recovery Email Summary

**Section-B security path shipped: POST /senders/{slug}/2fa (set/change 2FA password via one stateless edit_2fa, wrong password → 400 PASSWORD_INVALID, password never persisted D-03) + the two-request recovery-email flow (POST /2fa/recovery-email → EMAIL_CONFIRMATION_SENT+code_length, POST /2fa/recovery-email/confirm → ConfirmPasswordEmailRequest) — the last RED test (test_2fa) turned GREEN, closing PROF-05.**

## Performance

- **Duration:** ~22 min
- **Started:** 2026-07-04T09:22:00Z
- **Completed:** 2026-07-04T09:44:00Z
- **Tasks:** 2
- **Files modified:** 2 (0 created, 2 modified)

## Accomplishments
- `TelegramService` gained `change_2fa_password` (calls `client.edit_2fa` with NO `email=` kwarg → no `email_code_callback` → completes synchronously, RESEARCH §Pitfall 2), `start_recovery_email` (raw two-request flow: `GetPasswordRequest` + `compute_check` + `UpdatePasswordSettingsRequest`, pivots on `EmailUnconfirmedError` → returns `code_length`), and `confirm_recovery_email` (`ConfirmPasswordEmailRequest` on a fresh per-op client) — each a per-op client with `finally: disconnect_client` and errors propagating. Router-facing aliases `edit_2fa` / `set_recovery_email` delegate to the canonical impls (the names the endpoints call / the RED test patches).
- Three endpoints on the senders router: `POST /senders/{slug}/2fa` (set/change password, D-04), `POST /senders/{slug}/2fa/recovery-email` (step 1 → `{code: EMAIL_CONFIRMATION_SENT, code_length}`), `POST /senders/{slug}/2fa/recovery-email/confirm` (step 2). None writes any DB column — the 2FA password is transient (D-03, verified by grep: zero `sender.` assignments in the three bodies).
- `_status_for_profile_error` helper added (TOO_FRESH→409, FLOOD_WAIT→429, else 400) and wired as the single source of truth inside `_raise_profile_telegram_error`, which was refactored to derive its HTTP status from it and extended with the 2FA taxonomy (PASSWORD_INVALID / EMAIL_INVALID / EMAIL_CODE_INVALID / TOO_FRESH).
- `test_2fa` GREEN (was the last RED in `tests/test_account_profile.py`). Regression sample GREEN in isolation: `test_account_profile.py` + `test_senders.py` + `test_onboarding.py` = **45 passed**.

## Task Commits

Each task was committed atomically:

1. **Task 1: TelegramService 2FA methods (password + recovery-email two-step)** - `93528c3` (feat)
2. **Task 2: 2FA endpoints (password + recovery-email start/confirm)** - `c882201` (feat)

**Plan metadata:** (final docs commit — this SUMMARY + STATE + ROADMAP + REQUIREMENTS + deferred-items)

_Note: Task 1 is a `tdd="true"` task whose RED test (`test_2fa`) also requires the Task 2 endpoints; the test turns GREEN at Task 2 (cross-task test dependency, same as 20-02/20-03). RED baseline confirmed first (404 — endpoint absent), then verified GREEN after both edits landed via the test-overlay._

## Files Created/Modified
- `app/services/telegram.py` - +5 methods on `TelegramService`: `change_2fa_password` / `start_recovery_email` / `confirm_recovery_email` (canonical, per-op client, errors propagate, password never logged) + `edit_2fa` / `set_recovery_email` router-facing aliases.
- `app/routers/senders.py` - `TwoFAPasswordUpdate` / `RecoveryEmailStart` / `RecoveryEmailConfirm` schema imports; `_status_for_profile_error` helper; `_raise_profile_telegram_error` refactored (status derived from `_status_for_profile_error` + 2FA taxonomy needles); 3 endpoints (POST `/2fa`, POST `/2fa/recovery-email`, POST `/2fa/recovery-email/confirm`).

## Decisions Made
- **Implemented to the authoritative RED test contract, not the plan's literal suggested code (same reconciliation as 20-02/20-03).** The RED `test_2fa` patches `telegram_service.edit_2fa` and `telegram_service.set_recovery_email` (NOT the plan's `change_2fa_password` / `start_recovery_email`) and mocks bare `{"ok": True}` / `{"code_length": 6}` success dicts (no `success` key). So the canonical methods (named per the plan's grep acceptance) hold the per-op body, and those two alias names delegate to them; the endpoints call the aliases. The router relies on **raised** exceptions for the error path (SessionAuthError → 403, everything else → `_raise_profile_telegram_error`), never `res.get("success")`, because a `res.get("success")` guard would 400 the mocked `{"ok": True}` valid case.
- **`_status_for_profile_error` made load-bearing, not dead code.** The plan added it purely for grep acceptance while its endpoints used `res.get("success")` + the mapper. Since the router is raise-based, I wired `_status_for_profile_error(code)` as the single source of truth for the HTTP status inside `_raise_profile_telegram_error` (equivalent for every pre-existing code: all 400 except FLOOD_WAIT 429; adds TOO_FRESH → 409). This satisfies the grep acceptance AND keeps the helper genuinely used.
- **D-03 hard security invariant verified explicitly.** The three endpoint bodies contain zero `sender.` assignments and no `db.commit()` — the 2FA password is transient (request → Telethon only), never persisted, never logged (the canonical methods never log the password).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's suggested service/router shape did not satisfy the authoritative RED test**
- **Found during:** Task 1 + Task 2
- **Issue:** The plan named the methods `change_2fa_password` / `start_recovery_email` and had the router call them and check `res.get("success")`, but the RED `test_2fa` patches `telegram_service.edit_2fa` / `telegram_service.set_recovery_email` and mocks bare `{"ok": True}` / `{"code_length": 6}` (no `success` key). A `res.get("success")` guard would 400 the valid password case; the error path mock RAISES `Exception("PASSWORD_HASH_INVALID")` expecting 400 PASSWORD_INVALID.
- **Fix:** Canonical `change_2fa_password` / `start_recovery_email` / `confirm_recovery_email` (per-op body, grep acceptance) + `edit_2fa` / `set_recovery_email` aliases the endpoints call and the test patches; service methods raise; router maps errors via `_raise_profile_telegram_error` (extended with the 2FA taxonomy) + SessionAuthError → 403, never checks `res.get("success")`. Recovery-email response emits `{"code": "EMAIL_CONFIRMATION_SENT", "code_length": res.get("code_length")}`.
- **Files modified:** app/services/telegram.py, app/routers/senders.py
- **Verification:** `test_2fa` GREEN; `_raise_profile_telegram_error` refactor preserves all existing profile-error statuses (test_senders / test_account_profile GREEN in isolation).
- **Committed in:** `93528c3` (Task 1), `c882201` (Task 2)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary to satisfy the authoritative RED test contract (the plan's `<action>` snippets were non-binding illustrations, as 20-02/20-03 already flagged). No scope creep — the endpoints, the two-request recovery-email pivot, the error taxonomy, D-03/D-04 semantics, and PROF-05 are exactly as scoped.

## Issues Encountered
- **Full-suite instability from shared-DB test-ordering pollution (out of scope, NOT caused by 20-04).** The full `pytest` run reports ~91 failed + ~86 errors, with the cascade rooted in `Unhandled exception on POST /api/v1/auth/me` (workspace auto-create failing deep in the 900+ test session) poisoning every downstream test that bootstraps a workspace — spread across ~40 files including ones untouched since Phase 5 (`test_phase5_migration_017.py`, `test_workspace_router.py`). Proven to be a test-isolation issue, not a code regression: `test_2fa` GREEN; account-profile + senders + onboarding = 45 passed in isolation; the flagged files (`test_sender_lock`/`test_send`/`test_send_campaign`/`test_rotation_campaign`) = 28 passed as a group; `test_senders.py` fails 19 in the full run but passes all 21 in isolation. 20-04's endpoints write nothing to the DB (D-03), so they cannot pollute the shared `outreach_test` DB. Likely exacerbated by three parallel-agent batch merges (`quick-260704-buc`/`buq`/`bty`) landing in the shared test DB during the execution window. Logged to `.planning/phases/20-account-profile-management/deferred-items.md` for the verifier / parallel-tasks agent. Do NOT attribute to plan 20-04.
- **Parallel agent in the repo.** Followed the parallel-agent commit rule: staged only my two files per commit (`git add app/services/telegram.py`, then `git add app/routers/senders.py`), never `git add -A`; left the other agent's `.planning/quick/` dirs, `docs/db-schema-polina_gocrazy.md`, and its `.planning/STATE.md` edits untouched. A transient `.git/index.lock` from the parallel agent's commit cleared on its own; I did NOT remove it manually. The parallel agent's Batch G merge landed on top of my two commits (both preserved in linear history).

## Known Stubs
None introduced. The recovery-email two-request live confirm against a real Telegram inbox is (by plan design, RESEARCH §Sources tertiary + 20-VALIDATION §Manual-Only) the phase's MEDIUM-LOW-confidence assumption; automated tests mock Telethon, and the live end-to-end confirm is a manual verification carried by Plan 20-05's human-verify gate. This is intentional and documented, not a stub in 20-04's code.

## User Setup Required
None - no external service configuration required. No new migration in this plan (reuses the mig 049 columns from 20-01; 2FA writes nothing to the DB). Not yet deployed to prod (api + listener rebuild pending).

## Next Phase Readiness
- Plan 20-05 (frontend + handoff) can now wire the Section-B security UI: 2FA password set/change → POST `/2fa`, recovery-email → the two-step POST `/2fa/recovery-email` (surface `code_length` in the code-entry prompt) then POST `/2fa/recovery-email/confirm`. The C4 two-step interaction contract + copywriting are in 20-UI-SPEC.
- Plan 20-05's human-verify gate carries the live recovery-email confirm against a real test account with 2FA set (20-VALIDATION §Manual-Only).
- **Blocker for the phase gate (not for 20-04):** the full-suite shared-DB pollution (see Issues Encountered + deferred-items.md) should be re-checked once the parallel quick-tasks settle. PROF-05's automated coverage is GREEN; all four RED account-profile tests (photo/2fa/resync/photo_serve_auth) are now closed.

---
*Phase: 20-account-profile-management*
*Completed: 2026-07-04*

## Self-Check: PASSED
- Created/modified files present: 20-04-SUMMARY.md, deferred-items.md, app/services/telegram.py, app/routers/senders.py.
- All task commits present: `93528c3` (Task 1), `c882201` (Task 2).
