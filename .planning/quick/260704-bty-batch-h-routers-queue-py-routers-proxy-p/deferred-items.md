# Deferred items — quick-260704-bty (Batch H)

## Pre-existing test failure (NOT introduced by Batch H — out of scope)

Running the FULL test-overlay suite in this worktree (base `92bd54b`) after the two-file
deletion gives:

```
1 failed, 939 passed, 1 skipped, 10 warnings in 123s
FAILED tests/test_warmup_worker.py::test_restricted_sender_excluded
```

The single failure is **pre-existing** and **unrelated** to deleting
`app/routers/queue.py` / `app/routers/proxy_pool.py`:

- It is a RED scaffold test for an unimplemented feature. Its own assertion message
  says so verbatim: `spam_limited sender must be excluded from warmup pool selection
  — restriction clause not added yet (WARM-14)`.
- It fails **in isolation** (`pytest tests/test_warmup_worker.py::test_restricted_sender_excluded`
  → 1 failed in 1.6s), so it is not an ordering/cascade artifact.
- It concerns the warmup worker's pool-selection SQL — no code path touches the deleted
  router modules (which were unmounted, unimportable dead code importing a non-existent
  `app.routers.auth`).

**Action:** NOT fixed here — it is an unrelated not-yet-implemented feature (WARM-14),
out of scope for a dead-code deletion task.

## Note on the shared-checkout full-suite cascade (environmental, separately documented)

When the full suite is run against the newer shared-checkout base (which carries later
migrations 045/046 + Batch C/D/E/G test additions), it exhibits a large non-deterministic
`sqlalchemy.exc` fixture-setup cascade (`relation "workspaces" does not exist`). This was
measured this task to be pre-existing there too (baseline with both router files RESTORED
reproduced it identically — 144 failed / 84 errors vs 67 failed / 103 errors with the
deletion; the count variance is inherent to the poisoning). Root cause is already
documented in
`.planning/quick/260703-rm3-b-wr-05-wr-06-wr-08-wr-07-in-02-in-03/deferred-items.md`:
`tests/test_phase5_migration_017.py` pooled-connection poisoning. It is unrelated to this
deletion and out of scope. This worktree base (`92bd54b`) predates those migrations, which
is why the authoritative in-worktree run above is clean apart from the single WARM-14 RED.
