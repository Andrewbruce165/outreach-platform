# Phase 18 — Deferred / Out-of-Scope Items

## Pre-existing test failure (NOT introduced by Phase 18)

- **tests/test_warmup_worker.py::test_restricted_sender_excluded (WARM-14)** — RED
  scaffold from a parallel/uncommitted Phase 15 warmup effort. Asserts warmup pool
  selection excludes `restriction_status='spam_limited'` senders (a SQL restriction
  clause not yet added). Unrelated to Phase 18 (does not touch `_generate_message`
  or the LLM provider factory). Documented as a known baseline failure in project
  memory ("1 out-of-scope WARM-14 failure from parallel uncommitted Phase 15
  warmup"). Left untouched per scope boundary — belongs to Phase 15.
