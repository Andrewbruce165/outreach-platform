# Deferred Items — Phase 08 (pool-management-and-even-distribution)

Out-of-scope discoveries logged during plan execution. NOT fixed (per executor
scope-boundary rules: only auto-fix issues DIRECTLY caused by the current task's
changes).

## Pre-existing full-suite failures (found during 08-02 wave-merge sampling)

**Found during:** 08-02 (rebalance-service) full-suite regression check.

**Status:** PRE-EXISTING — reproduced with `app/services/rebalance.py` removed
from the tree, so NOT caused by the 08-02 change. The only file 08-02 added is
the brand-new `rebalance.py`, and nothing outside `tests/test_rebalance.py`
imports it (grep-confirmed).

**Symptom:** `69 failed, 593 passed, 1 skipped, 20 errors` on the full suite via
the test-overlay. The 20 ERRORS are setup-time asyncpg failures in
`tests/test_migration_014.py` and `tests/test_onboarding_reauth.py`
(`sqlalchemy ... asyncpg _prepare_and_execute` / prepared-statement path),
i.e. an infra/fixture-level problem, not application logic. The 69 failures are
spread across unrelated suites and are out of scope for the rebalance plan.

**Verification that 08-02 is clean:** `tests/test_rebalance.py` (POOL-07/08/08b)
is fully GREEN (3 passed), and all 08-02 acceptance grep-gates pass (SKIP LOCKED
present, status='pending' present, no `_pick_least_loaded` call, both tables
UPDATE'd, no migration added).

**Action:** Deferred — should be investigated as a separate task (likely a
conftest / asyncpg-prepared-statement / migration-014 fixture issue), not within
Phase 8 pool-management scope.
