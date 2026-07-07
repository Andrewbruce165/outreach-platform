---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 04
subsystem: telethon
tags: [telethon, account-import, sqlite-session, stringsession, fernet, proxy-pool, dedup, partial-failure]

# Dependency graph
requires:
  - phase: 21-01-schema-foundation-and-test-scaffold
    provides: "AccountImportItem/Job ORM + senders.client_fingerprint/twofa_password_enc cols + the RED contract import_one_account(db, item)->result_str"
  - phase: 21-02-fingerprint-seam-and-2fa-autofill
    provides: "make_telegram_client(fingerprint=) seam so an imported account reconnects under its own device fingerprint"
  - phase: 21-03-preview-unzip-pair-stage
    provides: "VendorAccountJson + build_fingerprint (both consumed here) in app/services/account_import.py"
provides:
  - "sqlite_to_string_session(bytes)->str — OFFLINE vendor SQLite .session → StringSession with a temp-file lifecycle (no socket)"
  - "encrypt_twofa(str|None)->str|None — Fernet-encrypt the vendor 2FA at rest (D-05) via the shared session key"
  - "resolve_import_proxy(db, ws, json_proxy)->(proxy|None, pool_row_id|None) — JSON-first then a free ProxyPool row, returning the row id to mark taken after create"
  - "import_one_account(db, item)->result_str — per-account convert/connect/get_me/dedup/create, partial-failure-safe (never raises into the batch)"
affects: [21-05-async-job-confirm-worker-status, 21-06-frontend-and-handoff]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Offline SQLite→StringSession via a temp '.session' path (mkstemp suffix='.session' so Telethon uses it verbatim), chmod 0600, deleted with its journal/WAL side files in a finally — vendor session bytes never linger on disk (Pitfall 4/9)"
    - "Per-account import result is a STRING result-code (imported/already_connected/convert_failed/auth_failed/banned/not_authorized/connect_failed/malformed_json/failed); the routine captures every per-account error and NEVER raises into the batch (D-10) — the worker persists the code"
    - "Two-stage dedup: cheap pre-connect skip by phone (never loads the session, never overwrites a live one — D-14) + authoritative post-get_me skip by telegram_id (IMPT-06)"
    - "Free-pool proxy is READ by the resolver (returns row id) and MARKED assigned_to_sender_id by the caller only after the sender exists — closes the Warning-1 contract gap"

key-files:
  created: []
  modified:
    - app/services/account_import.py

key-decisions:
  - "Signature follows the AUTHORITATIVE 21-01 RED contract import_one_account(db, item)->result_str (item = an account_import_items row dict), NOT the plan's illustrative import_one_account(db, ws, role, basename, bytes, vendor)->dict — the tests are the contract (mirrors the 21-03 basename-shape precedent)"
  - "Dedup is PHONE(basename)-based pre-connect + telegram_id post-connect, NOT slug-based: the dedup test's existing sender has slug 'existing-dup' (a slug/telegram_id-only check would either miss it or wrongly flag the partial-bad item that shares get_me id 555001 with the just-created good sender). Phone pre-check also lets a duplicate with a GARBAGE session still report already_connected without ever loading it"
  - "Imported phone = the item basename (the vendor names each file by E.164 phone), NOT me.phone: the stub's me.phone is a privacy-hidden/incidental value and the partial-success test asserts the created sender's phone == basename"
  - "restriction_status left to its server_default 'none' on INSERT (D-11) — imported accounts start active/none with NO @SpamBot probe"

patterns-established:
  - "A per-file import routine that is partial-failure-safe by construction: a broken pair fails its own item with a reason-code, the batch keeps going"

requirements-completed: [IMPT-03, IMPT-05, IMPT-06, IMPT-07]

# Metrics
duration: 20min
completed: 2026-07-07
---

# Phase 21 Plan 04: Per-Account Import Routine Summary

**`import_one_account(db, item)` recomposes the offline SQLite→StringSession recipe, the 21-02 fingerprint seam, and the onboarding create-path into one partial-failure-safe per-account routine: convert offline → connect under the account's own fingerprint → get_me → dedup (phone pre-connect + telegram_id post-connect) → create exactly one `active` sender with Fernet-2FA + a JSON-or-pool proxy, or skip+report — never raising into the batch.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-07T08:05:00Z
- **Completed:** 2026-07-07T08:24:25Z
- **Tasks:** 2
- **Files modified:** 1 (`app/services/account_import.py`)

## Accomplishments

- `sqlite_to_string_session(session_bytes) -> str`: writes vendor bytes to a unique temp `.session` path (Telethon uses it verbatim), `chmod 0600`, loads via `SQLiteSession` OFFLINE (no socket), guards empty/invalid auth_key (`ValueError("empty_or_invalid_session")`), and deletes the temp file + sqlite journal/WAL/SHM side files in a `finally` (Pitfall 4/9). Round-trips: `StringSession(returned).auth_key.key == original`.
- `encrypt_twofa(twofa) -> str | None`: Fernet-encrypts via the shared session key (D-05); `None`/empty → `None`.
- `resolve_import_proxy(db, ws, json_proxy) -> (proxy | None, pool_row_id | None)`: JSON proxy wins (`(json_proxy, None)`); else one FREE `ProxyPool` row (`assigned_to_sender_id IS NULL`) returned WITH its `id` so the caller marks the exact row taken; empty pool → `(None, None)`. Read-only — never writes the assignment.
- `import_one_account(db, item) -> result_str`: the ~10%-new core. Re-validates `VendorAccountJson` (basename injected as `session_file`), pre-connect dedup by phone (never loads a duplicate's session — D-14), offline convert (`convert_failed` on a broken pair), connect under `build_fingerprint(vendor)` + `get_me` + `disconnect` in a `finally`, authoritative `telegram_id` dedup (IMPT-06), then creates ONE `active`/`none` sender (fingerprint / Fernet-2FA / proxy) with `IntegrityError` race recovery and the exact-pool-row mark. Captures every per-account failure as a result-code and never raises into the batch (D-10).

## Task Commits

Each task was committed atomically:

1. **Task 1: offline SQLite→StringSession + encrypt_twofa + resolve_import_proxy** — `024ba31` (feat)
2. **Task 2: import_one_account (convert/connect/get_me/dedup, partial-failure-safe)** — `d3244be` (feat)

**Plan metadata:** _(this commit)_ (docs: complete plan)

## Files Created/Modified

- `app/services/account_import.py` — added `sqlite_to_string_session`, `encrypt_twofa`, `resolve_import_proxy`, `import_one_account` (+ `_mask_phone`/`_as_vendor_dict`/`_job_role` helpers); top-level Telethon / models / telegram-seam / encryption imports added; module docstring updated to reflect the 21-04 additions.

## Decisions Made

- **Signature = the RED contract, not the plan prose.** The 21-01 scaffold pins `import_one_account(db, item) -> result_str` and the tests call it with a single item-row dict and compare to a string. The plan's `(db, ws, role, basename, bytes, vendor) -> dict` shape was illustrative; the test is authoritative (same principle as 21-03's basename shape). `workspace_id`/`basename`/`session_blob`/`vendor_json` are read off the item; `role` is looked up from the item's `account_import_jobs` row.
- **Dedup is phone-pre-connect + telegram_id-post-connect, not slug.** See the Deviations section — this is the only scheme that satisfies both the dedup test (garbage session, matching phone, existing slug `existing-dup`) and the partial-success test (two garbage-vs-valid items that share a get_me id).
- **Imported phone = basename** (vendor files are named by E.164 phone); `me.phone` is unreliable/privacy-hidden and the test asserts the stored phone equals the basename.
- **No @SpamBot probe on import** — `restriction_status` left to its `server_default 'none'` (D-11); `lifecycle_status='active'`, `auth_status='ok'` set explicitly.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] import_one_account signature + return type follow the authoritative RED contract**
- **Found during:** Task 2 (reading `tests/test_account_import.py::test_dedup_skip_and_proxy` / `::test_partial_success_and_start_state`)
- **Issue:** The plan specified `import_one_account(db, workspace_id, role, basename, session_bytes, vendor_dict) -> {status, result, reason, sender_id}`, but the 21-01 RED tests call `import_one_account(db, dict(item))` and assert the return is a bare string (`== "already_connected"`, `in ("imported","ok")`). Implementing the plan's dict signature would fail collection/assertions.
- **Fix:** Implemented `import_one_account(db, item) -> str` returning a result-code string; the routine reads `workspace_id`/`basename`/`session_blob`/`vendor_json` off the item dict and resolves `role` from the item's job row.
- **Files modified:** app/services/account_import.py
- **Verification:** `test_dedup_skip_and_proxy` + `test_partial_success_and_start_state` GREEN.
- **Committed in:** `d3244be`

**2. [Rule 1 - Bug] Dedup is by phone (pre-connect) + telegram_id (post-connect), not by slug**
- **Found during:** Task 2 (reconciling the two dedup/partial tests, empirically probing `SQLiteSession`)
- **Issue:** The plan dedups by `slug = sender-{tg_id}`, but the dedup test's pre-existing sender has slug `existing-dup` (only its `telegram_id`/`phone` match), so a slug check would miss it and INSERT a duplicate. Empirically both garbage session blobs (`b"\x00sqlite"`, `b"\x00not-a-sqlite-file"`) raise `DatabaseError` identically — so a duplicate with a garbage session can only report `already_connected` if the dedup runs BEFORE conversion. Meanwhile a telegram_id-only check run post-get_me would wrongly flag the partial-bad item, which shares get_me id `555001` with the just-created good sender.
- **Fix:** Two-stage dedup — (a) pre-connect skip by `phone == basename` (never loads the session, never overwrites a live one; catches the dedup test's garbage duplicate), (b) post-get_me authoritative skip by `telegram_id` (IMPT-06 — same account, any filename). The partial-bad item fails at `sqlite_to_string_session` (`convert_failed`) before reaching either lookup that would falsely match.
- **Files modified:** app/services/account_import.py
- **Verification:** all 11 `tests/test_account_import.py` GREEN; existing session_string asserted untouched on the dup path.
- **Committed in:** `d3244be`

**3. [Rule 3 - Blocking] Module now imports Telethon + models + the telegram seam at top level**
- **Found during:** Task 2
- **Issue:** The 21-03 module docstring stated it "never imports Telethon"; the import routine requires `SQLiteSession`/`StringSession`, `make_telegram_client`/`AUTH_ERRORS`, `UserDeactivatedBanError`, `Sender`/`ProxyPool`/`AccountImportJob`, and `encrypt_session`.
- **Fix:** Added the imports and updated the module docstring; confirmed no import cycle (`app.services.telegram` and `app.models` were already imported transitively elsewhere).
- **Files modified:** app/services/account_import.py
- **Verification:** full-suite `--collect-only` = 1058 tests collected, 0 errors.
- **Committed in:** `024ba31` / `d3244be`

---

**Total deviations:** 3 auto-fixed (2 bugs against the authoritative RED contract, 1 blocking import).
**Impact on plan:** All three reconcile the implementation to the 21-01 RED contract and to empirically-verified Telethon behaviour. Behaviour delivered is exactly the plan's intent (offline convert, own-fingerprint connect, dedup-without-overwrite, active/none start, partial-failure-safe) — only the signature/return shape and dedup key changed. No scope creep.

## Issues Encountered

- The plan's stated `import_one_account` signature/return and slug-based dedup were internally inconsistent with the RED tests. Resolved by treating the tests as authoritative (documented above) and by empirically probing `SQLiteSession` on the two garbage blobs to prove the dedup must precede conversion for the phone-matched duplicate.

## Known Stubs

None. `sqlite_to_string_session` loads real bytes; `import_one_account` reads live item/vendor data, resolves a real proxy, and creates a real sender row. The Telethon client is stubbed only in tests (via `stub_import_telethon`, which patches `make_telegram_client`) — production connects for real through the shared seam.

## Authentication Gates

None. (Auth-failure of an imported session is a per-account result code — `auth_failed`/`banned`/`not_authorized` — not an interactive gate.)

## User Setup Required

None — no external service configuration required. Not yet deployed to prod (api/listener rebuild pending for the phase).

## Next Phase Readiness

- **21-05** (async confirm + worker + status) can now call `import_one_account(db, item)` per `account_import_items` row, persist the returned result-code / `sender_id` onto the item, NULL the `session_blob` on terminal status, and drive `account_import_jobs.processed/total` → `done`. The routine is a module attribute (`account_import.import_one_account`) so the worker's monkeypatch bites (kb_ingest precedent).
- The whole `tests/test_account_import.py` is GREEN (11 passed); `tests/test_account_import_worker.py` stays RED by design (its `AccountImportWorker` symbol is 21-05's).

---
*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Completed: 2026-07-07*

## Self-Check: PASSED

- `21-04-SUMMARY.md` and the modified `app/services/account_import.py` exist on disk; all 4 new symbols present.
- Task commits `024ba31` (feat) and `d3244be` (feat) present in git log.
- `tests/test_account_import.py`: 11 passed; full-suite `--collect-only` = 1058 collected, 0 errors.
