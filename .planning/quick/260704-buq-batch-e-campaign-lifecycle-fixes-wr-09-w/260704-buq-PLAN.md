---
phase: 260704-buq
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/campaign_enqueue.py
  - app/services/queue.py
  - app/routers/campaigns.py
  - app/schemas/__init__.py
  - tests/test_campaign_enqueue_worker.py
  - tests/test_queue_lifecycle_fixes.py
  - tests/test_campaign_lifecycle_fixes.py
autonomous: true
requirements: [WR-09, WR-12, IN-05, IN-06, IN-07, IN-10, IN-11, IN-12]

must_haves:
  truths:
    - "WR-09: the enqueue worker never inserts a pending row into a campaign that is not 'running' at insert time"
    - "IN-11: one campaign raising inside the enqueue tick does not abort enqueue for the remaining campaigns"
    - "WR-12a: a cold terminal-failed queue item (no prior sent for its campaign+phone) releases its campaign_contact_assignments row so the contact is eligible again"
    - "WR-12b: POST /campaigns/{id}/requeue-failed re-pends failed items and returns {requeued_count}"
    - "WR-12b: GET /campaigns/{id} response carries failed_count"
    - "IN-05: attach_sender only 409s when the newly-attached sender itself conflicts with another running campaign"
    - "IN-06: a duplicate-name collision in duplicate_campaign returns 409, not 500"
    - "IN-07: a past_stop_date fail fires the callback webhook and does not overwrite a row already cancelled by a concurrent API call"
    - "IN-10: pool_health.active excludes session_expired / lifecycle-paused senders"
    - "IN-12: dispatcher-sent messages are logged into messages with sent_by='ai', not 'human'"
  artifacts:
    - path: "app/services/campaign_enqueue.py"
      provides: "status-gated INSERT (WR-09) + per-campaign try/except in _tick (IN-11)"
    - path: "app/services/queue.py"
      provides: "cold-fail CCA release (WR-12a), stop_date guard+callback (IN-07), sent_by='ai' (IN-12)"
    - path: "app/routers/campaigns.py"
      provides: "requeue-failed endpoint + failed_count (WR-12b), attach lock filter (IN-05), duplicate IntegrityError (IN-06), pool_health active predicate (IN-10)"
    - path: "app/schemas/__init__.py"
      provides: "failed_count field on CampaignResponse (WR-12b)"
  key_links:
    - from: "app/services/campaign_enqueue.py INSERT"
      to: "campaigns.status='running'"
      via: "INSERT ... SELECT ... WHERE EXISTS"
      pattern: "WHERE EXISTS.*campaigns.*status = 'running'"
    - from: "app/services/queue.py _fail_item"
      to: "campaign_contact_assignments"
      via: "DELETE on cold terminal fail"
      pattern: "DELETE FROM campaign_contact_assignments"
    - from: "app/routers/campaigns.py requeue_failed"
      to: "message_queue status='failed' -> 'pending'"
      via: "UPDATE re-pend"
      pattern: "requeue-failed"
---

<objective>
Batch E of the checker+campaigns review fix plan: eight campaign-lifecycle correctness fixes across the enqueue worker, the queue dispatcher, and the campaigns router. These are all latent data-integrity bugs on a LIVE production system with real customer data — implement the exact verified specs below, do not re-derive.

Purpose: stop the campaign pipeline from (a) creating permanent zombie pending rows on finished campaigns, (b) silently absorbing contacts on terminal failure, (c) starving campaigns when one errors, (d) mis-reporting pool capacity, (e) corrupting AI-vs-human attribution, plus three smaller correctness holes (attach 409s, duplicate 500s, missing stop-date callbacks).

Output: 4 source files edited + 3 test files (1 existing, 2 new) + green test-overlay run + api rebuild + one-off prod remediation SQL + live verification.

Source: `.planning/reviews/260703-checker-campaigns-FIXPLAN.md` (Батч E) and the finding text (WR-09, WR-12, IN-05, IN-06, IN-07, IN-10, IN-11, IN-12) in `.planning/reviews/260703-checker-campaigns-REVIEW.md`.
</objective>

<context>
@/root/apps/aimly/tg-outreach/CLAUDE.md
@/root/apps/aimly/tg-outreach/.planning/reviews/260703-checker-campaigns-FIXPLAN.md

<constraints>
- **Русский** для общения с владельцем; код и коммиты — английский.
- **Parallel work in repo:** Phase 20 is executing concurrently. NEVER `git add -A`. Stage ONLY the specific files this plan touches (memory `feedback-parallel-agent-careful-commits.md`).
- **NEVER change** rate-limit / interval / long-pause / flood constants (CLAUDE.md guard). None of these fixes require it.
- **No migration.** failed_count is a computed COUNT(*) at read time, not a stored column. Do NOT add an ORM `.sql` migration.
- **Tests run ONLY via test-overlay.** Never `docker compose run --rm api pytest` bare (conftest guard DROPs the prod schema). Always the overlay form in Task 4.
- Do NOT touch unrelated files. Do NOT refactor beyond the specs. Do NOT touch `app/routers/conversations.py::send_message_from_ui` (its `sent_by='human'` is correct and must stay).
</constraints>

<interfaces>
<!-- Extracted from the codebase so the executor works from contracts, not exploration. -->

message_queue columns used here: id, workspace_id, campaign_id (NULLable), sender_id, item_type, status ('pending'|'processing'|'sent'|'failed'|'cancelled'), recipient_phone, recipient_name, message_text, priority, attempts, error_message, callback_url, extra_data (JSONB), scheduled_at, finished_at, created_at.

campaign_contact_assignments: UNIQUE(campaign_id, contact_phone). The enqueue worker's dedup is `contacts.identity NOT IN (SELECT contact_phone FROM campaign_contact_assignments WHERE campaign_id=:cid)`. Deleting a row here makes the contact eligible again on the next enqueue tick.

_fire_callback signature (queue.py:1421):
  async def _fire_callback(self, url, queue_id, status, sender_slug, recipient_phone,
      recipient_name=None, recipient_telegram_id=None, recipient_username=None,
      message_id=None, error=None, extra_data=None)
  Pattern to mirror (per-item, status="failed"): the SessionAuthError branch at queue.py:1235-1244.

_fail_item (queue.py:1260): terminal branch = `new_status == QueueItemStatus.failed` (attempts >= MAX_ATTEMPTS). It commits at the end (db.commit() line 1304). Keep any CCA delete in that SAME transaction (before that commit).

_check_sender_lock (campaigns.py:374): returns list of {sender_id, campaign_id, campaign_name} for OTHER running campaigns in the workspace sharing ≥1 sender with :cid. Callers: start_campaign (802), resume_campaign (854), attach_sender (1040).

create_campaign IntegrityError pattern to mirror (campaigns.py:492-502):
  try: await db.flush()
  except IntegrityError as e:
      await db.rollback()
      if "idx_campaigns_workspace_name" in str(e.orig).lower() or "duplicate" in str(e.orig).lower():
          raise HTTPException(409, detail={"code":"CAMPAIGN_NAME_DUPLICATE", ...})
      raise

_compute_pool_health (campaigns.py:278): `active = COUNT(*) FILTER (WHERE s.restriction_status='none')`.
_maybe_autopause eligibility predicate to mirror (campaign_enqueue.py:128-140):
  s.lifecycle_status='active' AND s.auth_status='ok' AND s.role='sender' AND s.restriction_status='none'.
  NOTE for IN-10: mirror `restriction_status='none' AND auth_status='ok' AND lifecycle_status='active'` — do NOT add role='sender' (campaign_senders should only hold sender-role rows; no evidence a role filter is needed on pool_health — leave it out).

CampaignResponse (schemas/__init__.py:796): add `failed_count: int = 0`. Built in _campaign_to_response (campaigns.py:306).

Test fixtures (tests/conftest.py): async_client, async_db_session, valid_supabase_jwt (callable, sub=...), test_workspace, test_sender_factory, test_folder, test_agent_factory, test_queue_item_factory. `_fail_item` is called directly in tests/test_queue_workspace_id.py:110. `__send_item_inner` via `queue_worker._QueueWorker__send_item_inner(item_id)` (tests/test_phase5_bot_filter.py:346).
</interfaces>
</context>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Enqueue-worker lifecycle safety — WR-09 status-gated INSERT + IN-11 per-campaign try/except</name>
  <files>app/services/campaign_enqueue.py, tests/test_campaign_enqueue_worker.py</files>
  <behavior>
    - WR-09: if a campaign flips to 'done'/'stopped'/anything != 'running' between tick-start snapshot and the per-contact commit, the worker's INSERT adds 0 rows (no zombie pending). Test: run _tick_one_campaign against a campaign whose status was flipped to 'done' after selection → 0 queue rows inserted, enqueued==0.
    - WR-09: for a still-'running' campaign the INSERT behaves exactly as before (rows inserted, enqueued incremented). Existing enqueue tests stay green.
    - IN-11: if _tick_one_campaign raises for one campaign, the loop logs, rolls back, and still processes the remaining running campaigns. Test: two running campaigns, monkeypatch _tick_one_campaign to raise on the first → second campaign still enqueues, tick does not propagate the exception.
  </behavior>
  <action>
    **WR-09** — in `_tick_one_campaign` (~line 313-334), change the per-contact `INSERT INTO message_queue (...) VALUES (...)` to an `INSERT ... SELECT ... WHERE EXISTS` that re-asserts campaign status at insert time, INSIDE the existing `begin_nested()` savepoint:
    ```sql
    INSERT INTO message_queue
        (workspace_id, campaign_id, sender_id, item_type, status,
         recipient_phone, recipient_name, message_text,
         priority, scheduled_at, created_at)
    SELECT :wid, :cid, :sid, 'message', 'pending',
           :phone, :name, :text, :priority, :scheduled, NOW()
    WHERE EXISTS (SELECT 1 FROM campaigns WHERE id = :cid AND status = 'running')
    ```
    Capture the result and only `enqueued += 1` when `result.rowcount == 1` (an EXISTS-miss inserts 0 rows → no-op, do not increment). Keep the same bind params (`priority` stays explicit 0 per WR-02 already in place).

    **IN-11** — in `_tick` (~line 99-106), wrap the per-campaign body (`await self._maybe_autopause(db, c)` + `await self._tick_one_campaign(db, c)`) in try/except. On exception: `logger.error("CampaignEnqueueWorker: campaign %s tick failed: %s", c.id, exc, exc_info=True)`, `await db.rollback()` (clear half-open TX state before the next campaign reuses `db`), then `continue`. Do not let it re-raise into `_run`.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_enqueue_worker.py -x -q</automated>
  </verify>
  <done>Both new tests (WR-09 status-gated no-op, IN-11 continue-on-error) pass; all pre-existing test_campaign_enqueue_worker.py tests stay green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Queue dispatcher fixes — WR-12a CCA release, IN-07 stop_date guard+callback, IN-12 sent_by='ai'</name>
  <files>app/services/queue.py, tests/test_queue_lifecycle_fixes.py</files>
  <behavior>
    - WR-12a: when `_fail_item` fails an item terminally (attempts>=MAX_ATTEMPTS) and there is NO 'sent' row for (campaign_id, recipient_phone), the matching campaign_contact_assignments row (campaign_id, contact_phone=recipient_phone) is deleted. Test: seed a CCA row + a failed-terminal item with no prior sent → after _fail_item the CCA row is gone. Control: with a prior 'sent' row present → CCA row is NOT deleted.
    - WR-12a: only fires when item.campaign_id IS NOT NULL (guard defensively). Same transaction as the status UPDATE.
    - IN-07: past_stop_date UPDATE only touches rows still 'pending' (add `AND status='pending'`), so a row cancelled concurrently is not clobbered back to 'failed'. Test: an item pre-set to 'cancelled' is NOT flipped to 'failed' by the stop_date fail path.
    - IN-07: after failing past_stop_date items, a callback fires for each item with a non-null callback_url (status="failed"). Test: assert _fire_callback is scheduled for a past_stop_date item that has a callback_url.
    - IN-12: dispatcher-sent messages insert into `messages` with sent_by='ai'.
  </behavior>
  <action>
    **IN-12** (smallest, do first) — in `_upsert_conversation` (~line 1408-1410), change the hardcoded `'human'` literal in the `INSERT INTO messages (... sent_by ...) VALUES (..., 'human', ...)` to `'ai'`. Enum per migration 017 is `'contact'|'ai'|'human'`; listener.py already uses `'ai'` for auto-replies. Do NOT touch `app/routers/conversations.py`.

    **WR-12a** — in `_fail_item` (~line 1260), inside the `if new_status == QueueItemStatus.failed:` branch (after the MessageLog add, before the final `await db.commit()` at ~1304, so it is atomic with the status UPDATE): if `item.campaign_id is not None`, check for a prior sent and release the cold CCA:
    ```python
    # WR-12: a cold terminal fail (never sent for this campaign+phone) must not
    # permanently absorb the contact. Release its sticky CCA so the enqueue
    # worker's NOT IN dedup makes it eligible again next tick. Engaged/sent
    # contacts (a prior 'sent' row exists) are left alone.
    if item.campaign_id is not None:
        has_sent = (await db.execute(text("""
            SELECT 1 FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone AND status = 'sent'
            LIMIT 1
        """), {"cid": str(item.campaign_id), "phone": item.recipient_phone})).first()
        if has_sent is None:
            await db.execute(text("""
                DELETE FROM campaign_contact_assignments
                WHERE campaign_id = :cid AND contact_phone = :phone
            """), {"cid": str(item.campaign_id), "phone": item.recipient_phone})
    ```

    **IN-07** — two UPDATE locations that fail items for `past_stop_date`:
    1. `_tick` batch fail (~line 304-315, `WHERE id = ANY(:ids)`).
    2. `_process_next_for_sender` pick-time fail (~line 541-551, `stop_date_failed_ids`).
    For BOTH:
    (a) Add `AND status = 'pending'` to the WHERE clause of the UPDATE (guard against a concurrent cancel between SELECT and UPDATE).
    (b) Fire the callback for each affected item that has a non-null callback_url, mirroring the SessionAuthError branch (queue.py:1235-1244) — `asyncio.create_task(self._fire_callback(url=..., queue_id=str(id), status="failed", sender_slug=..., recipient_phone=..., error="past_stop_date", extra_data=...))`. Use `RETURNING id, callback_url, recipient_phone, extra_data, sender_id` on the UPDATE to get the columns in one query, then resolve sender_slug with a small `SELECT id, slug FROM senders WHERE id = ANY(:sids)` map (sender_slug is not on message_queue). Only schedule a callback when callback_url is not null. Note: `_tick`'s fail runs inside `async with AsyncSessionLocal() as db:` — fire the callbacks after the `await db.commit()`; the per-sender path commits later at ~line 563, fire after that commit or right after the UPDATE (callbacks are fire-and-forget tasks, ordering vs commit is not load-bearing, but keep them after the UPDATE executes).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_lifecycle_fixes.py tests/test_queue_workspace_id.py -x -q</automated>
  </verify>
  <done>New tests for WR-12a (cold fail releases CCA / warm fail keeps it), IN-07 (status='pending' guard + callback fired), IN-12 (sent_by='ai') pass; test_queue_workspace_id.py (exercises _fail_item terminal path) stays green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Campaigns router + schema — WR-12b requeue+failed_count, IN-05 attach lock filter, IN-06 duplicate 409, IN-10 pool_health active</name>
  <files>app/routers/campaigns.py, app/schemas/__init__.py, tests/test_campaign_lifecycle_fixes.py</files>
  <behavior>
    - WR-12b: POST /campaigns/{id}/requeue-failed re-pends all status='failed' message_queue rows for the campaign (status='pending', attempts=0, error_message=NULL, finished_at=NULL, scheduled_at=NOW()) and returns {"requeued_count": N}. Workspace-scoped (404 for other workspace's campaign). Test: seed 2 failed items → requeued_count==2, rows now pending.
    - WR-12b: GET /campaigns/{id} response contains failed_count = COUNT of status='failed' message_queue rows. Test: seed 1 failed item → failed_count==1.
    - IN-05: attaching a free sender to campaign B, where B already contains a DIFFERENT sender that is also in running campaign A, does NOT 409 (only the newly-attached sender is checked). Test: attach a conflict-free sender → 200; attach a sender that IS in a running campaign → 409 SENDER_LOCK_CONFLICT.
    - IN-06: duplicate_campaign on a name collision returns 409 CAMPAIGN_NAME_DUPLICATE (not 500) when the INSERT hits the unique index.
    - IN-10: pool_health.active counts only senders with restriction_status='none' AND auth_status='ok' AND lifecycle_status='active'. Test: attach one healthy + one session_expired sender → active==1, total==2.
  </behavior>
  <action>
    **IN-10** — in `_compute_pool_health` (~line 287-297) change the active filter to:
    `COUNT(*) FILTER (WHERE s.restriction_status = 'none' AND s.auth_status = 'ok' AND s.lifecycle_status = 'active') AS active`.
    Leave `total` and `paused` (`restriction_status <> 'none'`) unchanged. Do NOT add a role filter or a 4th bucket.

    **IN-05** — change `_check_sender_lock` signature to `_check_sender_lock(db, ctx, campaign_id, only_sender_id: Optional[UUID] = None)`. When `only_sender_id` is provided, add `AND cs.sender_id = :only_sender_id` to the WHERE clause (bind `str(only_sender_id)`), so only that one sender's conflicts are returned. In `attach_sender` (~line 1040) call `_check_sender_lock(db, ctx, c.id, only_sender_id=payload.sender_id)`. Leave `start_campaign` (~802) and `resume_campaign` (~854) call sites unchanged (they must keep checking the full pool).

    **IN-06** — in `duplicate_campaign` (~line 987-992) wrap `db.add(new_c); await db.flush(); await db.commit()` in the same `try/except IntegrityError` pattern used by `create_campaign` (campaigns.py:492-502): on IntegrityError → `await db.rollback()` → if `"idx_campaigns_workspace_name" in msg or "duplicate" in msg` raise HTTPException(409, detail={"code":"CAMPAIGN_NAME_DUPLICATE", "message": f"Campaign '{candidate}' already exists"}), else re-raise. (Keep the existing TOCTOU name-pick loop — the try/except is the race-safe backstop.)

    **WR-12b (endpoint)** — add `POST /campaigns/{campaign_id}/requeue-failed` on the existing router. Under `Depends(auth_dep)`, load via `_load_campaign(db, ctx, campaign_id)` (workspace-scoped 404). Then:
    ```sql
    UPDATE message_queue
    SET status='pending', attempts=0, error_message=NULL, finished_at=NULL, scheduled_at=NOW()
    WHERE campaign_id = :cid AND status = 'failed'
    ```
    `await db.commit()`; return a small Pydantic response `{"requeued_count": result.rowcount or 0}` (define a `_RequeueFailedResponse(BaseModel){ requeued_count: int }` near the other inline response models, e.g. beside `_RerenderResponse`). Log the count.

    **WR-12b (failed_count)** — add `failed_count: int = 0` to `CampaignResponse` in `app/schemas/__init__.py` (place it near `is_exhausted` / `pool_health`). In `_campaign_to_response` (campaigns.py:306) compute it:
    ```python
    failed_count = (await db.execute(text(
        "SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid AND status = 'failed'"
    ), {"cid": str(campaign.id)})).scalar() or 0
    ```
    and pass `failed_count=failed_count` into the `CampaignResponse(...)` constructor.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_lifecycle_fixes.py tests/test_campaign_router.py -x -q</automated>
  </verify>
  <done>New tests for WR-12b (requeue + failed_count), IN-05 (attach filter), IN-06 (409 on collision), IN-10 (active excludes expired) pass; test_campaign_router.py stays green.</done>
</task>

<task type="auto">
  <name>Task 4: Full suite green + deploy api + WR-09 prod remediation + live verification</name>
  <files>(no source edits — build/ops/verify)</files>
  <action>
    1. **Full test-overlay run** — confirm the whole suite is green, not just the touched files:
       `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q`
       (The ephemeral db-test in tmpfs is torn down after the run. NEVER run bare `docker compose run --rm api pytest`. NEVER `down -v`.)
    2. **Deploy** — rebuild the api container only (both affected workers — queue dispatcher + campaign enqueue — run in the api process per main.py lifespan; the listener does not import the changed modules, so a listener rebuild is not required):
       `docker compose up -d --build api`
       Wait for health, then confirm no crash on boot: `docker compose logs --tail=60 api` (migrations applied, no traceback).
    3. **WR-09 prod remediation (one-off, AFTER deploy — NOT a migration)** — cancel the existing zombie pending row(s) on non-running campaigns:
       ```
       docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "UPDATE message_queue SET status='cancelled', finished_at=NOW(), error_message='zombie cleanup WR-09' WHERE status='pending' AND campaign_id IN (SELECT id FROM campaigns WHERE status NOT IN ('running','paused'));"
       ```
       (FIXPLAN verified 1 such row on 2026-07-03; the psql `-c` form is required — heredoc without `-i` is a silent no-op per memory.) Report the UPDATE count.
    4. **Live verification:**
       - failed_count + requeue-failed reach the wire: `curl -s http://127.0.0.1:8005/openapi.json | grep -o 'requeue-failed' | head -1` (endpoint present) and confirm `failed_count` appears in the CampaignResponse schema: `curl -s http://127.0.0.1:8005/openapi.json | grep -o 'failed_count' | head -1`.
       - IN-11 not breaking normal operation: watch a couple of enqueue ticks — `docker compose logs --tail=120 api | grep -i "CampaignEnqueueWorker"` shows normal ticks and no per-campaign error spam.
       - No new errors overall: `docker compose logs --tail=200 api | grep -iE "traceback|error" | tail -20` is clean of new regressions.
       - (Optional, nice-to-have) an authenticated `GET /api/v1/campaigns/{id}` for an existing campaign returns `failed_count` — do this only if a workspace JWT is readily available; otherwise the openapi check above is sufficient.
    5. **Commit** — stage ONLY this plan's files (Phase 20 is executing in parallel — NEVER `git add -A`):
       ```
       node .claude/get-shit-done/bin/gsd-tools.cjs commit "fix(quick-260704-buq): Batch E campaign lifecycle fixes (WR-09/WR-12/IN-05/06/07/10/11/12)" --files app/services/campaign_enqueue.py app/services/queue.py app/routers/campaigns.py app/schemas/__init__.py tests/test_campaign_enqueue_worker.py tests/test_queue_lifecycle_fixes.py tests/test_campaign_lifecycle_fixes.py .planning/quick/260704-buq-batch-e-campaign-lifecycle-fixes-wr-09-w/260704-buq-PLAN.md
       ```
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q</automated>
  </verify>
  <done>Full suite green; api rebuilt and healthy (no boot traceback); zombie pending row(s) cancelled (count reported); openapi.json exposes requeue-failed + failed_count; enqueue-worker logs clean; changes committed staging only the listed files.</done>
</task>

</tasks>

<verification>
- Every fix (WR-09, WR-12a, WR-12b, IN-05, IN-06, IN-07, IN-10, IN-11, IN-12) has at least one asserting test in one of the three test files.
- Full test-overlay suite is green (Task 4 step 1).
- api container rebuilt and boots clean.
- Zombie pending remediation SQL executed once, count reported.
- openapi.json shows the new endpoint + field.
</verification>

<success_criteria>
- 4 source files edited exactly per spec; no unrelated files touched; no rate/interval constants changed; no migration added.
- `conversations.py::send_message_from_ui` untouched (still sent_by='human').
- All new + pre-existing tests pass via test-overlay.
- Production api serving the fixes; WR-09 zombie row(s) cleaned; failed_count + requeue-failed live.
- Commit stages only this plan's file list.
</success_criteria>

<output>
After completion, create `.planning/quick/260704-buq-batch-e-campaign-lifecycle-fixes-wr-09-w/260704-buq-SUMMARY.md` recording: which findings were closed, the WR-09 remediation row count, the final test count, and confirmation the api was rebuilt (listener not required).
</output>
