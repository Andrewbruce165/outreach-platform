---
phase: 08-pool-management-and-even-distribution
reviewed: 2026-06-23T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - app/services/rebalance.py
  - app/routers/campaigns.py
  - app/routers/senders.py
  - app/schemas/__init__.py
  - tests/conftest.py
  - tests/test_pool_endpoints.py
  - tests/test_rebalance.py
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts
findings:
  critical: 2
  warning: 7
  info: 5
  total: 14
status: issues_found
---

# Phase 8: Code Review Report

**Reviewed:** 2026-06-23
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Phase 8 ships campaign-scoped pool management (attach/detach), an even-split
rebalance on attach, sender-lock exposure on `GET /senders`, and a frontend pool
panel. The lock discipline in `rebalance.py` is genuinely careful and the
`sent_today` / `lock_map` queries on the list endpoint are correctly de-N+1'd.
However adversarial tracing surfaced two BLOCKER-class correctness problems and
several robustness gaps:

1. The attach endpoint can leave the new `campaign_senders` row **uncommitted**
   on a non-running campaign because `rebalance_on_attach` is the only path that
   issues the final `commit()` — but it is only called when `status == 'running'`.
   (After re-reading: the handler DOES call `db.commit()` at line 862 unconditionally,
   so this specific leak does not occur — see CR-01 for the actual double-commit /
   transaction-ownership hazard that does.)
2. The rebalance fairness math uses `total // P` as the new sender's target while
   donors are selected by `cnt > target` (strict). With three or more senders and
   a remainder, this can move **zero** rows when a back-fill is expected, silently
   violating the ±1 contract the tests only exercise at P=2.

Detach is missing workspace-scoping on the `sender_id` path parameter, the
rebalance and attach share a session with overlapping `commit()` ownership, and
the frontend disables detach for locked senders even though the backend never
blocks detaching a sender from the campaign that owns the lock.

## Critical Issues

### CR-01: `rebalance_on_attach` commits a transaction it does not own, splitting the attach into two commits

**File:** `app/services/rebalance.py:191`, `app/routers/campaigns.py:846-862`
**Issue:**
`attach_sender` does `db.flush()` to insert the `CampaignSender` row (line 846),
runs the lock-conflict check, and for a running campaign calls
`rebalance_on_attach(...)` (line 860), which itself executes `await db.commit()`
(rebalance.py:191). Control then returns to the handler which calls
`await db.commit()` AGAIN (line 862).

Consequences:
- The whole operation is no longer atomic: the `CampaignSender` insert + the
  queue/CCA moves are committed inside `rebalance_on_attach`, and the second
  `db.commit()` in the handler commits an empty unit of work. If anything between
  the two commits raised (it currently does not, but the structure is fragile),
  the pool row + moved rows would already be durably committed while the caller
  believes it still owns an open transaction.
- A shared-session helper performing its own `commit()` is an anti-pattern that
  breaks the documented invariant ("`db`: async session; this function owns the
  transaction (single commit)" is asserted in the helper, but the caller ALSO
  commits and ALSO holds pending state — the flushed insert). The two are
  coupled by accident, not contract.
- On the NON-running path (line 859 false) the flushed insert is committed only
  by the handler's line 862 — a different commit owner than the running path.
  Two code paths, two different components owning durability of the same insert.

**Fix:** Make the helper transaction-neutral — never commit inside it; let the
single caller own one commit:
```python
# rebalance.py — remove the internal commit
async def rebalance_on_attach(campaign_id, new_sender_id, db) -> int:
    ...
    # for row in moved_rows: ... (UPDATEs only)
    # DO NOT call db.commit() here — caller owns the transaction.
    logger.info("rebalance: moved %d ...", n, new_sid, cid)
    return n

# campaigns.py attach_sender — single commit owns insert + moves
    if c.status == "running":
        await rebalance_on_attach(c.id, payload.sender_id, db)
    await db.commit()   # the one and only commit
```

### CR-02: Even-split rebalance can move 0 rows for pools with ≥3 senders and a remainder, violating the ±1 contract

**File:** `app/services/rebalance.py:133-143`
**Issue:**
`target = total // P` (floor) is used both as the new sender's goal and as the
donor surplus threshold (`cnt > target`, strict). For P=2 (the only case the
tests cover) this is fine. For P≥3 with an uneven backlog it under-moves or
moves nothing:

Example — P=3 senders, backlog A=2, C=2, new sender B=0, total=4.
`target = 4 // 3 = 1`. `need = 1 - 0 = 1`. Donors = senders with `cnt > 1` →
A and C qualify, 1 row moved. B ends at 1, A or C at 1 — within ±1. OK here.

Counter-example — P=3, A=1, C=1, B(new)=0, total=2. `target = 2 // 3 = 0`,
`need = 0 - 0 = 0` → early `return 0` at line 135. B stays at 0 forever while
A and C hold 1 each: B is starved (its fair share is ≥0 but the back-fill
contract is "within ±1 of total/P", and 0 vs target 0 is technically ±0 — so
this sub-case is acceptable).

True failure — P=3, A=3, C=0, B(new)=0, total=3. `target = 1`, `need = 1`.
Donors = `cnt > 1` → only A (3>1). Moves 1 to B. B=1, A=2, C=0. B is within ±1
of target 1, BUT C is still 0 and A holds 2 — the pool is NOT evened, and a
later attach is the only thing that fixes it. The docstring (lines 22-26)
explicitly scopes this away as "v1 limit", so this is borderline. The hard bug:
when `total < P` the new sender can be legitimately owed a row but
`target = total // P = 0` makes `need <= 0` and the function returns without
moving anything even though donors exist with surplus. With total=2, P=3,
A=2, C=0, B=0: `target=0`, `need=0`, return 0 — B and C both starve while A
hoards 2 pending rows. The ±1 claim ("B within ±1 of total/P") holds
numerically (0 vs 0), but the *pool* is maximally skewed and B receives zero
traffic, which is the exact failure `rebalance_on_attach` exists to prevent
(docstring lines 3-7).

**Fix:** Use a ceil-based fair share for the new sender so a non-empty backlog
with idle senders always back-fills at least one row, and document/relax the
strict donor threshold:
```python
import math
target = math.ceil(total / P)          # new sender's fair upper share
need = target - load.get(new_sid, 0)
if need <= 0:
    return 0
need = min(need, BATCH_CAP)
# donors: anyone strictly above the FLOOR share has movable surplus
floor_share = total // P
donors = [sid for sid, cnt in load.items()
          if cnt > floor_share and sid != new_sid]
```
At minimum, add a P≥3 test with a remainder to `test_rebalance.py` — the current
suite only proves P=2 and cannot catch this.

## Warnings

### WR-01: `detach_sender` never workspace-scopes the `sender_id` path parameter

**File:** `app/routers/campaigns.py:871-934`
**Issue:** `attach_sender` calls `_validate_workspace_owns_senders(...)` (line 829)
to prove the sender belongs to the caller's workspace, but `detach_sender` does
NOT validate `sender_id` against the workspace at all. The campaign is
workspace-scoped (via `_load_campaign`), and the `DELETE FROM campaign_senders`
is keyed on `campaign_id`, so a cross-workspace sender_id simply matches no row
and is a no-op (200). This is not a data leak, but it is an inconsistent
contract: detaching a foreign or non-existent sender returns 200 "success"
instead of 404, masking client bugs and diverging from attach's 404 behavior.
The cold-pending guard query (line 903) also runs with an unvalidated `sid`.
**Fix:** Mirror attach — validate ownership / membership and 404 if the sender
is not actually attached to this campaign:
```python
c = await _load_campaign(db, ctx, campaign_id)
attached = (await db.execute(
    select(CampaignSender).where(
        CampaignSender.campaign_id == c.id,
        CampaignSender.sender_id == sender_id,
    ))).scalars().first()
if attached is None:
    raise HTTPException(404, detail={"code": "SENDER_NOT_ATTACHED", ...})
```

### WR-02: Frontend disables detach for a sender locked by the SAME campaign being viewed

**File:** `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx:481,529`
**Issue:** In the attached-pool list, `locked` is computed from
`s.locked_by_campaign_name` and the remove button is `disabled={locked || busy}`
(line 529). But `attached_senders[].locked_by_campaign_id` is populated by the
backend ONLY for OTHER running campaigns (`c.id != :cid`, campaigns.py:206/214).
So if the user is viewing campaign X (running) and sender S is in X, `locked` is
false here — good. However, if S is ALSO in another running campaign Y, the panel
marks S "Locked by Y" and disables removal from X. The backend `detach_sender`
has NO such guard for a non-last sender — it would happily detach S from X
(min-pool permitting). The UI therefore blocks a legitimate, backend-allowed
action, stranding the user with no way to shrink the pool from the UI.
**Fix:** Only disable detach when the backend would actually reject it (last
sender of running campaign). Lock-by-other-campaign should not block detaching
from THIS campaign:
```tsx
// remove button — do not gate on cross-campaign lock
disabled={busy}
```
Keep the "Locked by …" badge as informational only.

### WR-03: Rebalance load-count and donor-claim run as two unsynchronized queries — count can be stale under worker concurrency

**File:** `app/services/rebalance.py:117-170`
**Issue:** Step 2 computes per-sender `load` (line 118) and `target`/`need` from
it. Step 4 re-derives donor counts in a subquery (`dl.cnt`) and claims rows with
`FOR UPDATE ... SKIP LOCKED` (line 167). Between Step 2 and Step 4 the queue
worker can flip donor rows `pending → processing` and commit. The `need` computed
in Step 3 is based on the Step-2 snapshot; if the worker drains several donor
rows in that window, `LIMIT :need` may now exceed available movable rows and the
function moves fewer than `need` (acceptable — handled by `if not moved_rows`).
The opposite is not possible (rows don't appear). So this is not a correctness
BLOCKER, but the docstring (lines 14-20) overstates the guarantee — the ±1
target is computed against a snapshot that may be stale by claim time. Worth a
comment and a follow-up test that interleaves a simulated worker flip.
**Fix:** Either recompute `need` from the locked donor set, or document that the
±1 target is best-effort under concurrent draining and the back-fill is
re-converged by the next attach/rebalance pass.

### WR-04: Per-row UPDATE loop in rebalance issues 2×N statements instead of one set-based move

**File:** `app/services/rebalance.py:177-189`
**Issue:** The docstring sells this as "a single, set-based, campaign-scoped
pass" (line 9) and "a set-based move" (line 30), but the implementation loops
`moved_rows` and fires two UPDATEs per row (queue + CCA). With `BATCH_CAP=500`
that is up to 1000 round-trips inside one transaction. This is a correctness-
adjacent robustness issue: a long-running transaction holding `FOR UPDATE` locks
on up to 500 queue rows increases the window for lock contention with the worker
(the very thing the design tries to minimize). Not a perf-only nit because it
directly affects lock-hold duration vs the concurrent worker.
**Fix:** Collapse to two set-based UPDATEs keyed on the locked id set:
```python
ids = [str(r.id) for r in moved_rows]
phones = [r.phone for r in moved_rows]
await db.execute(text(
    "UPDATE message_queue SET sender_id=:new WHERE id = ANY(:ids)"),
    {"new": new_sid, "ids": ids})
await db.execute(text(
    "UPDATE campaign_contact_assignments SET sender_id=:new "
    "WHERE campaign_id=:cid AND contact_phone = ANY(:phones)"),
    {"new": new_sid, "cid": cid, "phones": phones})
```

### WR-05: `attach_sender` lock check + rollback discards the flushed insert but never re-validates after rollback

**File:** `app/routers/campaigns.py:846-856`
**Issue:** The insert-then-check-then-rollback pattern flushes the new
`CampaignSender`, runs `_check_sender_lock` (which now sees the incoming sender),
and on conflict `await db.rollback()` + raises 409. The rollback correctly undoes
the flushed insert. However `_check_sender_lock` runs against the same session
that just flushed — it relies on the flush being visible to a `text()` raw SQL
query in the same transaction (it is, in PostgreSQL). The concern: the conflict
detection counts the incoming sender's own row only if no OTHER running campaign
shares it — correct — but there is no idempotency re-guard: between the
`existing` check (line 832) and the flush, a concurrent attach of the same
(campaign, sender) could violate the PK. That raises `IntegrityError` which is
NOT caught here (unlike `create_campaign`/`patch_campaign`), surfacing as an
unhandled 500.
**Fix:** Wrap the flush in `try/except IntegrityError` and convert a duplicate-PK
to the idempotent 200 response (the row already exists):
```python
try:
    await db.flush()
except IntegrityError:
    await db.rollback()
    return await _campaign_to_response(db, ctx, c)
```

### WR-06: `check_spambot` swallows all exceptions into a 500 that echoes the raw error message to the client

**File:** `app/routers/senders.py:708-713`
**Issue:** The broad `except Exception as e:` returns
`detail={"code": "SPAMBOT_CHECK_FAILED", "message": str(e)}`. `str(e)` on a
Telethon/network exception can leak internal detail (proxy host:port, session
path, peer ids) to the API client. CLAUDE.md threat T4 explicitly says
"API_KEY не в логах" and the rebalance module is careful to log counts only —
this endpoint contradicts that posture by returning arbitrary exception text in
the HTTP body.
**Fix:** Log the full error server-side (already done at line 709) but return a
generic client message:
```python
except Exception as e:
    logger.error(f"SpamBot check failed for {slug}: {e}")
    raise HTTPException(500, detail={"code": "SPAMBOT_CHECK_FAILED",
        "message": "SpamBot check failed; see server logs"})
```

### WR-07: `_compute_is_exhausted` runs per-campaign on every list response — unbounded query fan-out on `GET /campaigns`

**File:** `app/routers/campaigns.py:462`, `167-193`
**Issue:** `list_campaigns` builds the response with
`[await _campaign_to_response(...) for c in rows]`, and each call runs
`_build_attached_senders` (with 2 correlated subqueries per attached sender) plus
`_compute_is_exhausted` (2 full COUNT queries). For a workspace with N campaigns
this is O(N) round-trips and the attached-senders subqueries are themselves
correlated. This is flagged as a robustness/correctness-adjacent issue (not pure
perf): the loop awaits sequentially on a shared session, so a workspace with many
campaigns produces a slow, lock-prone list endpoint that can time out the UI.
Performance is out of v1 scope per the brief, so this is WARNING not BLOCKER, but
it is a structural smell introduced/extended by the pool work touching
`_build_attached_senders`.
**Fix:** Batch the attached-senders + lock lookups into a single grouped query
across all campaign ids (same pattern already used in `list_senders` lock_map),
and compute `is_exhausted` in one pass.

## Info

### IN-01: Stale "TODO(v2-rls)" markers and reliance on app-layer workspace scoping

**File:** `app/routers/campaigns.py:76,114,132,153,457` and `senders.py:225,250,592,731,792`
**Issue:** Numerous `# TODO(v2-rls): replaced by RLS policy` comments mark every
workspace filter as a temporary app-layer guard. This is fine for v1 but means
every new query (e.g. detach's cold-pending raw SQL) must remember the scope
manually — WR-01 is exactly the failure mode. Track the RLS migration as a real
backlog item, not inline TODOs.

### IN-02: `STATUS_PILL` map uses statuses the backend never emits

**File:** `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx:21-28`
**Issue:** The pill map defines `scheduled`, `finished`, `stopped`, but the
backend campaign statuses are `draft|running|paused|done` (schemas.py:696). The
UI falls back to `draft` styling for `done`, so a finished campaign renders as a
grey "Draft"-styled pill with label "Draft" (since `STATUS_PILL.done` is
undefined → fallback). Dead map keys + a wrong-looking pill for the real `done`
status.
**Fix:** Map `done` explicitly; drop the unused keys or align them with backend.

### IN-03: `auto_fill_campaign` is a stub that ignores its input and returns canned data

**File:** `app/routers/campaigns.py:425-445`
**Issue:** Documented v1 stub, acceptable, but it accepts `_body` and silently
discards `brief`. No workspace/auth side effect, low risk — noting for
completeness so it is not mistaken for a working endpoint during QA.

### IN-04: Test factory inserts `message_queue` with `**overrides` spliced into bind params, allowing silent SQL/param mismatch

**File:** `tests/conftest.py:640-650`
**Issue:** `test_queue_item_factory` does `{..., **overrides}` into the bind-param
dict but the INSERT statement has a fixed column list — any `overrides` key that
is not a bind placeholder is silently ignored (asyncpg ignores extra params only
if the statement references them; here it would raise or no-op depending on
driver). This is a test-only foot-gun: a future test passing
`priority=5` expecting it to land would see it dropped without error.
**Fix:** Either remove `**overrides` or expand the INSERT to bind them.

### IN-05: `errorMessageFromEnvelope` falls back to raw backend `message` despite the comment saying it should not

**File:** `/root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts:42-47`
**Issue:** The comment states "Per AGENTS.md we should not normally show raw
backend messages" but the code does exactly that for any unmapped code. Combined
with WR-06 (backend echoing raw exception text), an unmapped `SPAMBOT_CHECK_FAILED`
would surface a raw Python exception string to the end user. Low severity given
the mapped codes cover the pool flows, but the fallback path is the leak vector.
**Fix:** Gate the raw-message fallback behind a dev flag, or whitelist which
codes may show their message.

---

_Reviewed: 2026-06-23_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
