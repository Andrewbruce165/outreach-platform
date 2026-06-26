---
phase: 13-even-pacing-across-sending-window-smooth-new-dialog-distribu
reviewed: 2026-06-26T10:31:15Z
depth: standard
files_reviewed: 3
files_reviewed_list:
  - app/services/queue.py
  - tests/test_queue_even_pacing.py
  - tests/test_queue_new_dialog_limit.py
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 13: Code Review Report

**Reviewed:** 2026-06-26T10:31:15Z
**Depth:** standard
**Files Reviewed:** 3
**Status:** issues_found

## Summary

Phase 13 adds an "expected-by-now" even-pacing gate to `QueueWorker._process_next_for_sender`
in `app/services/queue.py`: a pure helper `_window_elapsed_fraction` computes how far
through today's campaign sending window we are, a per-`(sender,campaign)` pre-query reads
the campaign window + `max_new_dialogs_per_day`, and a correlated `COUNT(DISTINCT ...) < CAST(:expected_now AS DOUBLE PRECISION)`
subquery is ANDed beside the Phase 12 trailing-24h cap inside the new-dialog branch of the
candidate SELECT.

The **SQL safety is sound**: `:expected_now`, `:window_start_utc`, and `:sid` are all passed
as binds — no f-string interpolation in the pacing region, no injection surface. The explicit
`CAST(... AS DOUBLE PRECISION)` correctly avoids the bigint-truncation trap the docstring
describes. The PROTECTED rate constants and `_check_rate_limits` are genuinely untouched (the
new constants `PACE_JITTER_LOW/HIGH` are additive). The `SKIP LOCKED` / `LIMIT 8` /
`FOR UPDATE OF mq` invariants are preserved. `_window_elapsed_fraction` is defensively written
(invalid-tz guard, zero-width `or 24` guard, DST overshoot handled by the `[0,1]` clamp).

However, the **production gate is correct but the test suite has two genuinely flaky assertions**
caused by combining a real (un-mocked) `random.uniform` jitter with a wall-clock-dependent
`elapsed_fraction`. There is also a real (acknowledged) cross-campaign scoping mismatch when a
sender is attached to multiple running campaigns, and a start-of-window dead zone that is
correct-by-design but undocumented as a behavioural consequence. No Critical/security issues.

## Warnings

### WR-01: PACE-03 Case 1 (and PACE-07) "blocked" assertion is time + RNG flaky

**File:** `tests/test_queue_even_pacing.py:310-335` (and `tests/test_queue_even_pacing.py:589-613`)
**Issue:** Both tests set `cap=1`, seed one already-opened dialog, use a full-day window
(`work_hour_start=0, work_hour_end=24`), and assert the new dialog is **blocked**. Blocking
requires `count_opened (1) < expected_now` to be **false**, i.e. `expected_now <= 1`.

But `expected_now = c_cap * frac * random.uniform(0.75, 1.25)` is computed with the **real,
un-mocked** jitter (`queue.py:416-418`) and the **real wall-clock** `frac`. With `cap=1`,
`expected_now = frac * jitter`. For `jitter` near `1.25`, `expected_now > 1` whenever
`frac > 0.8` — i.e. whenever the suite runs after ~19:12 UTC. At that point `1 < expected_now`
is TRUE, the new dialog becomes eligible, and `assert blocked_qid not in captured["picked"]`
(line 330) / `assert ... == "pending"` (line 333) **fail nondeterministically**.

This is the classic reviewer trap: the test currently passes (CI runs earlier in the UTC day
or jitter rolls low), but it encodes a flake that will fire intermittently in production CI.
**Fix:** make the gate deterministic instead of relying on wall-clock + RNG. Either (a) patch
the jitter to a fixed value and freeze/inject a fraction for these cases, e.g.
`patch.object(queue_module.random, "uniform", return_value=1.0)` plus a tiny window whose
fraction is bounded, or (b) seed enough already-opened dialogs that `count_opened` exceeds the
maximum possible `expected_now` (`cap * 1.0 * PACE_JITTER_HIGH = cap * 1.25`), e.g. with `cap=1`
seed `count_opened >= 2` so `2 < 1.25` can never be true regardless of frac/jitter:
```python
# deterministic block: ensure count_opened > cap * PACE_JITTER_HIGH
await _set_cap(async_db_session, campaign_id=cid, cap=1)
await _seed_sent_dialog(..., recipient_phone="+79991110001")
await _seed_sent_dialog(..., recipient_phone="+79991110009")  # count_opened = 2 > 1.25
```

### WR-02: PACE-03 Case 2 "allowed" assertion fails in the first seconds of the UTC day

**File:** `tests/test_queue_even_pacing.py:337-362`
**Issue:** The "UNDER expected ⇒ allowed" case (`cap=1000`, `count_opened=0`, window 0..24)
asserts the item IS picked (line 359). Eligibility requires `0 < expected_now`, and
`expected_now = 1000 * frac * jitter`. At the very start of the UTC day `frac ≈ 0`, so
`expected_now ≈ 0` and `0 < 0` is FALSE → the item is **not** picked and the assertion fails.
The test's own comment (line 345) admits "except in the first seconds of the UTC day" — that is
an unguarded flake window, not a guarantee. (This is the same start-of-window dead zone as
IN-01, here surfacing as a test-reliability defect.)
**Fix:** Use a window whose fraction cannot be ~0 at test time (e.g. `work_hour_start = cur_hour - 1`,
`work_hour_end = cur_hour + 1` with the UTC-timezone override used in PACE-04/05 so the window
opened ~an hour ago), or inject a fixed `now`/fraction. Avoid asserting "allowed" on a window
that can legitimately produce `expected_now == 0`.

### WR-03: `expected_now` is computed for one campaign but applied to other campaigns' new dialogs

**File:** `app/services/queue.py:379-418` (pre-query) vs `app/services/queue.py:478-483` (subquery)
**Issue:** The pacing pre-query picks a SINGLE row's campaign (`ORDER BY mq.priority DESC,
mq.created_at ASC LIMIT 1`) and derives `expected_now` from THAT campaign's `c_cap`, `c_tz`,
`work_hour_start/end`. The candidate SELECT's pace subquery, however, is correctly scoped
per-row (`paced.campaign_id = mq.campaign_id`, line 481) and compares each campaign's own count
against the **single scalar** `:expected_now`. `campaign_senders` is a true many-to-many
(`PRIMARY KEY (campaign_id, sender_id)`, `migrations/016_phase4.sql:56-62`), so a sender can be
attached to multiple running campaigns simultaneously. When that happens, a new-dialog item of
campaign B is paced against campaign A's limit/window fraction — wrong cap, wrong window, wrong
fraction. The docstring (lines 370-375) acknowledges this as "acceptable," but it is a genuine
correctness gap, not merely cosmetic: a small-limit campaign B could be over-opened using a
large-limit campaign A's `expected_now`, or vice-versa.
**Fix:** Either correlate `expected_now` per campaign in SQL (compute the fraction-derived
ceiling as a per-row expression keyed on `c.id`/`c.max_new_dialogs_per_day`), or restrict the
pre-query + candidate SELECT to a single campaign per call (pace and pick within the same
`mq.campaign_id`). At minimum, document the multi-campaign case as a known limitation in
`CLAUDE.md` with the failure mode (cross-campaign over/under-pacing) spelled out, not just
"acceptable."

### WR-04: Pacing pre-query and candidate SELECT run in separate transactions (TOCTOU)

**File:** `app/services/queue.py:379-398` and `app/services/queue.py:420-494`
**Issue:** `expected_now` / `window_start_utc` are computed from a pre-query in one
`AsyncSessionLocal()` block (line 379), then the candidate SELECT runs in a **separate** session
(line 420). Between the two, another worker tick (or the same worker on the next iteration) may
send messages for this sender, advancing the pace count that the subquery re-reads at line
478-483 — but `expected_now` is already frozen from the earlier snapshot. The docstring calls
out a "benign double-open race" under READ COMMITTED, which covers the in-statement race, but
the **two-statement gap** additionally lets `expected_now` and the counted state drift apart.
Effect is bounded (at worst a small over/under-open, self-correcting next tick), so this is a
WARNING not a BLOCKER, but it is a real consistency seam.
**Fix:** If tightening is desired, compute the fraction-derived ceiling inside the candidate
SELECT's transaction (single round-trip) so the snapshot used for `expected_now` and the
counted rows are read under the same MVCC snapshot. Otherwise, document the two-statement
TOCTOU explicitly alongside the existing in-statement race note.

## Info

### IN-01: New dialogs are unconditionally blocked at the exact window start (frac == 0)

**File:** `app/services/queue.py:416-418`, `app/services/queue.py:478-483`
**Issue:** At the instant the window opens, `frac = 0.0` → `expected_now = c_cap * 0 * jitter = 0.0`.
The new-dialog predicate `count(...) < 0.0` is always FALSE (a `COUNT(DISTINCT)` is `>= 0`), so
the very first new dialog of the day cannot be opened until `frac` advances enough that
`expected_now >= some positive value > 0` for at least one dialog. For a window of width `W`
hours and limit `L`, the first opening becomes possible only after roughly
`(1 / (L * jitter)) * W` hours. With `L=50`, `W=11h`, that is ~13 minutes — fine; with a small
limit (`L=2`, `W=11h`) it is ~3.7 hours of dead time at the start of the window. This is
correct-by-design ("ramp up from zero") but is an emergent behaviour that is not documented as a
user-visible consequence, and it interacts with WR-02's flake.
**Fix:** Document the start-of-window ramp (and its dependence on `max_new_dialogs_per_day`) in
`CLAUDE.md`/UI-SPEC so operators understand why a freshly-opened low-limit campaign sends
nothing for the first part of the window. Optionally add a `ceil`/`+1` floor so at least one new
dialog can open shortly after the window starts, if product wants a non-zero ramp.

### IN-02: `_window_elapsed_fraction` post-midnight wrap branch is dead code under the DB CHECK

**File:** `app/services/queue.py:182-183`
**Issue:** The wrap-handling branch (`if work_hour_end <= work_hour_start and local.hour < work_hour_end`)
can never trigger with real campaign data: the schema enforces
`CHECK (work_hour_start >= 0 AND work_hour_end <= 24 AND work_hour_start < work_hour_end)`
(`migrations/016_phase4.sql:43`, `migrations/019_schema_drift_fix.sql:62`), so
`work_hour_end <= work_hour_start` is structurally impossible. The code comments already label
it "defensive only," so this is acknowledged dead code, not a bug. Likewise the `or 24`
zero-width guard at line 173 only matters for the directly-unit-tested degenerate case
(PACE-02 (f)), never for DB rows.
**Fix:** Keep as defensive, but consider a one-line comment cross-referencing the DB CHECK so a
future reader knows the branch is unreachable from `_process_next_for_sender` and exists only
for the pure-function unit test / future wrap support.

### IN-03: PACE-04/05 hour-guard handles 23→0 wrap but not the minute-boundary race

**File:** `tests/test_queue_even_pacing.py:394-399`, `tests/test_queue_even_pacing.py:478-485`
**Issue:** Both tests set `work_hour_start = cur_hour`, `work_hour_end = cur_hour + 1`, and force
`timezone = 'UTC'`, asserting "window start ≈ top of the current UTC hour (minutes ago)." If the
test crosses an hour boundary between `now = datetime.now(timezone.utc)` (captured at line
390/478) and the worker's own `now_utc = datetime.now(timezone.utc)` (re-read at `queue.py:376`),
the window the worker computes can differ from the one the test seeded around, and the
`pace_count == 0` divergence guard (PACE-04 line 443) or the `<= 1` picked assertion could
shift. Low probability (sub-second window), but it is an unguarded timing seam in an otherwise
carefully-determinized file.
**Fix:** Pin a single `now` and inject it (the helper already accepts `now=`), or assert on a
window that started comfortably before the current minute (e.g. `work_hour_start = cur_hour - 1`)
so an hour-rollover during the test cannot move the window boundary.

---

_Reviewed: 2026-06-26T10:31:15Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
