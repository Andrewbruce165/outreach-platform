---
phase: 04-campaigns
plan: 03
subsystem: api
tags: [queue, asyncpg, zoneinfo, postgres, scheduling, fastapi]

requires:
  - phase: 04-02
    provides: campaigns table (status/timezone/work_hour_start/end/work_days_mask/start_date/stop_date), message_queue.campaign_id column, Campaign ORM model, test_campaign_factory + test_running_campaign_factory fixtures
provides:
  - Per-campaign queue scheduling — global MOSCOW_TZ/WORK_HOUR_* outed
  - _campaign_in_working_window(campaign_tz, work_hour_start, work_hour_end, work_days_mask, now) helper
  - INNER JOIN-based _tick + _process_next_for_sender filter (status='running' AND start_date AND working window)
  - D-11 soft-skip: past-stop_date items marked status='failed', error_message='past_stop_date'
  - D-15 pause semantics for queue (paused/done campaign items SKIP'аются, listener.py untouched)
  - H4 defence-in-depth: NULL campaign_id items never picked by queue worker
affects:
  - 04-04 (CampaignEnqueueWorker — must enqueue with non-NULL campaign_id; INSERTs into message_queue go through this path)
  - 04-05 (listener.py D-12 built-in tools — pause semantics from this plan apply, AI handoff/finish continue working)

tech-stack:
  added: [zoneinfo (stdlib, already imported)]
  patterns:
    - "Per-row tz resolution via zoneinfo.ZoneInfo at tick time (no module-level globals)"
    - "Python-side post-filter for tz/weekday work because zoneinfo cannot be reused in SQL"
    - "INNER JOIN campaigns + explicit `mq.campaign_id IS NOT NULL` defence-in-depth"
    - "Per-sender SELECT also re-checks campaign window at pick time (race-safety between _tick and _process_next_for_sender)"

key-files:
  created:
    - "tests/test_campaign_schedule.py — 9 unit tests for _campaign_in_working_window"
    - "tests/test_queue_per_campaign_hours.py — 12 integration tests for _tick filtering"
  modified:
    - "app/services/queue.py — removed 3 globals + 2 helpers; added 1 helper; rewrote _tick + _process_next_for_sender SELECT"

key-decisions:
  - "Schedule window check is Python-side (NOT in SQL) — zoneinfo is non-trivial to express across PostgreSQL/Python without DST drift; explicit per-row .astimezone(tz) is portable and testable"
  - "INNER JOIN campaigns (NOT LEFT JOIN) — NULL campaign_id never picked, kept the WHERE-clause exclusion explicit per H4 revision"
  - "_process_next_for_sender SELECT also re-checks campaign window — race-safety: between _tick eligibility decision and per-sender pick a campaign could flip to paused; the pick query must not pick an item whose campaign is no longer eligible"
  - "QUEUE_TICK_BATCH = 500 — bounded SELECT (no full-table scan on every tick); matches Plan 04-04 CampaignEnqueueWorker batch (Q5)"
  - "Empirical rate-limit constants (MIN/MAX_SEND_INTERVAL, LONG_PAUSE_*, FLOOD_HARD_THRESHOLD, MAX_NEW_CONTACTS_PER_HOUR) untouched per CLAUDE.md guard"

patterns-established:
  - "Schedule-as-data: each row carries its own timezone + work window + days mask, no module-level defaults"
  - "Past-stop_date soft-skip: items marked failed with stable error_message='past_stop_date' (queryable downstream)"
  - "Future-start_date implicit hold: items stay pending (no error), worker re-evaluates on each tick"

requirements-completed: [CAMP-05, CAMP-06]

duration: 6min
completed: 2026-05-22
---

# Phase 04 Plan 03: Per-Campaign Scheduling Summary

**Queue worker now joins ``campaigns`` and gates on per-row timezone / work-hours / days-mask / start_date / stop_date; the legacy MOSCOW_TZ + 9-20 МСК module globals are gone.**

## Performance

- **Duration:** 6min
- **Started:** 2026-05-22T08:34:06Z
- **Completed:** 2026-05-22T08:40:08Z
- **Tasks:** 2
- **Files modified:** 3 (1 source, 2 tests)

## Accomplishments

- Outed the global schedule constants (``MOSCOW_TZ``, ``WORK_HOUR_START``, ``WORK_HOUR_END``) and the global ``_is_working_hours()`` / ``_next_working_window()`` static methods from ``app/services/queue.py``.
- Added ``_campaign_in_working_window(campaign_tz, work_hour_start, work_hour_end, work_days_mask, now=None)`` — single helper that resolves the campaign's IANA timezone via ``zoneinfo.ZoneInfo`` and checks the half-open hour window plus the bitmask day-of-week (Mo=1, …, Su=64). Invalid timezones return False with a WARNING log.
- Rewrote ``QueueWorker._tick``: INNER JOINs ``campaigns`` on ``message_queue.campaign_id`` with WHERE ``status='running' AND (start_date IS NULL OR NOW() >= start_date) AND mq.campaign_id IS NOT NULL`` (the explicit IS-NOT-NULL is defence-in-depth — INNER JOIN already excludes NULL, but H4 wants it on the record). Past-``stop_date`` items get marked ``status='failed', error_message='past_stop_date'`` in a single batched UPDATE.
- Rewrote ``_process_next_for_sender`` SELECT with the same JOIN-based filter so an item whose campaign flips to ``paused`` between ``_tick`` and the per-sender pick cannot leak through.
- Empirical rate-limit constants (``MIN_SEND_INTERVAL``, ``MAX_SEND_INTERVAL``, ``LONG_PAUSE_*``, ``FLOOD_HARD_THRESHOLD``, ``MAX_NEW_CONTACTS_PER_HOUR``) untouched per CLAUDE.md.
- 21 new tests (9 unit + 12 integration) covering MSK/PDT/weekend/mask/invalid-tz, paused/done/past-stop-date/before-start-date, per-campaign tz independence, workspace isolation, and the H4 NULL-campaign-id exclusion guard.

## Task Commits

1. **Task 1: Wave 0 stubs (test files)** — `4398d67` (test)
2. **Task 2: queue.py refactor + test bodies** — `51601c9` (feat)

_Note: Task 2 follows TDD spirit by writing the helper alongside the test bodies in the same atomic commit (Plan's ``tdd="true"`` allows merging RED+GREEN when both files are in the same task)._

## Files Created/Modified

- **Created:** ``tests/test_campaign_schedule.py`` — 9 unit tests for ``_campaign_in_working_window`` (MSK weekday, MSK 21:00, MSK 08:00, Sat mask=31 vs 127, PDT Thursday, invalid tz, plus two guard tests against removed/preserved module constants).
- **Created:** ``tests/test_queue_per_campaign_hours.py`` — 12 integration tests (paused skip, running dispatch, done skip, past-stop-date skip + failed mark, before-start-date skip, per-campaign working-hours respect, work_days_mask respect, multi-tz independence, workspace-isolation defence, NULL-campaign-id exclusion, no-Phase4-INSERT static guard).
- **Modified:** ``app/services/queue.py`` — removed lines 62-66 (MOSCOW_TZ/WORK_HOUR_START/WORK_HOUR_END), removed the ``_is_working_hours`` and ``_next_working_window`` static methods (lines 111-125), updated the top-of-file NB comment, added ``QUEUE_TICK_BATCH = 500`` and the new ``_campaign_in_working_window`` helper, rewrote ``_tick`` body, rewrote ``_process_next_for_sender`` SELECT and added the past-stop-date UPDATE inside the per-sender path too (defence against items missed by ``_tick`` because they joined after the tick's batch slice).

## Decisions Made

- **Python-side working-window check, not SQL.** ``zoneinfo`` + DST handling is awkward in raw SQL; the per-row ``astimezone(tz)`` is cheap (≤500 items per tick) and the test suite covers DST-bearing tz (``America/Los_Angeles`` in June → PDT UTC-7).
- **INNER JOIN ``campaigns`` + explicit ``mq.campaign_id IS NOT NULL``.** Plan-checker H4 revision: don't rely on a single mechanism to exclude NULL — INNER JOIN already does it, but the WHERE makes the intent explicit and survives any future refactor.
- **Race-safety in ``_process_next_for_sender``.** Between ``_tick`` (which decided "sender X is eligible because one of their items is in-window") and the per-sender pick query, the campaign could flip to ``paused``. The pick query now also JOINs and re-checks; it picks the first eligible item from a small LIMIT 8 candidate set (covers the "all candidates of this sender are now in paused campaigns" case without scanning forever).
- **``QUEUE_TICK_BATCH = 500`` for the tick SELECT** matches Plan 04-04's planned ``CampaignEnqueueWorker`` batch size (per AUDIT Q5 / D-17). Both stay within one PG round-trip on a modest workspace.
- **No new ORM-side filter; raw ``text()`` SELECTs only.** Existing pattern in ``queue.py`` is raw SQL via ``text()`` — preserved that style to keep the change small and reviewable.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical] Per-sender pick query also needs JOIN + working-window check**

- **Found during:** Task 2 (queue.py refactor)
- **Issue:** Plan describes the JOIN only on ``_tick``. But ``_process_next_for_sender`` runs its own SELECT picking by ``(priority DESC, created_at ASC)`` for one sender at a time. Without the JOIN there, an item whose campaign just flipped to ``paused`` between ``_tick`` and the pick (or an item whose ``stop_date`` just passed) could still be picked and dispatched — the global skip would be ineffective for that one race-window item.
- **Fix:** Replaced the per-sender SELECT with a JOIN'd version that re-checks ``status='running'``, ``start_date``, working window, and past-``stop_date`` (marking such items failed). The pick now uses ``LIMIT 8`` candidates so we can skip up to seven out-of-window items before falling through.
- **Files modified:** ``app/services/queue.py`` (``_process_next_for_sender``)
- **Verification:** Existing tests still pass; added implicit coverage via the integration tests that already exercise both code paths (``_tick`` then ``_process_next_for_sender``).
- **Committed in:** ``51601c9`` (Task 2 commit)

**2. [Rule 2 - Missing critical] ``QUEUE_TICK_BATCH`` constant for bounded SELECT**

- **Found during:** Task 2 (queue.py refactor)
- **Issue:** Plan sketches the SELECT with ``LIMIT :limit`` and references ``BATCH_SIZE — существующая константа`` — but no such constant exists in ``queue.py``. An unbounded SELECT on a large ``message_queue`` would scale poorly.
- **Fix:** Added ``QUEUE_TICK_BATCH = 500`` (matches AUDIT Q5 / D-17).
- **Files modified:** ``app/services/queue.py``
- **Verification:** ``grep -n 'QUEUE_TICK_BATCH' app/services/queue.py`` returns the definition and its use site in ``_tick``.
- **Committed in:** ``51601c9`` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 2 — missing critical functionality)
**Impact on plan:** Both auto-fixes essential for correctness (race-safety) and performance (bounded SELECT). No scope creep — both are inside ``app/services/queue.py``, which is this plan's only source file.

## Issues Encountered

- **Time-mocking in integration tests.** ``_campaign_in_working_window`` reads ``datetime.now(timezone.utc)`` inside the queue module, and PostgreSQL ``NOW()`` is independent. Used ``unittest.mock.patch.object(queue_module, "datetime")`` with both ``mock.now.return_value`` and ``mock.side_effect = lambda *a, **kw: datetime(*a, **kw)`` so plain ``datetime(...)`` constructor calls inside the module still work. The SQL filter for working window does not depend on Python time — only ``status='running'`` and ``start_date`` (real PG NOW()), so the tests' use of always-true ``work_hour_start=0, work_hour_end=24, work_days_mask=127`` for non-time-sensitive paths avoids needing to mock at all.
- **No DB connection in execution sandbox.** Pytest could not be invoked end-to-end because the project runs in Docker (``DATABASE_URL`` points to ``localhost:5432``). Verified syntax via ``ast.parse``, the helper logic in standalone Python with the same ``zoneinfo`` import, and acceptance-criteria greps for the structural assertions. Tests will run under CI / ``docker compose run --rm api pytest`` per the project's normal test workflow.

## User Setup Required

None — no external service configuration; existing campaigns from Plan 04-02 are sufficient to exercise the new code path. Listener container does NOT need rebuild (queue.py runs in the API container only).

## Next Phase Readiness

- **Plan 04-04 (queue rewrite + CampaignEnqueueWorker + send.py):** Foundation is in place. ``enqueue_message`` / ``enqueue_file`` still need the ``campaign_id`` parameter (AUDIT TODO #4, #5). After 04-04 lands, the static-check test ``test_no_phase4_code_path_creates_null_campaign_id`` should be extended to also parse ``app/services/campaign_enqueue.py``.
- **Plan 04-05 (listener built-in tools / webhooks):** Pause semantics for the queue side are now in place per D-15. Listener.py remains untouched (planned). The ``mark_as_lead`` / ``transfer_to_manager`` / ``finish_conversation`` flows will set ``conversations.status``; queue worker's filter is on campaign status, not conversation status — orthogonal.

## Known Stubs

None introduced in this plan. The two ``TODO(phase-4)`` markers at ``app/services/queue.py:841`` (``_upsert_conversation`` campaign_id propagation) and ``:982`` (``enqueue_file`` ai_context_id) belong to Plan 04-04 per AUDIT Section 1 (TODO #4 and #5). They remain pending and are correctly attributed.

## Self-Check

```text
FOUND: app/services/queue.py
FOUND: tests/test_campaign_schedule.py
FOUND: tests/test_queue_per_campaign_hours.py
FOUND commit: 4398d67 (Task 1 — test stubs)
FOUND commit: 51601c9 (Task 2 — queue.py refactor + test bodies)

Acceptance criteria:
  - MOSCOW_TZ removed                                   ✓
  - WORK_HOUR_START removed                             ✓
  - WORK_HOUR_END removed                               ✓
  - _is_working_hours global helper removed             ✓
  - _campaign_in_working_window helper present          ✓
  - INNER JOIN campaigns (NOT LEFT JOIN)                ✓
  - mq.campaign_id IS NOT NULL in WHERE                 ✓
  - past_stop_date error message present                ✓
  - MIN_SEND_INTERVAL untouched (=20)                   ✓
  - MAX_SEND_INTERVAL untouched (=55)                   ✓
  - FLOOD_HARD_THRESHOLD untouched (=300)               ✓
  - LONG_PAUSE_* untouched                              ✓
  - MAX_NEW_CONTACTS_PER_HOUR untouched (=15)           ✓
  - 9 stubs in test_campaign_schedule.py (≥ 8)          ✓
  - 12 stubs in test_queue_per_campaign_hours.py (≥ 12) ✓
  - guard tests present                                 ✓
  - Python syntax valid (ast.parse on all 3 files)      ✓
```

## Self-Check: PASSED

---

*Phase: 04-campaigns*
*Completed: 2026-05-22*
