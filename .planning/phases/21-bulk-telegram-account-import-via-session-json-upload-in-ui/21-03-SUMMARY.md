---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 03
subsystem: api
tags: [zipfile, pydantic, fastapi, account-import, bytea-staging, upload]

# Dependency graph
requires:
  - phase: 21-01-schema-foundation-and-test-scaffold
    provides: "AccountImportStaging ORM (zip_data BYTEA + summary JSONB + expires_at TTL) + test_preview_pairing RED contract"
  - phase: 21-02-fingerprint-seam-and-2fa-autofill
    provides: "client_fingerprint seam so build_fingerprint's D-01 mapping has a consumer downstream"
provides:
  - "app/services/account_import.py — VendorAccountJson (Pydantic v2, session_file required, app_id/app_hash ignored D-03) + build_fingerprint (D-01 JSON→Telethon mapping) + unpack_and_pair (basename pairing, no Telegram connect) + ImportZipError/ZipTooLargeError/TooManyAccountsError"
  - "POST /api/v1/accounts/import/preview — synchronous unzip+pair+validate, stages raw ZIP in account_import_stagings with a 30-min TTL, returns import_id + matched/unpaired/malformed; secrets never leave the box"
  - "app/config.py — MAX_IMPORT_UNCOMPRESSED_BYTES (50MB) + MAX_IMPORT_ACCOUNTS (500) ZIP-safety knobs"
affects: [21-04-per-account-import-routine, 21-05-async-job-confirm-worker-status, 21-06-frontend-and-handoff]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "In-memory ZIP unpack with defence-first safety: path-traversal/absolute rejection + uncompressed-size cap + distinct-basename cap BEFORE reading content (RESEARCH Pitfall 7)"
    - "Structured ValueError subclasses (ImportZipError.code/.http_status) let the router map ZIP failures to 413/422 without a bare 500"
    - "Secrets-free preview: bare basenames + has_2fa/has_proxy flags only; the twoFA value and .session bytes live solely in the staged zip_data BYTEA (D-07)"
    - "session_file is the authoritative pairing key when present; unpack_and_pair injects the archived filename basename as the fallback so a record omitting it still validates"

key-files:
  created:
    - app/services/account_import.py
    - app/routers/account_import.py
  modified:
    - app/config.py
    - app/main.py
    - tests/test_account_import.py

key-decisions:
  - "Each preview entry (matched/unpaired/malformed) carries a BARE basename key (extension stripped) plus the full filename separately — required by the 21-01 _basenames() RED contract which keys on 'basename'; the plan's example notation ('B.json') was illustrative, the test is authoritative"
  - "VendorAccountJson keeps session_file REQUIRED (satisfies the schema + grep) but unpack_and_pair does data.setdefault('session_file', filename_stem) before validating, so the real vendor records (which leave session_file absent, filling id/phone/username from get_me per D-11) still validate and pair by their archived basename"
  - "ZIP-safety failures raise ImportZipError subclasses carrying .code + .http_status; the router catches the base class and emits {code,message} at the right status — a totally-invalid/bomb/over-count ZIP is a 4xx, never a 500"
  - "build_fingerprint omits lang_pack (make_telegram_client forces 'tdesktop', D-04) and api_id/api_hash (stay global, D-03) — it only maps device/sdk/app_version/lang_code/system_lang_code"

patterns-established:
  - "Preview/staging mirrors contacts.py import_preview: read UploadFile → guard size → parse → stage BYTEA + expires_at → return import_id, no side effects"

requirements-completed: [IMPT-01]

# Metrics
duration: 5min
completed: 2026-07-07
---

# Phase 21 Plan 03: Preview / Unzip / Pair / Stage Summary

**Synchronous `POST /accounts/import/preview` unzips a bulk-account ZIP in memory, pairs `<base>.json`↔`<base>.session` by basename, validates each vendor JSON against `VendorAccountJson`, and returns a matched/unpaired/malformed summary while staging the raw ZIP (BYTEA + 30-min TTL) — with ZIP-bomb/path-traversal/oversized-batch guards and zero Telegram connect.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-07-07T07:57:40Z
- **Completed:** 2026-07-07T08:02:20Z
- **Tasks:** 2
- **Files modified:** 5 (2 created, 3 modified)

## Accomplishments

- `app/services/account_import.py`: `VendorAccountJson` (Pydantic v2, `extra="ignore"`, `session_file` required, `app_id`/`app_hash` ignored per D-03), `build_fingerprint` (D-01 JSON→Telethon device/locale mapping), and `unpack_and_pair(zip_bytes) -> {matched, unpaired, malformed}` — pairs by basename, reports orphans + malformed JSON per-file without aborting, and never imports Telethon.
- ZIP safety (RESEARCH Pitfall 7): absolute-path/`..` rejection before any read, an uncompressed-size cap and a distinct-account cap, all surfaced as `ImportZipError` subclasses (`ZipTooLargeError`→413, `TooManyAccountsError`→422, base `ImportZipError`/`BadZipFile`→422).
- `POST /api/v1/accounts/import/preview`: reads the multipart ZIP, fast-guards compressed size, maps `ImportZipError` to a structured 4xx, stages the raw ZIP in `account_import_stagings` with a 30-min TTL, and returns `import_id` + matched/unpaired/malformed — carrying only bare basenames + `has_2fa`/`has_proxy` flags (never the `twoFA` value or `.session` bytes; D-07). Registered in `main.py`.
- `app/config.py`: `MAX_IMPORT_UNCOMPRESSED_BYTES` (50 MB) + `MAX_IMPORT_ACCOUNTS` (500) knobs mirroring the existing `Field(...)` style.

## Task Commits

Each task was committed atomically:

1. **Task 1: VendorAccountJson schema + unpack_and_pair with ZIP safety** — `5dba815` (feat)
2. **Task 2: POST /accounts/import/preview + staging + router registration** — `6248eec` (feat)

**Plan metadata:** _(this commit)_ (docs: complete plan)

## Files Created/Modified

- `app/services/account_import.py` — `VendorAccountJson`, `build_fingerprint`, `unpack_and_pair`, `ImportZipError`/`ZipTooLargeError`/`TooManyAccountsError`.
- `app/routers/account_import.py` — `POST /import/preview` + co-located Pydantic request/response models; ZIP staging with TTL.
- `app/config.py` — two ZIP-safety knobs (`MAX_IMPORT_UNCOMPRESSED_BYTES`, `MAX_IMPORT_ACCOUNTS`).
- `app/main.py` — import + `include_router(account_import.router)` after `llm_settings.router`.
- `tests/test_account_import.py` — added `test_preview_endpoint_stages_and_returns` (endpoint stages a row with a future `expires_at` + excludes secrets).

## Decisions Made

- **Bare-basename entry shape:** the 21-01 `_basenames()` RED helper keys on a dict `basename` field, so every preview entry carries a bare basename (extension stripped) plus the full `filename` separately. The plan's `['B.json', ...]` notation was illustrative; the test is the contract.
- **`session_file` required but injected:** the schema keeps `session_file: str` (per the plan + acceptance grep), but `unpack_and_pair` does `data.setdefault('session_file', filename_stem)` before validating — so real vendor records (which leave `session_file`/`id`/`phone`/`username` null, filled later from `get_me` per D-11) still validate and pair by their archived basename.
- **Structured ZIP errors:** `ImportZipError` subclasses carry `.code`/`.http_status`; the router maps them to `{code, message}` at 413/422 so a bomb/traversal/over-count/undecodable ZIP is never a 500.
- **`build_fingerprint` omits `lang_pack`** (forced `tdesktop`, D-04) and `api_id`/`api_hash` (stay global, D-03).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Preview entry shape had to key on a bare `basename`, not the plan's filename string**
- **Found during:** Task 1 (satisfying `test_preview_pairing`)
- **Issue:** The plan's behavior spec listed unpaired/malformed as filename strings (`'B.json'`), but the 21-01 RED helper `_basenames()` extracts `entry.get("basename") or entry.get("name")` and the test asserts membership of the BARE basename (`"+15551234567"`, no extension). A list of filename strings (or dicts keyed only by filename) would have failed the assertion.
- **Fix:** Each entry is a dict carrying a bare `basename` (extension stripped) plus the full `filename` (and `reason` for malformed) — satisfying both `_basenames()` and the plan's "report the filename" intent.
- **Files modified:** app/services/account_import.py
- **Verification:** `test_preview_pairing` green; matched/unpaired/malformed basenames assert exactly.
- **Committed in:** `5dba815` (Task 1 commit)

**2. [Rule 2 - Missing Critical] Added an endpoint integration test for the preview route**
- **Found during:** Task 2 (acceptance criterion #5 references "the preview test passes: a ZIP posted to the endpoint returns import_id ... and stages a row with a future expires_at", but no such test existed in the scaffold — only the unit-level `test_preview_pairing`)
- **Issue:** Without an endpoint test the staging/TTL/secret-exclusion behavior was only grep-verified, not exercised.
- **Fix:** Added `test_preview_endpoint_stages_and_returns` (name contains "preview" so the plan's `-k preview` verify picks it up) — posts a real ZIP through the ASGI app, asserts import_id + the three lists + `has_2fa`/`has_proxy` flags, confirms the raw twoFA value is absent from the response, and queries `account_import_stagings` for a persisted row with a future `expires_at`.
- **Files modified:** tests/test_account_import.py
- **Verification:** `pytest tests/test_account_import.py -k preview` → 2 passed; full-suite `--collect-only` clean (1058 tests, 0 errors); workspace-router regression 8/8 green.
- **Committed in:** `6248eec` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 bug against the RED contract shape, 1 missing critical test).
**Impact on plan:** Both stay strictly within IMPT-01 scope — one reconciles the entry shape to the authoritative test, one makes acceptance criterion #5 genuinely testable. No product-behavior scope creep.

## Issues Encountered

- The `tests/test_account_import.py` file still contains downstream RED tests for 21-04/21-05 symbols (`sqlite_to_string_session`, `encrypt_twofa`, `import_one_account`) — these remain RED by design (those plans are not yet executed). This plan's verify commands target only the `preview` tests, which pass. Deferred in-body imports keep `--collect-only` clean (1058 collected, 0 errors).

## Known Stubs

None. `unpack_and_pair` reads real ZIP contents; the endpoint stages real bytes and returns real derived flags. No placeholder/hardcoded data. `build_fingerprint` is defined here for the 21-04 import routine to consume (its call site lands in 21-04) — it is a complete, tested-shape helper, not a stub.

## Authentication Gates

None.

## User Setup Required

None — no external service configuration required. `MAX_IMPORT_UNCOMPRESSED_BYTES` / `MAX_IMPORT_ACCOUNTS` have safe defaults and are env-overridable without a redeploy. Not yet deployed to prod (api rebuild pending for the phase).

## Next Phase Readiness

- IMPT-01 delivered: preview returns matched/unpaired/malformed, stages the ZIP with a TTL, and has no import side effects (no Telegram connect).
- **21-04** (per-account import routine) can now re-read the staged ZIP by `import_id` and consume `VendorAccountJson` + `build_fingerprint`; it adds `sqlite_to_string_session` / `encrypt_twofa` / `import_one_account` to this same module (their RED tests are waiting).
- **21-05** (async confirm + worker + status) will read `account_import_stagings.zip_data`, fan the pairs into `account_import_items`, and drive the job.

---
*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Completed: 2026-07-07*

## Self-Check: PASSED

- Both created files (`app/services/account_import.py`, `app/routers/account_import.py`) + `21-03-SUMMARY.md` exist on disk.
- Task commits `5dba815` (feat) and `6248eec` (feat) present in git log.
- `preview` tests green (2 passed); full-suite `--collect-only` clean (1058 tests, 0 errors); workspace-router regression 8/8 green.
