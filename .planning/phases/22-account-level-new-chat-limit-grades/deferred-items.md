# Phase 22 — Deferred Items

## Out-of-scope pre-existing test failure (logged during 22-05 execution)

- **`tests/test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14)** is RED
  and has been RED since Phase 15. It asserts a `spam_limited` sender is excluded from
  `_get_active_pool`, but the shipped implementation *intentionally includes*
  `spam_limited` accounts (Phase 15 D-14: warmup is trust-recovery for spam-limited
  accounts — `restriction_status IN ('none', 'spam_limited')`). The test is a stale RED
  guard that contradicts the deployed behaviour.
- **Not caused by 22-05.** Plan 22-05 only added `s.current_level` to the
  `_get_active_pool` SELECT; the restriction WHERE clause was untouched.
- **Action:** left as-is (out of scope for the shared-budget plan). Should be reconciled
  by whoever owns the WARM-14 guard — either delete the stale RED test or re-decide the
  spam_limited warmup policy.
