# Deferred / Out-of-scope items (Phase 19)

## Pre-existing test failure (NOT introduced by Phase 19)
- `tests/test_warmup_worker.py::test_restricted_sender_excluded` — fails on the
  clean baseline (main, without any Phase 19 changes) too. Documented in project
  memory as the known WARM-14 out-of-scope failure (parallel uncommitted Phase 15
  warmup work). Full suite otherwise GREEN: 939 passed, 1 skipped, 1 failed (WARM-14)
  via test-overlay on 2026-07-03 during plan 19-04 execution.
