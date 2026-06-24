---
phase: 10-pool-visibility
reviewed: 2026-06-24T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - app/models/__init__.py
  - app/routers/campaigns.py
  - app/routers/senders.py
  - app/schemas/__init__.py
  - app/services/listener.py
  - app/services/queue.py
  - app/services/restriction_audit.py
  - migrations/030_sender_restriction_events.sql
  - tests/test_pool_health.py
  - tests/test_restriction_audit.py
  - tests/test_sender_restriction.py
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
status: issues_found
---

# Phase 10: Code Review Report

**Reviewed:** 2026-06-24
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

Phase 10 adds a durable append-only restriction event-log (`sender_restriction_events`,
migration 030), a transaction-neutral writer (`restriction_audit.py`), write-point wiring
across `queue.py` / `listener.py`, a per-campaign `pool_health` aggregate, and an HLTH-03
history endpoint. The atomicity requirement (D-01) is correctly honored in the PEER_FLOOD,
ACCOUNT_FROZEN, cleared, banned and recipient_privacy paths — the event INSERT shares the
session that performs the `restriction_status` UPDATE (or the fail commit). The history
endpoint is properly workspace-scoped (defence-in-depth `workspace_id` filter + opaque 404).
Rate-limit intervals and FloodWait retry logic were not altered — only new event-recording
was added, as expected.

However, the **D-01 extension gate in `listener.py` is incorrect for the `unknown` verdict
and for pure recheck-interval bumps** — it will emit the very 37/day reconcile-noise events
the gate exists to suppress. There are also two audit-fidelity defects (events recorded for
state-changes that did not happen, and `flood_wait` polluting restriction analytics) and a
crash-on-missing-sender in the writer.

## Critical Issues

### CR-01: D-01 extension gate fires on pure recheck-interval bumps (the noise it must suppress)

**File:** `app/services/listener.py:1471`
**Issue:**
The gate is `if old_until is None or next_at > old_until + timedelta(minutes=1)`. In the
`else` branch, `next_at` defaults to `next_recheck = now + recheck_interval` (e.g. now+6h),
and is only overridden when `verdict == "limited"` AND SpamBot quoted a `limit_until`.

For `verdict == "unknown"` (or `limited` with no quoted date), `next_at` is a *pure
recheck-interval bump* with no SpamBot-quoted later release. The previous `restricted_until`
(`old_until`) was itself a recheck horizon set ~one interval ago and is now near expiry, so
`now + interval` is almost always `> old_until + 1 min`. The gate therefore evaluates TRUE
and writes an `extension` event on essentially every still-limited reconcile tick — exactly
the 37/day reconcile noise the comment (lines 1467-1470) and the module docstring
(`restriction_audit.py:18-24`) claim it suppresses.

The gate must distinguish a SpamBot-quoted forward shift from a mechanical recheck bump. The
test `test_reconcile_no_shift_no_event` only exercises the helper-level gate with
`restricted_until == old_until` (exact equality); it never exercises the listener call-site
with a recheck bump where `next_at > old_until`, so this slips through.

**Fix:** Only emit `extension` when the new release date came from a SpamBot quote, not from
the mechanical recheck interval. Track whether `next_at` was overridden by `limit_until`:
```python
quoted_shift = False
next_at = next_recheck
iso = result.get("limit_until")
if verdict == "limited" and iso:
    try:
        candidate = datetime.fromisoformat(iso) + timedelta(minutes=5)
        if candidate > datetime.now(timezone.utc):
            next_at = candidate
            quoted_shift = True
    except ValueError:
        pass
# Emit ONLY on a SpamBot-quoted forward shift, never on a bare recheck bump.
if quoted_shift and (old_until is None or next_at > old_until + timedelta(minutes=1)):
    await record_restriction_event(
        r[0], "extension", "spambot_reconcile", next_at, result.get("raw_text"), db=db,
    )
```

## Warnings

### WR-01: `spam_limited` event written even when the sender UPDATE is a no-op (already frozen)

**File:** `app/services/listener.py:937-962`
**Issue:**
The antispam-signal handler updates the sender with a guard:
`UPDATE senders SET restriction_status = 'spam_limited' ... WHERE id = :sid AND restriction_status <> 'frozen'`
(line 942). When the sender is already `frozen`, this UPDATE affects 0 rows (frozen-precedence
preserved, as intended). But `record_restriction_event(... "spam_limited" ...)` at line 959 is
called **unconditionally**, so the append-only log gets a `spam_limited` state-change row for a
state-change that never occurred. This corrupts the audit history the table exists to provide
(it will show a frozen account "becoming spam_limited"). Same divergence the same-TX guarantee
was meant to prevent — except here the event and the (non-)UPDATE genuinely disagree.

**Fix:** Use `RETURNING id` on the UPDATE and only record the event when a row was actually
changed:
```python
changed = (await session.execute(text("""
    UPDATE senders SET restriction_status = 'spam_limited', restricted_until = :recheck_at
    WHERE id = :sid AND restriction_status <> 'frozen'
    RETURNING id
"""), {"recheck_at": recheck_at, "sid": str(sender_id)})).fetchone()
...
if changed:
    await record_restriction_event(sender_id, "spam_limited", "antispam_signal",
                                   recheck_at, message_text, db=session)
```

### WR-02: `flood_wait` events are written as `category='restriction'`, polluting restriction analytics

**File:** `app/services/queue.py:716-722`
**Issue:**
The FloodWait path records an event with the default `category='restriction'`, while its own
comment (lines 717-718) states this path "does NOT change `senders.restriction_status`, so
pool_health is unaffected." A FloodWait is not an account restriction — it is Telegram's normal
rate-limit backoff. Recording it as a `restriction` category means it is *included* by the
`WHERE category='restriction'` analytics filter described in the migration header
(`migrations/030_sender_restriction_events.sql:18-20`), inflating the "how often did this
account hit a restriction" metric the log was built to answer. It also triggers an unnecessary
`activity_slice` computation (a `messages_log` scan) for a non-restriction event.

**Fix:** Either give FloodWait its own category (e.g. `category='flood_wait'`, and extend the
`sre_category_chk` CHECK constraint), or drop the event entirely if FloodWait is out of scope
for restriction analytics. At minimum, do not file it under `category='restriction'`.

### WR-03: `record_restriction_event` crashes if the sender row is missing

**File:** `app/services/restriction_audit.py:105-109`
**Issue:**
`_record` does `(await db.execute(...)).one()`. `Result.one()` raises `NoResultFound` if the
sender was deleted between the restriction event and this write (the senders FK is
`ON DELETE CASCADE`, and reconcile/queue ticks run on stale `sender_id`s read from a prior
batch SELECT). In the same-TX call-sites this `NoResultFound` propagates up and aborts the
caller's transaction — meaning a missing sender turns a routine reconcile/cleared into an
exception that rolls back the legitimate `restriction_status` UPDATE / queue resume that
preceded it. The audit write should never be able to roll back the state change it documents.

**Fix:** Use `.one_or_none()` and skip the write when the sender is gone:
```python
s = (await db.execute(text(""" ... """), {"sid": str(sender_id)})).one_or_none()
if s is None:
    logger.warning("restriction event for missing sender %s skipped", sender_id)
    return
```

### WR-04: `flood_wait` event committed in a separate transaction from the reschedule it describes

**File:** `app/services/queue.py:711-734`
**Issue:**
The FloodWait event is written and committed inside `db2` (lines 711-723), but the queue-item
reschedule that the event describes is applied on the outer `db` session and committed
separately at line 734. If the `db.commit()` at 734 fails (or the worker crashes between the
two commits), the event row persists with no corresponding queue state — the inverse of the
atomicity guarantee the phase is built around. The PEER_FLOOD/ACCOUNT_FROZEN paths correctly
co-locate the pause UPDATE, the status UPDATE and the event in one `db2` transaction; the
FloodWait path is the odd one out because the `message_queue` reschedule for the *whole*
sender (line 712-715) is in `db2` but the *single failed item* reschedule (725-733) is in `db`.

**Fix:** If WR-02 is resolved by dropping the FloodWait event from restriction analytics this
becomes moot; otherwise move the event write to share the transaction that records the item
state change, or accept and document that FloodWait events are best-effort/non-atomic.

## Info

### IN-01: `_compute_pool_health` SELECT omits the `workspace_id` defence-in-depth filter

**File:** `app/routers/campaigns.py:235-251`
**Issue:**
`_compute_pool_health` filters only on `cs.campaign_id`. It is safe today because every caller
(`get_campaign`, `list_campaigns`, etc.) loads the campaign via `_load_campaign`, which is
workspace-scoped, so `campaign_id` is already validated. But the sibling `_build_attached_senders`
and the HLTH-03 endpoint both add a redundant `workspace_id` predicate as defence-in-depth; this
aggregate does not. A future caller that passes an unvalidated `campaign_id` would leak another
tenant's pool counts.

**Fix:** Add `AND s.workspace_id = :wid` (and pass `ctx.workspace_id`) to match the convention
used elsewhere in this router.

### IN-02: `restriction_status` literal in schema cannot represent a `banned` auth state

**File:** `app/schemas/__init__.py:601`
**Issue:**
`CampaignSenderAttach.restriction_status` is `Literal["none", "spam_limited", "frozen"]`. The
DB column (`models/__init__.py:93`) is a free `VARCHAR(20)`. The listener sets
`auth_status='banned'` (not `restriction_status`), so today the column only ever holds the three
literal values and Pydantic validation will not reject. This is fine now but brittle: if a future
write ever sets `restriction_status` to anything outside the literal, every campaign GET in that
workspace will 500 on response validation. Worth a CHECK constraint on the column or a comment
tying the literal to the enforced value set.

**Fix:** Add a DB CHECK constraint mirroring the literal, or document the coupling so the literal
and the writable value set cannot silently diverge.

### IN-03: `activity_slice` computed for `cleared` / `banned` events where it has little meaning

**File:** `app/services/restriction_audit.py:124-153`
**Issue:**
Any `category='restriction'` event computes the `activity_slice` from `messages_log`, including
`cleared` and `banned` events emitted from the reconcile sweep. For `cleared` the slice describes
activity *after* the restriction was lifted (or near-zero during the pause), and for `banned` the
account can no longer send — so the slice is noise rather than the "what was it doing right before
it got limited" snapshot the docstring (lines 26-27) advertises. Not incorrect, but the data is
misleading for those event types.

**Fix:** Consider scoping the slice to the restriction-*onset* events (`spam_limited`, `frozen`),
or document that the slice is only diagnostic for onset rows.

---

_Reviewed: 2026-06-24_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
