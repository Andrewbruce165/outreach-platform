# Deferred items — quick-260703-rm3 (Batch B)

## Pre-existing full-suite cascade (NOT introduced by Batch B — out of scope)

Running the FULL suite via the test-overlay produces a large cascade of
`sqlalchemy.exc` fixture-setup errors + downstream failures. This was measured to
be **pre-existing** and is **not** caused by Batch B:

| Run                         | passed | failed | errors | skipped |
| --------------------------- | ------ | ------ | ------ | ------- |
| Baseline (main `08d567d`)   | 798    | 71     | 80     | 1       |
| Batch B (this task)         | 812    | 71     | 80     | 1       |

Delta = **+14 passed** (exactly the 14 new Batch B tests) with an **identical**
71 failed + 80 errors. The checker suite (`test_checker*.py`) is 100% green in
both runs; Batch B introduces **zero** new failures.

**Root cause (documented, environmental):** a pooled-connection poisoning cascade.
`tests/test_phase5_migration_017.py` re-applies the migration-017
`conversations_status_check` constraint; over the long full run, rows committed by
earlier factory-based tests (statuses added by LATER migrations, e.g. `no_reply`
from 045 / `telegram_service` from 046) can violate the re-applied older
constraint, aborting a transaction on a pooled asyncpg connection. Every
subsequent test that grabs that poisoned connection then errors in fixture setup
(`test_senders`, `test_warmup_*`, `test_restriction_audit`, `test_send`,
`test_pool_*`, `test_queue_*`, `test_rotation_campaign`, `test_workspace_*`, …).
This is the exact fragility called out in `tests/conftest.py`
(`test_conversation_factory` teardown comment) and project memory.

**Why targeted runs are green:** the poisoning only manifests when the
migration-reapply test runs alongside a large volume of accumulated committed
rows. Any bounded subset (e.g. `test_checker_resilience_batch_b.py` +
`test_restriction_audit.py` = 29 passed; the four `test_checker*` files =
50 passed) is green.

**Action:** NOT fixed here — it is a pre-existing test-harness/isolation issue
unrelated to the checker resilience fixes, and fixing it would be an out-of-scope
architectural change to the shared-DB test fixtures. Candidate for a dedicated
"test isolation hardening" task (function-scoped DB or per-test transaction
rollback for the factory commits, or moving `test_phase5_migration_017` onto the
dedicated `migrations_raw_dsn` DB the conftest already provides for reapply tests).
