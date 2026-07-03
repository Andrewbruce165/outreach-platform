---
phase: quick-260703-ssv
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/047_message_queue_priority_default.sql
  - migrations/048_sender_long_pause_until.sql
  - app/models/__init__.py
  - app/services/campaign_enqueue.py
  - app/services/queue.py
  - tests/test_queue_position.py
  - tests/test_queue_long_pause.py
autonomous: true
requirements: [WR-02, WR-03, WR-04]
must_haves:
  truths:
    - "message_queue.priority defaults to 0 at the DB level; no new NULL rows appear and existing NULLs are backfilled to 0"
    - "attempts and as_draft also have DB defaults (0 / false) and existing NULLs are backfilled"
    - "Queue position (WR-03) counts higher-priority items AND earlier same-priority items as 'ahead', and is NULL-safe via COALESCE"
    - "A sender in a long-pause does not block other senders' sends in the same queue tick (WR-04, no inline sleep in the shared loop)"
    - "A long-pause survives a process restart because it is read from senders.long_pause_until (durable), not in-memory state"
    - "The long-pause does not re-fire on consecutive ticks while a sender is already paused (marker guard)"
    - "Empirical rate/pause/interval constants (4/20/150, LONG_PAUSE_EVERY 12-25, LONG_PAUSE 180-600s) are UNCHANGED — only the mechanism changed"
  artifacts:
    - path: "migrations/047_message_queue_priority_default.sql"
      provides: "priority/attempts/as_draft SET DEFAULT + NULL backfill (WR-02)"
      contains: "ALTER TABLE message_queue ALTER COLUMN priority SET DEFAULT 0"
    - path: "migrations/048_sender_long_pause_until.sql"
      provides: "durable per-sender long_pause_until column (WR-04)"
      contains: "long_pause_until"
    - path: "app/services/queue.py"
      provides: "priority-aware _queue_position + non-blocking durable long-pause"
      min_lines: 30
    - path: "app/models/__init__.py"
      provides: "server_default on priority/attempts/as_draft + Sender.long_pause_until column"
    - path: "app/services/campaign_enqueue.py"
      provides: "explicit priority in the raw INSERT"
    - path: "tests/test_queue_position.py"
      provides: "unit coverage for priority/NULL-aware queue position"
    - path: "tests/test_queue_long_pause.py"
      provides: "coverage for non-blocking pause, restart-durability, no double-trigger"
  key_links:
    - from: "app/services/queue.py::_tick SELECT"
      to: "senders.long_pause_until"
      via: "JOIN senders + AND (s.long_pause_until IS NULL OR s.long_pause_until <= NOW())"
      pattern: "long_pause_until"
    - from: "app/services/queue.py::_process_next_for_sender"
      to: "senders.long_pause_until"
      via: "UPDATE senders SET long_pause_until = NOW() + interval (replaces asyncio.sleep)"
      pattern: "UPDATE senders SET long_pause_until"
    - from: "app/services/campaign_enqueue.py INSERT"
      to: "message_queue.priority"
      via: "explicit priority column in VALUES"
      pattern: "priority"
    - from: "app/database.py auto-applier"
      to: "schema_migrations"
      via: "047 + 048 recorded on api start"
      pattern: "0(47|48)_"
---

<objective>
Close **Batch C** (WR-02, WR-03 — queue priority default + position ordering) and **Batch D** (WR-04 — head-of-line blocking in the send loop) from `.planning/reviews/260703-checker-campaigns-FIXPLAN.md`.

Purpose: (1) `message_queue.priority` is NULL for every campaign-enqueued row (no DB default; raw INSERT omits it) — NULLs sort FIRST under `ORDER BY priority DESC`, inverting priority semantics; the reported queue position/ETA is also inverted and NULL-blind. (2) A single sender's 3-10 min long-pause is an inline `asyncio.sleep` in the shared queue tick — it stalls EVERY sender in EVERY workspace (same head-of-line class as the fixed warmup bug), and re-fires repeatedly on a static 30-min count.

Output: two idempotent migrations (047, 048), ORM server-default fixes, a priority-aware NULL-safe `_queue_position`, a durable non-blocking long-pause mechanism, targeted tests, and a live prod deploy + sanity check.

**Non-negotiable (CLAUDE.md + FIXPLAN):** Do NOT change the empirical values of rate limits, pause durations, or interval constants (4 msg/min, 20/hr, 150/day, `LONG_PAUSE_EVERY_MIN..MAX`, `LONG_PAUSE_MIN_SECS..MAX_SECS`). Change ONLY the *mechanism* (sleep → durable column check).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/reviews/260703-checker-campaigns-FIXPLAN.md
@.planning/reviews/260703-checker-campaigns-REVIEW.md

<interfaces>
<!-- Verified against the codebase. Executor should use these directly — no exploration needed. -->

## Batch C — current state

app/models/__init__.py:248-293 (class MessageQueue):
```python
as_draft = Column(Boolean, default=False)                    # line 267 — NO server_default
extra_data = Column(JSONB, default={})                       # line 275
priority = Column(Integer, default=0)                        # line 281 — NO server_default
attempts = Column(Integer, default=0)                        # line 293 — NO server_default
```
(`from sqlalchemy.sql import func, text` is already imported at models/__init__.py:4.)

app/services/campaign_enqueue.py:313-333 — raw INSERT omits `priority` (stores NULL):
```python
INSERT INTO message_queue
    (workspace_id, campaign_id, sender_id, item_type, status,
     recipient_phone, recipient_name, message_text,
     scheduled_at, created_at)
VALUES
    (:wid, :cid, :sid, 'message', 'pending',
     :phone, :name, :text,
     :scheduled, NOW())
```

app/services/queue.py:1550-1563 — `_queue_position` (WR-03, inverted + NULL-blind):
```python
async def _queue_position(db: AsyncSession, sender_id, item_id) -> int:
    """How many pending items are ahead of this one for the same sender."""
    r = await db.execute(
        text("""
            SELECT COUNT(*) FROM message_queue
            WHERE sender_id = :sid
              AND status = 'pending'
              AND (priority, created_at) > (
                  SELECT priority, created_at FROM message_queue WHERE id = :iid
              )
        """),
        {"sid": str(sender_id), "iid": str(item_id)}
    )
    return (r.scalar() or 0) + 1  # 1-based
```
Pick order (queue.py:394, 485): `ORDER BY mq.priority DESC, mq.created_at ASC`.

## Batch D — current state

app/services/queue.py empirical constants (lines 58-61) — DO NOT CHANGE VALUES:
```python
LONG_PAUSE_EVERY_MIN = 12
LONG_PAUSE_EVERY_MAX = 25
LONG_PAUSE_MIN_SECS = 180   # 3 minutes
LONG_PAUSE_MAX_SECS = 600   # 10 minutes
```

app/services/queue.py:250-272 — `_tick` candidate SELECT (add senders eligibility here):
```sql
FROM message_queue mq
JOIN campaigns c ON c.id = mq.campaign_id
WHERE mq.status = 'pending'
  AND mq.scheduled_at <= NOW()
  AND mq.campaign_id IS NOT NULL
  AND c.status = 'running'
  AND (c.start_date IS NULL OR NOW() >= c.start_date)
ORDER BY mq.scheduled_at ASC
LIMIT :batch
```

app/services/queue.py:323-361 — `_get_long_pause_seconds` (own session) + the blocking sleep:
```python
async def _get_long_pause_seconds(self, sender_id) -> Optional[int]:
    pause_every = random.randint(LONG_PAUSE_EVERY_MIN, LONG_PAUSE_EVERY_MAX)
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT COUNT(*) FROM message_queue
            WHERE sender_id = :sid AND status = 'sent'
              AND finished_at >= NOW() - INTERVAL '30 minutes'
        """), {"sid": str(sender_id)})
        recent_count = r.scalar() or 0
    if recent_count > 0 and recent_count % pause_every == 0:
        return random.randint(LONG_PAUSE_MIN_SECS, LONG_PAUSE_MAX_SECS)
    return None

async def _process_next_for_sender(self, sender_id):
    async with AsyncSessionLocal() as db:
        if not await self._check_rate_limits(db, sender_id):
            return
    long_pause = await self._get_long_pause_seconds(sender_id)
    if long_pause:
        logger.info(f"Sender {sender_id}: long pause {long_pause}s ...")
        await asyncio.sleep(long_pause)          # ← WR-04: remove this
    # ... Phase-13 pacing pre-query continues below ...
```

app/services/queue.py:316-319 — `_tick` iterates senders sequentially:
```python
for sender_id in eligible_sender_ids:
    await self._process_next_for_sender(sender_id)
    await asyncio.sleep(0.5)   # small PG-friendly pause — KEEP (not the long pause)
```

## Migrations / infra facts
- Highest existing migration = `046_telegram_service_status.sql`. New: **047** (Batch C), **048** (Batch D).
- Auto-applier: `app/database.py::_apply_migrations` runs every `migrations/*.sql` not in `schema_migrations` on api start, behind an advisory lock. Tracked column is `version` (the filename). Migrations MUST be idempotent; a failing migration aborts api start (fail-fast).
- QueueWorker (`queue_worker.start()`) runs in the **api** container (app/main.py:58 lifespan). Deploy step rebuilds api AND listener per CLAUDE.md.
- `test_queue_item_factory` (tests/conftest.py:790) hardcodes its INSERT column list (workspace_id, campaign_id, sender_id, recipient_phone, item_type, status, scheduled_at) — `**overrides` params are passed but NOT bound into the VALUES list, so it CANNOT set `priority`/`created_at`. Tests that need those columns must issue their own raw-SQL INSERTs.
- `tests/test_queue_new_dialog_limit.py:135` patches `worker._get_long_pause_seconds` → keep that method name intact (return type Optional[int]) so the patch keeps working.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Batch C schema — priority/attempts/as_draft DB defaults + backfill + explicit enqueue (WR-02)</name>
  <files>migrations/047_message_queue_priority_default.sql, app/models/__init__.py, app/services/campaign_enqueue.py</files>
  <action>
Fix the known "ORM `default=` vs `server_default=` drift" class for message_queue (same pattern already fixed for mig 040/042 elsewhere).

1. Create `migrations/047_message_queue_priority_default.sql` (idempotent — `ALTER COLUMN ... SET DEFAULT` is a natural no-op if already set; `UPDATE ... WHERE x IS NULL` is a no-op once backfilled; the auto-applier may re-run on drift):
```sql
-- 047_message_queue_priority_default.sql
-- WR-02: message_queue.priority/attempts/as_draft had NO DB DEFAULT; the
-- campaign_enqueue raw INSERT stored NULL. NULL priority sorts FIRST under
-- ORDER BY priority DESC → inverts documented "higher = processed first".
-- Set DB defaults + backfill existing NULLs. Idempotent.
ALTER TABLE message_queue ALTER COLUMN priority SET DEFAULT 0;
UPDATE message_queue SET priority = 0 WHERE priority IS NULL;

ALTER TABLE message_queue ALTER COLUMN attempts SET DEFAULT 0;
UPDATE message_queue SET attempts = 0 WHERE attempts IS NULL;

ALTER TABLE message_queue ALTER COLUMN as_draft SET DEFAULT false;
UPDATE message_queue SET as_draft = false WHERE as_draft IS NULL;
```
Do NOT add `SET NOT NULL` — out of scope (FIXPLAN specifies defaults + backfill only; ORM columns remain nullable-by-declaration and always supply a Python value on the ORM path).

2. `app/models/__init__.py` (class MessageQueue) — add `server_default` so `create_all` on a fresh/recovered DB matches the migration:
   - line 281: `priority = Column(Integer, default=0, server_default="0")`
   - line 293: `attempts = Column(Integer, default=0, server_default="0")`
   - line 267: `as_draft = Column(Boolean, default=False, server_default=text("false"))`  (`text` is already imported at line 4).

3. `app/services/campaign_enqueue.py:313-333` — pass `priority` explicitly in the raw INSERT (defence-in-depth per FIXPLAN step 3, even though the migration now defaults it). Add `priority` to the column list and `0` (or `:priority` bound to 0) to VALUES. Leave attempts/as_draft to the new DB default.

Parallel-repo caution (Phase 20 work is live in this repo): stage ONLY these three files when committing; never `git add -A`.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_enqueue.py tests/test_campaign_enqueue_worker.py -q</automated>
  </verify>
  <done>Migration 047 exists and is idempotent; ORM MessageQueue has server_default on priority/attempts/as_draft; campaign_enqueue INSERT names priority; targeted enqueue tests pass (no NEW failures vs baseline).</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Batch C — rewrite _queue_position priority-aware + NULL-safe (WR-03)</name>
  <files>app/services/queue.py, tests/test_queue_position.py</files>
  <behavior>
    - Higher-priority pending rows for the same sender count as "ahead" (position increases).
    - Same-priority rows created EARLIER count as ahead; same-priority rows created LATER do NOT.
    - NULL priority is treated as 0 via COALESCE on BOTH sides (rows with NULL priority still counted correctly).
    - Position is 1-based (an item with nothing ahead returns 1).
    - Ordering matches the pick order `priority DESC, created_at ASC`.
  </behavior>
  <action>
Rewrite `app/services/queue.py::_queue_position` (lines 1550-1563). The current tuple comparison `(priority, created_at) > (...)` is inverted (counts later same-priority rows as ahead) AND NULL-blind (NULL priority → whole comparison NULL → counts nothing).

Two-step, parameterized, NULL-safe:
```python
async def _queue_position(db: AsyncSession, sender_id, item_id) -> int:
    """How many pending items are ahead of this one for the same sender.

    'Ahead' mirrors the worker pick order (priority DESC, created_at ASC):
    higher COALESCE(priority,0), or same priority created earlier. NULL-safe.
    """
    ref = (await db.execute(
        text("SELECT COALESCE(priority, 0) AS p, created_at AS c "
             "FROM message_queue WHERE id = :iid"),
        {"iid": str(item_id)},
    )).first()
    if ref is None:
        return 1
    r = await db.execute(
        text("""
            SELECT COUNT(*) FROM message_queue
            WHERE sender_id = :sid
              AND status = 'pending'
              AND ( COALESCE(priority, 0) > :p
                    OR (COALESCE(priority, 0) = :p AND created_at < :c) )
        """),
        {"sid": str(sender_id), "p": ref.p, "c": ref.c},
    )
    return (r.scalar() or 0) + 1  # 1-based
```

Write `tests/test_queue_position.py` (integration, runs via test-overlay). NOTE: `test_queue_item_factory` cannot set `priority`/`created_at` (its INSERT column list is fixed) — issue your own raw-SQL INSERTs into `message_queue` to seed rows with explicit priority (including NULL) and controlled `created_at`, mirroring the factory's raw shape (conftest.py:836). Import `_queue_position` inside the test body (deferred import) to keep `--collect-only` clean. Cover the five behaviors above: a high-priority row ahead of a low one; NULL-priority rows treated as 0; same-priority earlier-ahead / later-not; 1-based when nothing ahead.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_position.py -q</automated>
  </verify>
  <done>_queue_position uses COALESCE + explicit "ahead" predicate matching the pick order; new tests cover mixed and NULL priorities and pass.</done>
</task>

<task type="auto">
  <name>Task 3: Batch D — durable non-blocking long-pause mechanism (WR-04 implementation)</name>
  <files>migrations/048_sender_long_pause_until.sql, app/models/__init__.py, app/services/queue.py</files>
  <action>
Replace the inline `asyncio.sleep(long_pause)` (head-of-line blocking) with a durable per-sender marker. Approved mechanism (FIXPLAN open-decision #2): `senders.long_pause_until` column. **Change ONLY the mechanism — keep every empirical constant (LONG_PAUSE_EVERY_MIN/MAX, LONG_PAUSE_MIN_SECS/MAX_SECS) and the pause_every modulo logic exactly as-is.**

1. Create `migrations/048_sender_long_pause_until.sql` (idempotent):
```sql
-- 048_sender_long_pause_until.sql
-- WR-04: durable per-sender long-pause marker. Replaces the inline
-- asyncio.sleep(long_pause) that stalled the whole shared queue tick. Survives
-- process restart; also acts as the "already paused, don't re-trigger" guard
-- against the modulo double-fire on a static 30-min count.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS long_pause_until TIMESTAMPTZ;
```

2. `app/models/__init__.py` (class Sender) — add near the other durable rest/restriction columns (see `checker_rest_until` at ~line 107 for the exact pattern):
```python
long_pause_until = Column(DateTime(timezone=True), nullable=True)  # WR-04: durable non-blocking long-pause marker
```

3. `app/services/queue.py::_tick` SELECT (lines 250-272) — durably exclude paused senders so a paused sender's items are never selected (this is what makes the pause survive restart — it is re-read from the DB every tick, no in-memory state):
   - Add `JOIN senders s ON s.id = mq.sender_id`.
   - Add to the WHERE: `AND (s.long_pause_until IS NULL OR s.long_pause_until <= NOW())`.

4. `app/services/queue.py::_get_long_pause_seconds` (lines 323-346) — add the "already paused, don't re-trigger" guard (WR-04 double-fire fix). In the same session, read `long_pause_until`; if it is in the future, `return None` (do NOT extend an active pause). Otherwise keep the existing `pause_every`/modulo logic unchanged and return the random duration. Keep the method NAME and `Optional[int]` return (test_queue_new_dialog_limit.py:135 patches it).

5. `app/services/queue.py::_process_next_for_sender` (lines 354-361) — replace the blocking sleep. When `long_pause` is due, persist it instead of sleeping, then `return` (skip this sender this tick; `_tick` will exclude it until it expires):
```python
long_pause = await self._get_long_pause_seconds(sender_id)
if long_pause:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE senders SET long_pause_until = NOW() + make_interval(secs => :dur) WHERE id = :sid"),
            {"dur": long_pause, "sid": str(sender_id)},
        )
        await db.commit()
    logger.info(f"Sender {sender_id}: long pause {long_pause}s set (durable, non-blocking)")
    return
```
Leave the inter-sender `await asyncio.sleep(0.5)` at line 319 untouched (that is the PG-friendly small pause, NOT the long pause).

Stage ONLY these three files on commit (Phase 20 parallel work).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py -q</automated>
  </verify>
  <done>Migration 048 adds long_pause_until; ORM Sender has the column; _tick excludes paused senders; _process_next_for_sender sets the durable marker and returns instead of sleeping; the modulo guard suppresses re-trigger while paused; empirical constants unchanged; existing pacing/new-dialog tests still pass.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 4: Batch D — tests for non-blocking, restart-durable, no-double-trigger pause (WR-04)</name>
  <files>tests/test_queue_long_pause.py</files>
  <behavior>
    - Non-blocking: with sender A due for a long pause, running `_process_next_for_sender(A)` sets `senders.long_pause_until` in the future and returns WITHOUT calling `asyncio.sleep` for the long duration; a second sender B in the same tick is still processed/eligible (its items are NOT blocked).
    - Restart-durable: after long_pause_until is set, a fresh `_tick` candidate SELECT (new session — simulates a process restart, no in-memory state) does NOT select sender A's pending items while long_pause_until > NOW(), and DOES select them again once long_pause_until <= NOW().
    - No double-trigger: `_get_long_pause_seconds(A)` returns None while `long_pause_until` is in the future even when the modulo condition would otherwise fire (guard prevents extending/re-firing an active pause).
  </behavior>
  <action>
Write `tests/test_queue_long_pause.py` (integration via test-overlay). Reuse `test_queue_item_factory` + `test_running_campaign_factory` where possible; issue raw-SQL to set `senders.long_pause_until` and `finished_at`/`status='sent'` counts as needed (factory can't set those columns — use raw INSERT/UPDATE mirroring conftest.py:836). Deferred in-body imports of the queue worker to keep `--collect-only` clean.

- Test 1 (non-blocking / eligibility): seed a running campaign with pending items for two senders; set sender A `long_pause_until = NOW() + interval '5 min'`; assert the `_tick` candidate SELECT (or the eligibility predicate) yields sender B but NOT sender A. Assert no long `asyncio.sleep` is invoked (monkeypatch `asyncio.sleep` and assert it is never called with a value >= LONG_PAUSE_MIN_SECS).
- Test 2 (restart-durable): set A `long_pause_until` in the future via raw SQL, then run the candidate SELECT in a brand-new `AsyncSessionLocal()` (no worker in-memory state) → A excluded. Move `long_pause_until` to the past → A included again. This proves the pause is read from the DB, not memory.
- Test 3 (no double-trigger): set A `long_pause_until` in the future AND seed enough sent rows in the last 30 min that the modulo would fire → assert `_get_long_pause_seconds(A)` returns None (does not re-trigger while paused).

Do not assert on exact pause values beyond the constant bounds — the durations are randomized and must stay untouched.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_long_pause.py -q</automated>
  </verify>
  <done>Three tests cover non-blocking behavior, restart durability (DB-read not memory), and no-double-trigger; all pass via test-overlay.</done>
</task>

<task type="auto">
  <name>Task 5: Deploy to prod + live sanity check</name>
  <files>(none — ops)</files>
  <action>
After Tasks 1-4 are committed (stage only the files this plan touches — Phase 20 work is live in the repo), deploy per CLAUDE.md and verify.

1. Backup first (cheap, ~1s): `/root/apps/aimly/tg-outreach/backup.sh` (records the pre-deploy dump under /root/backups/tg-outreach/ — restore point for the 047 backfill).
2. Deploy (the auto-applier runs migrations 047 + 048 on api start; fail-fast if a migration is not idempotent):
   ```bash
   cd /root/apps/aimly/tg-outreach && docker compose up -d --build api
   docker compose up -d --build listener
   ```
3. Live sanity checks (read-only):
   - Migrations recorded: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SELECT version FROM schema_migrations WHERE version LIKE '047%' OR version LIKE '048%' ORDER BY version;"` → expect both rows.
   - WR-02 backfill: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SELECT COUNT(*) AS null_priority FROM message_queue WHERE priority IS NULL;"` → expect 0.
   - WR-04 column present: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SELECT column_name FROM information_schema.columns WHERE table_name='senders' AND column_name='long_pause_until';"` → one row.
   - Queue tick not blocking: `docker compose logs --tail=200 api | grep -iE "long pause|error"` — a long pause should now log "set (durable, non-blocking)" (if any fires); confirm the queue worker keeps ticking (no multi-minute gap between tick log lines). The api container is where QueueWorker runs.
4. Record deploy outcome + verification results in the SUMMARY.
  </action>
  <verify>
    <automated>docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -tAc "SELECT COUNT(*) FROM schema_migrations WHERE version LIKE '047%' OR version LIKE '048%';"</automated>
  </verify>
  <done>api + listener rebuilt; migrations 047 and 048 present in schema_migrations; zero NULL-priority rows; senders.long_pause_until exists; queue worker logs show continuous ticking (no inline long-pause block).</done>
</task>

</tasks>

<verification>
- Migrations 047 + 048 are idempotent (re-run safe) and applied by the auto-applier without api-start failure.
- `message_queue.priority`/`attempts`/`as_draft` have DB defaults; no NULL priority rows remain in prod.
- `_queue_position` returns positions consistent with the `priority DESC, created_at ASC` pick order, NULL-safe.
- No inline long `asyncio.sleep` remains in the shared queue tick; a paused sender is excluded via `senders.long_pause_until` and other senders keep sending.
- Empirical constants (rate limits, LONG_PAUSE_EVERY/SECS bounds, pause_every logic) are byte-for-byte unchanged — grep-verify lines 58-61 of queue.py untouched.
- Targeted test files pass via test-overlay. NOTE: the full suite has a KNOWN pre-existing cascade (baseline 08d567d: ~71 failed/80 errors from `test_phase5_migration_017` pooled-conn poisoning, unrelated) — measure the DELTA (new tests pass + no NEW failures introduced), do NOT gate on full-suite green.
</verification>

<success_criteria>
- Batch C (WR-02, WR-03) and Batch D (WR-04) implemented exactly per FIXPLAN, mechanism-only for the pause.
- Two idempotent migrations committed and applied in prod.
- New tests cover priority/NULL queue position and the three WR-04 behaviors (non-blocking, restart-durable, no double-trigger).
- Deployed to prod (api + listener) with a passing live sanity check.
- Commits stage only this plan's files (Phase 20 parallel work untouched).
</success_criteria>

<output>
After completion, create `.planning/quick/260703-ssv-close-batch-c-queue-priority-position-wr/260703-ssv-SUMMARY.md`
</output>
