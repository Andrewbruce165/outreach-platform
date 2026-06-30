# Deferred / Out-of-Scope Items — Phase 17

These failures were observed during the full-suite run at the end of plan **17-02**
but are NOT in 17-02's scope (checker side only: `app/services/checker.py`). They are
RED tests owned by sibling plans / a parallel effort. Left untouched per the
scope-boundary rule (only auto-fix issues directly caused by this plan's changes).

## RED tests owned by 17-03 (sender resolve ladder — `app/services/telegram.py`)
- `tests/test_send.py::test_resolve_ladder_no_sender_resolvephone` (SRLD-03)
- `tests/test_send.py::test_import_gate_registered_only` (SRLD-04)
- `tests/test_send.py::test_lazy_import_no_delete_on_sender` (SRLD-05)
- `tests/test_send.py::test_stale_username_fallthrough` (SRLD-06)
- `tests/test_send.py::test_confidence_gated_cache_sender_read` (SRLD-07 sender side)

## RED tests owned by 17-04 (block capture + docs)
- `tests/test_send.py::test_user_blocked_records_event` (SRLD-08)
- `tests/test_restriction_audit.py::test_block_rate_aggregate` (SRLD-08 — `sender_block_rate` helper not built yet, ImportError)

## RED test owned by a parallel Phase 15 (warmup) effort — uncommitted working-tree work
- `tests/test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14 — warmup-pool
  restriction clause not yet added; lives in `app/services/warmup.py`, which is
  uncommitted prior-phase work owned by another session). Unrelated to checker changes.

All 3 checker-side SRLD tests (SRLD-01 ×2, SRLD-02, SRLD-07 checker) are GREEN after 17-02.
Baseline note (17-01 SUMMARY): 10 RED + 2 GREEN created; 17-02 flips 3 of the 10.
