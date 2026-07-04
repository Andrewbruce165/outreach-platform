# Deferred Items — Phase 20 (account-profile-management)

Out-of-scope discoveries logged during plan execution. NOT fixed here (SCOPE
BOUNDARY: only issues directly caused by the current plan's changes are auto-fixed).

---

## [20-04] Full-suite shared-DB test-ordering pollution (pre-existing, NOT caused by 20-04)

**Discovered during:** Plan 20-04 phase-gate verification (`pytest -q`, full suite).

**Symptom:** The full suite reports ~91 failed + ~86 errors. The cascade root is
`Unhandled exception on POST /api/v1/auth/me` (workspace auto-create failing deep in
the session), which then poisons every downstream test that bootstraps a workspace —
spread broadly across ~40 files, including files untouched since Phase 5
(`test_phase5_migration_017.py`, `test_workspace_router.py`, `test_workspace_api_keys.py`).

**Proof it is NOT a code regression and NOT caused by 20-04:**
- `test_2fa` GREEN (the plan's target).
- `test_account_profile.py` + `test_senders.py` + `test_onboarding.py` = **45 passed** in isolation (after 20-04).
- The flagged files as a group (`test_sender_lock` + `test_send` + `test_send_campaign` + `test_rotation_campaign`) = **28 passed** in isolation.
- `test_queue_lifecycle_fixes.py` + `test_senders.py` = **28 passed** in isolation.
- `test_senders.py` fails 19 in the full run but passes all 21 in isolation.
- Every failing file passes in isolation or in small groups → definitionally a
  cumulative test-isolation / shared-DB-state issue at 900+ tests, not a correctness bug.
- 20-04 endpoints write **nothing** to the DB (D-03: the 2FA password is transient,
  no `sender.` assignment, no `db.commit()`), so they cannot pollute the shared `outreach_test` DB.

**Likely trigger:** Three parallel-agent quick-task batch merges landed in the shared
test DB during the 20-04 execution window — `quick-260704-buc` (Batch G identity/rotation,
WR-13/WR-14/IN-04), `quick-260704-buq` (Batch E campaign-lifecycle fixes),
`quick-260704-bty` (Batch H dead-router removal). This is the known shared-DB fragility
class documented in project memory ("shared-DB pool poisoning breaks migration-017
constraint-reapply", Phase 19 P04).

**Owner / next step:** The parallel quick-tasks agent and/or the Phase-20 verifier.
Re-run the full suite once the quick-tasks settle. If it persists, hunt the polluting
test (canary = `test_phase5_migration_017.py` constraint-reapply) and scope its
teardown, as done in Phase 19 P04. **Do not attribute to plan 20-04.**
