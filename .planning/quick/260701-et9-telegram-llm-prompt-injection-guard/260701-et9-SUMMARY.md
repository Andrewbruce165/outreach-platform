---
phase: quick-260701-et9
plan: 01
subsystem: api
tags: [prompt-injection, llm, ai-engine, security, sanitization, unicode]

# Dependency graph
requires:
  - phase: quick-260629-g2z
    provides: "<user_message> isolation-boundary prompt fragment (_PROMPT_INJECTION_GUARD)"
provides:
  - "Pure sync helper sanitize_inbound(text, max_length=4096) in app/services/ai_engine.py"
  - "Delimiter-escape defence: <user_message>/</user_message> tokens stripped (any case/whitespace) before wrapping"
  - "Zero-width/control/bidi excision on inbound content (banlist-bypass defence), \\n and \\t preserved"
  - "Length cap on inbound content (… [truncated])"
  - "Inbound history rows sanitized; outbound (assistant) rows unchanged"
  - "Pure-unit test suite tests/test_sanitize_inbound.py (13 tests)"
affects: [ai_engine, listener, prompt-assembly]

# Tech tracking
tech-stack:
  added: []  # stdlib re + unicodedata only
  patterns:
    - "Inbound content sanitized at the LLM boundary; dedup keeps comparing RAW new_message so the current message is not duplicated into history"

key-files:
  created:
    - tests/test_sanitize_inbound.py
  modified:
    - app/services/ai_engine.py

key-decisions:
  - "Regex widened to <\\s*/?\\s*user_message\\s*> so '< / user_message >' (whitespace between < and /) is also neutralized — the plan's Task 2 mandated this hostile form but the plan's Task 1 regex </?\\s*user_message\\s*> could not match it (Rule 1 fix)."
  - "sanitize_inbound applied ONLY to the wrapped <user_message> string and inbound history rows; dedup comparison target left as RAW new_message (unchanged)."

patterns-established:
  - "Pure sync module-level sanitizer for contact-originated text at the LLM boundary"

requirements-completed: [ET9-01]

# Metrics
duration: ~20min
completed: 2026-07-01
---

# Phase quick-260701-et9: Telegram LLM Prompt-Injection Guard Summary

**Pure sync `sanitize_inbound` helper hardens the `<user_message>` isolation boundary — strips spoofed delimiter tags (any case/whitespace), excises zero-width/control/bidi chars, and caps length — applied at both the message-assembly and inbound-history call sites in ai_engine.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-01T10:45Z
- **Completed:** 2026-07-01T10:58Z
- **Tasks:** 2
- **Files modified:** 2 (1 modified, 1 created)

## Accomplishments
- Added module-level pure/sync `sanitize_inbound(text, max_length=4096)` between `_PROMPT_NO_EMOJI` and `class AIEngine` (plus `import re`, `import unicodedata`).
- Applied it at message assembly (`clean = sanitize_inbound(new_message)` used ONLY inside the wrapped `<user_message>` string) — dedup loop still compares RAW `new_message`.
- Applied it at the history builder for inbound rows only; outbound (assistant) content passes through unchanged.
- Added 13 pure-sync unit tests (no DB/async) covering delimiter escape, zero-width/bidi excision, length truncation, idempotency/plain-text-unchanged, newline/tab handling, empty/None.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add sanitize_inbound helper and apply at both inbound call sites** - `518dd07` (fix)
2. **Task 2: Pure-unit tests for sanitize_inbound** - `1f7137f` (test)

## Files Created/Modified
- `app/services/ai_engine.py` - Added `sanitize_inbound` + `_ZERO_WIDTH_BIDI` set + `_USER_MSG_TAG_RE`; imports `re`/`unicodedata`; applied at message assembly (line ~1344) and history builder (line ~770). Dedup comparison target unchanged.
- `tests/test_sanitize_inbound.py` - New pure-sync test file, 13 tests, imports `sanitize_inbound` directly.

## Decisions Made
- **Regex widened (Rule 1 fix):** the plan's Task 1 gave `</?\s*user_message\s*>` but Task 2 mandated the hostile form `"< / user_message >"` (whitespace between `<` and `/`) be neutralized. That form does not match the plan's regex, so `test_whitespaced_tags_removed` failed. Widened to `<\s*/?\s*user_message\s*>` — allows whitespace on both sides of the optional slash — which de-fangs that spoof vector and satisfies the mandated test. Helper docstring ("any case/whitespace") stays accurate.
- Sanitization applied to the wrapped `<user_message>` string and inbound history rows only; dedup keeps comparing RAW `new_message` (grep guard confirms line untouched).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Delimiter regex could not neutralize the mandated whitespaced tag form**
- **Found during:** Task 2 (unit tests)
- **Issue:** Plan's Task 1 regex `</?\s*user_message\s*>` requires `/` immediately after `<`, so the hostile form `"< / user_message >"` (whitespace between `<` and `/`), which the plan's Task 2 explicitly requires to be stripped, passed through unchanged → `test_whitespaced_tags_removed` failed.
- **Fix:** Widened regex to `<\s*/?\s*user_message\s*>` (whitespace allowed on both sides of the optional slash). This is a genuine spoofing vector, so hardening it strengthens the guard.
- **Files modified:** app/services/ai_engine.py
- **Verification:** `tests/test_sanitize_inbound.py` 13/13 green; full suite 865 passed.
- **Committed in:** 518dd07 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug/security-hardening)
**Impact on plan:** The fix is necessary to satisfy the plan's own Task 2 mandate and closes a real delimiter-spoof vector. No scope creep.

## Issues Encountered
- **Test-overlay env/container isolation in the worktree:** the base `docker-compose.yml` pins fixed `container_name` values (outreach-platform-db/api/listener) that collide with the running prod containers, and the worktree lacks the `.env` that supplies compose interpolation. Resolved by (a) supplying `--env-file /root/apps/aimly/tg-outreach/.env` for `${VAR}` interpolation (the test overlay still overrides `DATABASE_URL` → `outreach_test`, so prod DB is never touched), and (b) temporarily dropping the fixed container names in the worktree's own `docker-compose.yml` for the test runs, then reverting them (NOT committed — prod compose must keep its fixed names). No prod containers/volumes were touched; the isolated stack ran under the worktree project name with an ephemeral tmpfs `db-test`.
- **Pre-existing unrelated test failure (out of scope):** `tests/test_warmup_worker.py::test_restricted_sender_excluded` fails on the baseline too (proven by stashing the ai_engine change — still `1 failed`). It is a RED scaffold for unimplemented warmup-pool restriction filtering (WARM-14); its own assertion message says "restriction clause not added yet (WARM-14)". Logged to `deferred-items.md`, not fixed (scope boundary).

## Test Results (real output, via test-overlay)

Command (mandatory test-overlay form; `--env-file` supplies compose interpolation, `DATABASE_URL` overridden to `outreach_test`):
```
docker compose --env-file /root/apps/aimly/tg-outreach/.env -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q
```

- **Targeted:** `tests/test_sanitize_inbound.py` → **13 passed**
- **Task 1 verify one-liner:** prints `OK`
- **Full suite:** `====== 1 failed, 865 passed, 1 skipped, 10 warnings in 153.03s ======` → **PYTEST_EXIT=1**
  - The single failure is the pre-existing, unrelated `test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14 scaffold, fails identically on baseline). Every test related to this change is green.
- **Grep guard:** `grep -n 'msg\["content"\] != new_message' app/services/ai_engine.py` → returns line 1340 (dedup compares RAW `new_message`, unchanged).

## User Setup Required
None - code-only change, no external service configuration, no migration.

## Next Phase Readiness
- Guard is in place and unit-tested. NOT yet deployed — deploy via `docker compose up -d --build api` (and `listener`) on the prod checkout when ready. No migration.
- Suggest addressing the pre-existing WARM-14 warmup-worker RED test separately (tracked in `deferred-items.md`).

---
*Phase: quick-260701-et9-telegram-llm-prompt-injection-guard*
*Completed: 2026-07-01*

## Self-Check: PASSED

- FOUND: app/services/ai_engine.py
- FOUND: tests/test_sanitize_inbound.py
- FOUND: .planning/quick/260701-et9-telegram-llm-prompt-injection-guard/260701-et9-SUMMARY.md
- FOUND: .planning/quick/260701-et9-telegram-llm-prompt-injection-guard/deferred-items.md
- FOUND commit: 518dd07 (Task 1)
- FOUND commit: 1f7137f (Task 2)
