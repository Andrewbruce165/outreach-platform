---
status: issues
files_reviewed: 19
files_reviewed_list:
  - app/config.py
  - app/models/__init__.py
  - app/routers/campaigns.py
  - app/routers/check_contacts.py
  - app/routers/send.py
  - app/services/campaign_enqueue.py
  - app/services/checker.py
  - app/services/contact_check_worker.py
  - app/services/failover.py
  - app/services/follow_up.py
  - app/services/queue.py
  - app/services/rebalance.py
  - app/services/restriction_audit.py
  - app/services/rotation.py
  - app/services/telegram.py
  - app/services/template.py
  - migrations/046_telegram_service_status.sql
  - migrations/047_message_queue_priority_default.sql
  - migrations/048_sender_long_pause_until.sql
findings:
  critical: 2
  warning: 4
  info: 3
  total: 9
depth: deep
date: 2026-07-06
scope: re-review of 260703 findings + fix regressions
---

# Re-Review: Checker + Campaign Sending — Verification of Fix Batches A–H

**Reviewed:** 2026-07-06 (verification re-review of `.planning/reviews/260703-checker-campaigns-REVIEW.md`)
**Depth:** deep (current code re-read per finding; fix-commit diffs inspected; live prod state and logs consulted read-only)
**Auxiliary files consulted:** `app/utils/phone.py`, `app/routers/senders.py` (Phase-20 seams), `app/services/warmup.py`, `app/main.py`, `app/data/control_set_known_live.txt`

## Headline

25 of 28 original findings are genuinely FIXED at the root cause, with correct, well-commented implementations. **However, two of the fixes introduced NEW critical regressions, both live in prod right now:**

1. **CR-03** — the CR-01 fix (live-only recovery probes) removed the accidental rate-limiting the cache provided. `_recover_checkers` has **no interval gate, no cap, and no cooldown re-extension on a failed probe**, so a parked checker whose cooldown elapsed is live-probed every ~12.5 s forever. Prod logs confirm `sender-8525079460` (trip #8) has been burning ~20k live `ResolvePhone` attempts/day since 2026-07-03 18:56 — ~2.5 days of continuous contacts-API hammering on an already-throttled account, invisible in logs.
2. **CR-04** — the WR-14 fix changed `TelegramService.get_client` to a 3-required-arg signature but left **9 stale 2-arg call sites** in the Phase-20 profile/username/photo/2FA methods. Every one of those endpoints crashes with `TypeError` at runtime; `/username-check` silently returns `available=true` for every input because the router swallows the crash. Deployed.

## Verification Matrix

| # | Verdict | Evidence (current code / prod) |
|---|---|---|
| CR-01 | **FIXED-WITH-REGRESSION** | `probe_checker` → `probe_control` (contact_check_worker.py:618-624), `_recover_checkers` → `probe_control` (:811-817). `probe_control` (checker.py:389-468) never calls `_lookup_cache`/`_save_cache`, never touches `contacts`. Flood/truncated/empty probe = MISS (:629-640); recovery clean requires full live sample (:821-833). Regression → **CR-03** below. |
| CR-02 | **FIXED** | `_is_throttle_signal` judges live-only end-to-end: `live = [r for r in results if not r.get("from_cache")]`, `summary["registered"]` gate removed (contact_check_worker.py:127-134). Both `check_phones` (checker.py:532-543) and `check_usernames` (:687-692) tag `from_cache` on every result, so the filter is sound for both paths. |
| WR-01 | **FIXED** | `app/routers/queue.py` + `proxy_pool.py` deleted (a4aebaf); `app/routers/` contains neither; `main.py` imports only `app.services.queue`; no test imports the modules (only table-name strings in conftest). |
| WR-02 | **FIXED** | Migration 047 (DEFAULT + backfill for priority/attempts/as_draft, idempotent); ORM `server_default` on all three (models/__init__.py:284, 298, 310); campaign INSERT passes `priority: 0` explicitly (campaign_enqueue.py:337, 352). Prod verified: 0 NULLs across 281 rows; column defaults `0/0/false`. Residual: `extra_data` still NULL from the raw path — see IN-14. |
| WR-03 | **FIXED** | `_queue_position` rewritten (queue.py:1634-1660): `COALESCE(priority,0)` both sides, ahead = higher priority OR same priority + earlier `created_at` — mirrors pick order `priority DESC, created_at ASC`. |
| WR-04 | **FIXED** | Durable (`senders.long_pause_until`, mig 048), non-blocking (marker UPDATE + `return` instead of sleep, queue.py:363-380), replica-safe (state read from DB each tick), eligibility gates on it (`_tick` SQL, queue.py:269-272). Double-fire guard covers the active-pause window (queue.py:330-338). Residual stochastic ~1/14 immediate re-fire after expiry on an unchanged counter — see IN-13 (benign direction: extra rest). |
| WR-05 | **FIXED** | `_FLOOD_WAIT_INLINE_CAP = 60` (checker.py:73); `min(exc.seconds, cap)` in `probe_control` (:459), `_check_phones_locked` (:567), `_check_usernames_locked` (:706); `flood_wait_hit=True` still returned so the degrade path fires. |
| WR-06 | **FIXED** | `CheckerService._get_client` classifies auth/ban like `TelegramService.get_client`, flags `auth_status` **by id** (checker.py:205-272) → LATERAL gate excludes next tick; both `_tick` except branches back off via `_rest_checker` (contact_check_worker.py:391-400, 425-433). Residual: the probe/recovery path calls `_get_client` without `sender_id` (checker.py:422) so a dead-session **parked** checker is never auth-flagged and retries connect every cycle — subsumed by CR-03's missing recovery gate. |
| WR-07 | **FIXED** | `import_completed` marked before inspecting `res.users` (checker.py:150-157); cleanup in `finally` — `DeleteContactsRequest` for surfaced users, `DeleteByPhonesRequest` for empty imports (:164-177); docstring contract now matches code. |
| WR-08 | **FIXED** | Sample computed once before the recovery loop; empty/missing control set → WARN + early return before the loop (contact_check_worker.py:793-807); empty-control-set inline signal degrades REST-ONLY (`checker_rest_until`, self-clearing) + ERROR log, never `spam_limited` (:744-759); batch still finalizes suspect. |
| WR-09 | **PARTIAL** | INSERT re-asserts `status='running'` via `WHERE EXISTS` (campaign_enqueue.py:332-344, rowcount-guarded). But the per-campaign batch commits only at :366 — a `/finish` that commits after an early INSERT statement passed EXISTS but before the batch commit still strands rows `_cancel_pending_queue` cannot see (window narrowed from ~30 s to the batch duration, not closed). No worker-side idempotent cleanup of pending rows on non-running campaigns was added; `detach_sender`'s cold-pending guard still has no campaign-status filter (campaigns.py:1167-1187) so any stranded row 409s detach forever. Prod today: 0 zombies on done/draft campaigns. New zombie-creation path via `/requeue-failed` — see WR-17. |
| WR-10 | **FIXED** | `/send` normalizes before the key enters the pipeline: username keys pass through, phones forced through `normalize_to_e164`, 422 `INVALID_PHONE` on failure (send.py:115-131); `recipient_key` used for dedup, rotation, template lookup, and enqueue (:146, 202, 221, 245). `normalize_to_e164` covers `8…`/`7…`/formatted RU inputs (utils/phone.py:18-54). |
| WR-11 | **FIXED** | (a) 409 `CAMPAIGN_NOT_RUNNING` for non-running campaigns (send.py:105-113). (b) Idempotent replay: existing `pending/processing` row for `(campaign_id, recipient_key)` returned as 200 with its own queue_id/position (:133-163) — the owner-approved return-existing semantics. Interaction gap with Phase-19 follow-up pings — see WR-16. |
| WR-12 | **FIXED-WITH-REGRESSION** | Implemented per fixplan option (в): cold terminal fail releases the sticky CCA in the same TX (queue.py:1343-1358); `failed_count` in campaign response (campaigns.py:322-325); `POST /requeue-failed` (campaigns.py:737-764). Regression: no loop-breaker for permanently-failing cold recipients — see WR-15. Residual: the `SessionAuthError` bulk-fail (queue.py:1216-1220) still fails cold items without releasing CCA (recoverable only via `/requeue-failed`). |
| WR-13 | **FIXED** | Step-1 sticky eligibility now `lifecycle='active' AND auth='ok' AND role='sender' AND restriction_status='none'` (rotation.py:77-103); ineligible sticky sender falls through to reassignment. failover.py:26-35 docstring updated — it keeps its own bulk implementation for the SKIP-LOCKED contract, not to route around the bug. |
| WR-14 | **FIXED-WITH-REGRESSION** | `_set_auth_status` updates by primary key (telegram.py:127-143); `get_client` lock + auth updates keyed on `sender_id` (:311-344); `CheckerService` locks keyed on `checker_id` (checker.py:386, 417, 614); queue.py:878, senders.py:857, warmup.py:714 pass the new 3-arg form. Regression: 9 Phase-20 call sites still 2-arg — see **CR-04**. |
| IN-01 | **FIXED** | `probe_control` no longer dead code — called at contact_check_worker.py:618 and :811. |
| IN-02 | **FIXED** | `PhoneNumberInvalidError` → `{"error": "invalid_phone"}` (checker.py:127-131); cache write skipped for errors (:529-530); error key threaded into results (:539-543); `_apply_results` error branch reachable → `tg_status='error'` (contact_check_worker.py:923-939). |
| IN-03 | **FIXED** | LATERAL pick `ORDER BY checker_rest_until NULLS FIRST, id` (contact_check_worker.py:292). |
| IN-04 | **FIXED** | Strict-mode manual-takeover guard now `ORDER BY updated_at DESC LIMIT 1` (queue.py:780-786). |
| IN-05 | **FIXED** | `_check_sender_lock(only_sender_id=…)` (campaigns.py:384-420); attach passes `payload.sender_id` (:1113); start/resume still scan the full pool. |
| IN-06 | **FIXED** | `duplicate_campaign` flush+commit wrapped in IntegrityError → 409 (campaigns.py:1049-1063), mirroring create. |
| IN-07 | **FIXED** | `_fail_past_stop_date_items`: `AND status='pending'` guard + RETURNING + per-item failure callback (queue.py:1247-1297); used from both fail sites (:305, :533). |
| IN-08 | **FIXED** | `resolver = None if res.get("from_cache") else checker_id` stamped in all three UPDATE branches (contact_check_worker.py:916-922, 953, 979, 996). |
| IN-09 | **FIXED** | Explicit `sender_slug` path 409s on `restriction_status != 'none'` with structured detail (send.py:180-198). |
| IN-10 | **FIXED** | `pool_health.active` = restriction none AND auth ok AND lifecycle active (campaigns.py:288-302), mirroring `_maybe_autopause`. |
| IN-11 | **FIXED** | Per-campaign try/except with `db.rollback()` + continue (campaign_enqueue.py:104-118). |
| IN-12 | **FIXED** | Dispatcher-written messages logged `sent_by='ai'` (queue.py:1465). Manual UI sends go through the conversations router, not the queue — attribution is now consistent. |

## Deployment & Prod-State Verification (2026-07-06, read-only)

| Check | Result |
|---|---|
| Migrations 046/047/048 in `schema_migrations` | **Present** (`046_telegram_service_status`, `047_message_queue_priority_default`, `048_sender_long_pause_until`; `049_account_profile` also applied) |
| `message_queue.priority` NULLs / default | **0 NULLs** (also 0 for attempts/as_draft) across 281 rows; column defaults `priority=0`, `attempts=0`, `as_draft=false` |
| Zombie pending on non-running campaigns | **0** on `done`/`draft`. (11 `pending` rows exist on **paused** campaigns — that is by-design: `_cancel_pending_queue` deliberately excludes pause so items resume with the campaign.) |
| `senders.long_pause_until` | **Column exists** |
| Containers rebuilt after fixes | **Yes.** api created `2026-07-04T10:06:09Z` (> last fix commit 041e10e `2026-07-04T09:41:24Z` and > HEAD fd1e58e `10:03:52Z`); listener created `2026-07-06T06:48:11Z`. All batches A–H are deployed — **which also means CR-03 and CR-04 are live**. |
| Poisoned cache on control numbers | **Clean.** All 49 control numbers checked: 129 `contacts_cache` rows, **0** with `is_registered=false`. |
| Live CR-03 evidence | `sender-8525079460`: `restriction_status='spam_limited'`, `checker_trip_count=8`, `restricted_until=2026-07-03 18:56:37` (elapsed). api log tail: Telethon `Connecting/Disconnecting` cycle every ~12.5 s (389 connects in the last 5 000 log lines) with zero send/warmup/checker log lines — the silent recovery-probe loop. Last audit event for the sender: `spam_limited` 2026-07-03 12:56 (after a day of flap: 4 × cleared/re-tripped on 07-02/07-03). No events since — recovery misses write nothing. |

Note on log forensics: `docker logs --since` returned empty for this container (json-file driver quirk); all log evidence above was gathered via `--tail` windows.

## New / Still-Open Findings

### CR-03: `_recover_checkers` live-probes a parked checker every worker cycle — unbounded contacts-API burn on an already-throttled account (regression of the CR-01 fix)

**File:** `app/services/contact_check_worker.py:771-833` (no gate, no re-extension); `app/services/checker.py:389-468` (`probe_control`, live-only), `:422` (`_get_client` without `sender_id`)
**Introduced by:** e8c1c67 (Batch A)

**Issue:** `_recover_checkers` runs on every worker cycle (~5 s poll) and selects every checker with `restriction_status='spam_limited' AND restricted_until <= NOW()`. On a failed recovery probe it does `continue` — it does **not** re-extend `restricted_until`, does **not** bump the trip ladder, does **not** consult `_last_probe_at` (the b7j PROBE-02 gate covers only `_probe_cycle`), and does **not** count against the daily cap (`probe_control` writes no `contacts_cache` rows, which is what the cap counts; `_recover_checkers` has no cap predicate anyway). Before Batch A the probe went through `check_phones`, whose cache-first read made repeat probes free (the CR-01 bug — but also an accidental rate limiter). Now every probe is 3 live `ResolvePhone` calls.

**Failure scenario (CONFIRMED LIVE):** a checker trips → escalating cooldown (correct) → cooldown elapses → checker is still throttled → recovery probe misses → next cycle probes again → ~3 live resolves every ~12.5 s ≈ **20 000+ resolve attempts/day** on a shadow-throttled account, indefinitely. This (a) is 50× the 400/day cap the whole Phase-14 design enforces, (b) plausibly keeps the throttle refreshed so recovery can *never* succeed (self-defeating), (c) consumes ~55% of the single ContactCheckWorker coroutine's wall-clock in probe pacing sleeps — halving resolve throughput for every workspace — and (d) if the throttle manifests as FloodWait, blocks the worker up to 60 s per cycle (the WR-05 cap bounds one sleep, not the loop). Prod: `sender-8525079460` has been in this loop for ~2.5 days. Companion hole: `probe_control` calls `_get_client` without `sender_id` (checker.py:422), so a parked checker whose *session died* also spins in this loop forever without ever being flagged `session_expired`.

**Fix:**
```python
# _recover_checkers, after `if not clean: continue` → replace with:
if not clean:
    # Re-arm the cooldown at the current ladder rung so the next recovery
    # attempt waits (base * 2^(trip-1), capped) instead of next cycle.
    await db.execute(text("""
        UPDATE senders SET restricted_until = NOW() + make_interval(secs => :cd)
        WHERE id = :id
    """), {"cd": cooldown_for_trip(r.checker_trip_count), "id": str(r.id)})
    logger.warning("recovery probe MISS for checker %s — re-armed cooldown", r.id)
    continue
```
(or, minimally, gate `_recover_checkers` on the same `_last_probe_at` interval as `_probe_cycle`). Also pass `checker_id` into `_get_client` from `probe_control` so a dead session gets flagged, and log every recovery miss (see IN-15). **Immediate ops action:** push `sender-8525079460.restricted_until` far into the future (as was done for the other three parked checkers) to stop the live burn before the code fix ships.

### CR-04: `get_client` signature change (WR-14) left 9 stale 2-arg call sites — every Phase-20 profile/username/photo/2FA endpoint crashes; `/username-check` silently lies

**File:** `app/services/telegram.py:1152, 1177, 1206, 1253, 1291, 1326, 1390, 1456, 1510` (calls `self.get_client(sender_slug, encrypted_session, proxy=proxy)`) vs `:291-297` (signature `get_client(self, sender_slug, sender_id, encrypted_session, proxy=None)`)
**Introduced by:** 02957b6 (Batch G, 09:23Z) — updated only `send_message_by_telegram_id`; the 20-02/20-03 methods (f556ad5 09:03Z, 337b683 09:13Z) already existed and were missed; the 20-04 2FA methods (93528c3 09:25Z) copied the stale in-file pattern two minutes later.

**Issue:** Each call binds `sender_id=encrypted_session` and raises `TypeError: get_client() missing 1 required positional argument: 'encrypted_session'` before any Telegram I/O. Affected methods: `update_profile`, `check_username`, `set_username`, `set_profile_photo`, `delete_profile_photo`, `resync_profile`, `change_2fa_password`, `start_recovery_email`, `confirm_recovery_email`. Verified deployed: the running api container has the 3-required-arg definition and all 9 stale sites.

**Failure scenario:** every account-profile management endpoint (Phase 20) returns 500. Worse, `routers/senders.py:1028-1034` wraps `check_username` in a broad `except → return available=True` "best-effort fall-through", so **`GET /senders/{slug}/username-check` returns `available: true` for every username, always** — a silent functional lie the UI will act on (user picks a "free" username, `set_username` then 500s).

**Fix:** pass `str(sender.id)` at all 9 sites (the routers already hold the `Sender` row — thread `sender_id` through the method signatures):
```python
client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy)
```
Add one integration test that calls each TelegramService public method with a mocked client factory — a pure signature-drift canary.

### WR-09 (still open — residual): finish-vs-enqueue race narrowed, not closed; no idempotent zombie cleanup

**File:** `app/services/campaign_enqueue.py:332-356` (per-INSERT EXISTS), `:366` (batch commit); `app/routers/campaigns.py:1167-1187` (detach guard, no campaign-status filter)
**Issue:** an INSERT that passed the `EXISTS(status='running')` check early in a batch is invisible to `/finish`'s `_cancel_pending_queue` until the worker's outer commit; a finish committing inside that window (up to the multi-second batch duration at `batch_size=500`) still strands pending rows on a done campaign, and nothing ever cleans them (`_cancel_pending_queue` runs only on the transition; the dispatcher just ignores them; detach 409s forever).
**Fix:** add the review's third suggested leg — an idempotent worker-side sweep (once per tick: `UPDATE message_queue SET status='cancelled' … WHERE status='pending' AND campaign_id IN (SELECT id FROM campaigns WHERE status IN ('done'))`), which also retroactively heals WR-17 zombies; or re-check campaign status immediately before the per-campaign commit and roll back.

### WR-15: WR-12's CCA release creates an infinite re-enqueue/re-send loop for cold contacts that fail with permanent recipient-level errors (regression of the WR-12 fix)

**File:** `app/services/queue.py:1343-1358` (unconditional cold-fail CCA release); `app/services/campaign_enqueue.py:242-277` (re-selects the contact once CCA is gone); `app/services/queue.py:1108-1130` (PRIVACY_RESTRICTED → `_fail_item`)
**Introduced by:** fbf75e6 (Batch E)

**Issue:** the release fires for **every** cold terminal fail with no memory of how many times this (campaign, phone) already cycled. A recipient that fails *permanently* at the recipient level — `PRIVACY_RESTRICTED` (UserNotMutualContactError, common in cold outreach to `registered` contacts), persistent resolve failures, USER_IS_BLOCKED — produces: 3 send attempts → terminal fail → CCA deleted → no conversation exists, so the enqueue worker's dedup re-selects the contact next tick → new queue row → 3 more real Telegram attempts → forever.

**Failure scenario:** a folder with N privacy-restricted contacts turns into a perpetual background loop of ~3 Telegram resolve+send attempts per contact per cycle: burns the sender's resolve ladder (including gated ImportContacts — the documented shadow-ban accelerator) against the same dead numbers indefinitely, wastes pick slots against sendable contacts, and grows `message_queue`/`messages_log` without bound. Nothing surfaces it: `failed_count` climbs but the campaign never exhausts.

**Fix:** make the release error-aware and bounded — skip the CCA release for recipient-permanent error classes (`PRIVACY_RESTRICTED`, `USER_IS_BLOCKED`, entity-not-found), or count prior terminal fails for the (campaign, phone) and stop releasing after N (e.g. 2) cycles:
```python
prior_fails = SELECT COUNT(*) FROM message_queue
              WHERE campaign_id=:cid AND recipient_phone=:phone AND status='failed'
if has_sent is None and error_class_is_transient(error) and prior_fails < 3:
    DELETE FROM campaign_contact_assignments ...
```

### WR-16: `/send` dedup returns ANY pending row for (campaign, recipient) — Phase-19 follow-up pings and long-rescheduled rows silently swallow legitimate new pushes

**File:** `app/routers/send.py:133-163`; interacts with `app/services/follow_up.py:273-283` (pings are `message_queue` rows with the same `campaign_id`/`recipient_phone`) and `app/services/queue.py:971-976, 1017-1021` (FloodWait / PEER_FLOOD reschedule pending rows hours-to-24h into the future)

**Issue:** the WR-11 dedup keys only on `(campaign_id, recipient_phone, status IN pending/processing)` — it cannot distinguish an n8n *retry of the same message* (the intended case) from a *deliberately different* message. Two concrete live cases: (a) FollowUpWorker has a ping pending for the contact → an operator/n8n push of new content returns 200 with the **ping's** queue_id; the pushed text is silently discarded. (b) A sender hit PEER_FLOOD → its pending rows are rescheduled +24h → for the next 24h every push for those recipients "succeeds" by returning the stale opener row. The caller has no signal that nothing new was enqueued.

**Fix:** narrow the dedup to genuine replays — match on message content too (`AND mq.message_text = :rendered` or an explicit client idempotency key in `metadata`), exclude `extra_data->>'kind' = 'followup'` rows, and add `"deduplicated": true` to the response so callers can detect the replay path. (Return-existing semantics itself was owner-approved; the follow-up/reschedule interplay was not part of that decision.)

### WR-17: `POST /campaigns/{id}/requeue-failed` has no campaign-status guard — re-pending failed rows on a done campaign manufactures exactly the WR-09 zombies

**File:** `app/routers/campaigns.py:737-764`
**Introduced by:** 88bf741 (Batch E)

**Issue:** the endpoint re-pends **all** failed rows (`attempts=0, scheduled_at=NOW()`) for any campaign in any status. On a `done` campaign the dispatcher never sends them (`c.status='running'` join) and `_cancel_pending_queue` never runs again → permanent pending rows that pin `is_exhausted=false` and 409-block `detach_sender` (campaigns.py:1167-1187) with a misleading "wait for the queue to drain" message. It also re-pends rows failed for permanent reasons ("Conversation taken over manually", privacy) indiscriminately.

**Fix:** mirror `/send`'s WR-11(a): 409 `CAMPAIGN_NOT_RUNNING` unless the campaign is running (or auto-scope the UPDATE with `AND EXISTS (SELECT 1 FROM campaigns WHERE id=:cid AND status='running')`); optionally exclude `error_message='Conversation taken over manually'` rows.

### IN-13: Long pause can re-fire once on the same unchanged 30-min counter immediately after expiry

**File:** `app/services/queue.py:326-353`
**Issue:** the WR-04 double-fire guard blocks re-triggering only *while* `long_pause_until` is in the future. On the first evaluation after expiry the 30-min sent count is unchanged and `pause_every` is redrawn per call, so with probability ≈ 1/14 the same counter immediately draws a second 3–10 min pause. Direction of failure is benign (extra rest, no send risk); at most a minor throughput dent.
**Fix (optional):** persist the counter value that triggered the pause (e.g. keep `long_pause_until` non-NULL as an "already fired at count N" marker until a new send moves the counter) or store `last_pause_count` alongside.

### IN-14: `extra_data` still NULL from the campaign raw-INSERT path (migration 047 covered priority/attempts/as_draft only)

**File:** `app/services/campaign_enqueue.py:332-344` (INSERT omits `extra_data`); `app/models/__init__.py:292` (`default={}`, no `server_default`)
**Issue:** the original WR-02 recommended the same default treatment for `extra_data`; 047 skipped it. All reads today are `item.extra_data or {}`-guarded, so no live bug — but it remains one unguarded read away, and it is the last surviving instance of the ORM `default=` vs `server_default=` drift class in this table.
**Fix:** `ALTER TABLE message_queue ALTER COLUMN extra_data SET DEFAULT '{}'::jsonb; UPDATE … WHERE extra_data IS NULL;` + `server_default` on the ORM column.

### IN-15: Recovery-probe misses are completely silent — the CR-03 loop was invisible in logs

**File:** `app/services/contact_check_worker.py:809-833` (miss → bare `continue`); `app/services/checker.py:389-468` (`probe_control` logs only errors/FloodWait)
**Issue:** a recovery probe that misses writes no log line, no audit event, and no state change. 2.5 days of continuous live probing on prod produced zero application log lines (only telethon connect noise) — no operator signal exists for the failure mode. Also `_maybe_degrade_on_signal`'s audit `raw_text` uses `summary['checked']` which counts cache hits, slightly misstating the "anomalous empty-rate N/N" denominator (cosmetic).
**Fix:** log each recovery miss at WARNING with slug + consecutive-miss context (rate-limited if the CR-03 gate isn't added first); consider a `recovery_miss` audit event every Nth miss.

## Conclusion

**Is the subsystem materially safer than on 07-03?** For the *data-integrity* core — yes, substantially. The two critical lying-detector holes (CR-01/CR-02) are correctly closed: probes are live-only end-to-end, the inline anomaly detector can no longer be masked by cache hits, prod cache shows zero poisoned control rows, and the priority/position/lifecycle/identity fixes (WR-02/03/04/10/11/13, IN-*) are all genuine root-cause fixes verified in current code and in the prod schema. The Phase-14 promise — "false negatives are never silently finalized" — now holds.

For *account safety and feature availability* — no, two regressions must be fixed before the system can be trusted:

1. **CR-03 is actively burning a checker account right now** (~20k live resolves/day on `sender-8525079460` since 07-03). Ops mitigation today (park `restricted_until` far-future), code fix (recovery gate + cooldown re-arm) immediately after.
2. **CR-04 has all Phase-20 profile/2FA/photo endpoints dead on arrival** and `/username-check` returning false positives. One-line-per-site fix, nine sites.

Secondary (should ship in the next batch): WR-15's permanent-failure resend loop and WR-17's zombie-manufacturing endpoint, both of which quietly undo parts of the value the E-batch fixes added. WR-16 and the WR-09 residual are real but lower urgency. The structural risks list from the original review (single-coroutine workers, in-memory health state, at-least-once sends) stands unchanged — CR-03 is, notably, a fresh demonstration of structural risk #1 (a single hot path monopolizing the shared worker).

---

_Reviewed: 2026-07-06_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
