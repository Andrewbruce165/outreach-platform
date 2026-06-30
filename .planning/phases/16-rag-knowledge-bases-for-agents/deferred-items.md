# Phase 16 — Deferred Items (out-of-scope discoveries)

Logged during plan execution; NOT fixed (scope boundary). For a future hardening pass.

## From 16-04 (search-tool-wiring)

- **Random-order cross-test pollution in the senders/campaigns cluster.** The full
  suite reports ~33 failures ONLY under the default `pytest-randomly` shuffle
  (`test_senders.py`, `test_sender_lock.py`, `test_send_campaign.py`,
  `test_rerender_pending_queue.py`, `test_campaign_enqueue_worker.py`,
  `test_recontact.py`). Proven pre-existing: the 16-03 baseline reproduces the
  same shuffle-only failures, and under deterministic order (`-p no:randomly`)
  both the baseline and 16-04 are clean (835 passed / 1 known warmup RED). This is
  a session-scoped DB-fixture isolation fragility in the senders suite, unrelated
  to RAG. Fix = per-test cleanup / function-scoped state in those suites.

- **`test_warmup_worker.py::test_restricted_sender_excluded` (Phase-15 RED).** Assertion
  message: "restriction clause not added yet (WARM-14)". Driven by the uncommitted
  Phase-15 WIP in the working tree (`app/services/warmup.py`). Phase 16 never touches
  warmup. Owned by Phase 15. Also documented in 16-02 / 16-03 summaries.
