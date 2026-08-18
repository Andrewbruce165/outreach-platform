---
phase: quick-260818-s0k
plan: 01
subsystem: anti-ban
tags: [queue, senders, campaigns, postgres, sqlalchemy, anti-spam, burst-desync]

# Dependency graph
requires:
  - phase: C&C mass-ban remediation (B1–H5, commit a5f92e1)
    provides: proxy hygiene, tier-3 send-time safety, warmup gate, opener paraphrase — B4 is the timing layer on top
provides:
  - "senders.send_stagger_until — per-sender 'not before T' marker for the FIRST cold dialog after a campaign goes running"
  - "app/services/send_stagger.py::apply_send_stagger — transaction-neutral set-based even-split layout over the eligible pool"
  - "SEND_STAGGER_WINDOW_SECONDS config knob (default 3600, 0 = kill switch, no code release needed)"
  - "New-dialog-only stagger gate in QueueWorker._process_next_for_sender"
affects: [queue pacing work, campaign start/resume flows, future anti-ban timing layers, concurrent-sender cap (spec 5.2, deferred)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-sender 'skip until T' marker (same family as long_pause_until / proxy_switch_pending_at / checker_rest_until) — additive, self-expiring, NOT a restriction"
    - "Set-based even-split layout with structural (not probabilistic) distinctness: row_number() OVER (ORDER BY random()) slot + random() jitter inside the slot"

key-files:
  created:
    - migrations/066_sender_send_stagger.sql
    - app/services/send_stagger.py
    - tests/test_send_stagger.py
  modified:
    - app/models/__init__.py
    - app/config.py
    - app/routers/campaigns.py
    - app/services/queue.py

key-decisions:
  - "Window length is a config knob (send_stagger_window_seconds, default 3600); 0 is the only kill switch — no campaign-level desync flag, no campaigns migration (D-1)"
  - "Stagger is re-laid on EVERY transition to running (first start AND every resume) — a resume makes the whole pool due at once too (D-2)"
  - "Even split with in-slot jitter over a random permutation: offset_i = i*(W/N) + random()*(W/N) — disjoint half-open slots make collision and the < W bound structural, not probabilistic (D-3)"
  - "Gate lives ONLY in the new-dialog branch of _process_next_for_sender; NOT in _tick and NOT in _check_rate_limits, which would skip the sender's whole tick and starve follow-ups (D-5)"
  - "N < 2 eligible senders is a no-op that clears markers — nothing to desync, and delaying a solo sender by up to an hour would read as a broken start (D-6)"
  - "ORM column nullable with NO default= and NO server_default — sidesteps the create_all-vs-migration default drift class entirely"
  - "Concurrent-sender cap (spec §5.2) explicitly out of scope (D-4)"

patterns-established:
  - "Layout services are transaction-neutral (never commit) — the router owns the transaction, same contract as services/rebalance.py"
  - "Eligible-pool SQL filter copied verbatim from rebalance.py:353-367 with a 'keep in sync' note"

requirements-completed: [B4-DESYNC]

# Metrics
duration: 35min
completed: 2026-08-18
---

# Quick 260818-s0k: B4 Burst Desync (Staggered Send Start Per Sender) Summary

**Per-sender `send_stagger_until` marker laid out as an even split with jitter across a campaign's eligible senders on every start/resume, gating only the new-dialog branch of the send worker — the pool no longer opens cold dialogs in the same tick, while follow-ups are never delayed.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-08-18T20:17Z
- **Completed:** 2026-08-18T20:52Z
- **Tasks:** 3/3
- **Files modified/created:** 7 (3 created, 4 modified)

## Accomplishments

- Migration 066 adds `senders.send_stagger_until TIMESTAMPTZ` (idempotent `ADD COLUMN IF NOT EXISTS`, verified by applying it twice against a scratch DB) plus a nullable no-default ORM column.
- `apply_send_stagger` lays out one marker per `W/N` slot over the campaign's attached ELIGIBLE senders (verbatim rebalance eligible-pool filter), in a single set-based `UPDATE`; it clears markers on attached-but-ineligible senders so a recovered sender never carries a stale future timestamp.
- Both `/campaigns/{id}/start` and `/campaigns/{id}/resume` call it right before `db.commit()`, so the layout and the `status='running'` flip land in the same transaction.
- The send worker gains exactly one additive AND-predicate (plus one bind and a comment) inside the new-dialog branch of the candidate SELECT; follow-ups bypass it exactly as they already bypass cap + pace + `spam_limited`.
- `SEND_STAGGER_WINDOW_SECONDS=0` fully disables the feature at both ends (nothing written, gate bind false) — rollback is an env change plus a container restart.
- 16 new tests; the full targeted regression set (114 tests) is green.

## Task Commits

1. **Task 1: Schema + config knob** — `b7cd008` (feat)
2. **Task 2: apply_send_stagger layout service wired into start + resume** — `54f74c8` (feat)
3. **Task 3: New-dialog-only gate in the send worker + regression** — `0481480` (feat)

Each task was written test-first (RED confirmed before the implementation commit), then squashed into one commit per task since the RED state never left the working tree.

## Files Created/Modified

- `migrations/066_sender_send_stagger.sql` — idempotent `ADD COLUMN IF NOT EXISTS send_stagger_until TIMESTAMPTZ`, with the full B4 rationale and an explicit "NOT a restriction" note.
- `app/models/__init__.py` — `Sender.send_stagger_until` next to `proxy_switch_pending_at`; nullable, no python-side or server default.
- `app/config.py` — `send_stagger_window_seconds` (default 3600, alias `SEND_STAGGER_WINDOW_SECONDS`).
- `app/services/send_stagger.py` — `apply_send_stagger(db, campaign_id) -> int`; transaction-neutral, set-based, returns the number of senders staggered (0 for the W=0 / N<2 no-op paths).
- `app/routers/campaigns.py` — import + two call sites (`start_campaign`, `resume_campaign`), each immediately after `c.paused_at = None` and before `await db.commit()`.
- `app/services/queue.py` — the stagger predicate + `:stagger_on` bind inside the new-dialog branch of `_process_next_for_sender`. Nothing else in the file changed.
- `tests/test_send_stagger.py` — 16 tests: schema/ORM/knob, layout + even split + resume re-lay + ineligible-skip + stale-marker clear + kill switch + N=1, then baseline pick / gate / follow-up bypass / expiry / kill switch, plus a PROTECTED source-introspection regression.

## Decisions Made

All decisions were pre-locked in the plan (D-1..D-6) and followed as written. Two implementation-level choices worth recording:

- **Kill-switch test helper mutates the cached `Settings` instance in place and deliberately does NOT call `get_settings.cache_clear()`.** The plan suggested clearing the lru_cache; clearing it would rebuild `Settings` from the environment and silently drop the override, making the test pass for the wrong reason. The original value is restored in a `finally`, so no other test can inherit the zeroed knob.
- **One extra test beyond the plan's 4-15:** `test_stale_marker_cleared_when_sender_becomes_ineligible` — covers action step 5 (clearing markers on attached-but-ineligible senders), which had no test in the plan's behaviour list.

## Deviations from Plan

None — plan executed as written (the two notes above are test-mechanics refinements inside the specified test module, not scope or behaviour changes).

## Issues Encountered

- **Compose project-name collision in the worktree.** Running the test overlay from the worktree tried to create the prod-named `outreach-platform-db` container (base compose `container_name` + `depends_on: db`) and failed. Resolved by running under an isolated project (`-p gsd-quick-s0k`), starting only `db-test`, and using `run --rm --no-deps api pytest`. Prod containers were never touched (`outreach-platform-db/api/listener` still up), and the isolated project + its stray empty volume were removed afterwards.
- **`.env` not present in the worktree** → `Settings` validation error on `telegram_api_id`. Resolved by passing `--env-file /root/apps/aimly/tg-outreach/.env`; the test overlay still hardcodes the `outreach_test` DSN for the api service, so the prod DSN cannot leak.

## Verification

Targeted regression through the test overlay — **114 passed**:

```
tests/test_send_stagger.py (16) tests/test_queue_new_dialog_limit.py (7)
tests/test_queue_even_pacing.py (9) tests/test_send.py (14)
tests/test_send_hardening.py (7) tests/test_rebalance.py (15)
tests/test_campaign_enqueue_worker.py (22) tests/test_campaign_router.py (24)
```

Plan verification checklist:

1. `git diff --stat` touches only the 7 planned files. ✅
2. `git diff app/services/queue.py` = the predicate + bind + comment, nothing else. ✅
3. `grep -n send_stagger_until app/services/queue.py` → lines 587-588 only, inside `_process_next_for_sender`. ✅
4. `grep -n apply_send_stagger app/routers/campaigns.py` → import + exactly 2 call sites. ✅
5. Migration re-applied against a scratch DB → `NOTICE: column already exists, skipping`. ✅
6. Targeted regression exits 0. ✅

## User Setup Required

None. `SEND_STAGGER_WINDOW_SECONDS` is optional — the 3600s default applies without any env change. To disable the feature set `SEND_STAGGER_WINDOW_SECONDS=0` and restart api.

## Post-Deploy Verification (for the user, after `docker compose up -d --build api listener`)

Not part of automated verification. On a cold test campaign after start, check `message_queue`:

- no ≥5 sends inside any single minute across the whole pool;
- each sender's first `finished_at` is smeared across the stagger window instead of landing in one tick.

```sql
SELECT date_trunc('minute', finished_at) AS minute, COUNT(*) AS sends,
       COUNT(DISTINCT sender_id) AS senders
FROM message_queue
WHERE campaign_id = '<campaign-id>' AND status = 'sent'
GROUP BY 1 ORDER BY 1;
```

## Next Readiness

- B4 is a timing layer, not a standalone fix: per the spec §3 it is only meaningful with visible opener variation (H5, `campaigns.opener_paraphrase_enabled`) enabled. Enable H5 on the campaign before judging the effect.
- Spec §5.2 (cap on concurrently-active senders) remains deliberately unimplemented (D-4) and can be revisited if live `message_queue` still shows minute-level clustering after this ships.
- Deploy is api-only for the layout + gate (`docker compose up -d --build api`); the migration auto-applies on api start (fail-fast if it errors).

## Self-Check: PASSED

- All claimed files exist on disk (`migrations/066_sender_send_stagger.sql`, `app/services/send_stagger.py`, `tests/test_send_stagger.py`, plus the 4 modified files).
- All claimed commits exist: `b7cd008`, `54f74c8`, `0481480`.
- Working tree clean apart from this untracked SUMMARY (the orchestrator owns the docs commit).

---
*Quick task: 260818-s0k*
*Completed: 2026-08-18*
