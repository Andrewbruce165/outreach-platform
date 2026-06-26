---
phase: 13-even-pacing-across-sending-window-smooth-new-dialog-distribu
verified: 2026-06-26T11:00:00Z
status: passed
score: 11/11 must-haves verified
gaps: []
human_verification: []
---

# Phase 13: Even Pacing Across Sending Window — Verification Report

**Phase Goal:** Распределять открытие новых диалогов равномерно по активному окну рассылки кампании (even pacing of new cold dialogs across the campaign's daily window), instead of front-loading the daily limit. Derived pacing: expected-by-now = max_new_dialogs_per_day × elapsed_window_fraction × jitter, gating new-dialog items only; follow-ups, the base 20–55s interval, fatigue, long pauses, and the Phase 12 cap stay untouched.

**Verified:** 2026-06-26T11:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | D-05: new cold dialog eligible iff count_opened_since_window_start < max_new_dialogs_per_day × elapsed_fraction × jitter | ✓ VERIFIED | `queue.py:416-483`: pre-query computes `expected_now`, subquery `COUNT(DISTINCT paced.recipient_phone) ... >= :window_start_utc) < CAST(:expected_now AS DOUBLE PRECISION)` ANDed in new-dialog branch |
| 2 | D-06: pacing numerator counts from TODAY's window start (`finished_at >= :window_start_utc`), NOT trailing-24h — distinct from Phase 12 cap | ✓ VERIFIED | `queue.py:478-483`: pacing subquery uses `:window_start_utc`; cap subquery uses `NOW() - INTERVAL '24 hours'` — two separate correlated subqueries, separate counters |
| 3 | D-01: denominator is RAW window width (work_hour_end − work_hour_start), no long-pause subtraction | ✓ VERIFIED | `_window_elapsed_fraction` line 173: `width_h = (work_hour_end - work_hour_start) % 24 or 24` — raw width, no pause deduction |
| 4 | D-10: pacing is an ADDITIONAL gate on top of base 20–55s interval; base interval/fatigue/long pauses stay untouched | ✓ VERIFIED | `_check_rate_limits` (line 540–end): zero references to `window_start`, `expected_now`, `PACE_JITTER`; pacing subquery is added in a separate predicate branch |
| 5 | D-08: jitter via `random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)` applied to expected_now each call | ✓ VERIFIED | `queue.py:416-417`: `expected_now = camp_row.c_cap * frac * random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)` |
| 6 | D-03: narrow window + high limit → no crash, limit simply not reached (structural clamp, no `max()`) | ✓ VERIFIED | No numeric `max(target, base)` in source; `_window_elapsed_fraction` has `or 24` guard; conservative defaults `expected_now = 0.0` on no-campaign row |
| 7 | D-07: pacing lives in the per-item SELECT, NOT in `_check_rate_limits`; follow-ups bypass pacing entirely | ✓ VERIFIED | Follow-up `EXISTS prior sent` branch (lines 462-468) precedes and is mutually exclusive with the new-dialog branch that contains the pacing subquery; `_check_rate_limits` has zero pacing references |
| 8 | D-02: work_days_mask does not enter the pacing denominator | ✓ VERIFIED | `_window_elapsed_fraction` signature and body: no `work_days_mask` parameter; mask used only in `_campaign_in_working_window` |
| 9 | D-04: catch-up bounded by existing gates (base 20–55s, 4/min, 15 new contacts/h); no separate burst mechanism | ✓ VERIFIED | `_check_rate_limits` untouched; LIMIT 8 + FOR UPDATE OF mq SKIP LOCKED preserved; worker picks one item per call |
| 10 | D-09: no new DB columns, API fields, UI, migration, openapi regen — change confined to queue.py | ✓ VERIFIED | Git diff shows only `app/services/queue.py` and `tests/` changed under `app/`; last migration is `033_campaign_max_new_dialogs.sql` (Phase 12) |
| 11 | PROTECTED constants (MIN/MAX_SEND_INTERVAL, SEND_INTERVAL_FATIGUE, LONG_PAUSE_*, MAX_NEW_CONTACTS_PER_HOUR) unchanged | ✓ VERIFIED | `queue.py:41-61`: all 8 constants present at original values; `PACE_JITTER_LOW/HIGH` appended after line 69 in new sub-block |

**Score:** 11/11 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/services/queue.py` | PACE_JITTER constants, `_window_elapsed_fraction` helper, pacing predicate in `_process_next_for_sender` | ✓ VERIFIED | `PACE_JITTER_LOW = 0.75`, `PACE_JITTER_HIGH = 1.25` at lines 79-80; `_window_elapsed_fraction` at lines 125-186; pre-query + pacing subquery at lines 363-493 |
| `tests/test_queue_even_pacing.py` | 7 tests mapped 1:1 to PACE-01..07, GREEN after 13-02 | ✓ VERIFIED | 627-line file with 7 test functions: `test_protected_constants_intact`, `test_window_elapsed_fraction`, `test_pacing_gate`, `test_pace_counter_window_start`, `test_interval_floor`, `test_catchup_no_burst`, `test_followup_bypasses_pacing` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `queue.py::_process_next_for_sender` | `_window_elapsed_fraction` | Python call at line 403 | ✓ WIRED | `window_start_utc, frac = _window_elapsed_fraction(campaign_tz=..., work_hour_start=..., work_hour_end=..., now=now_utc)` |
| `queue.py::_process_next_for_sender candidate SELECT` | `message_queue` (count since window start) | Correlated COUNT subquery with `:window_start_utc` and `:expected_now` bind params | ✓ WIRED | Lines 478-483: `COUNT(DISTINCT paced.recipient_phone) FROM message_queue paced WHERE ... paced.finished_at >= :window_start_utc) < CAST(:expected_now AS DOUBLE PRECISION)` |
| `tests/test_queue_even_pacing.py` | `queue.py::_process_next_for_sender` | `worker._process_next_for_sender(sid)` calls with `_check_rate_limits` mocked True | ✓ WIRED | All 5 integration tests (PACE-03..07) invoke `worker._process_next_for_sender(sid)` directly |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `_process_next_for_sender` | `expected_now` | Pre-query fetches `c.max_new_dialogs_per_day` from DB; `_window_elapsed_fraction` computes fraction from real `datetime.now(timezone.utc)` | Yes — DB-backed campaign cap × wall-clock fraction × live jitter | ✓ FLOWING |
| `_process_next_for_sender` | `window_start_utc` | `_window_elapsed_fraction` converts `work_hour_start` in campaign timezone to UTC via `zoneinfo.ZoneInfo` | Yes — timezone-aware computation, injectable for tests | ✓ FLOWING |
| Pacing subquery | `COUNT(DISTINCT paced.recipient_phone)` | Counts actual `status='sent'` rows in `message_queue` since `:window_start_utc` | Yes — real DB rows, not mocked | ✓ FLOWING |

---

### Behavioral Spot-Checks

Step 7b: SKIPPED for live Telegram worker behaviour (requires running Docker stack and seeded DB). The full test suite was confirmed GREEN by the orchestrator (756 passed, 1 skipped, exit 0 via test-overlay). Individual test names are confirmed present in the file.

Key source-level spot-checks performed inline via `grep`:

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| `CAST(:expected_now AS DOUBLE PRECISION)` bigint-truncation fix present | `grep -n "CAST"` in queue.py line 483 | Found at line 483 | ✓ PASS |
| `FOR UPDATE OF mq SKIP LOCKED` preserved | grep line 487 | Found at line 487 | ✓ PASS |
| `LIMIT 8` preserved | grep line 486 | Found at line 486 | ✓ PASS |
| `_check_rate_limits` free of pacing refs | grep from line 540 onward for `window_start/expected_now/PACE_JITTER` | Zero matches | ✓ PASS |
| No new migration files from phase 13 | `ls migrations/` last file = `033_*` | Confirmed — no `034_*` | ✓ PASS |
| Phase 12 trailing-24h cap predicate untouched | grep `NOW() - INTERVAL '24 hours'` line 476 | Present at line 476 | ✓ PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| PACE-01 | 13-01, 13-02 | New pacing constants appended without modifying PROTECTED constants | ✓ SATISFIED | `PACE_JITTER_LOW = 0.75`, `PACE_JITTER_HIGH = 1.25` at lines 79-80; all 8 PROTECTED constants at original values; test `test_protected_constants_intact` asserts all |
| PACE-02 | 13-01, 13-02 | Pure `_window_elapsed_fraction` helper with injectable `now`, clamped [0,1], raw window width | ✓ SATISFIED | `_window_elapsed_fraction` lines 125-186; `max(0.0, min(1.0, frac))` at line 186; 6 boundary cases in `test_window_elapsed_fraction` |
| PACE-03 | 13-01, 13-02 | Expected-by-now pacing predicate beside Phase 12 cap in new-dialog branch; LIMIT 8 + SKIP LOCKED preserved; follow-ups bypass | ✓ SATISFIED | Lines 461-487: follow-up `EXISTS` branch OR new-dialog branch with both cap subquery and pacing subquery; `LIMIT 8` + `FOR UPDATE OF mq SKIP LOCKED` confirmed |
| PACE-04 | 13-01, 13-02 | Pacing numerator counts from today's window start (distinct from trailing-24h cap) | ✓ SATISFIED | Two separate correlated subqueries at lines 471-476 (trailing-24h) and 478-483 (window-start); `test_pace_counter_window_start` exercises the divergence |
| PACE-05 | 13-01, 13-02 | Structural clamp (no `max()` numeric expression); no crash on tight window | ✓ SATISFIED | No `max(target,` in pacing region; `width_h or 24` guards zero-width; `expected_now = 0.0` conservative default on no-campaign |
| PACE-06 | 13-01, 13-02 | Jitter via `random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)`; one item per call | ✓ SATISFIED | Line 417: `random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)`; `LIMIT 8` candidate pool but worker takes first matching item — `test_catchup_no_burst` asserts `len(captured["picked"]) <= 1` |
| PACE-07 | 13-01, 13-02 | Follow-ups never throttled by pacing; `_check_rate_limits` untouched and pacing-free | ✓ SATISFIED | Lines 462-468: follow-up `EXISTS` branch is separate from (and not subject to) the pacing subquery; `_check_rate_limits` lines 540+ have zero pacing references; `test_followup_bypasses_pacing` introspection guard asserts this |

All 7 requirements: ✓ SATISFIED. No orphaned requirements — REQUIREMENTS.md traceability table marks all PACE-01..07 as Complete.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tests/test_queue_even_pacing.py` | 330, 333, 591 | PACE-03 Case 1 and PACE-07 "blocked" assertion relies on real wall-clock fraction + un-mocked jitter; with `cap=1` and 1 opened dialog, the assertion breaks when frac > 0.8 (after ~19:12 UTC, jitter near 1.25 → expected_now > 1 → new dialog becomes eligible) | ⚠️ Warning | WR-01 from code review — latent flake, self-admitted in test comment. No production regression risk. |
| `tests/test_queue_even_pacing.py` | 345, 359 | PACE-03 Case 2 "allowed" assertion: `cap=1000`, full-day window, comment admits "except in the first seconds of the UTC day" — `frac ≈ 0` at UTC midnight makes `expected_now ≈ 0` → item NOT picked, assertion fails | ⚠️ Warning | WR-02 from code review — unguarded flake at UTC midnight boundary. No production regression risk. |
| `app/services/queue.py` | 379-398 vs 478-483 | Pre-query derives `expected_now` from one campaign (LIMIT 1) but pacing subquery is scoped `paced.campaign_id = mq.campaign_id` — cross-campaign mismatch when a sender is attached to multiple running campaigns | ⚠️ Warning | WR-03 from code review — documented as acceptable in source comment ("in practice a sender's queued items belong to its attached campaign"); not a crash, not a data-loss issue. Does not block the phase goal. |
| `app/services/queue.py` | 379-401 vs 420-494 | Pre-query and candidate SELECT run in separate `AsyncSessionLocal()` blocks (TOCTOU) | ⚠️ Warning | WR-04 from code review — bounded effect (at most ~1 extra new dialog per tick, self-correcting); same posture as Phase 12 accepted race. Does not block the phase goal. |
| `app/services/queue.py` | 182-183 | `work_hour_end <= work_hour_start` defensive branch is dead code under DB CHECK constraint | ℹ️ Info | IN-02 from code review — acknowledged dead code, not a bug. |
| `app/services/queue.py` | 416-418 | At exact window start (`frac == 0.0`), `expected_now = 0.0` → `COUNT(...) < 0` always FALSE → all new dialogs blocked until fraction advances (low-limit campaigns: up to ~hours of dead time) | ℹ️ Info | IN-01 from code review — correct-by-design behaviour but undocumented user-visible consequence. Not a blocker. |
| `tests/test_queue_even_pacing.py` | 390-399, 478-485 | PACE-04/05 `cur_hour`-based window guard handles 23→0 wrap but not minute-boundary race between test setup and worker `now_utc` re-read | ℹ️ Info | IN-03 from code review — low probability sub-second race, not a blocker. |

**Blocker anti-patterns:** 0

The 4 Warnings are advisory (code review WR-01..04); none blocks the phase goal. The test flakes (WR-01/WR-02) are latent and may affect CI reliability intermittently but do not indicate incorrect production behaviour. The recommended fixes from the review are captured here for future phases.

---

### Human Verification Required

None — phase 13 is a pure queue-internal logic change with no UI, no new API surface, and no external service integration. All verification was completed programmatically via source inspection and (per orchestrator confirmation) full test suite run.

---

## Gaps Summary

No gaps. All 11 observable truths are verified, all 7 PACE requirements are satisfied, both key links are wired, data flows from real DB-backed campaign rows through tested Python math into SQL bind parameters, and the change is correctly confined to `app/services/queue.py` (plus the test scaffold). The critical bigint-truncation bug caught by the orchestrator's post-merge gate (`CAST(:expected_now AS DOUBLE PRECISION)`, commit 3a4111e) is present and correct in the codebase.

The 4 advisory warnings from the code review are documented above but do not block goal achievement. They are candidates for a future maintenance pass.

---

_Verified: 2026-06-26T11:00:00Z_
_Verifier: Claude (gsd-verifier)_
