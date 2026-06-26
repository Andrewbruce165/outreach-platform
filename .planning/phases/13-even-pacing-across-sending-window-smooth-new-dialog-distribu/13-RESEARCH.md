# Phase 13: Even pacing across sending window (smooth new-dialog distribution) - Research

**Researched:** 2026-06-26
**Domain:** Queue-worker pacing logic — derived target interval over a per-campaign sending window, integrated into the existing `_process_next_for_sender` item-selection SQL. Pure Python/PostgreSQL, no external libs.
**Confidence:** HIGH (single-file brownfield change; all integration points read directly from source; no new dependencies)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Denominator = RAW window `(work_hour_end − work_hour_start)`, no long-pause subtraction. Target interval = `window_width / max_new_dialogs_per_day`. (11h / 50 ≈ 13 min.)
- **D-02:** `work_days_mask` does NOT affect the denominator — daily limit; the mask only decides whether we send at all today (existing working-window logic).
- **D-03:** **Interval floor** — target interval never drops below the base 20–55s gate: effective min gap for a new dialog = `max(target, base_per_send)`. If the window physically cannot fit the limit at this floor, the limit simply **is not reached** (safety over volume) — a deliberate consequence.
- **D-04:** **Hybrid catch-up with burst-cap.** Catch-up exists but NOT faster than existing gates: base 20–55s per-send, `4/min`, `15 new contacts/hour`, `MAX_NEW_CONTACTS_PER_HOUR`. No separate burst mechanism — existing gates ARE the catch-up speed ceiling. Full burst (ignore interval) — REJECTED. "No catch-up" (hard min-gap) — REJECTED.
- **D-05:** Pacing mechanic = **"expected-by-now" model**, NOT a fixed min-gap: `expected = (elapsed_window / full_window_today) × max_new_dialogs_per_day`; if actual opened < expected, a new dialog is eligible now (under base gates), else wait. (Exact SQL-shape = planner's discretion.)
- **D-06:** The pacing-check NUMERATOR ("how many new opened so far") counts **from the start of TODAY's campaign window**, NOT trailing-24h. This DIFFERS from Phase 12's cap counter (Phase 12 D-05 = trailing-24h rolling). Two distinct counters — do not conflate.
- **D-07:** **Each sender paces independently.** Phase 12 made the limit per-(sender,campaign); accounts are already isolated. Roadmap "pool batching" reduces to per-sender pacing — no explicit batch groups, no new coordination layer.
- **D-08:** **Jitter is MANDATORY, not discretion.** Floating target interval (random shift, e.g. ±20–30%) so openings don't form a machine grid. Goal: even during catch-up, multiple new dialogs must NOT fire simultaneously / within a couple seconds.
- **D-09:** **Auto-derived pacing, no new fields.** Even pacing always on; interval derived from window + `max_new_dialogs_per_day`. NO new DB columns / API fields / UI / openapi regen. All logic inside the queue-worker. Per-campaign even/burst mode — REJECTED. Global feature-flag — considered, not chosen (can return later for rollback).
- **D-10:** Target interval is an **additional gate ON TOP** of the existing per-send interval, taken as `max(target_new_dialog, base_20–55s)`. Base interval, fatigue factor, and long pauses stay untouched (CLAUDE.md guard); the new pacing is a separate predicate for new-dialog items only — follow-ups never see it.

### Claude's Discretion
- Exact SQL-shape of the pacing check (D-05/D-06): correlated subquery vs CTE vs window-count — must preserve `LIMIT 8` / `FOR UPDATE OF mq SKIP LOCKED` and not break the Phase 4 D-15 per-campaign working-window re-check or the Phase 12 new-dialog cap predicate.
- Exact jitter magnitude (D-08), within roadmap's "floating intervals" (guideline ±20–30%).
- Where to place new pacing constants (modular vs inline), following the existing rate-const block.
- How exactly to compute "elapsed_window / full_window" with per-campaign timezone and midnight-crossing, reusing `_campaign_in_working_window`.

### Deferred Ideas (OUT OF SCOPE)
- Per-campaign even/burst mode — rejected this phase (extra migration+API+UI).
- Global on/off feature-flag — emergency-rollback only, not in scope (can add later).
- Analytics/dashboard "how evenly the pace runs" — separate feature.
- Long-pause accounting in the window denominator (~0.65×) — rejected for simplicity (raw window, D-01); revisit only if the limit tail is systematically under-delivered in practice.
</user_constraints>

## Project Constraints (from CLAUDE.md)

- **PROTECTED empirical constants — DO NOT MODIFY:** `MIN_SEND_INTERVAL=20`, `MAX_SEND_INTERVAL=55`, `SEND_INTERVAL_FATIGUE=0.5`, `LONG_PAUSE_*`, `MAX_NEW_CONTACTS_PER_HOUR=15`, per-sender `rate_per_min/hour/day` (4/20/150), `FLOOD_HARD_THRESHOLD`. The phase ADDS a new upper pacing gate; it never touches the rate-limit / debounce / long-pause / flood logic.
- **Async everywhere** — all DB access via `async`/`await` + `AsyncSession`. No `time.sleep()`, no sync `requests`, no `print()` (use `logging`).
- **Tests run ONLY via test-overlay:** `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. NEVER `docker compose run --rm api pytest` (conftest guard DROP SCHEMA on prod). NEVER `down -v` (deletes prod `postgres_data`).
- **Phase is `queue.py`-only** (D-09): no migration, no model change, no schema, no API, no openapi regen, no frontend. Reads `campaigns.work_hour_start/end`, `work_days_mask`, `timezone`, `max_new_dialogs_per_day` (all already present from Phase 4/12).
- **Russian** for discussion; English for code and commits.

<phase_requirements>
## Phase Requirements (PACE-*) — candidate decomposition

Mirrors the NDLG-01..06 style (REQUIREMENTS.md:143). Phase 13 is logic-only so it has fewer, denser requirements. Planner should adopt/refine these and register them in REQUIREMENTS.md.

| ID | Description | Decisions | Verification |
|----|-------------|-----------|--------------|
| **PACE-01** | New pacing constants (jitter fraction; any helper bounds) added to the rate-config block (`queue.py:39-69`) without modifying any PROTECTED constant; source-introspection guard asserts `MIN/MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `MAX_NEW_CONTACTS_PER_HOUR` unchanged | D-08, discretion | unit: `inspect.getsource` assertions (mirror `test_check_rate_limits_untouched`) |
| **PACE-02** | "Window start today" + "elapsed fraction" computed per-campaign timezone from `work_hour_start/end` and `now`, correctly for midnight-crossing windows; clamped to `[0,1]` (never negative, never >1) — exposed as a Python helper next to `_campaign_in_working_window` | D-01, D-02, D-05, D-06, discretion | unit: pure-function table tests across tz / boundary / midnight-cross / DST inputs |
| **PACE-03** | Pacing predicate added to the `_process_next_for_sender` candidate SELECT: a new-dialog item is eligible iff `count_opened_since_window_start < ceil/floor(expected)`; follow-up / re-contact items bypass it entirely; `LIMIT 8` + `FOR UPDATE OF mq SKIP LOCKED` + Phase 4 D-15 working-window re-check + Phase 12 trailing-24h cap predicate all preserved | D-05, D-06, D-07, D-09, D-10 | integration: extend `test_queue_new_dialog_limit.py` style — under cap but over expected ⇒ not picked; under expected ⇒ picked; follow-up always picked |
| **PACE-04** | Pacing numerator counts new dialogs opened **since the start of today's window** (NOT trailing-24h) — a distinct counter from the Phase 12 cap; verified by a case where the two counters diverge (e.g. dialogs opened yesterday count toward 24h cap but NOT toward today's pace) | D-06 | integration: seed `sent` rows before vs after window start; assert pace counter ignores pre-window rows while the cap counter does not |
| **PACE-05** | Target-interval clamp `max(target, base_20–55s)` applied so a new dialog also respects a per-send min gap; when the window cannot fit the limit at the floor, the limit is simply not reached (no special-casing, no error) | D-03, D-10 | integration: narrow window + high limit ⇒ openings spaced at the base floor, total < limit; no crash |
| **PACE-06** | Jitter applied to the target interval / eligibility so that, even during catch-up, ≥2 new-dialog items in one tick (`LIMIT 8`) do NOT all fire within a couple seconds — base per-send interval + jitter de-grids openings; follow-ups unaffected | D-04, D-08 | integration: catch-up scenario (0 opened, large expected) ⇒ at most 1 new dialog leaves per tick / per base-interval; jitter present in source |
| **PACE-07** | Follow-ups and AI replies are NEVER throttled by pacing (only cold first-touches via the queue are paced); `_check_rate_limits` (4/20/150 + 15/h) untouched (not where pacing lives) | D-07, D-10, CLAUDE.md guard | integration: follow-up item picked while pacing would block a new dialog; introspection guard on `_check_rate_limits` |

> No API/UI/migration requirements — D-09 forbids new surface. There is no PACE analog to NDLG-03/04/05/06.
</phase_requirements>

## Summary

Phase 13 adds a single new behaviour to the queue worker: **new cold dialogs are released evenly across the campaign's daily sending window instead of in a front-loaded burst.** The mechanic is an "expected-by-now" gate (D-05): at any instant, `expected = (elapsed_fraction_of_today's_window) × max_new_dialogs_per_day`; a new-dialog item is eligible only if the count of new dialogs opened **since the start of today's window** is below that expectation. Catch-up is implicit and naturally bounded — there is no burst path, the existing 20–55s / 4-per-min / 15-per-hour gates cap how fast a behind-schedule sender can recover (D-04). Jitter on the target interval (D-08, mandatory) prevents the `LIMIT 8` candidate batch from firing several new dialogs within seconds during catch-up.

This is a `queue.py`-only change (D-09): no migration, no model, no API, no UI. It reads columns that already exist (`work_hour_start/end`, `work_days_mask`, `timezone` from Phase 4; `max_new_dialogs_per_day` from Phase 12). The integration point is the same SELECT in `_process_next_for_sender` (`queue.py:300-336`) that Phase 12 modified — the pacing predicate sits **beside** the Phase 12 cap predicate inside the `(follow-up OR new-dialog-allowed)` clause, and an interval clamp sits **on top of** the base interval gate (D-10).

**Primary recommendation:** Compute "elapsed fraction of today's window" in **Python** (reusing the `zoneinfo` logic already proven in `_campaign_in_working_window` — `zoneinfo` is awkward in SQL, which is exactly why Phase 4 kept the window check Python-side) and pass `max_new_dialogs_per_day × elapsed_fraction` as a single bind parameter into the SELECT. The pacing numerator (new dialogs opened since window-start) is a correlated `COUNT(DISTINCT recipient_phone)` subquery cloned from the Phase 12 cap subquery but with a different time floor (`window_start_utc` bind param instead of `NOW() - INTERVAL '24 hours'`). This keeps `zoneinfo`/DST math in tested Python, keeps the SQL shape identical to the proven Phase 12 pattern, and preserves `LIMIT 8` / `FOR UPDATE OF mq SKIP LOCKED`.

## Standard Stack

No new dependencies. Everything is in the stdlib + existing project libs.

### Core (already present)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `zoneinfo` (stdlib) | py3.11 | Per-campaign tz, DST-correct window math | Already used by `_campaign_in_working_window`; battle-tested in this file |
| `random` (stdlib) | py3.11 | Jitter (`random.uniform`) | Existing idiom for every interval/pause in `queue.py` |
| `datetime` (stdlib) | py3.11 | Window-start / elapsed computation | Already imported |
| SQLAlchemy core `text()` | 2.0.25 | Raw parametrised SQL in the SELECT | Existing pattern throughout `queue.py` |
| asyncpg | 0.29.0 | async PG driver | Existing |

**Installation:** none.

**Version verification:** Confirmed from `requirements.txt` — `sqlalchemy==2.0.25`, `asyncpg==0.29.0`, Python 3.11 (`zoneinfo` is stdlib). No package additions for this phase.

## Architecture Patterns

### Where the change lands (single file)
```
app/services/queue.py
├── :39-69   rate-config block        # PACE-01: add jitter constant(s) here (PROTECTED block — append only, change nothing)
├── :73-111  _campaign_in_working_window  # READ pattern; clone its zoneinfo/window logic into a new helper
├── (new)    _window_elapsed_fraction(...)  # PACE-02: pure function → (window_start_utc, elapsed_fraction in [0,1])
├── :300-336 _process_next_for_sender SELECT  # PACE-03/04/06/07: add pacing subquery beside Phase 12 cap predicate
└── :504-528 base interval gate (_check_rate_limits)  # PACE-05: clamp target on top — see note below
```

### Pattern 1: Python-computed window math, SQL-consumed counter (RECOMMENDED)
**What:** Compute `window_start_utc` (the UTC instant of today's `work_hour_start` in the campaign tz) and `elapsed_fraction = clamp((now − window_start)/(window_width), 0, 1)` in a new pure Python helper. Then `expected = max_new_dialogs_per_day × elapsed_fraction`. Pass `window_start_utc` and `expected` (or its floor/ceil) as bind params into the SELECT.

**When to use:** Always here. `zoneinfo` + DST + midnight-cross is the exact reason Phase 4 (D-15) kept the working-window decision in Python (`queue.py:170` comment: "zoneinfo is awkward in SQL"). Replicating that math in SQL with `AT TIME ZONE` would be a second, untested source of truth.

**Sketch (planner refines):**
```python
# Source: derived from _campaign_in_working_window (queue.py:73-111)
def _window_elapsed_fraction(*, campaign_tz, work_hour_start, work_hour_end,
                             now=None) -> tuple[datetime, float]:
    """Return (window_start_utc, elapsed_fraction in [0,1]) for TODAY's window.
    Raw window width per D-01 (no long-pause subtraction). Handles midnight cross.
    Caller must already know now is inside the window (worker only paces in-window items)."""
    if now is None:
        now = datetime.now(timezone.utc)
    tz = zoneinfo.ZoneInfo(campaign_tz)
    local = now.astimezone(tz)
    # window width in hours (raw, D-01); handle wrap past midnight (end <= start ⇒ +24)
    width_h = (work_hour_end - work_hour_start) % 24 or 24
    # local date of the window START (if past midnight and before end, start was yesterday)
    start_local = local.replace(hour=work_hour_start, minute=0, second=0, microsecond=0)
    if work_hour_end <= work_hour_start and local.hour < work_hour_end:
        start_local -= timedelta(days=1)          # we're in the post-midnight tail
    window_start_utc = start_local.astimezone(timezone.utc)
    elapsed = (now - window_start_utc).total_seconds()
    frac = elapsed / (width_h * 3600)
    return window_start_utc, max(0.0, min(1.0, frac))   # clamp — never <0 or >1
```
**Landmines (call out in tests):** `elapsed` can go slightly negative on the exact boundary or DST spring-forward; the `clamp` makes it safe → `expected ≈ 0` → no new dialogs yet (correct, conservative). `width_h or 24` guards a degenerate `start==end` window.

### Pattern 2: Pacing predicate beside the Phase 12 cap (RECOMMENDED SQL shape)
**What:** The Phase 12 SELECT already has a `(follow-up EXISTS … OR new-dialog-under-cap …)` clause (`queue.py:317-333`). Add the pacing condition as an **additional AND** inside the new-dialog branch — a new dialog must be both under the 24h cap (Phase 12) **and** under the expected-by-now count (Phase 13).

```sql
-- Source: extends queue.py:317-333 (Phase 12 predicate). New lines marked +.
AND (
  /* follow-up — never throttled (D-07/D-10) */
  EXISTS (SELECT 1 FROM message_queue prior
            WHERE prior.campaign_id = mq.campaign_id
              AND prior.recipient_phone = mq.recipient_phone
              AND prior.status = 'sent')
  OR
  /* new dialog: Phase 12 trailing-24h cap … */
  ( (SELECT COUNT(DISTINCT opened.recipient_phone) FROM message_queue opened
       WHERE opened.sender_id = mq.sender_id AND opened.campaign_id = mq.campaign_id
         AND opened.status = 'sent'
         AND opened.finished_at >= NOW() - INTERVAL '24 hours') < c.max_new_dialogs_per_day
+   /* … AND Phase 13 expected-by-now pace, counted from TODAY's window start (D-06) */
+   AND (SELECT COUNT(DISTINCT paced.recipient_phone) FROM message_queue paced
+         WHERE paced.sender_id = mq.sender_id AND paced.campaign_id = mq.campaign_id
+           AND paced.status = 'sent'
+           AND paced.finished_at >= :window_start_utc) < :expected_now
  )
)
ORDER BY mq.priority DESC, mq.created_at ASC
LIMIT 8
FOR UPDATE OF mq SKIP LOCKED
```
**Why this shape:** identical correlated-subquery form as Phase 12 (planner already has a tested template), preserves `LIMIT 8` / `FOR UPDATE OF mq SKIP LOCKED` (the subqueries don't lock — `FOR UPDATE OF mq` only locks `mq`, and read subqueries against the same table under READ COMMITTED do not escalate the row lock or break SKIP LOCKED). Two binds added: `:window_start_utc`, `:expected_now`.

**Locking / read-consistency note (answers the brief's Q1 concern):** Under PostgreSQL READ COMMITTED (the default), each `COUNT` subquery and the candidate rows are evaluated against the same statement snapshot, so the count is consistent within the statement. `FOR UPDATE OF mq SKIP LOCKED` locks only matched `mq` rows; correlated read-only subqueries against `message_queue` do **not** acquire row locks and do **not** interfere with another worker's SKIP LOCKED. There is no lock escalation in PostgreSQL (it's row-level MVCC). The benign race — two parallel workers both seeing `count < expected` and each opening one dialog — at worst opens ~1 extra new dialog per tick across senders; harmless and self-correcting (next tick recomputes). This is exactly the same benign-race posture Phase 12 already accepted.

### Pattern 3: Interval clamp on top of base (D-10, PACE-05)
**What:** The base interval gate lives at `queue.py:504-528` inside `_check_rate_limits` (the `required_interval` block). But `_check_rate_limits` is per-tick and per-sender — it does NOT distinguish new-dialog from follow-up items, and the Phase 12/13 predicates correctly live in the **item SELECT**, not in `_check_rate_limits` (D-07). So `max(target, base)` is best realised **structurally** rather than by editing the base gate:

- The base 20–55s interval already enforces the per-send floor for ALL items (so the floor side of `max()` is automatic and untouched — CLAUDE.md guard satisfied).
- The "target ≥ base" upper side is realised by the pacing predicate itself: if the sender is already at/above `expected`, no new dialog is selected → the next new dialog waits until `expected` ticks up, which (by construction `target = window/limit`) is the target interval. When `target < base` (narrow window / high limit, D-03), the base interval gate is the binding constraint and the limit is simply not reached — **no special-casing needed**, which the brief asks to confirm: the predicate naturally yields D-03's "safety over volume" because base interval throttling slows opens below the target rate and `expected` is reached late or never.

**Conclusion for the planner:** Do NOT add a numeric `max(target, base)` computation. The clamp emerges from (a) base interval untouched + (b) the expected-by-now predicate. The only thing to verify is that **no code path lets a new dialog bypass the base interval** — and it can't, because `_check_rate_limits` (with the base gate) runs before item selection in `_process_next_for_sender` (`queue.py:276`).

### Pattern 4: Jitter (D-08, PACE-06)
**What:** Jitter must ensure that even when many new-dialog items are eligible (catch-up, `count << expected`), they don't all leave within seconds. Two complementary mechanisms:
1. **Base interval already gates per-send to 20–55s** (random per call, `queue.py:520`) — between two *sends* by the same sender there is always ≥20s. Because `_process_next_for_sender` processes **one** item per call and `_check_rate_limits` re-checks the interval each time, two new dialogs from the same sender are already ≥20–55s apart. The `LIMIT 8` batch does NOT fire 8 sends — it's a candidate pool from which exactly one item is picked (`item_id = r.item_id; break` at `queue.py:355`). **This is the key realisation: jitter at the item level is not needed to prevent same-tick multi-fire — the worker only sends one item per `_process_next_for_sender` call.**
2. **Jitter on the expected threshold / target** (the mandated D-08 floating interval) prevents the *grid* pattern across the day: randomise `expected_now` by ±20–30% (e.g. `expected * random.uniform(0.7, 1.3)`) **per evaluation**, so the eligibility boundary floats and openings don't land on exact `window/limit` ticks. Apply it in Python where `expected_now` is computed (before binding), using the existing `random.uniform` idiom.

**Recommendation:** Apply jitter as a ±20–30% multiplier on `expected_now` (or equivalently on the derived target interval) in Python, computed fresh each `_process_next_for_sender` call. Document in source that same-sender multi-fire is already prevented by the base interval + one-item-per-call design; jitter's job is grid-breaking (D-08's "machine grid" concern), not burst prevention.

### Anti-Patterns to Avoid
- **Computing window-start/elapsed in SQL with `AT TIME ZONE`:** duplicates `zoneinfo`/DST logic, untested, diverges from the Python source of truth Phase 4 deliberately chose. Compute in Python, bind the result.
- **Putting pacing in `_check_rate_limits`:** it's a per-tick `return False` gate → would throttle follow-ups too (violates D-07/D-10). Same trap Phase 12 avoided (D-07).
- **Adding a numeric `max(target, base)`:** unnecessary and risks touching the PROTECTED base gate. The clamp emerges structurally (Pattern 3).
- **Jittering by sleeping in the worker loop:** no `time.sleep`/extra `asyncio.sleep` for pacing — pacing is a SELECT predicate, not a sleep. (The existing long-pause `asyncio.sleep` is separate and untouched.)
- **Counting pending/processing in the pace numerator:** count only `status='sent'` (mirror Phase 12 D-04) — pace reflects dialogs actually opened.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-campaign tz + DST + midnight window | Custom offset arithmetic | Clone `_campaign_in_working_window`'s `zoneinfo.ZoneInfo(...).astimezone()` approach | Already correct, already in this file, DST-safe |
| New-dialog distinct count | New table / new column | Clone the Phase 12 `COUNT(DISTINCT recipient_phone)` subquery with a different time floor | Proven shape, preserves SKIP LOCKED |
| Per-send min gap / burst prevention | New rate limiter | Existing base 20–55s gate + one-item-per-call worker | Already enforces ≥20s between sends; PROTECTED, do not re-implement |
| Catch-up rate ceiling | Custom burst budget | Existing 4/min, 15/h, 150/day gates | D-04: existing gates ARE the catch-up ceiling |
| Randomisation | New jitter framework | `random.uniform` (existing idiom) | Used everywhere in `queue.py` already |

**Key insight:** Phase 13 is almost entirely *composition* of mechanisms that already exist. The only genuinely new code is (a) one pure Python helper for elapsed-fraction, and (b) one correlated subquery + two bind params in the existing SELECT, plus a jitter multiplier. Everything else is reuse.

## Common Pitfalls

### Pitfall 1: Conflating the two counters (Phase 12 24h cap vs Phase 13 window-start pace)
**What goes wrong:** Using `NOW() - INTERVAL '24 hours'` as the pacing floor (copy-paste from Phase 12).
**Why it happens:** The subquery is cloned from Phase 12 which uses trailing-24h (D-05 of Phase 12). D-06 of Phase 13 explicitly requires **window-start of today**.
**How to avoid:** Pass a distinct `:window_start_utc` bind; PACE-04 must include a test where a dialog opened *yesterday* counts toward the 24h cap but NOT toward today's pace.
**Warning signs:** Early-morning openings blocked because yesterday-evening's dialogs inflate the pace numerator.

### Pitfall 2: `elapsed_fraction` going negative or >1 at boundaries / DST
**What goes wrong:** Negative elapsed (just-opened window, or spring-forward) → negative `expected` → could underflow; >1 (just before close) is fine but should saturate at the full limit.
**Why it happens:** `(now − window_start)` is not guaranteed inside `[0, width]`; DST shifts the wall-clock width.
**How to avoid:** Clamp to `[0,1]` (Pattern 1). PACE-02 tests boundary/midnight/DST inputs.
**Warning signs:** First-minute-of-window opens nothing forever, or limit overshoots.

### Pitfall 3: Midnight-crossing window (e.g. 22:00–06:00) computing yesterday's start wrong
**What goes wrong:** `window_start` always set to today's `work_hour_start` even when `now` is in the post-midnight tail → elapsed wildly off.
**Why it happens:** Naive `local.replace(hour=work_hour_start)` ignores the wrap.
**How to avoid:** The `if work_hour_end <= work_hour_start and local.hour < work_hour_end: start -= 1 day` branch (Pattern 1). Note: `_campaign_in_working_window` (queue.py:109) uses a simple `start <= hour < end` which does NOT currently support wrap windows — confirm with the planner whether any campaign actually uses a wrap window; if none do, the wrap branch is defensive only. **OPEN QUESTION below.**
**Warning signs:** Overnight campaigns pace incorrectly.

### Pitfall 4: Worker `now` is not injectable → time-based tests are nondeterministic
**What goes wrong:** `_process_next_for_sender` and `_campaign_in_working_window` call `datetime.now(timezone.utc)` internally; the pacing helper would too. Tests can't freeze time (no `freezegun` in the project), so an "expected-by-now" assertion that depends on wall-clock fraction is flaky.
**Why it happens:** No time-injection seam in the worker; existing tests sidestep it by using `work_hour_start=0, work_hour_end=24` (fraction is whatever the wall clock says, but the *cap* tests don't depend on fraction).
**How to avoid:** Make `_window_elapsed_fraction(now=...)` accept an injectable `now` (like `_campaign_in_working_window` already does) and unit-test it as a pure function (PACE-02). For the integration test (PACE-03/04), drive the *outcome* by seeding the count and choosing window bounds so the expected fraction is deterministic enough: e.g. `work_hour_start=0, work_hour_end=24` makes `expected = limit × (seconds_since_midnight_UTC / 86400)` — still wall-clock dependent. **Better:** seed so the test is robust to fraction — e.g. set `max_new_dialogs_per_day` high and `window` tiny-elapsed so `expected≈0` ⇒ "over expected ⇒ blocked"; or seed `count=0` with a wide-elapsed window so `expected≥1` ⇒ "under expected ⇒ allowed". See Validation Architecture for the deterministic recipe.
**Warning signs:** Tests pass at noon, fail at midnight.

### Pitfall 5: Breaking the Phase 4 / Phase 12 invariants in the shared SELECT
**What goes wrong:** Refactoring the SELECT (e.g. to a CTE) drops `FOR UPDATE OF mq SKIP LOCKED` or the per-campaign working-window re-check at `queue.py:344-356`.
**Why it happens:** CTE + `FOR UPDATE` has PostgreSQL restrictions (`FOR UPDATE` not allowed in some CTE positions).
**How to avoid:** Keep the correlated-subquery shape (Pattern 2) — no CTE. The Python post-filter loop (`queue.py:344-356`) and `LIMIT 8` stay byte-for-byte. Add a regression test that the SELECT still has `FOR UPDATE OF mq SKIP LOCKED` and `LIMIT 8` (source introspection, like `test_check_rate_limits_untouched`).
**Warning signs:** Double-processing under the parallel worker; working-window leak.

## Code Examples

### Eligibility outcome the planner must produce (conceptual)
```python
# Source: composition of queue.py:276 (rate gate) + :300-336 (item SELECT)
# 1. _check_rate_limits → enforces base 20–55s + 4/20/150 + 15/h (UNTOUCHED, D-09)
# 2. item SELECT → new-dialog item eligible iff:
#       NOT follow-up  AND  count_24h < max_new_dialogs_per_day   (Phase 12)
#                      AND  count_since_window_start < expected_now (Phase 13, D-05/D-06)
#    where expected_now = max_new_dialogs_per_day
#                         * clamp(elapsed/width, 0, 1)
#                         * random.uniform(0.7, 1.3)   # jitter, D-08
```

### Constant placement (PACE-01) — append to the rate-config block (do NOT edit existing lines)
```python
# Source: append after queue.py:69, following the existing block style
# ── Even-pacing config (Phase 13) ───────────────────────────────────────────
# Jitter on the derived target interval so new-dialog openings don't form a
# machine grid (D-08). ±25% spread via random.uniform on the expected-by-now count.
PACE_JITTER_LOW = 0.75
PACE_JITTER_HIGH = 1.25
```

## State of the Art

Not applicable — this is internal queue-worker logic, not a library-driven domain. No framework choice, no version churn. The "state of the art" is the project's own established pattern (Phase 4 per-campaign window + Phase 12 per-item cap), which this phase extends.

## Open Questions

1. **Does any real campaign use a midnight-crossing window (`work_hour_end <= work_hour_start`)?**
   - What we know: `_campaign_in_working_window` (queue.py:109) uses `work_hour_start <= hour < work_hour_end` — a half-open interval that does NOT support wrap (a 22→06 window would never be "in window"). So the *existing* system effectively assumes non-wrap windows.
   - What's unclear: whether the schema/UI allows `end < start` at all, or constrains `start < end`.
   - Recommendation: Planner should treat non-wrap as the supported case (match existing `_campaign_in_working_window` semantics) and add the wrap branch as defensive-only (or omit it and assert `start < end`). Do NOT make Phase 13 the first code to "support" wrap windows the rest of the system doesn't honour. Quick check: `SELECT count(*) FROM campaigns WHERE work_hour_end <= work_hour_start;` — if 0, wrap is moot.

2. **`floor` vs `ceil` on `expected_now`?**
   - What we know: `expected = limit × frac × jitter` is fractional. `count < expected` with a fractional RHS: PostgreSQL compares int `< numeric` fine.
   - What's unclear: whether to floor (stricter, may under-deliver the last dialog) or compare against the raw fractional value (smoother).
   - Recommendation: Compare `count < expected_now` directly (no floor) — smoothest, and the jitter already blurs the boundary. Let the planner confirm in a worked 50/11h example.

3. **Jitter on `expected` vs on a derived target interval — equivalent?**
   - What we know: D-08 says "floating target interval ±20–30%". Jittering `expected_now` by ±25% is mathematically the inverse of jittering the interval.
   - Recommendation: Jitter `expected_now` (single multiply, fits the SQL-bind design). Document the equivalence in source. Planner's discretion per CONTEXT.md.

## Environment Availability

Skipped — no external dependencies. The phase is pure Python/PostgreSQL logic inside an existing service; PostgreSQL and Python 3.11 are already the running stack (verified: `requirements.txt`, `docker-compose.yml` db service). No new tools, CLIs, runtimes, or services introduced.

## Validation Architecture

> nyquist_validation = true (config.json) → section included.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (async fixtures), SQLAlchemy async over real PostgreSQL (NOT mocked) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`: `python_files=test_*.py`, asyncio mode) |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_even_pacing.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| Hard rule | NEVER run pytest without the test-overlay (conftest guard DROP SCHEMA → prod). NEVER `down -v`. |

### Closest existing analog to extend
- **`tests/test_queue_new_dialog_limit.py`** (Phase 12) — the direct template. Reuse its helpers verbatim: `_insert_pending_item`, `_seed_sent_dialog` (note: seeds `finished_at=NOW()` explicitly — the conftest factory leaves `finished_at=NULL` so counts return 0), `_run_worker_capturing_picked` (mocks `_check_rate_limits→True`, `_get_long_pause_seconds→None`, `_send_item→capture`), `_set_cap`. Fixtures: `async_db_session`, `test_running_campaign_factory(sender_count, work_hour_start, work_hour_end, work_days_mask)`.
- **`tests/test_queue_per_campaign_hours.py`** (Phase 4) — pattern for window/time-based assertions and source-introspection guards (`ast`/`inspect`).
- Both run against the ephemeral tmpfs `db-test` (conftest `_setup_database`); real `NOW()`, no `freezegun`.

### Determinism recipe (addresses Pitfall 4 — worker `now` not injectable)
1. **Unit-test `_window_elapsed_fraction(now=...)` as a pure function** (inject `now`) across: window start exactly, mid-window, just before close, boundary (frac→0), past close (saturate→1), midnight-cross, a DST transition date. This is where the math is verified deterministically.
2. **Integration tests pick fraction-robust scenarios** so the wall-clock fraction can't flip the assertion:
   - "over expected ⇒ blocked": set `work_hour_start=0, work_hour_end=24` (frac = sec-since-UTC-midnight/86400 ≤ 1), `max_new_dialogs_per_day=1`, seed `count_since_window_start ≥ 1` → `expected ≤ 1` and `count ≥ 1 ⇒ count < expected` is false at almost all times → blocked. (Choose seed/limit so the inequality holds for any frac in (0,1].)
   - "under expected ⇒ allowed": `work_hour_start=0, work_hour_end=24`, `max_new_dialogs_per_day` large (e.g. 1000), `count=0` → `expected = 1000 × frac` which is ≥1 except in the first ~86s of UTC day; assert allowed. (Add a tiny guard or run-time tolerance, or seed via a window where elapsed is guaranteed non-trivial.)
   - "follow-up always allowed": seed a prior `sent` to the phone (PACE-07) — independent of fraction.
3. **Source-introspection guards** (no DB): assert `_check_rate_limits` source still lacks pacing refs and `MIN/MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `MAX_NEW_CONTACTS_PER_HOUR` unchanged; assert the SELECT source still contains `FOR UPDATE OF mq SKIP LOCKED` and `LIMIT 8`.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| PACE-01 | New jitter const added, PROTECTED consts unchanged | unit (introspection) | `pytest tests/test_queue_even_pacing.py::test_protected_constants_intact -x` | ❌ Wave 0 |
| PACE-02 | elapsed-fraction helper correct across tz/boundary/midnight/DST | unit (pure fn) | `pytest tests/test_queue_even_pacing.py::test_window_elapsed_fraction -x` | ❌ Wave 0 |
| PACE-03 | over-expected new dialog NOT picked; under-expected picked; SKIP LOCKED/LIMIT 8 intact | integration | `pytest tests/test_queue_even_pacing.py::test_pacing_gate -x` | ❌ Wave 0 |
| PACE-04 | pace counter = window-start (not 24h); diverges from Phase 12 cap | integration | `pytest tests/test_queue_even_pacing.py::test_pace_counter_window_start -x` | ❌ Wave 0 |
| PACE-05 | narrow window + high limit ⇒ base-floor spacing, limit not reached, no crash | integration | `pytest tests/test_queue_even_pacing.py::test_interval_floor -x` | ❌ Wave 0 |
| PACE-06 | catch-up: ≤1 new dialog per call; jitter present | integration + introspection | `pytest tests/test_queue_even_pacing.py::test_catchup_no_burst -x` | ❌ Wave 0 |
| PACE-07 | follow-up never throttled; `_check_rate_limits` untouched | integration + introspection | `pytest tests/test_queue_even_pacing.py::test_followup_bypasses_pacing -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_queue_even_pacing.py -x` (+ `tests/test_queue_new_dialog_limit.py tests/test_queue_per_campaign_hours.py` to catch SELECT-regression).
- **Per wave merge:** full suite (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`).
- **Phase gate:** full suite green before `/gsd:verify-work` (baseline is currently GREEN per MEMORY — gates can trust `TEST_EXIT==0`).

### Wave 0 Gaps
- [ ] `tests/test_queue_even_pacing.py` — new file; covers PACE-01..07. Copy helpers from `test_queue_new_dialog_limit.py` (`_insert_pending_item`, `_seed_sent_dialog`, `_run_worker_capturing_picked`, `_set_cap`, `_item_status`).
- [ ] No new fixtures needed — `async_db_session`, `test_running_campaign_factory`, `_seed_sent_dialog` cover it. The pacing helper's injectable `now` removes the need for `freezegun`.
- [ ] No framework install — pytest/pytest-asyncio already present and green.

## Sources

### Primary (HIGH confidence)
- `app/services/queue.py` (read in full) — rate-config block (:39-69), `_campaign_in_working_window` (:73-111), `_process_next_for_sender` SELECT + Phase 12 predicate (:300-336), Python post-filter (:344-356), base interval gate (:504-528).
- `.planning/phases/13-.../13-CONTEXT.md` — locked decisions D-01..D-10 + discretion.
- `.planning/phases/12-.../12-CONTEXT.md` — input contract (per-(sender,campaign) cap, new-dialog predicate, trailing-24h floor).
- `.planning/ROADMAP.md` §Phase 13 (:323-344) — scope/acceptance.
- `.planning/REQUIREMENTS.md` (:120-151) — NDLG-01..06 pattern to mirror.
- `tests/test_queue_new_dialog_limit.py`, `tests/test_queue_per_campaign_hours.py`, `tests/conftest.py` (:600-838) — test/fixture patterns; `requirements.txt` (sqlalchemy 2.0.25, asyncpg 0.29.0).
- `CLAUDE.md` (root + project) — PROTECTED constants, test-overlay rule, async rules.
- `MEMORY.md` — test baseline GREEN; never `down -v`; parallel-agent careful commits.

### Secondary (MEDIUM confidence)
- PostgreSQL READ COMMITTED / `FOR UPDATE … SKIP LOCKED` semantics (lock scope, no escalation) — standard PG behaviour, consistent with the already-shipped Phase 12 use of the same construct.

### Tertiary (LOW confidence)
- None — no unverified claims; the phase is internal logic verified against source.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new deps; all stdlib/existing, versions verified in `requirements.txt`.
- Architecture / SQL shape: HIGH — extends the byte-for-byte Phase 12 pattern that already ships and is tested; locking semantics are standard PG.
- Pitfalls: HIGH — derived directly from the locked decisions and the actual worker code (now-not-injectable, two-counter divergence, midnight wrap, boundary clamp).
- Test strategy: HIGH — direct analog (`test_queue_new_dialog_limit.py`) read in full; determinism recipe accounts for the no-`freezegun` constraint via injectable `now`.

**Research date:** 2026-06-26
**Valid until:** 2026-07-26 (stable internal logic; only invalidated if PROTECTED rate constants or the queue SELECT shape change in another phase)

## RESEARCH COMPLETE
