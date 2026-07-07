# Deferred Items — Phase 21

Out-of-scope discoveries found during execution. NOT fixed (SCOPE BOUNDARY: only
auto-fix issues directly caused by the current task's changes).

## Discovered during 21-02 (fingerprint seam + 2FA autofill)

### 1. Onboarding reauth tests insert MagicMock as tg_username (pre-existing)

- **Files:** `tests/test_onboarding_plainflow_reauth.py`, `tests/test_onboarding_reauth.py`
- **Symptom:** `asyncpg DataError: invalid input for query argument $16 (tg_username): expected str, got MagicMock`. The onboarding reauth flow inserts `get_me().username` into `senders.tg_username`; these tests mock `get_me()` without setting `.username` to a real string, so a `MagicMock` reaches the INSERT.
- **Verified pre-existing:** fails identically with the 21-02 Task-2 changes stashed (3 fail in `test_onboarding_plainflow_reauth.py` alone; 5 fail across both files run together). None of these files were touched by 21-02, whose changes are purely additive `fingerprint=` params + SELECT-column additions on telegram/queue/listener/warmup/checker/contact_check_worker.
- **Likely fix:** set `mock_get_me.return_value.username = "some_handle"` (or `None`) in the onboarding test fixtures/mocks, or make the onboarding INSERT coerce a non-str username to None.

### 2. WARM-14 warmup restriction-clause RED scaffold (pre-existing, documented)

- **File:** `tests/test_warmup_worker.py::test_restricted_sender_excluded`
- **Symptom:** `spam_limited sender must be excluded from warmup pool selection — restriction clause not added yet (WARM-14)`.
- **Status:** long-standing out-of-scope failure carried across many prior phase summaries ("1 pre-existing out-of-scope WARM-14 failure"). Belongs to a warmup-pool restriction task, not to Phase 21.

## Discovered during 21-05 (async confirm + worker + status)

### 3. Full-suite shared-DB test-ordering pollution (pre-existing, NOT caused by 21-05)

- **Symptom:** A full `pytest -q` via test-overlay reports **89 failed + 115 errors** (`sqlalchemy.exc...` setup errors) spread across unrelated files — `test_send*`, `test_rotation_campaign`, `test_sender_lock`, `test_restriction_audit`, etc. Each of these files **passes when run in isolation** (verified: `test_sender_lock.py` → 5/5 pass alone); the failures only appear in the full ordered run → classic shared-DB / test-ordering pollution.
- **Verified pre-existing:** ran the FULL suite at the parent commit `f9f718f` (21-04 completion, before any 21-05 code) → identical magnitude **89 failed / 115 errors / 853 passed**. My 21-05 HEAD (`549f7c4`) → **89 failed / 115 errors / 856 passed** — the only delta is my 3 new confirm/status endpoint tests passing (+ the worker test flipping green, absorbed in the flaky ±1). 21-05 does NOT increase the failure count, and its own targeted files are 4/4 green.
- **Why 21-05 code can't be the cause:** the `async_client` conftest fixture uses `ASGITransport(app=app)` with **no LifespanManager**, so the FastAPI lifespan never runs in tests → `account_import_worker.start()` (my only cross-cutting change) is inert during the suite. All other 21-05 changes are additive to the account-import subsystem.
- **Status:** matches the Phase-20 SUMMARY note ("full-suite run has pre-existing shared-DB test-ordering pollution unrelated to this phase"). Belongs to a test-isolation/conftest hardening task, not to Phase 21. Do NOT chase per-file — they pass alone.
