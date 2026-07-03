---
status: issues
files_reviewed: 16
findings:
  critical: 2
  warning: 14
  info: 12
  total: 28
depth: deep
date: 2026-07-03
scope: checker + campaign sending
---

# Cross-Cutting Review: Contact-Checking Pipeline + Campaign Sending Pipeline

**Reviewed:** 2026-07-03
**Depth:** deep (cross-file call-chain tracing; live prod schema consulted read-only for verification)
**Files Reviewed:** 16 (config.py, models/__init__.py, routers/{campaigns,check_contacts,queue,send}.py, services/{campaign_enqueue,checker,contact_check_worker,failover,queue,rebalance,restriction_audit,rotation,telegram,template}.py)
**Status:** issues_found

---

## Architecture Assessment

### Data flow — contact-checking pipeline

```
POST /contacts/recheck ──> contacts.tg_status='pending'
                                 │
ContactCheckWorker._run loop (api container, single asyncio task):
  1. _recover_checkers  ── re-probe cooled-down spam_limited checkers ──> clear or leave parked
  2. _probe_cycle       ── control-probe eligible checkers (≤1/15min each)
  3. _tick:
     SELECT pending contacts + JOIN LATERAL eligible checker
       (role=checker, auth ok, restriction none, not paused, not resting,
        under durable daily cap)  FOR UPDATE OF c SKIP LOCKED
     └─ claim: tg_checked_at=NOW() (5-min claim window)
     └─ per checker group:
          CheckerService.check_phones / check_usernames
            └─ _lookup_cache (workspace-wide, 7-day TTL, negative bucket
               confidence-gated) → live ResolvePhone → ImportContacts fallback
               (+DeleteContacts) → _save_cache
          _maybe_degrade_on_signal (inline FloodWait / all-empty anomaly)
          _apply_results:
            registered  → 'registered' (+provenance, confidence high iff clean)
            not_reg     → clean: 'not_registered'/high  | suspect: rollback to 'pending'
          _rest_checker (checker_rest_until), _reset_checker_trip on clean batch
```

### Data flow — campaign sending pipeline

```
campaigns router (CRUD + start/pause/resume/finish, sender lock, pool mgmt)
        │ status='running'
CampaignEnqueueWorker (30s tick):
  _maybe_autopause (zero eligible senders + work remaining → paused)
  _tick_one_campaign:
    SELECT folder contacts tg_status='registered'
      NOT IN campaign_contact_assignments (dedup)
      NOT IN conversations (identity-scoped re-contact policy)
    per contact (savepoint): rotation.get_or_assign_sender (sticky CCA,
      ON CONFLICT DO NOTHING) → render_template → INSERT message_queue(pending)
        │
QueueWorker (3s tick):
  _tick: pending items JOIN running campaigns → per-sender eligibility
  _process_next_for_sender: rate gates (4/20/150 + 15 uniq/h + 20-55s interval
    + long pause) → Phase-12 new-dialog cap + Phase-13 pacing → pick item
    FOR UPDATE SKIP LOCKED → status='processing' → commit
  __send_item_inner: pre-send guards (manual takeover, follow-up replied-since)
    → TelegramService.get_client (per-op connect) → resolve ladder
      (per-sender cache → captured-@username ResolveUsername → gated ImportContacts)
    → send → sent + messages_log + _upsert_conversation | error branches:
      FLOOD_WAIT (reschedule), PEER_FLOOD/ACCOUNT_FROZEN (flag sender +
      restriction_audit same-TX + pause backlog 24h + failover_cold_backlog),
      PRIVACY/BLOCKED (audit + fail item), retry ≤3 else failed
rebalance_on_attach / failover_cold_backlog: set-based CCA+queue moves under
  the worker's own SKIP LOCKED discipline.
```

### Strengths

- **Postgres-as-queue discipline is largely correct.** `FOR UPDATE ... SKIP LOCKED` + status guards are used consistently across the worker pick, rebalance, and failover; queue/CCA moves happen in one transaction; `recover_stuck_jobs` reclaims orphaned `processing` rows at startup.
- **Restriction handling is atomic and auditable.** `record_restriction_event` shares the caller's transaction with the `senders` status UPDATE (audit and state cannot diverge on crash), `.one_or_none()` guards deleted senders, and the `extension` de-noising gate is well thought out.
- **Workspace scoping is systematically enforced** in every live router examined (`auth_dep` + explicit `workspace_id` predicates, defence-in-depth guards in workers). No cross-tenant read/write was found in any *mounted* endpoint.
- **The checker suspect/rollback machinery is a genuinely good design** (finalize `not_registered` only at high confidence, registered always kept, provenance columns), and the escalating trip ladder with "reset only on a clean real batch" closes the flapping loop correctly — *at the state-machine level*. Its input signals, however, have two holes (CR-01, CR-02).
- **Idempotent dedup at the campaign layer**: `campaign_contact_assignments` UNIQUE(campaign_id, contact_phone) + `ON CONFLICT DO NOTHING` makes the enqueue worker race-safe against a second replica for the cold-open path.

### Top structural risks

1. **Single-coroutine workers with inline sleeps.** One asyncio task drains all senders' queues and one drains all workspaces' checks. Any inline sleep (3–10 min long-pause, unbounded FloodWait sleep in the checker) stalls *every* tenant (WR-04, WR-05). This is the same head-of-line failure class as the already-fixed warmup bug. As tenant count grows this becomes the first scaling wall — before Postgres does.
2. **Critical health state is in-memory and per-process.** `_consecutive_misses`, `_degraded_this_tick`, `_last_probe_at`, the Telethon per-slug locks, and the SpamBot self-check registry all live on singletons. A second api replica (or api-vs-listener split) silently breaks probe accounting and lock exclusivity. The durable columns (trip count, rest, restriction) are right; the transient layer is not replica-safe. Documented as v1-single-worker, but nothing *enforces* it (no advisory lock around worker ticks, unlike migrations).
3. **The identity-key string (`+E164` or `@username`) is the join key across five tables** (contacts→cca→message_queue→conversations→contacts_cache) with normalization enforced only at CSV import. Any un-normalized entry point (WR-10: `/send`) forks a contact's identity and silently defeats every dedup/re-contact/protection predicate downstream.
4. **At-least-once send semantics with no idempotency key.** `recover_stuck_jobs` re-pends any `processing` row >10 min old; a worker crash after Telegram accepted but before the `sent` commit produces a duplicate message. `/send` has no request idempotency either (WR-11). For an outreach product where a duplicate cold opener is a spam signal, this deserves an explicit dedup key (e.g. UNIQUE(campaign_id, recipient_phone) partial index on live statuses).
5. **The resolve cache doubles as the health-probe's data source** (CR-01). Cache-as-optimization and cache-as-truth are conflated: the component whose entire job is to detect a lying checker reads the very cache that lying checkers write. The correct live-only primitive (`probe_control`) was built and then never wired.
6. **Failure is an absorbing state for contacts.** A queue item that fails 3 transient errors permanently consumes its contact (sticky CCA, no requeue, no UI surface) — over a long campaign this silently erodes reach (WR-12).

---

## Critical Issues

### CR-01: Control-probe and recovery-probe run through the cache-consulting `check_phones` instead of the live-only `probe_control` — probes stop testing anything and can cascade-park the whole checker pool

**File:** `app/services/contact_check_worker.py:591-599` (probe), `:748-756` (recovery); `app/services/checker.py:393-404` (cache-first), `:427` (cache write); `app/services/checker.py:305-372` (`probe_control`, dead code)

**Issue:** `probe_checker` and `_recover_checkers` both call `checker_service.check_phones(...)` on the control sample. `check_phones` (a) consults `_lookup_cache` first — a **workspace-wide, any-sender, 7-day** cache — and (b) writes every live result back via `_save_cache`. The codebase already contains the correct primitive: `CheckerService.probe_control` (checker.py:305), whose own docstring states *"a probe that consults contacts_cache tests nothing — a silently-throttled checker would 'pass' on stale cached hits"*. It is never called anywhere (verified by grep across app/ and tests/).

**Concrete failure scenarios (all traced through the code):**

1. **Probe goes dormant.** First probes live-resolve control numbers and cache them `is_registered=true`. Every subsequent probe of those numbers within 7 days is a cache hit (`_lookup_cache` serves positives unconditionally) → `miss=False` without a single Telegram call. With 49 control numbers, 3-number samples and 15-min probe intervals, the control set is fully cached within hours; from then on the "active probe backstop" verifies nothing until rows age out.
2. **Fake recovery.** `_recover_checkers` clears `spam_limited` when `all(is_registered)` on the sample — which can be satisfied **entirely from another checker's cached positives**. A still-throttled checker is returned to rotation with zero live evidence. The trip-ladder (deliberately not reset here) limits flap frequency but each fake recovery still yields another poisoned batch and more contacts-API burn on a throttled account.
3. **Pool-wide false-degrade cascade (workspace-cache poisoning path the task asked to hunt).** A throttled checker live-resolves control number X → false → `_save_cache(..., is_registered=false)`. The control contact's `contacts` row is `registered/high/clean`, so the negative-bucket suppression gate in `_lookup_cache` (`tg_probe_state='suspect' OR tg_confidence IS DISTINCT FROM 'high'`) does **not** suppress it. Now every probe of X by **every** checker in the workspace reads the cached false → counts a MISS → after 2 cycles all checkers are flagged `spam_limited`; recovery probes read the same cached false and never come back clean. One bad live resolve of one control number can park the entire pool for up to 7 days (until the false row leaves the TTL window — it is never overwritten, because cache hits skip the live path).

**Fix:**
```python
# contact_check_worker.py — probe_checker and _recover_checkers:
summary = await checker_service.probe_control(
    checker_slug=row.slug,
    encrypted_session=row.session_string,
    phones=sample,
    proxy=row.proxy,
)
# probe_control is live-only: never reads _lookup_cache, never _save_cache,
# never touches contacts rows — exactly the Pitfall-1 contract.
```
Additionally treat `flood_wait_hit`/short `checked` in the probe result as a miss (a probe cut off by FloodWait is not clean).

---

### CR-02: Inline throttle detector counts cache-hit positives — a fully-poisoned live batch escapes detection and finalizes false negatives as `not_registered / high / clean`

**File:** `app/services/contact_check_worker.py:121-132` (`_is_throttle_signal`); `app/services/checker.py:465-467` (`registered` counts ALL results incl. cache hits)

**Issue:** The anomaly branch requires `summary.get("registered", 0) == 0`, but `summary["registered"]` is computed in `_check_phones_locked` over **all** results, including `from_cache=True` entries. The `live` list is correctly filtered, but the `registered == 0` pre-condition is not.

**Concrete failure scenario:** After a `/contacts/recheck` of a folder (the standard post-incident flow — this endpoint flips *all* statuses, including previously-`registered` contacts, back to `pending`), a batch typically mixes cache-hit positives with live resolves. A throttled checker returns 20+ live results, **all false**, plus 2 cached positives → `registered == 2` → `_is_throttle_signal` returns False → `probe_state` stays `clean` (the decoupled probe is dormant per CR-01) → `_apply_results` finalizes 20+ false negatives as `tg_status='not_registered', tg_confidence='high', tg_probe_state='clean'`. That is byte-for-byte the Phase-14 root bug (live leads silently discarded at high confidence), reopened for every mixed cache/live batch. The comment on line 118-119 ("Only live results count toward the anomaly") describes the intended behavior; the code does not implement it.

**Fix:**
```python
live = [r for r in results if not r.get("from_cache")]
if len(live) < ANOMALY_MIN_BATCH:
    return False
if not any(r.get("is_registered") for r in live):   # live-only, drop summary["registered"]
    return True
return False
```

---

## Warnings

### WR-01: `app/routers/queue.py` is dead code with a broken import — and workspace-unscoped if ever mounted

**File:** `app/routers/queue.py:18`; `app/main.py:21-38, 189-204`
**Issue:** The router imports `from app.routers.auth import verify_api_key`, but `app/routers/auth.py` does not exist (verified: not in `app/routers/`; `check_contacts.py:4` even documents the shim as removed). The module is not imported by `main.py`, so the app boots — but any future `include_router(queue.router)` crashes at import. Worse, if fixed naively, all three endpoints are tenancy-unsafe: `GET /queue/stats/{slug}` looks up `Sender.slug` globally (slug is only per-workspace unique after migration 014 → `scalar_one_or_none()` raises `MultipleResultsFound`, and it leaks another tenant's stats), and `GET/DELETE /queue/{id}` read/cancel any workspace's queue item (message text + phone). `app/routers/proxy_pool.py` has the same broken import.
**Fix:** Delete both dead routers, or rewrite them on `auth_dep` with `workspace_id` predicates before ever mounting. Do not leave a file that compiles in grep but not in Python.

### WR-02: `message_queue.priority` is NULL for every campaign-enqueued row (no DB default + raw INSERT omits it) — NULLs sort FIRST under `ORDER BY priority DESC`, inverting priority semantics

**File:** `app/models/__init__.py:281` (`default=0`, no `server_default`); `app/services/campaign_enqueue.py:313-333` (INSERT omits priority); `app/services/queue.py:485, 394` (`ORDER BY mq.priority DESC`)
**Issue:** Verified against prod: the live `message_queue.priority` column has **no DEFAULT** and all 276 existing rows have `priority IS NULL`. ORM-path inserts (`enqueue_message`, priority param default 0) store `0`; the campaign worker's raw INSERT stores `NULL`. Postgres `DESC` ordering puts NULLs first → NULL-priority campaign rows permanently outrank *any* explicitly-prioritized row (including intended high-priority follow-ups/manual pushes), which is the exact inverse of the documented "higher = processed first". This is a fresh instance of the known ORM `default=` vs `server_default=` drift class the review was asked to flag.
**Fix:** Migration: `ALTER TABLE message_queue ALTER COLUMN priority SET DEFAULT 0; UPDATE message_queue SET priority = 0 WHERE priority IS NULL;` + add `server_default="0"` to the ORM column + pass `priority` explicitly in the campaign_enqueue INSERT. Same treatment recommended for `attempts`/`as_draft`/`extra_data` (currently NULL from the raw path; handled by `or`-guards today, but one unguarded read away from a bug).

### WR-03: `_queue_position` comparison is inverted (and NULL-blind) — reported queue position/ETA is wrong

**File:** `app/services/queue.py:1550-1563`
**Issue:** `(priority, created_at) > (item.priority, item.created_at)` counts rows with the *same* priority created **later** as "ahead" (row-wise tuple `>` means `priority > p OR (priority = p AND created_at > c)`), while the pick order is `priority DESC, created_at ASC`. A freshly enqueued item therefore always reports position 1 and `estimated_send_at = now + ~37s` regardless of a 500-deep backlog. With NULL priorities (WR-02) the tuple comparison is NULL → nothing is counted at all. `/send`'s `queue_position`/`estimated_send_at` response fields (consumed by n8n) are fiction.
**Fix:**
```sql
SELECT COUNT(*) FROM message_queue
WHERE sender_id = :sid AND status = 'pending'
  AND (COALESCE(priority,0), created_at) < (COALESCE(t.priority,0)*-1 ... )
```
— i.e. express "ahead" explicitly: `(COALESCE(priority,0) > :p) OR (COALESCE(priority,0) = :p AND created_at < :c)` with the item's values bound as parameters.

### WR-04: 3–10 minute long-pause sleeps inline in the shared worker loop — one sender's pause stalls every sender in every workspace

**File:** `app/services/queue.py:355-361` (`await asyncio.sleep(long_pause)`), `:316-319` (sequential per-sender loop in `_tick`)
**Issue:** `_tick` iterates `eligible_sender_ids` sequentially and `_process_next_for_sender` awaits the long human-pause inline. A single sender drawing a 600s pause freezes the *entire* queue worker (all tenants) for 10 minutes. Because `_get_long_pause_seconds` re-evaluates `recent_count % pause_every == 0` on an unchanged 30-min count, the same sender can re-trigger pauses on consecutive ticks. This is the same head-of-line-blocking class as the fixed warmup bug (project memory), still live in the send path.
**Fix:** Never sleep the shared loop. Persist the pause as data: `UPDATE message_queue SET scheduled_at = NOW() + :pause WHERE sender_id=:sid AND status='pending'` (or track a per-sender `next_send_at`), then `return` — the existing scheduled_at gate enforces it for free.

### WR-05: Unbounded FloodWait sleep inside checker batches blocks the whole ContactCheckWorker (probes, recovery, all workspaces) while holding the per-checker lock

**File:** `app/services/checker.py:448-455`, `:583-589` (`await asyncio.sleep(exc.seconds)`); `app/services/contact_check_worker.py:203-214` (single sequential loop)
**Issue:** Telethon raises `FloodWaitError` for waits above `flood_sleep_threshold=60`; `exc.seconds` can be hours (86400s FloodWaits are documented in the wild). `_check_phones_locked` sleeps the full duration inside the batch, holding the `asyncio.Lock` for that slug, and since `ContactCheckWorker._run` awaits `check_phones` directly, the *entire* worker — recovery probes, control probes, and resolution for every workspace — halts for the duration. The queue worker's own FloodWait handling (reschedule, don't sleep) shows the correct pattern; the checker predates it.
**Fix:** Cap the inline sleep (e.g. `min(exc.seconds, 60)`) and otherwise return the partial batch with `flood_wait_hit=True` immediately — the inline degrade path (`_maybe_degrade_on_signal`) already parks the checker with a durable cooldown, which is the right place for long waits to live.

### WR-06: A checker with a dead session is never flagged — the pipeline stalls silently in a 5-second hot loop

**File:** `app/services/checker.py:184-188` (`raise Exception("Checker session is not authorized...")`); `app/services/contact_check_worker.py:384-388, 413-417` (log-and-continue)
**Issue:** `CheckerService._get_client` raises a generic `Exception` on unauthorized sessions and never updates `senders.auth_status` (unlike `TelegramService.get_client`, which sets `session_expired`/`banned`). The `_tick` LATERAL keeps selecting the dead checker (`auth_status='ok'` still true), the batch raises, the error is logged, and the same contacts are re-claimed every 5 minutes forever — connect attempts every 5s poll tick, zero resolution progress, no operator signal. Project memory confirms this exact blind spot bit prod ("BOTH working CA checkers session_expired → checking stalled"). There is also no error-based backoff: any persistent exception (network, frozen account) produces the same hot loop.
**Fix:** In `_get_client`, classify Telethon auth errors and unauthorized sessions the same way `TelegramService.get_client` does (set `auth_status='session_expired'` on the sender row, raise a typed error); in `_tick`'s except branch, add a short per-checker backoff (reuse `checker_rest_until`) so a persistently-failing checker doesn't spin.

### WR-07: ImportContacts fallback leaves every *unregistered* number saved in the checker's address book — the documented Pitfall-4 shadow-ban accelerator, half-fixed

**File:** `app/services/checker.py:136-161`
**Issue:** Cleanup (`DeleteContactsRequest`) runs only when `res.users` is non-empty (registered numbers). When the import surfaces no user, Telegram still stores the phone as a saved contact ("X joined Telegram" notification plumbing) — and for a cold-base workflow the *majority* of import-fallback calls are exactly these unregistered numbers. The docstring's own warning ("uncleaned imports leak the recipient's PII into the checker's contact list and shift its behavioural profile toward 'mass contact importer'") applies verbatim; the `finally`-cleanup claim in the docstring is also inaccurate — cleanup is not in a `finally` block (lines 152-155).
**Fix:** After an empty import, call `functions.contacts.DeleteByPhonesRequest(phones=[phone])` (best-effort, logged) so unregistered imports don't accumulate; move both cleanups into a `finally` to match the documented contract.

### WR-08: If the control-set file is missing, inline degrade still parks checkers but recovery is permanently disabled; also `return` instead of `continue` aborts recovery for all remaining checkers

**File:** `app/services/contact_check_worker.py:744-747`, `:71-76`, `:498-500`
**Issue:** With `_CONTROL_SET == []` (file missing — a real risk on fresh deploys/images since it only warns at import), `_probe_cycle` returns early *and* `_recover_checkers` bails (`if not sample: return`) — but `_maybe_degrade_on_signal` still flags checkers `spam_limited` + `lifecycle_status='paused'` on FloodWait/anomaly. Nothing ever clears them (the sender SpamBot reconcile deliberately doesn't handle contacts-API throttles), so one inline trip permanently kills each checker with no recovery path. Separately, `return` (not `continue`) on an empty sample exits the loop for **all** pending recoveries, and the same `return` would fire mid-loop if `_CONTROL_SET` were ever emptied at runtime.
**Fix:** `if not sample: return` → compute the sample once before the loop and bail with a WARNING that recovery is disabled; if the control set is empty, either refuse to inline-degrade to `spam_limited` (degrade to rest-only) or make api startup fail loudly when `_flag_checker_degraded` can never be undone.

### WR-09: finish/delete vs. enqueue-worker race leaves permanent zombie `pending` rows on a done campaign — which then block sender detach and pin `is_exhausted=false`

**File:** `app/routers/campaigns.py:870-889` (`finish` → `_cancel_pending_queue` → commit); `app/services/campaign_enqueue.py:88-112, 269-343` (tick reads `status='running'` once, commits at batch end)
**Issue:** The enqueue worker snapshots running campaigns at tick start and commits its INSERTs up to tens of seconds later. If `POST /finish` (or `/stop`) lands in between, `_cancel_pending_queue` cancels the rows that exist *now*, then the worker's commit adds fresh `pending` rows to the now-`done` campaign. The dispatcher never sends them (INNER JOIN `c.status='running'`), but they are permanent: `_cancel_pending_queue` never runs again for that campaign, `detach_sender`'s cold-pending guard (campaigns.py:1094-1114 — no campaign-status filter) returns 409 forever, and `_compute_is_exhausted` keeps reporting `pending_count > 0`. This defeats the exact "prevents zombie 'pending' rows lingering forever" contract stated at campaigns.py:135-138.
**Fix:** Make the worker's INSERT re-assert campaign status: `INSERT ... SELECT ... WHERE EXISTS (SELECT 1 FROM campaigns WHERE id=:cid AND status='running')`, or re-check status right before the per-campaign commit and roll back; alternatively run `_cancel_pending_queue` idempotently from the worker when it sees pending rows on a non-running campaign.

### WR-10: `/send` never normalizes `recipient_phone` — un-normalized input forks the contact identity across the whole pipeline

**File:** `app/routers/send.py:131, 150-158, 171-181`; `app/utils/phone.py:18-54` (normalizer exists, unused here); `app/schemas/__init__.py:24` (plain `str`)
**Issue:** The identity key (`+E164` or `@handle`) is the join key for `campaign_contact_assignments`, `message_queue.recipient_phone`, `conversations.contact_phone`, `contacts_cache.phone` and every dedup/re-contact/protection predicate. Contacts are normalized at import, but `/send` (the n8n push path) passes `recipient_phone` verbatim. A push of `89001234567` for a contact stored as `+79001234567`: `_lookup_contact_dict` misses (template vars silently blank), rotation creates a *second* CCA identity, the enqueue worker's `NOT IN` dedup can't see it → the same human can receive two cold openers under two identities, and the re-contact protection never links the resulting conversation to the contact.
**Fix:** In `send_message`, normalize first: `phone = normalize_to_e164(request.recipient_phone) if not is_username_key(request.recipient_phone) else request.recipient_phone`; 422 on `None`.

### WR-11: `/send` has no idempotency and no campaign-status check — retries double-send; pushes into draft/done campaigns create unsendable-forever rows

**File:** `app/routers/send.py:84-99` (only existence validated), `:169-182`; `app/services/queue.py:1437-1494` (`enqueue_message` — plain INSERT)
**Issue:** (a) Nothing dedups queue rows per (campaign, recipient): an n8n retry (timeout → replay, the normal failure mode for webhook flows) enqueues the same opener twice; the worker will send both, ~40s apart — a spam signal against the sender account. The campaign-worker path dedups via CCA, the push path does not. (b) The endpoint accepts `campaign_id` in any status; an item enqueued into a `draft`/`done` campaign passes validation, returns success + ETA, and then sits `pending` forever (dispatcher requires `status='running'`; `_cancel_pending_queue` only fires on finish/delete transitions that already happened).
**Fix:** Reject non-running campaigns (409 `CAMPAIGN_NOT_RUNNING`) unless explicitly overridden; add a dedup guard (reject or return the existing queue row when a `pending/processing` row for `(campaign_id, recipient_phone)` exists), and/or accept a client idempotency key persisted in `extra_data` with a partial unique index.

### WR-12: A permanently-failed queue item silently consumes its contact for the campaign — no requeue, no visibility

**File:** `app/services/queue.py:1232-1276` (`_fail_item`, terminal after 3 attempts); `app/services/campaign_enqueue.py:240-243` (dedup on CCA existence)
**Issue:** The enqueue dedup treats a CCA row as "handled", but the CCA row is created at enqueue time — before any send. If the item then fails permanently (3 transient network errors, `past_stop_date`, "Sender not eligible" at pick time, manual-takeover guard, `SessionAuthError` bulk-fail), the contact is never re-selected: the assignment persists, no path re-enqueues failed items, and `_compute_is_exhausted` counts the campaign as exhausted. Prod already shows 127 failed vs 135 sent rows — i.e. today roughly half the attempted contacts are silently absorbed. There is no endpoint or worker to retry/requeue failures.
**Fix:** Either delete the CCA row when an item fails terminally without a prior `sent` for that (campaign, phone), or add a requeue path (`POST /campaigns/{id}/requeue-failed` and/or worker sweep) that re-pends cold failed items; surface failed-count in the campaign response so exhaustion isn't mistaken for completion.

### WR-13: Rotation's sticky-assignment happy path ignores `restriction_status` (and `role`) — repeat sends keep routing to a spam-limited sender

**File:** `app/services/rotation.py:72-97` (eligibility = `lifecycle_status='active' AND auth_status='ok'` only) vs `:113-123` (candidates additionally require `role='sender' AND restriction_status='none'`)
**Issue:** Step 1 returns an existing assignment whenever the sender is merely active+authed; a `spam_limited`/`frozen` sender therefore keeps receiving *new* queue rows for its already-assigned contacts via `/send` (and any future rotation caller), which then sit until reconcile lifts the flag — instead of rotating to a healthy pool member. `failover.py:26-30` explicitly documents this as a trap it had to work around ("its stale-CCA short-circuit ignores restriction_status and would hand the backlog straight back to the just-frozen sender"). A trap that every future caller must individually know about is a defect in the shared function, not in its callers.
**Fix:** Extend the step-1 eligibility predicate to match the candidate filter (`AND s.role='sender' AND s.restriction_status='none'`); the existing "stale assignment → reassign below" branch already handles the fallout correctly.

### WR-14: `_set_auth_status` keys on a non-unique slug — duplicate slugs across workspaces turn session-death handling into a `MultipleResultsFound` crash

**File:** `app/services/telegram.py:127-136`; `app/models/__init__.py:81-83` (slug unique **per-workspace** only, migration 014)
**Issue:** `select(Sender).where(Sender.slug == slug)` + `scalar_one_or_none()` assumes global slug uniqueness that was removed in Phase 02.1. Slugs are derived from the Telegram account (`sender-<id>`), so the same account onboarded into two workspaces (a normal SaaS event — e.g. an agency moving accounts) yields two rows: `scalar_one_or_none()` raises `MultipleResultsFound` *inside the auth-error handler*, replacing the intended `SessionAuthError` with an unrelated exception → queue worker's generic handler retries the item 3× and fails it, while `auth_status` is never set → every subsequent item for the dead session burns 3 attempts forever. The per-slug locks in `TelegramService._locks`/`CheckerService._locks` share the same collision (cross-workspace serialization — benign but wrong).
**Fix:** Pass `sender_id` through (the callers have the `Sender` row) and update by primary key: `UPDATE senders SET auth_status=:st WHERE id=:sid`. Key the client locks by `sender.id`, not slug.

---

## Info

### IN-01: `CheckerService.probe_control` is dead code
**File:** `app/services/checker.py:305-372`
**Issue:** The live-only probe primitive — the correct implementation for CR-01 — is defined, documented, and never called (verified across app/ and tests/). **Fix:** wire it per CR-01; until then it's a misleading artifact suggesting the probe is cache-proof.

### IN-02: `_apply_results` error branch is unreachable; invalid phones finalize as high-confidence `not_registered`
**File:** `app/services/contact_check_worker.py:847-863`; `app/services/checker.py:429-435, 572-577`
**Issue:** `check_phones`/`check_usernames` result entries never carry an `"error"` key, so `tg_status='error'` is never set from the batch path; `PhoneNumberInvalidError` maps to `is_registered=False` and finalizes as `not_registered/high/clean` — mislabeled provenance for garbage input. **Fix:** have the checker tag invalid numbers (`{"error": "invalid_phone"}`) so the error branch fires.

### IN-03: LATERAL checker pick is `LIMIT 1` with no ORDER BY
**File:** `app/services/contact_check_worker.py:253-287`
**Issue:** Checker choice is planner-dependent (physical row order), so "rotation" between ≥2 healthy checkers relies entirely on the rest-gate side effect. **Fix:** add `ORDER BY checker_rest_until NULLS FIRST, id` (or round-robin on last-used) for determinism.

### IN-04: Conversation lookups use `LIMIT 1` without ORDER BY in strict mode
**File:** `app/services/queue.py:765-772` (pre-send guard), `:1342-1347` uses `ORDER BY created_at DESC` but the guard variant has none
**Issue:** With duplicate conversation rows for the same (workspace, sender, phone) — which the recontact machinery deliberately creates — the manual-takeover guard reads an arbitrary row's `ai_enabled`. **Fix:** `ORDER BY updated_at DESC LIMIT 1` in the strict guard too.

### IN-05: `attach_sender` 409s on conflicts unrelated to the sender being attached
**File:** `app/routers/campaigns.py:1040-1047`
**Issue:** `_check_sender_lock` scans the whole pool; attaching a free sender to a paused campaign that already shares a *different* sender with a running campaign returns `SENDER_LOCK_CONFLICT` for the innocent attach. **Fix:** filter the conflict list to `payload.sender_id` on the attach path.

### IN-06: `duplicate_campaign` name-pick loop is TOCTOU — concurrent duplicates 500 on the unique index
**File:** `app/routers/campaigns.py:928-941, 987-992`
**Issue:** The SELECT-loop + INSERT is not race-safe and, unlike `create_campaign`, the commit is not wrapped in an IntegrityError→409 handler. **Fix:** reuse the create-path IntegrityError handling.

### IN-07: `past_stop_date` failures skip the callback webhook and can clobber a concurrent cancel
**File:** `app/services/queue.py:299-314, 513-523`
**Issue:** Items failed for `past_stop_date` never fire `callback_url` (every other failure path does), and the UPDATE has no `AND status='pending'` guard, so a row cancelled by the API between SELECT and UPDATE is overwritten to `failed`. **Fix:** add the status guard and the callback.

### IN-08: Cache-hit results are stamped with false resolver provenance
**File:** `app/services/contact_check_worker.py:864-928`
**Issue:** `from_cache=True` results finalize with `tg_resolved_by=<current checker>` and `tg_confidence='high'` even though the current checker never touched Telegram for them — provenance says "this checker resolved it live" when it served another sender's (possibly 6-day-old) cache row. Weakens the D-09 audit trail used for suspect forensics. **Fix:** stamp `tg_resolved_by=NULL` (or a `resolved_from='cache'` marker) for cache-served results.

### IN-09: Explicit `sender_slug` path in `/send` ignores `restriction_status`
**File:** `app/routers/send.py:116-128`
**Issue:** A `spam_limited`/`frozen` sender passes the readiness check (only lifecycle+auth), so callers get 200 and the item waits out the restriction invisibly — inconsistent with the rotation path, which excludes restricted senders. Not a send leak (worker gates), but a misleading contract. **Fix:** include `restriction_status != 'none'` in the 409 `SENDER_NOT_READY` branch.

### IN-10: `pool_health.active` counts senders that cannot send
**File:** `app/routers/campaigns.py:278-303`
**Issue:** The aggregate keys only on `restriction_status`; a `session_expired` or lifecycle-`paused` sender counts as `active`. Prod memory shows exactly this state (expired CA checkers). The UI health signal overstates capacity. **Fix:** `active = restriction none AND auth ok AND lifecycle active` (mirror `_maybe_autopause`'s eligibility predicate).

### IN-11: One failing campaign aborts the whole enqueue tick for all campaigns
**File:** `app/services/campaign_enqueue.py:99-112`
**Issue:** `_tick` has no per-campaign try/except; a persistent SQL error in one campaign (e.g. FK violation on a concurrently-deleted campaign, malformed custom JSONB) re-raises to `_run` and starves every later campaign in the list each tick. **Fix:** wrap `_tick_one_campaign` per campaign, log and continue.

### IN-12: Campaign/queue sends are logged into `messages` with `sent_by='human'`
**File:** `app/services/queue.py:1379-1389`
**Issue:** Every queue-worker send — including AI-campaign cold openers — inserts `sent_by='human'`, corrupting any human-vs-AI analytics/inbox attribution built on this column. **Fix:** derive `sent_by` from the item (`'ai'`/`'campaign'` when `campaign_id` set or extra_data says followup, `'human'` only for manual UI sends).

---

## Cross-cutting notes (no action required, for the record)

- **Known-intentional items verified and excluded:** hardcoded rate constants, template snapshot at enqueue, `is_registered=false` privacy semantics, Phase-14/17 suspect/rest/trip machinery and D-10 country neutrality, raw-SQL migrations. CR-01/CR-02 are *not* re-reports of the accepted residual soft-throttle risk — they are implementation holes in the two detectors that were built specifically to bound that risk.
- The `contacts_cache` workspace-scoped-not-per-checker caveat from the project memory **does** still bite via the probe path — that concrete path is documented inside CR-01 scenario 3.
- Both containers (api + listener) opening the same Telethon `StringSession` concurrently is an accepted design (per-op connect/disconnect on the api side); it remains the most fragile shared-state seam in the system and the first place to look when `AuthKeyDuplicated`/update-loss anomalies appear.

---

_Reviewed: 2026-07-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
