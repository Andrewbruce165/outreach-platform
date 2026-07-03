# Deferred Items — quick 260703-j25 (Batch A)

Out-of-scope discoveries logged during execution. NOT fixed by this task
(SCOPE BOUNDARY: only issues directly caused by this task's changes are auto-fixed).

## 1. Pre-existing test/code drift: `test_warmup_worker.py::test_restricted_sender_excluded`

- **Status:** FAILING before and after Batch A (fails in isolation running only
  `tests/test_warmup_worker.py`), so NOT caused by this task. This task touched only
  `app/services/checker.py`, `app/services/contact_check_worker.py`,
  `tests/test_checker_probe.py`, `tests/test_checker_probe_burn.py` — none affect the
  warmup pool SQL.
- **Root cause:** `app/services/warmup.py::_get_active_pool` (lines ~202-206) was
  **deliberately** changed to INCLUDE `spam_limited` senders in the warmup pool
  ("прогрев — это и есть восстановление доверия для аккаунта под спам-ограничением";
  only `frozen` is excluded). But `test_restricted_sender_excluded` (WARM-14) still
  asserts `spam_limited` is EXCLUDED. Code and test contradict each other.
- **Evidence:** `assert 'restricted-6cb355' not in ['restricted-6cb355']` — the
  spam_limited sender IS in the pool by current design.
- **Recommended resolution (separate task):** either update/retire the WARM-14 test to
  match the intended "warmup includes spam_limited" behavior, or (if exclusion is the
  desired product behavior) re-add the restriction clause to `_get_active_pool`. This
  is a warmup-domain decision, out of scope for the checker Batch A fix.
