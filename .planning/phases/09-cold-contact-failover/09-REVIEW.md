---
phase: 09-cold-contact-failover
reviewed: 2026-06-24T00:00:00Z
depth: standard
files_reviewed: 5
files_reviewed_list:
  - app/services/failover.py
  - app/services/queue.py
  - app/services/listener.py
  - tests/test_failover.py
  - tests/conftest.py
findings:
  critical: 1
  warning: 5
  info: 3
  total: 9
status: issues_found
---

# Phase 9: Code Review Report

**Reviewed:** 2026-06-24
**Depth:** standard
**Files Reviewed:** 5
**Status:** issues_found

## Summary

Phase 9 adds `app/services/failover.py::failover_cold_backlog`, wired into three freeze
paths (PEER_FLOOD + ACCOUNT_FROZEN in `queue.py`, antispam-signal in `listener.py`). The
core reassignment logic is sound: the candidate filter correctly excludes the frozen
sender (`restriction_status = 'none'`), the per-row `_pick_least_loaded` achieves an even
spread because each in-loop CCA UPDATE is visible to the next pick, `FOR UPDATE OF mq SKIP
LOCKED` + `status = 'pending'` matches the worker's claim discipline, and `scheduled_at =
NOW()` correctly sheds the +24h freeze pause. The additive call-site edits do not touch the
rate-limiter constants, FloodWait retry, or the +24h pause logic. Logging is COUNT + UUIDs
only (PII-clean).

However there is one BLOCKER: the `db=None` (queue.py) path opens a **second concurrent
session** while the outer `_send_item` session is mid-transaction, and the failover SELECT
can be silently starved by the freeze `db2` transaction's row locks under concurrency — and
more concretely, in the queue.py paths the failover runs on a *separate* session that cannot
see uncommitted state and is not covered by the outer transaction's atomicity, breaking the
"pause + flag + failover land together" invariant the design claims (it only holds on the
listener path). Plus several WARNING-level correctness gaps around cross-campaign predicate
scope and the engaged-conversation divergence from `rebalance.py`.

## Critical Issues

### CR-01: queue.py freeze paths run failover on a separate, non-atomic session — partial-failure leaves CCA/queue split-brain

**File:** `app/services/queue.py:743-769` (PEER_FLOOD), `app/services/queue.py:791-812` (ACCOUNT_FROZEN)
**File:** `app/services/failover.py:108-114`

**Issue:**
On the queue.py paths the sequence is:

1. `db2` session: UPDATE message_queue pause +24h + UPDATE senders restriction → **commit** (one transaction).
2. `failover_cold_backlog(sender.id)` with `db=None` → opens a **third** session, moves rows, **commits separately**.
3. `await self._fail_item(db, item, ...)` commits the trigger item on the **outer** `db` session.

These are three independent transactions. The design docstring (`failover.py:38-41`) claims
the listener path lands "pause+flag+failover in one atomic commit" — true there — but the
queue.py path has **no such atomicity**. If the process is killed (OOM, SIGKILL, container
restart) between step 1 and step 2, the sender is flagged frozen and its backlog is paused
+24h, but the cold backlog is NOT moved — the exact 24h-stall this phase exists to prevent,
silently reintroduced on any crash window. Worse, if `failover_cold_backlog` raises midway
(e.g. a DB hiccup after it has UPDATEd some queue rows but its own commit has not yet run),
the helper's session rolls back cleanly, but there is no retry — the freeze is already
committed (step 1) so the backlog stays paused on the dead sender and failover never re-runs
(the sender is no longer `restriction_status='none'`, and nothing re-invokes failover for an
already-frozen sender). The reconcile-resume loop only un-pauses on the *original* frozen
sender once SpamBot says "free" — it never moves the backlog. So a transient failure in step
2 permanently defeats the failover for that freeze event.

Additionally, `failover_cold_backlog` is called **outside** any `try/except` in the freeze
block. If it raises, the exception propagates up past `await self._fail_item(...)` (which is
never reached) into the outer `except Exception` handler at `queue.py:899`, which then calls
`_fail_item` AGAIN — but the freeze-specific `return` is skipped, so the trigger item is
failed via the generic path with a misleading `str(exc)` error message instead of the
PEER_FLOOD/ACCOUNT_FROZEN error, and the callback fires with the wrong error.

**Fix:**
Wrap the failover call so a failure cannot abort the freeze handling, and prefer passing the
freeze session so the move is atomic with the flag (mirror the listener path). Minimal
hardening that keeps the existing `db2` structure:

```python
# inside the db2 block, BEFORE db2.commit(), so flag+pause+move commit together:
async with AsyncSessionLocal() as db2:
    await db2.execute(text("UPDATE message_queue SET scheduled_at = :pause_until "
                           "WHERE sender_id = :sid AND status = 'pending'"),
                      {"pause_until": pause_until, "sid": str(sender.id)})
    await db2.execute(text("UPDATE senders SET restriction_status = 'spam_limited', "
                           "restricted_until = :recheck_at WHERE id = :sid"),
                      {"recheck_at": recheck_at, "sid": str(sender.id)})
    from app.services.failover import failover_cold_backlog
    await failover_cold_backlog(sender.id, db2)   # transaction-neutral, same commit
    await db2.commit()
```

If keeping the `db=None` call is required, at minimum guard it so it can never abort
`_fail_item`/callback:

```python
from app.services.failover import failover_cold_backlog
try:
    await failover_cold_backlog(sender.id)
except Exception as exc:
    logger.error("failover after PEER_FLOOD for sender %s failed: %s",
                 sender.id, exc, exc_info=True)
```

## Warnings

### WR-01: Engaged-conversation predicate omits `campaign_id` → cross-campaign false "engaged"

**File:** `app/services/failover.py:78-83`

**Issue:**
The conversation NOT EXISTS guard joins on `cv.workspace_id = mq.workspace_id AND
cv.contact_phone = mq.recipient_phone` — but NOT on `campaign_id`. A contact who is engaged
(has a has-message dialog) in campaign **A** will make their cold-pending row in campaign
**B** appear "engaged" and therefore non-movable, even though B has no started dialog with
them. In a multi-campaign workspace this silently leaves cold backlog stuck on the frozen
sender. The `sent`/`processing` guard above it IS campaign-scoped (`s.campaign_id =
mq.campaign_id`), so the two halves of the predicate use inconsistent scoping.

**Fix:** Either scope the conversation join to the campaign, or document that conversations
are intentionally workspace-global identity (matching `rebalance.py:59-63`). If campaign
scoping is intended:
```sql
AND NOT EXISTS (
    SELECT 1 FROM conversations cv
    JOIN messages m ON m.conversation_id = cv.id
    WHERE cv.workspace_id = mq.workspace_id
      AND cv.contact_phone = mq.recipient_phone
      AND cv.campaign_id = mq.campaign_id
)
```

### WR-02: `_pick_least_loaded` counts CCA globally → cross-campaign load skews the "even spread"

**File:** `app/services/failover.py:195`, `app/services/rotation.py:198-217`

**Issue:**
`_pick_least_loaded` counts `campaign_contact_assignments` for a sender **across ALL
campaigns** (`rotation.py:204-214` has no campaign filter). `rebalance.py:29-31` explicitly
documents that it deliberately does NOT reuse `_pick_least_loaded` for exactly this reason
("it counts load GLOBALLY across all campaigns"). Phase 9 reuses it anyway. Consequence: a
healthy receiver that already carries a large assignment count in an unrelated campaign will
be deprioritised, so the failover spread within *this* campaign is not actually even — it is
even with respect to global load. The unit tests pass only because every test uses
freshly-created senders with zero pre-existing assignments. With real multi-campaign data the
"even spread" claim (FAIL-01 / D-09) does not hold.

**Fix:** Use a campaign-scoped least-loaded count (mirror the set-based load count in
`rebalance.py:124-133`) instead of the global `_pick_least_loaded`, or document that the
spread is intentionally global-load-balanced and update the FAIL-01 claim.

### WR-03: O(rows) `_pick_least_loaded` round-trips serialise a COUNT per moved row

**File:** `app/services/failover.py:194-212`

**Issue:**
The per-row loop issues one `_pick_least_loaded` COUNT query (a full GROUP BY over
`campaign_contact_assignments`) plus two UPDATEs per moved row. For a large cold backlog
(`QUEUE_TICK_BATCH`-sized, up to hundreds of rows) on a frozen sender this is hundreds of
sequential round-trips inside the freeze handler, which on the listener path holds the
single antispam transaction open the whole time, and on the queue path blocks `_send_item`.
`rebalance.py` deliberately does a single set-based move for the same reason. (Flagged as a
robustness/transaction-duration concern, not raw perf — a long-held freeze transaction
increases lock contention with the worker.)

**Fix:** Compute the spread set-based (round-robin or even-split assignment computed in one
pass) rather than a COUNT round-trip per row, as `rebalance.py` does.

### WR-04: failover called only on `_send_item`-driven freezes — bulk FloodWait/auth paths never trigger it

**File:** `app/services/queue.py:704-715` (hard FloodWait), `app/services/queue.py:872-887` (SessionAuthError)

**Issue:**
The phase claims to cover "when a sender freezes." But `_send_item` has two other paths that
take a sender out of service for the cold backlog: the hard-FloodWait branch
(`retry_after >= FLOOD_HARD_THRESHOLD`, lines 704-715 and 844-854) reschedules ALL pending
items but does NOT call failover, and the `SessionAuthError` branch (872-887) FAILS all
pending+processing items but does NOT call failover. A sender hit by a 300s+ FloodWait has
its whole cold backlog parked for 5+ minutes with no reassignment, and a dead-session sender
hard-fails its backlog entirely. These are arguably in-scope freeze conditions left
uncovered. At minimum this is an under-documented scope gap.

**Fix:** Confirm intended scope. If hard-FloodWait should also fail over, add the call (note:
hard-FloodWait does NOT set `restriction_status`, so the candidate filter would still include
the sender — failover would need a different exclusion, e.g. pass an explicit
`exclude_sender_id`). Document the deliberate exclusion otherwise.

### WR-05: claimed-rows FOR UPDATE lock can starve concurrent rebalance / second failover into silent partial moves

**File:** `app/services/failover.py:175-184`

**Issue:**
The claim uses `FOR UPDATE OF mq SKIP LOCKED`. If a concurrent `rebalance_on_attach` (or a
second failover for a different frozen sender in the same campaign) holds locks on some of
this frozen sender's cold rows, `SKIP LOCKED` silently skips them and they remain on the
frozen sender — failover returns a partial count with no signal that some rows were left
behind, and no mechanism re-runs failover for them (see CR-01: an already-frozen sender is
never re-failovered). The idempotency test (FAIL-06) only covers the sequential second-call
case, not concurrent contention. This is acceptable best-effort behaviour by design, but the
"nothing is ever lost" claim (`failover.py:20`) is only true for *eventual* resume via
reconcile, not for failover itself — the skipped rows stall the full +24h.

**Fix:** Document the partial-move-under-contention semantics explicitly, or have the
reconcile-resume loop re-attempt failover (not just un-pause) for senders still
`restriction_status != 'none'` whose cold backlog hasn't moved.

## Info

### IN-01: Engaged predicate diverges from rebalance.py with no shared source

**File:** `app/services/failover.py:68-84` vs `app/services/rebalance.py:50-64`

**Issue:** Two near-identical `_COLD_PENDING_PREDICATE` constants now exist with subtly
different semantics: failover requires `JOIN messages` (empty conversation = still cold,
movable) and `status IN ('sent','processing')`; rebalance treats ANY conversation as engaged
and only `status = 'sent'`. A contact with an empty conversation is movable by failover but
NOT by rebalance — the two services will disagree about the same row. The divergence is
documented in the failover docstring (D-04/D-05) but the duplicated SQL string is a
maintenance hazard: a future fix to one predicate will not propagate to the other.

**Fix:** Extract a single parameterised predicate builder shared by both services, or add a
cross-reference comment in each pointing at the other and the deliberate differences.

### IN-02: `bot_id` flows into antispam logging but failover stays clean — verify no PII via bot path

**File:** `app/services/listener.py:957-962`, `app/services/failover.py:217-221`

**Issue:** The failover log line is correctly PII-free (COUNT + sender/campaign UUIDs). The
surrounding antispam handler logs `bot_name` and `bot_id` (not recipient PII, so acceptable),
and `_handle_bot_message` logs `name, phone` of the bot sender at `listener.py:1042-1045` —
that `phone` is the *bot account's* phone, not an outreach recipient, so it is not
outreach-PII, but it is worth confirming this is acceptable under the CLAUDE.md "no PII in
logs" rule since `%s (%s)` with a phone reads like a PII leak on a quick scan.

**Fix:** No code change required; confirm the bot-account phone is acceptable to log, or drop
it to match the failover discipline.

### IN-03: Test suite never exercises the multi-campaign / pre-loaded-sender spread

**File:** `tests/test_failover.py:106-135`

**Issue:** Every test creates fresh senders with zero pre-existing CCA load and a single
campaign, so the global-count behaviour of `_pick_least_loaded` (WR-02) and the
cross-campaign conversation scope (WR-01) are never tested. The "even spread" assertion
(FAIL-01) passes trivially. A regression in either area would not be caught.

**Fix:** Add a test where a healthy receiver already carries assignments in a *second*
campaign, and assert the spread is still even within the failover campaign (this will fail
today, confirming WR-02).

---

_Reviewed: 2026-06-24_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
