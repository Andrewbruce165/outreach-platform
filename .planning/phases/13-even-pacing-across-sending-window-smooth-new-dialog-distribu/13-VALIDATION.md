---
phase: 13
slug: even-pacing-across-sending-window-smooth-new-dialog-distribu
status: finalized
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-26
---

# Phase 13 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (async fixtures), SQLAlchemy async over real ephemeral PostgreSQL (tmpfs `db-test`, NOT mocked) |
| **Config file** | tests/conftest.py + docker-compose.test.yml + pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_even_pacing.py -x -q` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` |
| **Estimated runtime** | quick run seconds; full suite ~1–2 min (queue suite is fast, real PG in tmpfs) |

> NEVER run `docker compose run --rm api pytest` without the test overlay — conftest guard DROP SCHEMA hits prod. NEVER `docker compose down -v` (wipes prod postgres_data volume). Test-overlay db-test is ephemeral (tmpfs).

---

## Sampling Rate

- **After every task commit:** quick run command (+ `tests/test_queue_new_dialog_limit.py tests/test_queue_per_campaign_hours.py` to catch shared-SELECT regression)
- **After every plan wave:** full suite command
- **Before `/gsd:verify-work`:** full suite must be green (baseline is GREEN per MEMORY — gates can trust `TEST_EXIT==0`)
- **Max feedback latency:** < 2 min (full suite)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 13-01 T1 | 13-01 | 1 | PACE-01, PACE-02 | div-by-zero, frac clamp | constants intact + jitter consts; clamped elapsed-fraction, no ZeroDivisionError | unit (introspection + pure fn) | `pytest tests/test_queue_even_pacing.py::test_protected_constants_intact tests/test_queue_even_pacing.py::test_window_elapsed_fraction -x` | ❌ W0 (created by 13-01) | ⬜ pending |
| 13-01 T2 | 13-01 | 1 | PACE-03..07 | SQL-inj (bind), SKIP LOCKED | over/under-expected gate, window-start counter, floor, no-burst, follow-up bypass | integration + introspection | `pytest tests/test_queue_even_pacing.py -x` | ❌ W0 (created by 13-01) | ⬜ pending |
| 13-02 T1 | 13-02 | 2 | PACE-01, PACE-02 | div-by-zero, frac clamp | `width or 24` guard; `[0,1]` clamp; injectable now | unit | `pytest tests/test_queue_even_pacing.py::test_protected_constants_intact tests/test_queue_even_pacing.py::test_window_elapsed_fraction -x` | ✅ (after 13-01) | ⬜ pending |
| 13-02 T2 | 13-02 | 2 | PACE-03, PACE-04, PACE-05, PACE-06, PACE-07 | SQL-inj (bind params), lock escalation, expected=0 | `:window_start_utc`/`:expected_now` binds; correlated subquery (no CTE) preserves `FOR UPDATE OF mq SKIP LOCKED`/`LIMIT 8`; jitter; follow-up bypass; `_check_rate_limits` untouched | integration + introspection | `pytest tests/test_queue_even_pacing.py -q` | ✅ (after 13-01) | ⬜ pending |
| 13-02 T3 | 13-02 | 2 | PACE-01..07 (regression) | shared-SELECT regression | Phase 4/9/12 queue tests still green; only queue.py + test file changed | full suite | `pytest -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky.*

---

## Wave 0 Requirements

- [ ] `tests/test_queue_even_pacing.py` — new file (plan 13-01); 7 tests mapping 1:1 to PACE-01..07. Closest analog: `tests/test_queue_new_dialog_limit.py` (helpers copied verbatim). Deferred imports (`_window_elapsed_fraction`, `PACE_JITTER_*`) inside test bodies keep `--collect-only` clean while staying RED.
- [ ] Injectable `now` into `_window_elapsed_fraction` so time math is unit-testable deterministically (no freezegun in project).
- [ ] No new fixtures needed — `async_db_session`, `test_running_campaign_factory`, `_seed_sent_dialog` cover it.
- [ ] No framework install — pytest/pytest-asyncio already present and green.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (none — all behaviors covered by automated tests) | — | The "spread across the window, not bunched" acceptance is verified deterministically via the expected-by-now predicate tests (over/under-expected, window-start counter divergence, no-burst per call) rather than long-running observation; the injectable-`now` helper removes wall-clock flakiness. | — |

*No manual-only verifications required: the determinism recipe (RESEARCH §Validation Architecture) makes the window-spread behavior unit/integration-testable.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies (13-02 tasks depend on the 13-01 scaffold)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (test_queue_even_pacing.py created in 13-01 before any 13-02 task runs)
- [x] No watch-mode flags
- [x] Feedback latency < target (< 2 min full suite)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** finalized 2026-06-26 (planner)
