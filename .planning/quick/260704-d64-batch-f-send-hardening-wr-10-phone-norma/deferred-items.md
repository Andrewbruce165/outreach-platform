# Deferred Items — quick-260704-d64 (Batch F /send hardening)

Out-of-scope discoveries logged during execution. NOT fixed here (per deviation-rules
scope boundary: only auto-fix issues directly caused by this task's changes).

## Pre-existing full-suite collapse — shared-DB pooled-connection poisoning (NOT a d64 regression)

**Observed:** Full test-overlay suite run reported `95 failed, 580 passed, 347 errors`
(1014 collected). The `d64` change (`app/routers/send.py` + `tests/test_send_hardening.py`)
is exonerated:
- `tests/test_send_hardening.py` alone → 7 passed.
- The five files that ERRORed in the full run (`test_send_hardening`, `test_sender_lock`,
  `test_senders`, `test_spambot_selfcheck`, `test_warmup_isolation`) → **42 passed together**.
- The errors are in fixture setup (poisoned pooled connection), not in test bodies.

**Root cause (first failure, `-x` run):**
`tests/test_onboarding_plainflow_reauth.py::test_plainflow_reauth_upserts_existing_sender`
(and its two siblings) FAIL IN ISOLATION (`3 failed in 1.50s`) with:

```
asyncpg.exceptions.DataError: invalid input for query argument $16:
  <MagicMock name='mock.username' ...> (expected str, got MagicMock)
[SQL: INSERT INTO senders (... tg_username ...) VALUES (... $16::VARCHAR ...)]
```

The Phase-20 (account-profile-management) onboarding finalize / re-auth path now
persists `tg_username` from `client.get_me().username` (PROF-08, STATE.md 20-02). This
test's mocked Telethon client leaves `.username` as an **unstubbed MagicMock**, so a
`MagicMock` is bound into the `senders` INSERT → asyncpg `DataError`. The failed flush
aborts the transaction on the shared session-scoped asyncpg connection; the poisoned
connection is then handed to every subsequent DB-touching test → the 347-error /
95-failure cascade (everything alphabetically after `test_onboarding_plainflow_reauth`).

**This is a test-MOCK defect, not a prod defect.** In production `client.get_me().username`
returns a real `str` or `None`; onboarding re-auth was deployed + live-verified 2026-07-02
(MEMORY: re-auth upsert fix commit 3261529). Only the stale test mock is wrong.

**Same class as previously logged cascades** (STATE.md Quick Tasks): Batch B `260703-rm3`
("корень `test_phase5_migration_017` pooled-conn poisoning, вне объёма"), Batch G
`260704-buc` (822 passed/86 failed/84 errors, "0 регрессий … через тот же pre-existing …
каскад"), and Phase `20-04` ("shared-DB test-ordering pollution … NOT a 20-04 regression").
The d64 run surfaces an ADDITIONAL earlier poisoner (`test_onboarding_plainflow_reauth`)
introduced by Phase-20 `tg_username` persistence.

**Owner / fix location:** Phase 20 (account-profile-management). Fix = stub
`client.get_me().username` (and `.first_name`/`.last_name`/bio) to real strings/None in
`tests/test_onboarding_plainflow_reauth.py`'s mock, so the Phase-20 profile-cache write
receives a `str`, not a `MagicMock`. Belongs to the Phase-20 executor / a Phase-20
test-hardening quick task, NOT this Batch-F /send task.

**Broader structural fix (deferred, repo-wide):** the shared session-scoped asyncpg
connection pool has no per-test transaction isolation for code paths that open their own
`AsyncSessionLocal()` and COMMIT (or abort). One aborted flush poisons the pool for the
rest of the session. A conftest-level fix (rollback/recycle the pooled connection on
test teardown, or function-scoped engine) would stop the whole cascade class. Repeatedly
logged; not owned by any single quick task.
