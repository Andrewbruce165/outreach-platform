---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 02
subsystem: telethon
tags: [telethon, fingerprint, 2fa, encryption, account-import, profile-management]

# Dependency graph
requires:
  - phase: 21-01-schema-foundation-and-test-scaffold
    provides: "senders.client_fingerprint (JSONB) + senders.twofa_password_enc (TEXT) columns + ORM mirror + RED test scaffold (test_fingerprint_override_and_strict_fallback etc.)"
  - phase: 20-account-profile-management
    provides: "TelegramService profile/2FA methods (update_profile/set_username/set_profile_photo/resync_profile/change_2fa_password/recovery-email) + senders.py profile/2FA endpoints + update_sender_2fa"
provides:
  - "make_telegram_client(fingerprint=None) + get_client(fingerprint=None) — additive per-account device/locale override with STRICT NULL fallback (13 phone senders byte-identical to today)"
  - "client_fingerprint threaded through EVERY automated hot path (queue ORM, listener 24/7 reconnect, warmup, checker resolve + control-probe + recovery via contact_check_worker)"
  - "fingerprint param on all 10 canonical + 6 alias TelegramService profile/2FA methods; senders.py passes sender.client_fingerprint at all 10 profile/2FA call sites"
  - "IMPT-10: update_sender_2fa auto-fills the stored decrypted twofa_password_enc as current_password for imported accounts (D-06), connects under the account fingerprint, never returns plaintext (D-07)"
affects: [21-03-preview-unzip-pair-stage, 21-04-per-account-import-routine, 21-05-async-job-confirm-worker-status, 21-06-frontend-and-handoff]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Additive keyword-only fingerprint override with strict NULL fallback (fp = {**_CLIENT_FINGERPRINT, **(fingerprint or {})}) — non-NULL overrides device/version/locale, lang_pack still forced 'tdesktop', api_id/api_hash stay global (D-02/D-03/D-04)"
    - "Two-level LATERAL SELECT must carry the new column on BOTH inner subquery AND outer projection (contact_check_worker._tick) so first.client_fingerprint resolves on the outer Row"
    - "Dict-vs-ORM client-build fork: ORM rows read sender.client_fingerprint directly; raw-SQL dict rows add the column to the SELECT and read via .get('client_fingerprint')"

key-files:
  created: []
  modified:
    - app/services/telegram.py
    - app/services/queue.py
    - app/services/listener.py
    - app/services/warmup.py
    - app/services/checker.py
    - app/services/contact_check_worker.py
    - app/routers/senders.py
    - tests/test_account_import.py

key-decisions:
  - "Strict NULL fallback (D-02): make_telegram_client(fingerprint=None) is byte-identical to today; the 13 phone-onboarded senders (NULL fingerprint) resolve to the global _CLIENT_FINGERPRINT — zero regression, proven by test_null_fingerprint_matches_global on the built-client kwargs"
  - "lang_pack='tdesktop' forced UNCONDITIONALLY after construction (D-04) — it is the field that terminates sessions when empty; fingerprint never overrides it"
  - "api_id/api_hash always the global settings values, never per-account (D-03)"
  - "contact_check_worker._tick two-level LATERAL: BOTH the inner subquery SELECT and the outer derived-table projection (s.client_fingerprint, unaliased) must carry the column, else first.client_fingerprint raises AttributeError on every tick — a whole-pool regression a grep count cannot catch (verified GREEN via the _tick-driving test_contact_check_worker.py suite)"
  - "IMPT-10 (D-06/D-07): update_sender_2fa decrypts stored twofa_password_enc as current_password ONLY when the request omits it; decrypted plaintext used solely to build the edit_2fa request — never returned, never logged, never re-persisted; endpoint still returns exactly {\"success\": True}"
  - "conversations.py send_message_by_telegram_id caller stays fingerprint=None (documented DEFERRED) — the METHOD gained the param (no signature drift), but the single manual-inbox-send path is a weak antifraud signal (D-04), not the persistent listener connect"

patterns-established:
  - "Per-account fingerprint seam: additive keyword-only fingerprint param threaded from every client-build call site down to make_telegram_client, NULL-safe end to end"
  - "Test-isolation: patch instance singletons via unittest.mock patch.object (deletes the instance attr on exit), NOT monkeypatch.setattr on the instance (leaves a bound-method instance attr that shadows a later class-level patch.object)"

requirements-completed: [IMPT-04, IMPT-10]

# Metrics
duration: 36min
completed: 2026-07-07
---

# Phase 21 Plan 02: Fingerprint Seam and 2FA Autofill Summary

**Per-account `client_fingerprint` override wired from `make_telegram_client`/`get_client` through every automated hot path (queue, listener, warmup, checker + worker) AND all 16 Phase-20 profile/2FA `TelegramService` methods, with a strict NULL fallback keeping the 13 phone senders byte-identical; plus IMPT-10 imported-account 2FA autofill from the stored encrypted password (never returned).**

## Performance

- **Duration:** 36 min (across two executor sessions — interrupted, continued)
- **Started:** 2026-07-07T07:14:00Z
- **Completed:** 2026-07-07T07:50:08Z
- **Tasks:** 3
- **Files modified:** 8 (0 created, 8 modified)

## Accomplishments

- `make_telegram_client(session, ..., fingerprint=None)` + `get_client(..., fingerprint=None)`: `fp = {**_CLIENT_FINGERPRINT, **(fingerprint or {})}`, `lang_pack='tdesktop'` still forced, api_id/api_hash still global — NULL is byte-identical to today (D-02/D-03/D-04).
- `client_fingerprint` threaded through EVERY automated client-build path: queue (ORM), listener 24/7 reconnect (dict SELECT + `.get`), warmup (dict SELECT + `.get`), checker `_get_client`/`check_phones`/`_check_phones_locked`/`probe_control`/`check_usernames`/`_check_usernames_locked` (param down the primitive call graph), and `contact_check_worker` `_tick` (both inner LATERAL + outer projection), `probe_checker`, `_recover_checkers`.
- All 10 canonical + 6 alias Phase-20 profile/2FA `TelegramService` methods gained a keyword-only `fingerprint` param and forward it into `self.get_client`; `senders.py` passes `fingerprint=sender.client_fingerprint` at all 10 profile/2FA call sites — no `TypeError`.
- IMPT-10: `update_sender_2fa` falls back to the decrypted stored `twofa_password_enc` as `current_password` when the request omits it, connects under the account fingerprint, and still returns only `{"success": True}` (plaintext never surfaced).

## Task Commits

Each task was committed atomically:

1. **Task 1: fingerprint override on make_telegram_client + get_client (strict NULL fallback)** — `34e412a` (feat)
2. **Task 2: thread client_fingerprint through automated hot paths (queue/listener/warmup/checker/contact_check_worker) + kwargs/regression tests** — `71fd7b1` (feat)
3. **Task 3: thread fingerprint through Phase-20 profile/2FA methods + senders.py + IMPT-10 2FA autofill** — `531b2f0` (feat)

**Plan metadata:** _(this commit)_ (docs: complete plan)

## Files Created/Modified

- `app/services/telegram.py` — fingerprint override seam on `make_telegram_client` + `get_client` + all 16 profile/2FA methods; strict NULL fallback.
- `app/services/queue.py` — `get_client(..., fingerprint=sender.client_fingerprint)` (ORM path).
- `app/services/listener.py` — `get_active_senders` SELECT + dict carry `client_fingerprint`; `start_client` reconnect passes `fingerprint=sender_info.get("client_fingerprint")` (the persistent 24/7 path).
- `app/services/warmup.py` — `senders_map` SELECT + dict carry `client_fingerprint`; `_send_via_telethon` threads `from_sender.get("client_fingerprint")`.
- `app/services/checker.py` — `fingerprint` param down `_get_client`/`check_phones`/`_check_phones_locked`/`probe_control`/`check_usernames`/`_check_usernames_locked`.
- `app/services/contact_check_worker.py` — `_tick` inner LATERAL + outer projection carry `client_fingerprint` → `common` dict threads it into `check_phones`/`check_usernames`; `probe_checker` + `_recover_checkers` SELECTs + `probe_control` calls thread it.
- `app/routers/senders.py` — `fingerprint=sender.client_fingerprint` at all 10 profile/2FA call sites; IMPT-10 stored-2FA autofill in `update_sender_2fa`.
- `tests/test_account_import.py` — `test_null_fingerprint_matches_global`, `test_checker_get_client_threads_fingerprint`, `test_2fa_autofill_uses_stored_password`, `test_profile_method_accepts_fingerprint`.

## Decisions Made

- Strict NULL fallback (D-02) keeps the 13 phone-onboarded senders exactly as today; fingerprint is purely additive.
- `lang_pack='tdesktop'` forced unconditionally (D-04); api_id/api_hash stay global (D-03).
- The two-level `_tick` LATERAL needs the column on BOTH the inner subquery and the outer projection — a grep count alone cannot prove this, so the `_tick`-driving `test_contact_check_worker.py` suite (16 tests) was run GREEN as the real runtime assertion.
- IMPT-10 (D-06/D-07): stored 2FA password decrypted server-side only, used to build the `edit_2fa` request, never returned/logged/re-persisted.
- `conversations.py` manual-inbox-send caller stays `fingerprint=None` (documented DEFERRED in the plan interfaces) — the method still gained the param, so no signature drift.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test-isolation leak: `test_profile_method_accepts_fingerprint` poisoned the cr04 signature test**
- **Found during:** Task 3 (continuation — running the `sender or profile or 2fa` suite)
- **Issue:** The new test used `monkeypatch.setattr(telegram_service, "get_client", ...)` on the singleton INSTANCE. monkeypatch restores a resolved-from-class attribute by re-`setattr`-ing a bound method into the instance `__dict__`, which then SHADOWED the class-level `patch.object(type(telegram_service), "get_client", ...)` that `test_cr04_profile_call_signatures.py::test_check_username_forwards_sender_id_to_get_client` relies on. In the full run `check_username` therefore called the REAL `get_client`, hit `decrypt_session("enc-session")` → `cryptography.fernet.InvalidToken`, and the cr04 test failed (passed in isolation — a classic order-dependent pollution).
- **Fix:** Rewrote the test to patch via `unittest.mock.patch.object` (context manager) instead of `monkeypatch.setattr` on the instance — `patch.object` deletes the instance attribute on exit, so no leakage. Added an explanatory NB in the docstring.
- **Files modified:** tests/test_account_import.py
- **Verification:** `pytest test_profile_method_accepts_fingerprint test_check_username_forwards_sender_id_to_get_client` (sequence) → 2 passed; the `sender or profile or 2fa` run dropped from 5 failing to 4 (all 4 remaining are the sanctioned pre-existing failures).
- **Committed in:** `531b2f0` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — a test I authored in this plan).
**Impact on plan:** The bug was in the plan's own new test, not in product code; fix restores test isolation. No scope creep, no product behavior change.

## Issues Encountered

- Running `tests/test_account_import.py` with `-x` stops at downstream RED scaffold tests (`app.services.account_import` module belongs to 21-03/21-04, intentionally still RED). Verified the 5 21-02-relevant tests (`fingerprint`/`2fa_autofill`/`profile_method`/`null_fingerprint`/`checker_get_client`) by name-selection → all GREEN.

## Deferred Issues

None from this plan's scope. Two OUT-OF-SCOPE pre-existing test failures are documented in [`deferred-items.md`](./deferred-items.md) and explicitly sanctioned by the project rules ("do not chase"):
1. Onboarding reauth tests insert a `MagicMock` as `tg_username` (3 tests in `test_onboarding_plainflow_reauth.py` / `test_onboarding_reauth.py`) — verified pre-existing (fails identically with 21-02 changes stashed); none of these files were touched by this plan.
2. `test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14) — long-standing warmup-pool restriction scaffold, belongs to a warmup task, not Phase 21.

## Known Stubs

None. All fingerprint threading reads live column values (NULL for existing senders → strict global fallback); no placeholder or hardcoded fingerprint. The 2FA autofill reads the real stored `twofa_password_enc`.

## Test Results

- 21-02 target tests (5): all GREEN (`test_fingerprint_override_and_strict_fallback`, `test_null_fingerprint_matches_global`, `test_checker_get_client_threads_fingerprint`, `test_2fa_autofill_uses_stored_password`, `test_profile_method_accepts_fingerprint`).
- `tests/test_contact_check_worker.py`: 16/16 GREEN — the real runtime assertion for the two-level LATERAL outer-projection blocker.
- `tests/ -k "queue or warmup or checker or listener or contact_check"`: 220 passed, 1 failed (WARM-14 pre-existing).
- `tests/ -k "sender or profile or 2fa"`: 139 passed, 4 failed (all 4 = the sanctioned pre-existing failures above).

## Authentication Gates

None.

## User Setup Required

None - no external service configuration required. Migration 051 (from 21-01) auto-applies on the next `docker compose up -d --build api`; this plan added no migration.

## Next Phase Readiness

- IMPT-04 seam is live and threaded everywhere a client is built for a sender; IMPT-10 read-path is live and secret-safe.
- **21-03** (preview/unzip/pair/stage) and **21-04** (per-account import routine) can now store a `client_fingerprint` and a Fernet-encrypted `twofa_password_enc` knowing the reconnect + 2FA-change paths will honor them.
- These changes are committed to `main` but NOT yet deployed to prod (deploy via `docker compose up -d --build api && docker compose up -d --build listener` when the phase is ready). NULL fallback means deploying early is safe for the 13 existing senders.

---
*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Completed: 2026-07-07*

## Self-Check: PASSED

- `21-02-SUMMARY.md` and `deferred-items.md` exist on disk.
- Task commits `34e412a` (feat), `71fd7b1` (feat), `531b2f0` (feat) present in git log.
- All 5 21-02 target tests GREEN; the only remaining test failures are the 4 sanctioned pre-existing ones (3 onboarding MagicMock tg_username + WARM-14).
