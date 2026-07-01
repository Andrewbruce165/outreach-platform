# Deferred items — 260701-et9

## Pre-existing failing test (out of scope)

- **`tests/test_warmup_worker.py::test_restricted_sender_excluded`** — fails on the
  baseline (proven by stashing the ai_engine change and re-running: still `1 failed`).
  It is a RED scaffold test for unimplemented warmup-pool restriction filtering
  (WARM-14): the assertion message itself says *"restriction clause not added yet
  (WARM-14)"*. Unrelated to this task (touches the warmup worker, not `ai_engine`).
  NOT fixed — outside the scope boundary of the prompt-injection guard.
