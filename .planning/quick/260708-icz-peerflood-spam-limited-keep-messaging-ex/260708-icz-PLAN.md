---
phase: quick-260708-icz
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/queue.py
  - app/services/follow_up.py
  - tests/test_sender_restriction.py
autonomous: true
requirements: [PEERFLOOD-01]
must_haves:
  truths:
    - "A spam_limited sender STOPS sending first messages to new contacts (new-dialog queue items are not picked)."
    - "A spam_limited sender CONTINUES sending follow-up items to contacts that already received a first message (follow-up branch of the pick SELECT still passes)."
    - "A spam_limited sender CONTINUES follow-up pings from the FollowUpWorker (_ping no longer early-returns for spam_limited)."
    - "On PeerFloodError the sender is flagged spam_limited with restricted_until ~= now + recheck interval (~1h), but its pending queue rows are NOT bulk-pushed +24h anymore."
    - "A frozen sender remains fully blocked: _check_rate_limits skips the whole tick, _ping skips, and the ACCOUNT_FROZEN handler still bulk-pauses all pending +24h."
    - "After the ~1h reconcile sweep clears the spam_limited flag, the sender naturally resumes new-contact sends (no code re-enable needed)."
  artifacts:
    - path: "app/services/queue.py"
      provides: "frozen-only tick gate, spam_limited new-dialog SELECT guard, PEER_FLOOD handler without bulk +24h reschedule"
    - path: "app/services/follow_up.py"
      provides: "_ping frozen-only skip guard"
    - path: "tests/test_sender_restriction.py"
      provides: "updated pre-send gate assertion matching frozen-only semantics"
  key_links:
    - from: "app/services/queue.py::_check_rate_limits"
      to: "restriction_status"
      via: "early-return only when == 'frozen'"
      pattern: "restriction_status == \"frozen\""
    - from: "app/services/queue.py::_process_next_for_sender SELECT"
      to: "senders.restriction_status"
      via: "JOIN senders s + s.restriction_status <> 'spam_limited' ANDed onto new-dialog branch"
      pattern: "s.restriction_status <> 'spam_limited'"
    - from: "app/services/follow_up.py::_ping"
      to: "r.sender_restriction_status"
      via: "skip only when == 'frozen'"
      pattern: "sender_restriction_status == \"frozen\""
---

<objective>
Change the spam_limited (PeerFloodError) response so a flagged sender keeps servicing
already-started conversations and follow-up pings, but stops writing to NEW contacts for
~1 hour (until the reconcile sweep clears the flag). A frozen (ACCOUNT_FROZEN) sender must
stay fully blocked.

Purpose: PeerFlood is a "slow down new outreach" signal, not a "stop everything" signal.
Freezing all pending rows +24h needlessly strands live dialogs and follow-ups on the
account for a full day. Scoping the pause to new contacts keeps engaged conversations warm
while still backing off the risky behavior (opening new dialogs) that triggered the limit.

Output: 4 code edits across 2 service files + 1 test update, run via test-overlay, deployed.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@app/services/queue.py
@app/services/follow_up.py

<interfaces>
<!-- Current shape of the code being edited. Executor should edit in place, not reinvent. -->

app/services/queue.py::_check_rate_limits (~line 592) — CURRENT:
```python
        if sender_row.restriction_status != "none":
            logger.debug(
                f"Sender {sender_id}: restricted "
                f"({sender_row.restriction_status}, until={sender_row.restricted_until}) — skipping tick"
            )
            return False
```
The sender_row SELECT (~line 566) already returns restriction_status + restricted_until.

app/services/queue.py::_process_next_for_sender pick SELECT (~lines 466-510) — CURRENT shape:
```sql
FROM message_queue mq
JOIN campaigns c ON c.id = mq.campaign_id
WHERE mq.sender_id = :sid
  AND mq.status = 'pending'
  ... campaign window filters ...
  AND (
    /* follow-up to an existing contact — never blocked */
    EXISTS (SELECT 1 FROM message_queue prior WHERE prior.campaign_id = mq.campaign_id
              AND prior.recipient_phone = mq.recipient_phone AND prior.status = 'sent')
    OR
    /* new dialog — Phase 12 cap AND Phase 13 pace */
    ((SELECT COUNT(DISTINCT opened.recipient_phone) ... ) < c.max_new_dialogs_per_day
      AND (SELECT COUNT(DISTINCT paced.recipient_phone) ... ) < CAST(:expected_now AS DOUBLE PRECISION))
  )
ORDER BY mq.priority DESC, mq.created_at ASC
LIMIT 8
FOR UPDATE OF mq SKIP LOCKED
```

app/services/queue.py PEER_FLOOD handler (~lines 1077-1136) — CURRENT: opens db2, runs a
bulk `UPDATE message_queue SET scheduled_at = :pause_until WHERE sender_id=:sid AND status='pending'`
(pause_until = NOW()+24h), then UPDATE senders spam_limited + record_restriction_event +
failover_cold_backlog + rollback_suspect_resolve_fails + callback + _fail_item.

app/services/queue.py ACCOUNT_FROZEN handler (~lines 1138-1189) — DO NOT TOUCH. Keeps its
bulk +24h pause of all pending rows (frozen = everything stops).

app/services/follow_up.py::_ping (~line 227) — CURRENT:
```python
        if r.sender_restriction_status and r.sender_restriction_status != "none":
            logger.info(...)
            return False
```

app/services/failover.py::failover_cold_backlog — DO NOT CHANGE. Already moves ONLY cold /
never-contacted rows onto healthy senders and leaves engaged (sent/dialog) rows put.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Scope spam_limited to new contacts in queue.py (3 edits)</name>
  <files>app/services/queue.py</files>
  <action>
Make three coordinated edits. Frozen precedence must be preserved everywhere.

EDIT 1 — `_check_rate_limits` (~line 592, the `restriction_status != "none"` early-return):
Change the condition so the tick is skipped ONLY when the sender is `frozen`. When
`spam_limited`, do NOT early-return here — fall through to the normal 4/20/150 + 15/h
rate-limit checks below (the new-dialog gating is enforced downstream in EDIT 2, so
follow-ups still flow but new dialogs are blocked). Replace with:

```python
        # PeerFlood (spam_limited) is a "back off NEW outreach" signal, not
        # "stop everything": a spam_limited sender must keep servicing already-
        # started dialogs and follow-up pings, so we DO let the tick proceed to
        # the normal rate-limit checks. New-contact (new-dialog) sends are gated
        # separately in _process_next_for_sender's pick SELECT. Only 'frozen'
        # (Telegram ACCOUNT_FROZEN — all writes blocked) skips the whole tick.
        # The listener reconcile sweep clears the spam_limited flag once SpamBot
        # says the account is free again (~1h RESTRICTION_RECHECK_INTERVAL).
        if sender_row.restriction_status == "frozen":
            logger.debug(
                f"Sender {sender_id}: frozen "
                f"(until={sender_row.restricted_until}) — skipping tick"
            )
            return False
```

EDIT 2 — the pick SELECT in `_process_next_for_sender` (~lines 466-510):
Add `JOIN senders s ON s.id = mq.sender_id` right after the existing
`JOIN campaigns c ON c.id = mq.campaign_id`. Then AND a spam_limited guard ONTO THE
NEW-DIALOG BRANCH ONLY. The follow-up `EXISTS(...)` branch must remain untouched (it must
still always pass). Concretely, wrap the new-dialog predicate so it becomes
`(s.restriction_status <> 'spam_limited' AND (<existing Phase-12 cap AND Phase-13 pace>))`.
Preserve the Phase-12 cap subquery and Phase-13 pace subquery VERBATIM — only prepend the
`s.restriction_status <> 'spam_limited' AND` guard inside the new-dialog branch. Keep
`FOR UPDATE OF mq SKIP LOCKED` exactly as-is (lock only mq, not the joined senders/campaigns
rows). Update the leading comment block to note: new-dialog sends are additionally gated by
the sender NOT being spam_limited; follow-ups bypass it (as they already bypass cap+pace);
frozen never reaches this SELECT because _check_rate_limits skips its tick.

EDIT 3 — the `PEER_FLOOD` handler (~lines 1077-1136):
REMOVE the bulk 24h reschedule of pending rows only. Delete the
`pause_until = datetime.now(timezone.utc) + timedelta(hours=24)` line AND the
`UPDATE message_queue SET scheduled_at = :pause_until WHERE sender_id = :sid AND status = 'pending'`
statement (with its bind). Keep EVERYTHING else in the branch exactly as-is:
the `recheck_at` computation, the `UPDATE senders SET restriction_status='spam_limited',
restricted_until=:recheck_at`, the `record_restriction_event(sender.id, "spam_limited",
"queue_error", recheck_at, error_msg, db=db2)` call, the `failover_cold_backlog(sender.id)`
call, the `rollback_suspect_resolve_fails(sender.id)` call, the callback fire, and the final
`_fail_item`. Update the branch comment and the `logger.critical` message to say the pending
queue is NO LONGER bulk-paused — new-contact sends are now suppressed by the spam_limited
flag (via _check_rate_limits fall-through + the new-dialog SELECT guard) and resume after the
~1h reconcile sweep clears the flag; engaged dialogs + follow-ups keep flowing meanwhile.
(db2 session is still needed for the senders UPDATE + audit event — keep the `async with`.)

DO NOT touch the ACCOUNT_FROZEN handler (~lines 1138-1189): frozen still bulk-pauses all
pending +24h. DO NOT touch the FLOOD_WAIT handler. DO NOT change any empirical rate-limit
constants or intervals (CLAUDE.md guard).
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && python -c "import ast,sys; ast.parse(open('app/services/queue.py').read()); print('queue.py parses OK')" && grep -q 'restriction_status == "frozen"' app/services/queue.py && grep -q "s.restriction_status <> 'spam_limited'" app/services/queue.py && test "$(grep -c 'timedelta(hours=24)' app/services/queue.py)" -ge 1</automated>
  </verify>
  <done>queue.py parses. _check_rate_limits early-returns only on frozen. The pick SELECT joins senders and ANDs `s.restriction_status <> 'spam_limited'` onto the new-dialog branch (follow-up EXISTS branch unchanged). PEER_FLOOD handler no longer bulk-reschedules pending +24h but still flags spam_limited + audit + failover + rollback + callback + _fail_item. ACCOUNT_FROZEN handler still has its `timedelta(hours=24)` bulk pause.</done>
</task>

<task type="auto">
  <name>Task 2: Keep follow-up pings for spam_limited, update test, run targeted suite, deploy</name>
  <files>app/services/follow_up.py, tests/test_sender_restriction.py</files>
  <action>
EDIT 4 — `app/services/follow_up.py::_ping` (~line 227):
Change the skip guard so a ping is skipped ONLY when the owning sender is `frozen`. Replace:

```python
        if r.sender_restriction_status and r.sender_restriction_status != "none":
```
with:
```python
        if r.sender_restriction_status == "frozen":
```
Update the surrounding D-14 comment to say: spam_limited no longer blocks follow-up pings
(a spam_limited sender keeps pinging already-messaged contacts); only 'frozen' (ACCOUNT_FROZEN,
all writes blocked) skips the ping and retries next tick. The auto-finish time threshold still
closes durably-frozen dialogs regardless. Leave the rest of `_ping` unchanged (double-enqueue
guard, ping-text generation, status flip, counter, enqueue).

TEST UPDATE — `tests/test_sender_restriction.py::test_queue_pre_send_skips_restricted`
(~line 284): it currently asserts `'restriction_status != "none"' in src` of
`_check_rate_limits`. That string no longer exists after EDIT 1. Update the test to assert the
new frozen-only semantics: assert `'restriction_status == "frozen"' in src` and assert the old
`'restriction_status != "none"'` string is NOT present (i.e. spam_limited no longer early-returns
the whole tick). Update its docstring accordingly.

Note (no change needed, just confirm): `test_check_rate_limits_untouched`
(tests/test_queue_new_dialog_limit.py) only checks `max_new_dialogs_per_day` is absent from
_check_rate_limits — still true. `test_peer_flood_sets_one_hour_recheck_with_matching_audit`
asserts `"timedelta(hours=24)" in src` of the WHOLE QueueWorker — still true because the
ACCOUNT_FROZEN handler keeps it. `test_paused_frozen` (test_follow_up.py) tests a PAUSED
CAMPAIGN, not a restricted sender — unaffected. Do NOT edit those.

Then run the targeted test subset via the TEST OVERLAY ONLY (never plain
`docker compose run --rm api pytest` — conftest guard / prod DATABASE_URL risk per CLAUDE.md).
Full suite baseline is known RED/order-dependent, so run a targeted `-k` subset.

Finally DEPLOY (restart does NOT pick up code changes): rebuild api and listener.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && python -c "import ast; ast.parse(open('app/services/follow_up.py').read()); print('follow_up.py parses OK')" && grep -q 'sender_restriction_status == "frozen"' app/services/follow_up.py && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_sender_restriction.py tests/test_follow_up.py tests/test_queue_new_dialog_limit.py tests/test_restriction_audit.py -k "restrict or peer_flood or frozen or new_dialog or ping or followup or rate_limits" -q</automated>
  </verify>
  <done>follow_up.py parses; _ping skips only on frozen. test_queue_pre_send_skips_restricted updated and passes; the targeted queue/follow_up/restriction subset is GREEN via the test overlay. api + listener rebuilt (`docker compose up -d --build api` then `... --build listener`) and both containers healthy.</done>
</task>

</tasks>

<verification>
- Frozen precedence intact: a frozen sender skips the whole tick (_check_rate_limits), skips
  pings (_ping), and its ACCOUNT_FROZEN handler still bulk-pauses all pending +24h.
- spam_limited sender: tick proceeds; the pick SELECT's follow-up branch still passes but the
  new-dialog branch is blocked by `s.restriction_status <> 'spam_limited'`; FollowUpWorker
  pings still enqueue.
- PEER_FLOOD handler still flags spam_limited + writes restricted_until (~1h) + audit event +
  failover_cold_backlog + rollback_suspect_resolve_fails + callback + _fail_item, but no longer
  bulk-pushes pending rows +24h.
- No schema change / no migration added.
- Untouched by design: ACCOUNT_FROZEN handler, listener AI-answer path, campaign_enqueue.py /
  failover.py / rotation.py new-contact selection (they already filter restriction_status='none'),
  empirical rate-limit constants & intervals.
</verification>

<success_criteria>
- All four edits applied exactly as specified across queue.py + follow_up.py.
- Targeted restriction/queue/follow_up test subset GREEN via test overlay.
- test_queue_pre_send_skips_restricted updated to frozen-only semantics.
- api + listener rebuilt and running.
- On a live PeerFlood, the account keeps replying in started dialogs and keeps sending
  follow-up pings, but opens no new dialogs until the ~1h reconcile sweep clears the flag;
  a frozen account stops everything.
</success_criteria>

<output>
After completion, create `.planning/quick/260708-icz-peerflood-spam-limited-keep-messaging-ex/260708-icz-SUMMARY.md`
</output>
