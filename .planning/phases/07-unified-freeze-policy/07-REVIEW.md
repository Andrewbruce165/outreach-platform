---
phase: 07-unified-freeze-policy
reviewed: 2026-06-23T00:00:00Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - app/services/listener.py
  - app/services/rotation.py
  - tests/test_rotation_campaign.py
  - tests/test_spambot_selfcheck.py
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-06-23
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

Phase 07 rewrites `_handle_antispam_signal` (listener.py) to mirror the PEER_FLOOD
soft-restriction contract — pause pending queue items (`scheduled_at +24h`, status stays
`pending`) + flag the sender `spam_limited` with a frozen-precedence guard, instead of the
old terminal `status='failed'` + `ai_enabled=false` behaviour. It also adds a
`restriction_status = 'none'` filter to the rotation candidate SELECT, and updates two test
modules to the new contract.

The listener rewrite is largely faithful to the PEER_FLOOD reference (queue.py:739-754) and
the resume path (queue tick gate at queue.py:401 + restriction reconcile sweep) is coherent:
once `spam_limited` is set, the queue worker skips the sender entirely until the reconcile
sweep clears the flag and resets `scheduled_at = NOW()`, so the +24h pause is a backstop, not
the primary gate. That part is sound.

The one material defect is that the rotation freeze filter was only applied to the
**fresh-assignment** path (Step 3) and not to the **existing-assignment happy path** (Step 1).
A contact already pinned to a sender that later becomes `spam_limited`/`frozen` will keep being
routed to that restricted sender — directly contradicting FRZ-04 ("a restricted sender must
not be assigned a cold contact") for any contact that already has a `campaign_contact_assignments`
row. The new test only exercises fresh phones, so this gap passes CI undetected.

Remaining findings are robustness / consistency issues in the listener handler and test
coverage gaps.

## Critical Issues

### CR-01: Rotation freeze filter bypassed on the existing-assignment path

**File:** `app/services/rotation.py:74-97` (Step 1) vs `app/services/rotation.py:112-126` (Step 3)
**Issue:**
The phase added `AND s.restriction_status = 'none'` to the Step 3 candidate SELECT (the pool
scan for a *new* assignment). But Step 1 — the existing-assignment lookup — computes eligibility
as:

```sql
(s.lifecycle_status = 'active' AND s.auth_status = 'ok') AS is_eligible
```

with **no** `restriction_status` check. When `row.is_eligible` is true the function returns the
already-assigned sender immediately (lines 91-96), never reaching the Step 3 filter.

Consequence: any contact that already has a `campaign_contact_assignments` row pointing at a
sender which subsequently becomes `spam_limited` (or `frozen`) will continue to be handed that
restricted sender on every send. This is exactly the situation FRZ-04 is meant to prevent, and
it is the *common* case in production (assignments are sticky — created on first contact and
reused), not an edge case. The fresh-assignment path that the new test covers is the *rare* one.

The existing-assignment branch should treat `restriction_status <> 'none'` the same way it
treats an offline sender — fall through to reassignment (the `# else: assignment exists but
sender went offline → reassign below` path already exists).

**Fix:**
```sql
-- rotation.py Step 1 query, add restriction_status to the eligibility expression:
SELECT cca.sender_id,
       c.workspace_id AS workspace_id,
       (s.lifecycle_status = 'active'
        AND s.auth_status = 'ok'
        AND s.restriction_status = 'none') AS is_eligible
FROM campaign_contact_assignments cca
JOIN campaigns c ON c.id = cca.campaign_id
JOIN senders s ON s.id = cca.sender_id
WHERE cca.campaign_id = :cid
  AND cca.contact_phone = :phone
```
With this change, a restricted existing-assignment falls into the reassignment branch
(`existing_sender_id is not None`) and is UPDATEd to a healthy sender via Step 5. Add a
regression test that assigns a phone, flags the assigned sender `spam_limited`, then asserts a
second `get_or_assign_sender` call returns a *different*, healthy sender.

## Warnings

### WR-01: Antispam handler no longer disables AI — divergence from PEER_FLOOD it claims to mirror

**File:** `app/services/listener.py:888-896, 936-944`
**Issue:**
The docstring and inline comments assert the new handler "mirrors the PEER_FLOOD-ветку in
queue.py". It does mirror the queue-pause + `spam_limited` flag. But the design intentionally
leaves `ai_enabled` untouched (FRZ-03), whereas the *old* antispam handler disabled AI across
all the sender's conversations. PEER_FLOOD in queue.py never touched `ai_enabled` either, so the
new behaviour is arguably consistent — but the justification ("Telegram does not block replies
in established dialogs under a soft spam-limit") is an unverified assumption baked into a safety
mechanism. If that assumption is wrong, the AI keeps generating outbound replies from an account
Telegram is actively flagging, accelerating a hard ban. This is a deliberate behaviour change
from "stop everything" to "keep replying" and deserves an explicit risk note / monitoring hook,
not just a code comment. At minimum confirm with the FRZ-03 requirement owner that solicited
replies are genuinely safe under spam-limit before shipping.
**Fix:** Document the FRZ-03 assumption and its blast radius in the phase decision record, and
consider gating AI replies behind the same `restriction_status = 'none'` check used elsewhere if
the assumption is not confirmed.

### WR-02: `paused_count` uses `len(.fetchall())` on an UPDATE...RETURNING — fragile and unnecessary

**File:** `app/services/listener.py:924-932`
**Issue:**
```python
paused = await session.execute(text("""
    UPDATE message_queue SET scheduled_at = :pause_until
    WHERE sender_id = :sid AND status = 'pending'
    RETURNING id
"""), ...)
paused_count = len(paused.fetchall())
```
The PEER_FLOOD reference it claims to mirror (queue.py:744-748) does **not** use `RETURNING` —
it just runs the UPDATE. Here `RETURNING id` + `fetchall()` materialises every paused row's id
purely to count them for a log line. `result.rowcount` returns the same number without pulling
rows over the wire. More importantly, mixing `RETURNING` semantics here (kept) while the mirror
source omits it is an inconsistency that invites confusion about whether the RETURNING is
load-bearing. It is not.
**Fix:**
```python
paused = await session.execute(text("""
    UPDATE message_queue SET scheduled_at = :pause_until
    WHERE sender_id = :sid AND status = 'pending'
"""), {"pause_until": pause_until, "sid": str(sender_id)})
paused_count = paused.rowcount
```

### WR-03: `restricted_until` reset can starve an imminent recheck on repeated signals

**File:** `app/services/listener.py:936-944`
**Issue:**
The sender-flag UPDATE has no guard against re-pushing `restricted_until` for an *already*
`spam_limited` sender:
```sql
UPDATE senders
SET restriction_status = 'spam_limited',
    restricted_until = :recheck_at      -- always now()+6h
WHERE id = :sid AND restriction_status <> 'frozen'
```
If a flagged sender keeps receiving antispam messages (SpamBot can send several), each one
resets `restricted_until` to now+6h, indefinitely deferring the reconcile sweep that would clear
the restriction. The sender could remain `spam_limited` far longer than warranted because every
fresh signal slides the recheck window forward. PEER_FLOOD in queue.py has the same shape but is
driven by send attempts (which are gated off once flagged), so it self-limits; the listener path
is driven by *inbound* messages, which are not gated.
**Fix:** Only advance `restricted_until` when it would extend, or skip the timestamp update for
an already-restricted sender:
```sql
SET restriction_status = 'spam_limited',
    restricted_until = GREATEST(senders.restricted_until, :recheck_at)
WHERE id = :sid AND restriction_status <> 'frozen'
```
(or add `AND restriction_status = 'none'` if a second signal should never re-arm the clock).

### WR-04: Test relies on real cross-session commit visibility with no transaction isolation

**File:** `tests/test_spambot_selfcheck.py:133-171`, `tests/test_rotation_campaign.py:100-117`
**Issue:**
`_handle_antispam_signal` opens its own `AsyncSessionLocal()` and commits real data, while the
test seeds + asserts through the separate `async_db_session` fixture (which only rolls back at
teardown, conftest.py:189-194 — it does not wrap the test in a nested savepoint). The test
therefore persists committed rows that survive the handler's independent commit. This works only
because the fixture commits its seed first and the db is the ephemeral tmpfs `db-test`. It is
correct *today*, but it is brittle: any future move to a nested-transaction / SAVEPOINT-per-test
isolation model (a common hardening step) will break these tests silently, because the handler's
separate connection won't see the test's uncommitted savepoint data. There is no assertion or
comment pinning this assumption.
**Fix:** Add a comment in both tests noting they depend on commit-visible (non-isolated) fixture
semantics because the SUT uses its own `AsyncSessionLocal`. If isolation is ever introduced,
these tests must seed via a committing connection the SUT can see.

## Info

### IN-01: Stale "auto-cancel" wording in the self-check guard log/comment

**File:** `app/services/listener.py:900-909`
**Issue:** The handler no longer cancels anything (it pauses + flags), but the early-return log
still says `skip auto-cancel` and the comment says "do NOT pause the queue or flag the sender".
The behaviour-naming drift (`auto-cancel`) is a leftover from the pre-Phase-07 terminal-fail
design and is now misleading.
**Fix:** Reword to `skip pause+flag (solicited self-check)`.

### IN-02: Test docstring/section header still references retired "cancellation" semantics nearby

**File:** `tests/test_spambot_selfcheck.py:1-13` (module docstring)
**Issue:** The module docstring still describes the handler as "cancelling the sender's own queue
+ disabling AI" and "skips the auto-cancel". After Phase 07 the handler pauses + flags and leaves
AI on; the docstring now describes behaviour the code no longer has. The renamed test
(`..._pauses_and_flags_...`) is correct, but the file-level narrative was not updated.
**Fix:** Update the module docstring to the pause+flag contract.

### IN-03: `message_text` / `bot_name` / `bot_id` params now only used for logging

**File:** `app/services/listener.py:881-886, 948-953`
**Issue:** The old handler embedded `message_text[:200]` into `paused_reason` / `error_message`
columns. The rewrite drops those writes; `message_text` is now unused except that `bot_name` /
`bot_id` appear only in the warning log and `message_text` is not referenced at all in the body.
Not a bug, but the now-unused `message_text` parameter is dead-ish (carried only for signature
compatibility with the two call sites at listener.py:636-638 and :660). Consider logging a
truncated `message_text` for forensic value, or document why it is intentionally dropped.
**Fix:** Either log `message_text[:200]` in the ANTISPAM warning for traceability, or add a
comment that the payload is intentionally not persisted post-Phase-07.

---

_Reviewed: 2026-06-23_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
