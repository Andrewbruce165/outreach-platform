# Phase 9: Cold-Contact Failover - Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 4 (1 new service, 2 modified call-site files, 1 new test)
**Analogs found:** 4 / 4 (every file has a strong in-repo analog)

> Built on top of `09-RESEARCH.md`, which already names `rebalance.py::rebalance_on_attach` as the primary analog. This map adds verified current line numbers and copy-ready excerpts per file. Where the literal CONTEXT wording (D-09 "call get_or_assign_sender per row") conflicts with the grounded code, the research's resolution (inline the rebalance candidate-filter + `_pick_least_loaded` + dual-UPDATE) wins — see Shared Pattern "Healthy-pool resolution".

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/services/failover.py` (NEW) | service | batch / transform (set-based queue-row move) | `app/services/rebalance.py::rebalance_on_attach` | exact (behavioral twin) |
| `app/services/queue.py` (MODIFY — 2 call sites) | service / worker | event-driven (freeze handler) | the existing `db2` freeze blocks at L743 / L784 within the same file | self-analog (insert after existing block) |
| `app/services/listener.py` (MODIFY — 1 call site) | service / listener | event-driven (antispam signal) | `app/services/campaigns.py::attach_sender` caller of `rebalance_on_attach` (transaction-neutral pattern) | role-match |
| `tests/test_failover.py` (NEW) | test | unit + integration | `tests/test_rebalance.py` | exact (clone structure + helpers) |

## Pattern Assignments

### `app/services/failover.py` (service, batch/transform) — NEW

**Analog:** `app/services/rebalance.py` (entire module, 1-215). This is a near-clone. Copy its module structure: docstring explaining concurrency discipline, `logging` + `from sqlalchemy import text` + `AsyncSession` imports, a `_*_PREDICATE` SQL constant, then the async helper. Three deliberate divergences flagged below.

**Imports pattern** (rebalance.py:33-39):
```python
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
```
Plus, for the per-caller session-owning mode (research Transaction Boundaries), import `AsyncSessionLocal` the same way queue.py does (`from app.database import AsyncSessionLocal` — see queue.py freeze blocks).

**Recommended signature** (research §Transaction Boundaries — supports both wirings):
```python
async def failover_cold_backlog(
    frozen_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    """Move the frozen sender's cold-pending backlog onto healthy pool senders.
    db is None  → helper opens+commits its OWN session (queue.py callers).
    db passed   → transaction-neutral, caller commits (listener antispam path).
    Returns total rows moved (0 if nothing movable or no healthy receiver — D-13).
    """
```

**Core pattern — movable-row claim + dual UPDATE** (copy from rebalance.py:169-205, with two Phase-9 edits):
```python
# claim under the SAME lock discipline as the worker (rebalance.py:169-186):
moved_rows = (await db.execute(
    text(f"""
        SELECT mq.id AS id, mq.recipient_phone AS phone
        FROM message_queue mq
        WHERE {_COLD_PENDING_PREDICATE}
          AND mq.sender_id = :frozen_sid
        FOR UPDATE OF mq SKIP LOCKED
    """),
    {"cid": cid, "frozen_sid": frozen_sid},
)).fetchall()

# reassign queue row + sticky CCA in lock-step (rebalance.py:191-205):
for row in moved_rows:
    await db.execute(
        text("UPDATE message_queue SET sender_id = :new, scheduled_at = NOW() "  # <-- EDIT 1
             "WHERE id = :rid"),
        {"new": new_sid, "rid": str(row.id)},
    )
    await db.execute(
        text("""
            UPDATE campaign_contact_assignments
            SET sender_id = :new
            WHERE campaign_id = :cid AND contact_phone = :phone
        """),
        {"new": new_sid, "cid": cid, "phone": row.phone},
    )
```

**Three deliberate divergences from rebalance.py** (do NOT copy blindly):
1. **`scheduled_at = NOW()` in the queue UPDATE** (Pitfall 2 / D-10). rebalance.py:195 does NOT touch `scheduled_at`. The freeze path just pushed every pending row +24h (queue.py:745, queue.py:786, listener.py:926), so a moved row MUST be reset to NOW() or it idles 24h on the healthy sender.
2. **Per-row destination, not one named sender** (D-09). rebalance back-fills ONE `new_sender_id`. Failover spreads each row across the healthy pool — resolve the healthy candidate set (Shared Pattern below) and pick per row via `_pick_least_loaded`. Do NOT delegate to `get_or_assign_sender` (Pitfall 1: its CCA short-circuit returns the frozen sender).
3. **Wider movable predicate** (D-05) — see next.

**Movable predicate (`_COLD_PENDING_PREDICATE`)** — extends rebalance.py:50-64. rebalance's `NOT EXISTS conversations` is too strict (blocks empty conversations D-05 wants moved) and only checks `status='sent'`. Replace the two `NOT EXISTS` clauses with the research-resolved form (09-RESEARCH.md §D-06 Resolution):
```sql
mq.status = 'pending'
AND mq.item_type = 'message'              -- D-04.1 (rebalance omits this)
AND mq.campaign_id = :cid
AND NOT EXISTS (                           -- D-04.2: never sent in this campaign
    SELECT 1 FROM message_queue s
    WHERE s.campaign_id = mq.campaign_id
      AND s.recipient_phone = mq.recipient_phone
      AND s.status IN ('sent', 'processing')   -- WIDER than rebalance ('sent' only)
)
AND NOT EXISTS (                           -- D-04.3 + D-05: no STARTED dialog
    SELECT 1 FROM conversations cv
    JOIN messages m ON m.conversation_id = cv.id   -- JOIN makes empty-conv MOVABLE
    WHERE cv.workspace_id = mq.workspace_id
      AND cv.contact_phone = mq.recipient_phone
)
```
Anchor the "0 messages" check on the **`messages`** table (migration 017: `conversation_id`, `direction`), NOT `messages_log` (outbound-only, no inbound). `messages` has no `recipient_phone` — join `conversations` then check `messages.conversation_id`. Do not filter by `direction` (any row = engaged).

**Logging pattern — COUNT + UUIDs only, never PII** (copy rebalance.py:209-213):
```python
logger.info(
    "failover: moved %d cold-pending rows off sender %s to %d receivers in campaign %s",
    n, frozen_sid, len(set(receivers)), cid,
)
```
Plus a "nowhere to move" log when the pool has no healthy receiver (D-13 / FAIL-07).

**Cross-campaign scope** (research Open Question 1): the helper is keyed on `sender_id`, not `campaign_id`. Group the frozen sender's movable rows by `campaign_id`, resolve the healthy pool per campaign, skip campaigns with <2 eligible senders. Cheap at v1 scale.

---

### `app/services/queue.py` (service/worker, event-driven) — MODIFY 2 call sites

**Analog:** the existing freeze blocks in the same file (self-pattern). Insert the failover call **after** the existing `db2.commit()` so the `restriction_status` flag is committed and visible (Pitfall 3 — frozen sender must not pick itself as receiver).

**Call site 1 — PEER_FLOOD block** (verified at queue.py:733-774). The `db2` block ends at **L754** (`await db2.commit()`); `_fail_item` + `return` at L773-774. Insert after L754, before the callback fire:
```python
# after db2.commit() at queue.py:754 — sender is now flagged spam_limited
from app.services.failover import failover_cold_backlog
await failover_cold_backlog(sender.id)   # db=None → owns its own committed session
```

**Call site 2 — ACCOUNT_FROZEN block** (verified at queue.py:776-812). Identical `db2` shape; `db2.commit()` at **L795**, `_fail_item` + `return` at L811-812. Insert after L795 the same way (`failover_cold_backlog(sender.id)`).

**Why `db=None` here** (research Transaction Boundaries): the freeze write is already committed in the short-lived `db2`, and the worker's outer `db` is mid-item (about to `_fail_item` + return). The helper opening its OWN committed session is the safe, best-effort wiring. Mirror the existing `async with AsyncSessionLocal() as db2:` style (queue.py:743, queue.py:784) inside the helper.

---

### `app/services/listener.py` (service/listener, event-driven) — MODIFY 1 call site

**Analog:** `campaigns.py::attach_sender`'s transaction-neutral call to `rebalance_on_attach` (CR-01 pattern — pass the open session, helper does NOT commit, caller commits once).

**Call site — `_handle_antispam_signal`** (verified at listener.py:881-957). The session is `async with AsyncSessionLocal() as session:` opened at **L919**; pause UPDATE at L924-931, flag UPDATE at L936-944, `session.commit()` at **L946**. Insert the failover call **before** L946 so pause + flag + failover land in ONE commit (transaction-neutral mode):
```python
# inside the `async with ... as session:` block, AFTER the flag UPDATE (L944),
# BEFORE session.commit() (L946):
from app.services.failover import failover_cold_backlog
await failover_cold_backlog(sender_id, session)   # pass session → caller commits
```
Statement order matters (Pitfall 3): the `UPDATE senders SET restriction_status='spam_limited'` (L936-944) must precede the failover call so the flag is visible to the helper's candidate SELECT within the same session.

---

### `tests/test_failover.py` (test, unit + integration) — NEW

**Analog:** `tests/test_rebalance.py` (1-204). Clone its structure exactly.

**Import-inside-body pattern** (test_rebalance.py:51 — keeps `--collect-only` clean while module is a Wave-0 RED stub):
```python
import pytest
from sqlalchemy import text
pytestmark = pytest.mark.asyncio

async def test_failover_spreads_to_healthy_pool(...):
    from app.services.failover import failover_cold_backlog   # import inside body
    ...
```

**Copy helpers verbatim** (test_rebalance.py:26-41): `_pending_counts(db, campaign_id)` and `_cca_sender_for(db, campaign_id, contact_phone)`.

**Reuse fixtures** (conftest.py): `test_running_campaign_factory(sender_count=N)` (L679), `test_queue_item_factory` (L599), `async_db_session`.

**Fixture extension needed (Wave-0 gap):** `test_queue_item_factory` (conftest.py:626-674) supports `with_conversation=True` (L663-672) but inserts NO `messages` row — that is exactly the **empty-conversation = movable** case (D-05), already producible as `with_conversation=True`. For the **has-message = NOT movable** case (FAIL-05 / Pitfall 4), add an optional `with_message=True` flag that inserts a `messages` row (`conversation_id`, `direction`) after the conversation insert, OR use `test_conversation_factory` (conftest.py:696) + a manual `messages` insert.

**Test → requirement map** (from 09-RESEARCH.md §Phase Requirements → Test Map):
| Test name | Req |
|-----------|-----|
| `test_failover_spreads_to_healthy_pool` | FAIL-01 |
| `test_failover_skips_engaged` | FAIL-03 |
| `test_failover_moves_empty_conversation` | FAIL-03 / D-05 |
| `test_failover_cca_in_sync` | FAIL-04 |
| `test_failover_leaves_engaged` | FAIL-05 |
| `test_failover_idempotent` | FAIL-06 |
| `test_failover_no_receiver_keeps_paused` | FAIL-07 / D-13 |
| `test_failover_excludes_frozen_as_receiver` | FAIL-01 / Pitfall 1 |
| `test_peer_flood_triggers_failover` (+ frozen + antispam) | FAIL-02 |

**Run command (test-overlay ONLY — CLAUDE.md hard rule):**
```
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x
```

---

## Shared Patterns

### Healthy-pool resolution + least-loaded (the D-09 intent, grounded)
**Source:** `app/services/rebalance.py:99-114` (candidate filter is `rotation.py:112-124` verbatim) + `rotation.py:198` (`_pick_least_loaded`)
**Apply to:** `failover.py` (per-row destination selection)

```python
# Source: rebalance.py:99-114 — resolve the campaign's HEALTHY pool.
# The restriction_status='none' clause excludes the just-frozen sender automatically
# (Pitfall 1 / Pitfall 3) — this is why we do NOT call get_or_assign_sender directly.
pool_rows = (await db.execute(text("""
    SELECT s.id AS sid
    FROM campaign_senders cs
    JOIN senders s ON s.id = cs.sender_id
    JOIN campaigns c ON c.id = cs.campaign_id
    WHERE cs.campaign_id = :cid
      AND s.lifecycle_status = 'active'
      AND s.auth_status = 'ok'
      AND s.role = 'sender'
      AND s.restriction_status = 'none'
      AND s.workspace_id = c.workspace_id
"""), {"cid": cid})).fetchall()
```
Then per movable row call `_pick_least_loaded(db, candidates)` (rotation.py:198) for an even spread. **Do NOT** call `get_or_assign_sender` for selection — its CCA short-circuit (rotation.py:71-97) returns the frozen sender because its eligibility check (rotation.py:76) is `active AND ok` and ignores `restriction_status` (Pitfall 1). This is the single most important grounded deviation from CONTEXT D-09's literal wording.

### Worker-safe row claim (concurrency)
**Source:** `rebalance.py:183` (`FOR UPDATE OF mq SKIP LOCKED`) + `status='pending'` guard in the predicate
**Apply to:** `failover.py` movable-row SELECT
The `status='pending'` guard excludes rows the worker already flipped to `processing` (committed before the Telegram send); `SKIP LOCKED` skips rows the worker is locking. Guarantees idempotency (FAIL-06): a 2nd call moves 0.

### Lock-step queue + CCA UPDATE
**Source:** `rebalance.py:191-205`
**Apply to:** `failover.py` reassignment loop
queue UPDATE + CCA UPDATE back-to-back in ONE transaction so an observer never sees `message_queue.sender_id` and `campaign_contact_assignments.sender_id` disagree (D-10 / FAIL-04). Phase-9 edit: add `scheduled_at = NOW()` to the queue UPDATE.

### COUNT-only audit logging (no PII)
**Source:** `rebalance.py:209-213`
**Apply to:** all `failover.py` log lines
Log COUNT + sender UUIDs + campaign UUID only — never recipient phones or payloads (CLAUDE.md: "API_KEY не в логах"; D-12 / FAIL-08).

### Transaction-neutral vs own-session (multi-caller wiring)
**Source:** `rebalance.py` docstring CR-01 (caller commits) + queue.py freeze `db2` blocks (own committed session)
**Apply to:** `failover.py` signature (`db: AsyncSession | None`)
- listener antispam (transaction-neutral): pass `session`, helper does NOT commit.
- queue.py PEER_FLOOD / ACCOUNT_FROZEN: pass nothing, helper opens + commits its own `AsyncSessionLocal()` session (best-effort, the freeze write is already committed in `db2`).

### Reconcile-resume fallback (D-13) — already exists, NO code needed
**Source:** `app/services/listener.py:1402-1407` (SpamBot 'free' verdict resume loop)
Rows that could NOT be moved (no healthy receiver) keep `sender_id = frozen_sender` + `scheduled_at = +24h`; the existing reconcile loop pulls them back to NOW() when the sender clears. Moved rows have a different `sender_id`, so this loop won't double-resume them. FAIL-07 needs no new code — just a test asserting rows stay paused.

## No Analog Found

None. Every new/modified file maps to a shipped, tested in-repo analog. Phase 9 is a behavioral twin of Phase 8 `rebalance.py`.

## Metadata

**Analog search scope:** `app/services/` (rebalance.py, rotation.py, queue.py, listener.py, campaigns.py), `tests/` (test_rebalance.py, conftest.py)
**Files scanned:** 6 read directly (rebalance.py, rotation.py, queue.py freeze blocks, listener.py antispam block, test_rebalance.py, conftest.py fixtures)
**Line numbers:** verified against current source on 2026-06-24 (PEER_FLOOD 733-774 / db2.commit L754; ACCOUNT_FROZEN 776-812 / db2.commit L795; antispam 881-957 / session.commit L946; rebalance dual-UPDATE 191-205; candidate filter 99-114)
**Pattern extraction date:** 2026-06-24
