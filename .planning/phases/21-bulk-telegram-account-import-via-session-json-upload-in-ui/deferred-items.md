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
