# Phase 07: Unified Freeze Policy - Pattern Map

**Mapped:** 2026-06-23
**Files analyzed:** 4 (3 source modifications + 1 test rewrite, plus 1 net-new test)
**Analogs found:** 5 / 5 (every modified file has an in-repo analog at HEAD)

> This is a brownfield convergence. The "analog" for the listener rewrite lives in the **same process family** (queue worker / listener) — `_handle_antispam_signal` must be rewritten to byte-for-byte mirror the PEER_FLOOD soft-restriction branch in `queue.py`. No external patterns; no new migration (028 already shipped the columns).

## File Classification

| Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------|------|-----------|----------------|---------------|
| `app/services/listener.py` (`_handle_antispam_signal`, lines 881-957) | service / event-handler | event-driven (inbound SpamBot msg → DB state machine) | `app/services/queue.py:733-754` (PEER_FLOOD branch) | **exact** — same two UPDATEs, same separate-session pattern, same timestamps |
| `app/services/rotation.py` (candidate filter, lines 112-124) | service | request-response (sender selection query) | self — extend existing WHERE clause (cf. `queue.py:401` skip semantics) | **exact** — one WHERE clause add, mirrors existing `auth_status='ok'` guard |
| `app/services/queue.py` (pre-send skip, lines 397-406) | service | request-response | **no change needed** — already implemented (FRZ-05) | n/a — assert via test only |
| `tests/test_spambot_selfcheck.py` (`test_antispam_guard_cancels_when_no_selfcheck`, lines 133-158) | test (DB-backed unit) | event-driven assertion | self — flip assertions to new contract | **exact** — same fixtures, same seed helper |
| `tests/test_rotation_campaign.py` (NEW test) | test (DB-backed unit) | request-response assertion | `test_rotation_skips_inactive_senders` (lines 80-97) | **exact** — clone, swap `auth_status='locked'` → `restriction_status='spam_limited'` |

---

## Pattern Assignments

### `app/services/listener.py` — rewrite `_handle_antispam_signal` (service, event-driven)

**Analog:** `app/services/queue.py:733-754` (PEER_FLOOD branch). The rewrite replaces the body of the existing handler (lines 907-948) with the PEER_FLOOD soft-restriction write, **deleting** the `ai_enabled=false` UPDATE entirely.

**KEEP unchanged — self-check early-return (`listener.py:901-905`)** must remain the FIRST statement (Pitfall 2):
```python
if telegram_service.is_spambot_selfcheck(sender_slug):
    logger.info(
        f"🔕 [{sender_slug}] solicited SpamBot reply during self-check — skip auto-cancel"
    )
    return
```

**DELETE — the `ai_enabled=false` block (`listener.py:909-925`).** This is the ONLY antispam-triggered `ai_enabled` write (grep-verified in RESEARCH §State Inventory). Removing it is sufficient to keep replies flowing — `ai_engine` has no `restriction_status` gate.
```python
# DELETE THIS — do not preserve disabled_count/disabled_rows either
UPDATE conversations
SET ai_enabled = false, paused_at = NOW(), paused_reason = :reason, updated_at = NOW()
WHERE sender_id = :sender_id AND ai_enabled = true
RETURNING id
```

**REPLACE — the terminal-fail block (`listener.py:930-946`)** with the PEER_FLOOD pause+flag. Current bad code:
```python
# CURRENT (BAD): listener.py:930-946 — terminal kill, no auto-resume (b7cc7d06 incident)
UPDATE message_queue
SET status = 'failed', error_message = :reason, finished_at = NOW()
WHERE sender_id = :sender_id AND status IN ('pending', 'processing')
RETURNING id
```

**Copy this verbatim pattern from `queue.py:739-754`** (adapt the bind-var name `sender.id` → the handler's `sender_id`):
```python
# Source: app/services/queue.py:739-754  (PEER_FLOOD)
pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
recheck_at = datetime.now(timezone.utc) + timedelta(
    seconds=get_settings().restriction_recheck_interval_seconds
)
async with AsyncSessionLocal() as db2:
    await db2.execute(text("""
        UPDATE message_queue SET scheduled_at = :pause_until
        WHERE sender_id = :sid AND status = 'pending'
    """), {"pause_until": pause_until, "sid": str(sender.id)})
    await db2.execute(text("""
        UPDATE senders
        SET restriction_status = 'spam_limited',
            restricted_until = :recheck_at
        WHERE id = :sid
    """), {"recheck_at": recheck_at, "sid": str(sender.id)})
    await db2.commit()
```

**Pattern properties to replicate (do NOT deviate):**
- Pending items are **paused** (`scheduled_at` +24h), status stays `'pending'` — NOT `'failed'`. The reconcile resume query (`listener.py:1402-1408`) only finds `status='pending' AND scheduled_at > NOW()`; failing them breaks auto-resume (Pitfall 1).
- Scope the queue UPDATE to `status = 'pending'` ONLY (drop the current `'processing'` clause) — matches PEER_FLOOD exactly and avoids the in-flight lost-update race (Pitfall 3, Open Q2).
- Two distinct timestamps: `pause_until = now + 24h` (empirical queue pause — DO NOT change, CLAUDE.md hard rule), `recheck_at = now + restriction_recheck_interval_seconds` (default 6h, `config.py:79`). Copy lines 739-742 byte-for-byte (Pitfall 4).
- Write `restriction_status='spam_limited'` (soft), NOT `'frozen'`. ACCOUNT_FROZEN (`queue.py:789-794`) is the frozen writer — leave it alone.
- Keep the **separate `AsyncSessionLocal()` session** + single `commit()`, wrapped in the existing `try/except` that logs-and-swallows (`listener.py:907,956`) so a transient DB error never poisons the listener loop.
- **Open decision for planner (RESEARCH Open Q1):** optionally guard `AND restriction_status <> 'frozen'` on the senders UPDATE to avoid downgrading a hard-frozen sender. Recommended but benign either way (frozen accounts aren't sending).
- **Imports already present** in `listener.py`: `datetime`, `timezone`, `timedelta`, `AsyncSessionLocal`, `text`, `get_settings`, `telegram_service`. No new imports needed (same module set as `queue.py`).
- Update the docstring (lines 888-892) to reflect new behaviour ("pause + flag spam_limited, leave AI on") and the closing `logger.warning` (lines 950-954) to drop the `disabled_count` reference.

---

### `app/services/rotation.py` — exclude restricted senders (service, request-response)

**Analog:** the existing WHERE clause in the same query (`rotation.py:112-124`) — add one line mirroring the adjacent `auth_status = 'ok'` guard. Semantically aligned with `queue.py:401` (worker skips `restriction_status != 'none'`).

**Current query (`rotation.py:112-124`):**
```python
candidates_rows = await db.execute(
    text("""
        SELECT s.id AS sid
        FROM campaign_senders cs
        JOIN senders s ON s.id = cs.sender_id
        WHERE cs.campaign_id = :cid
          AND s.lifecycle_status = 'active'
          AND s.auth_status = 'ok'
          AND s.role = 'sender'
          AND s.workspace_id = :wid
    """),
    {"cid": cid_str, "wid": workspace_id_str},
)
```

**Change — add ONE clause** (a `spam_limited` sender stays `lifecycle_status='active'` + `auth_status='ok'` by design per migration 028, so it currently slips through):
```python
          AND s.role = 'sender'
          AND s.restriction_status = 'none'   # <-- ADD (FRZ-04)
          AND s.workspace_id = :wid
```
No other rotation logic changes. No new bind vars.

---

### `app/services/queue.py` — pre-send skip (service, request-response) — NO CODE CHANGE

**Already implemented (FRZ-05).** `_check_rate_limits` already SELECTs `restriction_status, restricted_until` (`queue.py:377-383`) and returns `False` when `restriction_status != "none"` (`queue.py:401-406`). The phase only needs a regression assertion, which already exists and passes: `tests/test_sender_restriction.py::test_queue_pre_send_skips_restricted` (line 214). Do not modify this file for FRZ-05.

---

### `tests/test_spambot_selfcheck.py` — flip the cancel-path assertions (test)

**Analog:** the file's own sibling test + the `_seed_queue_and_conversation` helper (lines 80-95). Fixtures `async_db_session`, `test_sender_factory`, `test_workspace` already wired (conftest lines 187/349/359).

**`test_antispam_guard_cancels_when_no_selfcheck` (lines 133-158) currently asserts the OLD contract and WILL BREAK — this is expected, part of the phase (RESEARCH Wave 0 Gap, CRITICAL):**
```python
# CURRENT (must change):
assert q_status == "failed"       # → becomes "pending"
assert ai_enabled is False        # → becomes True
```

**New assertions (new freeze contract):**
```python
assert q_status == "pending"                       # paused, not failed
assert ai_enabled is True                          # AI left on — replies flow
# also assert the sender flag was written:
restriction = (await async_db_session.execute(
    text("SELECT restriction_status FROM senders WHERE id = :id"),
    {"id": str(sender.id)},
)).scalar()
assert restriction == "spam_limited"
```
Rename the test (e.g. `test_antispam_guard_pauses_and_flags_when_no_selfcheck`) and update its docstring. `test_antispam_guard_skips_when_selfcheck_active` (lines 101-127) already asserts `pending` + `ai_enabled True` — it matches the new behaviour and should pass as-is (the marker short-circuits before any write).

**Optional extension (covers FRZ-02 resume linkage):** after the handler runs, assert `scheduled_at > NOW()` on the paused item so the reconcile resume query (`status='pending' AND scheduled_at > NOW()`) will pick it up.

---

### `tests/test_rotation_campaign.py` — NEW test for FRZ-04 (test)

**Analog:** `test_rotation_skips_inactive_senders` (lines 80-97). Clone it; the only change is the disqualifier — swap `auth_status='locked'` for `restriction_status='spam_limited'`. The factory accepts it directly (`defaults.update(overrides)`, conftest:383).

```python
async def test_rotation_skips_restricted_senders(
    async_db_session, test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """spam_limited sender (still active/ok) must NOT be assigned a cold contact (FRZ-04)."""
    s_limited = await test_sender_factory(slug="limited", restriction_status="spam_limited")
    s_active = await test_sender_factory(slug="alive")
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], s_limited.id)
    await attach_sender_to_campaign(camp["id"], s_active.id)

    sender = await get_or_assign_sender(camp["id"], "+79991112299", async_db_session)
    assert sender is not None
    assert sender.id == s_active.id
```
Fixtures `test_campaign_factory` and `attach_sender_to_campaign` are already used throughout this file (no new fixtures). `get_or_assign_sender` already imported at top (line 15).

---

## Shared Patterns

### Soft-restriction write (the canonical pattern this phase converges on)
**Source:** `app/services/queue.py:739-754` (PEER_FLOOD); identical structure at `queue.py:780-795` (ACCOUNT_FROZEN) and consumed by `listener.py:1394-1409` (reconcile resume).
**Apply to:** `_handle_antispam_signal` rewrite.
- Two columns only: `senders.restriction_status` + `senders.restricted_until`.
- Queue paused via `scheduled_at` push, status preserved as `'pending'`.
- Separate `AsyncSessionLocal()` session, single `commit()`.

### Auto-resume dependency (read-side contract)
**Source:** `app/services/listener.py:1366-1411` (`_restriction_reconcile_tick`).
**Applies to:** the listener rewrite must produce state this sweep can consume — selection needs `restriction_status <> 'none' AND restricted_until IS NOT NULL AND restricted_until <= NOW()`; resume needs items in `status='pending' AND scheduled_at > NOW()`. This is WHY pause-not-fail is mandatory.

### Restriction-aware sender filtering
**Source:** `app/services/queue.py:401` (worker skip).
**Apply to:** `rotation.py` candidate filter — same `restriction_status != 'none'` semantics expressed as a SQL WHERE clause.

### DB-backed listener test scaffold
**Source:** `tests/test_spambot_selfcheck.py:71-95` (`_sender_info`, `_seed_queue_and_conversation`) + conftest fixtures (`async_db_session`:187, `test_workspace`:349, `test_sender_factory`:359).
**Apply to:** both test edits. Reuse the seed helper; the factory takes arbitrary column overrides.

### Test execution (MANDATORY — overlay only)
**Source:** project CLAUDE.md + RESEARCH §Validation; `docker-compose.test.yml`.
**Apply to:** every pytest run in this phase. NEVER run bare `docker compose run --rm api pytest` (DATABASE_URL → prod, conftest DROP SCHEMA — 2026-05-26 incident; guard at `tests/conftest.py:46-77`).
- Quick run (per task commit):
  ```
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest \
    tests/test_spambot_selfcheck.py tests/test_sender_restriction.py tests/test_rotation_campaign.py -x
  ```
- Full suite (per wave merge / phase gate):
  ```
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
  ```

---

## No Analog Found

None. Every modified file maps to an exact in-repo analog. No file requires falling back to RESEARCH.md external patterns.

## Anti-Patterns (do NOT introduce — from CLAUDE.md + RESEARCH)

| Don't | Why |
|-------|-----|
| Change the 24h `pause_until` or `4/20/150` rate constants | Empirical, CLAUDE.md hard rule — copy `now + timedelta(hours=24)` verbatim |
| Set paused items to `status='failed'` | Breaks reconcile auto-resume (Pitfall 1, b7cc7d06 incident) |
| Pause `status='processing'` items | Lost-update race with in-flight send (Pitfall 3) — scope to `pending` only |
| Add a `restriction_status` check in `ai_engine` | Unnecessary — just delete the `ai_enabled=false` block; ai_engine never gates on restriction |
| Add a new migration / Alembic | 028 already ships the columns; if ever needed, raw idempotent SQL only |
| Reorder the `is_spambot_selfcheck` early-return | Must stay first or reconcile's own SpamBot ping re-flags the sender (Pitfall 2, infinite loop) |
| Touch the FloodWait / PEER_FLOOD / ACCOUNT_FROZEN branches in `queue.py` | This phase only adds the antispam *mirror* in the listener; the queue branches stay untouched (CLAUDE.md) |

## Metadata

**Analog search scope:** `app/services/{queue,listener,rotation}.py`, `app/config.py`, `tests/{test_spambot_selfcheck,test_rotation_campaign,test_sender_restriction}.py`, `tests/conftest.py`.
**Files scanned (read at HEAD):** 8.
**Verification:** all cited line ranges read directly from working tree (not just trusted from RESEARCH.md) — line numbers confirmed current.
**Pattern extraction date:** 2026-06-23
