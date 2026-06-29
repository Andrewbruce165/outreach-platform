---
phase: 15-account-warmup-via-inter-account-ai-chat
plan: 03
subsystem: warmup-engine
tags: [warmup, telethon, postgres, sqlalchemy, tdd, multitenancy, restriction]

# Dependency graph
requires:
  - phase: 15 (plan 01)
    provides: warmup_settings table (mig 038) + WarmupSettings ORM + WARM-06/10/14 RED tests
  - phase: 10-account-health
    provides: senders.restriction_status / restricted_until (the D-14 gate inputs)
  - phase: 14-reliable-contact-resolution
    provides: RESV-05 restriction-skip clause pattern (contact_check_worker.py)
provides:
  - enabled-gated warmup pool selection (D-06) — disabled/absent warmup_settings yields zero sessions
  - per-workspace content resolver _get_warmup_content with code-default fallback (D-10)
  - restriction-skip clause in _get_active_pool + is_eligible (D-14) — restricted/frozen senders excluded
  - verified-no-regression invariants D-03 (queue bypass) / D-12 (combine) / D-13 (no auto-enroll)
affects: [15-04-router]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Enabled-gate via LEFT JOIN warmup_settings + COALESCE(ws.enabled,false)=true — 'no row' = OFF (default-OFF opt-in)"
    - "RESV-05 restriction-skip reused from contact_check_worker for the warmup pool (D-14)"
    - "Per-workspace content resolved once per workspace-group in _create_new_sessions; prompt threaded as arg to _generate_message with code-default fallback"

key-files:
  created:
    - .planning/phases/15-account-warmup-via-inter-account-ai-chat/15-03-SUMMARY.md
  modified:
    - app/services/warmup.py
    - tests/test_warmup_workspace_isolation.py

key-decisions:
  - "Mid-session restriction also stops warmup: _process_session is_eligible now ANDs restriction_status='none' AND no future restricted_until (mirror of pool clause)"
  - "Content resolved once per workspace-group (topics) + per-sender-workspace (prompt) — no module-level WARMUP_TOPICS/WARMUP_SYSTEM_PROMPT used when a workspace is configured"
  - "Pre-Phase-15 CR-04 isolation tests repaired to enable warmup (regression from the new enabled-gate, Rule 3)"

requirements-completed: [WARM-03, WARM-06, WARM-10, WARM-12, WARM-13, WARM-14]

# Metrics
duration: ~20min
completed: 2026-06-29
---

# Phase 15 Plan 03: Warmup Engine — Enabled-Gate, Content Resolver, Restriction-Skip Summary

**The global warmup worker now honors the per-workspace master flag (skips disabled/absent warmup_settings), reads per-workspace topics+prompt with byte-identical code-default fallback, and excludes spam_limited/frozen/future-restricted senders — all without touching the protected direct-Telethon send path or the campaign queue. WARM-06/10/14 RED tests turned green; WARM-03/12/13 invariants verified.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 (2 TDD + 1 verify-no-regression)
- **Files modified:** 2 (1 product, 1 pre-existing test repair)

## Accomplishments

- **Task 1 (D-06 + D-14):** `_get_active_pool` extended with `LEFT JOIN warmup_settings ws ON ws.workspace_id = wp.workspace_id` + `AND COALESCE(ws.enabled, false) = true` (enabled-gate, "no row" = OFF) and the RESV-05 restriction clause `AND s.restriction_status = 'none' AND (s.restricted_until IS NULL OR s.restricted_until <= NOW())`. `_process_session` `is_eligible` now also fetches `restriction_status`/`restricted_until` and ANDs them in (a `_not_restricted` helper) so an account restricted mid-session stops too. Cross-tenant pairing assertion, LEVEL_CONFIG, `_is_working_hours` untouched.
- **Task 2 (D-10):** added `async def _get_warmup_content(db, workspace_id) -> tuple[list[str], str]` — empty topics / NULL prompt / missing row all COALESCE to `WARMUP_TOPICS` + `WARMUP_SYSTEM_PROMPT`. Wired into `_create_new_sessions` (resolve topics once per workspace-group, `random.choice(ws_topics)`) and `_process_session` (resolve the per-workspace prompt and thread it to `_generate_message`, which now accepts `system_prompt: Optional[str] = None` defaulting to the constant). Unconfigured behaviour byte-identical to before.
- **Task 3 (D-03/D-12/D-13 verify-no-regression):** confirmed directly (see below). No new product behaviour.

## Exact clauses added

**`_get_active_pool` WHERE (added):**
```sql
LEFT JOIN warmup_settings ws ON ws.workspace_id = wp.workspace_id
...
AND COALESCE(ws.enabled, false) = true
AND s.restriction_status = 'none'
AND (s.restricted_until IS NULL OR s.restricted_until <= NOW())
```

**`_process_session` is_eligible (added):** fetch `restriction_status, restricted_until`; `is_eligible = (lifecycle=='active' and auth=='ok' and _not_restricted(...))`.

**Content resolver:**
```python
async def _get_warmup_content(self, db, workspace_id) -> tuple[list[str], str]:
    row = (await db.execute(text(
        "SELECT topics, system_prompt FROM warmup_settings WHERE workspace_id = :wid"
    ), {"wid": workspace_id})).fetchone()
    topics = (row[0] if row and row[0] else None) or WARMUP_TOPICS
    prompt = (row[1] if row and row[1] else None) or WARMUP_SYSTEM_PROMPT
    return topics, prompt
```

## Verified-No-Regression Invariants (Task 3)

- **WARM-03 (D-03):** `inspect`/source check of `_send_via_telethon` — contains `send_message`, does NOT contain `message_queue`. Warmup stays on the direct-Telethon path; `git diff` of `app/services/queue.py` empty for this plan (protected MIN/MAX_SEND_INTERVAL, 4/20/150, MAX_NEW_CONTACTS_PER_HOUR untouched).
- **WARM-12 (D-12):** no code auto-pauses warmup when a sender is in an active campaign — combine is allowed by omission (no such branch added or present).
- **WARM-13 (D-13):** `grep -c "warmup_pool" app/routers/onboarding.py` = 0 — onboarding never auto-enrolls; enroll is only via the explicit pool endpoint.

## Task Commits

1. **Task 1: enabled-gate + restriction-skip in pool selection** — `4f34aeb` (feat)
2. **Task 2: per-workspace content resolver with code-default fallback** — `5742a22` (feat)
3. **Regression fix: enable warmup_settings in pre-Phase-15 isolation tests** — `3513053` (test)

(Task 3 added no product/test code beyond the direct verifications above — the introspection guard could not be committed to `tests/test_warmup_worker.py` under this plan's mandatory staging constraint; the three invariants are verified directly and recorded here.)

## Test Results

- WARM-06/10/14 (`tests/test_warmup_worker.py`): **3 passed** via test-overlay.
- `tests/test_warmup_workspace_isolation.py`: **4 passed** (repaired — see deviation).
- Full suite: **801 passed, 1 skipped, 5 failed** — the 5 failures are the Plan 02 (`test_warmup_isolation.py`, 3) and Plan 04 (`test_warmup_router.py`, 2) RED tests that 15-01-SUMMARY documents as going green in Plans 02/04, NOT this plan. No regression introduced by Plan 03 (passing count rose 798 → 801).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Repaired pre-Phase-15 CR-04 isolation tests broken by the new enabled-gate**
- **Found during:** Task 1 full-suite regression check.
- **Issue:** `tests/test_warmup_workspace_isolation.py` (3 of 4 tests) enroll senders without an `enabled` `warmup_settings` row. The new D-06 enabled-gate (`COALESCE(ws.enabled,false)=true`) correctly emptied `_get_active_pool` for those workspaces, so the cross-tenant/workspace_id invariant tests failed.
- **Fix:** added an `_enable_warmup(db, workspace_id)` helper (idempotent INSERT…ON CONFLICT enabled=true) and called it in the three pool-dependent tests. The 4th test (`test_floodwait_update_only_affects_active`) needed no change. The invariants those tests pin are unchanged — they just now opt the test workspaces into warmup, as production workspaces must.
- **Files modified:** `tests/test_warmup_workspace_isolation.py`.
- **Commit:** `3513053`.
- **Staging note:** committed separately, staging ONLY that one test file (per the parallel-agent rule: never `git add -A`).

### Deviation from Task 3 instruction (introspection guard test)

- The plan's Task 3 suggested ADDING `test_warmup_send_path_bypasses_queue` to `tests/test_warmup_worker.py`. The mandatory parallel-agent staging rule for this run restricts commits to `app/services/warmup.py` + this SUMMARY (and, by Rule-3 necessity, the one regression-test file). To honor that hard constraint I did NOT add a new test to `test_warmup_worker.py`; instead the three D-03/D-12/D-13 invariants are verified directly (source-introspection of `_send_via_telethon`, onboarding grep, queue.py diff) and recorded in this SUMMARY. The invariants hold; only the artifact location differs.

**Total deviations:** 1 Rule-3 regression fix + 1 Task-3-artifact-location deviation driven by the mandatory staging constraint. No scope creep; LEVEL_CONFIG, `_is_working_hours`, FloodWait handling, and `queue.py` untouched.

## Known Stubs

None. All changes are production-real engine behaviour; content resolver has a real DB-backed source with code-default fallback (the fallback is intended, not a stub).

## Self-Check: PASSED

- `app/services/warmup.py` contains `warmup_settings`, `COALESCE(ws.enabled, false) = true`, `restriction_status = 'none'`, `_get_warmup_content`, `WARMUP_TOPICS`, `WARMUP_SYSTEM_PROMPT` — verified.
- Cross-tenant assertion (`Cross-tenant warmup pair`) still present — verified.
- Commits `4f34aeb`, `5742a22`, `3513053` exist in git history.
- WARM-06/10/14 green; full suite 801 passed with only the documented Plan 02/04 RED tests remaining.

---
*Phase: 15-account-warmup-via-inter-account-ai-chat*
*Completed: 2026-06-29*
