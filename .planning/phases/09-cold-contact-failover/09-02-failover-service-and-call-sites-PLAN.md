---
phase: 09-cold-contact-failover
plan: 02
type: execute
wave: 2
depends_on: ["09-01"]
files_modified:
  - app/services/failover.py
  - app/services/queue.py
  - app/services/listener.py
  - tests/test_failover.py
autonomous: true
requirements: [FAIL-01, FAIL-02, FAIL-03, FAIL-04, FAIL-05, FAIL-06, FAIL-07, FAIL-08, FAIL-09]
nyquist_compliant: true

must_haves:
  truths:
    - "On freeze, a frozen sender's cold-pending backlog is reassigned to healthy pool senders inline, with zero new worker"
    - "Failover is triggered from all three freeze paths: PEER_FLOOD, ACCOUNT_FROZEN, antispam-signal"
    - "Engaged dialogs (any messages row) stay on the frozen sender and keep replying"
    - "The frozen sender is never chosen as a failover receiver"
    - "When no healthy receiver exists, rows stay paused on the frozen sender; nothing is lost or failed"
    - "Moved rows are sendable immediately (scheduled_at=NOW), not after the +24h freeze pause"
    - "No new migration is added"
  artifacts:
    - path: "app/services/failover.py"
      provides: "failover_cold_backlog(frozen_sender_id, db=None) — per-row even-spread reassignment off a frozen sender"
      contains: "async def failover_cold_backlog"
      min_lines: 80
    - path: "app/services/queue.py"
      provides: "failover_cold_backlog call after PEER_FLOOD db2.commit and after ACCOUNT_FROZEN db2.commit"
      contains: "failover_cold_backlog"
    - path: "app/services/listener.py"
      provides: "transaction-neutral failover_cold_backlog(sender_id, session) before session.commit in _handle_antispam_signal"
      contains: "failover_cold_backlog"
    - path: "tests/test_failover.py"
      provides: "FAIL-02 integration tests for the 3 call sites added on top of 09-01 unit stubs"
      contains: "test_peer_flood_triggers_failover"
  key_links:
    - from: "app/services/failover.py"
      to: "message_queue + campaign_contact_assignments"
      via: "lock-step dual UPDATE (sender_id + scheduled_at=NOW on queue, sender_id on CCA)"
      pattern: "UPDATE message_queue SET sender_id.*scheduled_at = NOW"
    - from: "app/services/failover.py"
      to: "healthy pool resolution"
      via: "candidate filter restriction_status='none' + _pick_least_loaded per row (NOT get_or_assign_sender)"
      pattern: "restriction_status = 'none'"
    - from: "app/services/queue.py"
      to: "app.services.failover.failover_cold_backlog"
      via: "call after db2.commit() in both freeze blocks (db=None, own session)"
      pattern: "failover_cold_backlog"
    - from: "app/services/listener.py"
      to: "app.services.failover.failover_cold_backlog"
      via: "call before session.commit() in _handle_antispam_signal (pass session)"
      pattern: "failover_cold_backlog"
---

<objective>
Implement cold-contact failover: a new `app/services/failover.py::failover_cold_backlog` that moves a frozen sender's cold-pending backlog onto healthy pool senders, wired inline into the three freeze paths (PEER_FLOOD, ACCOUNT_FROZEN, antispam-signal). Turns the 09-01 RED tests GREEN and adds the FAIL-02 call-site integration tests.

Purpose: Close the gap that caused the b7cc7d06 incident — when a sender freezes, its un-contacted backlog now spreads to healthy accounts instead of stalling. Engaged dialogs stay put (continuity). Best-effort: if no healthy receiver, rows stay paused and the existing reconcile loop resumes them.
Output: app/services/failover.py (new), 3 call-site edits, FAIL-02 tests, full suite green. No migration (FAIL-09).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/09-cold-contact-failover/09-CONTEXT.md
@.planning/phases/09-cold-contact-failover/09-RESEARCH.md
@.planning/phases/09-cold-contact-failover/09-PATTERNS.md
@.planning/phases/09-cold-contact-failover/09-VALIDATION.md
@app/services/rebalance.py
@app/services/rotation.py

<interfaces>
<!-- Contracts the executor implements/uses. Extracted from the grounded research. -->

NEW helper to create (app/services/failover.py):
```python
async def failover_cold_backlog(
    frozen_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    """Move the frozen sender's cold-pending backlog onto healthy pool senders.
    db is None  → helper opens+commits its OWN session (queue.py callers).
    db passed   → transaction-neutral, caller commits (listener antispam path).
    Returns total rows moved (0 if nothing movable or no healthy receiver — D-13)."""
```

Movable predicate (_COLD_PENDING_PREDICATE — extends rebalance.py:50-64, D-06 resolved):
```sql
mq.status = 'pending'
AND mq.item_type = 'message'                 -- D-04.1
AND mq.campaign_id = :cid
AND NOT EXISTS (                              -- D-04.2 never sent in this campaign
    SELECT 1 FROM message_queue s
    WHERE s.campaign_id = mq.campaign_id
      AND s.recipient_phone = mq.recipient_phone
      AND s.status IN ('sent', 'processing')
)
AND NOT EXISTS (                              -- D-04.3 + D-05 no STARTED dialog
    SELECT 1 FROM conversations cv
    JOIN messages m ON m.conversation_id = cv.id
    WHERE cv.workspace_id = mq.workspace_id
      AND cv.contact_phone = mq.recipient_phone
)
```

Healthy-pool resolution (copy rebalance.py:99-114; restriction_status='none' excludes frozen sender):
```sql
SELECT s.id AS sid
FROM campaign_senders cs
JOIN senders s ON s.id = cs.sender_id
JOIN campaigns c ON c.id = cs.campaign_id
WHERE cs.campaign_id = :cid
  AND s.lifecycle_status = 'active' AND s.auth_status = 'ok'
  AND s.role = 'sender' AND s.restriction_status = 'none'
  AND s.workspace_id = c.workspace_id
```

Reused (DO NOT modify): rotation._pick_least_loaded(db, candidates) (rotation.py:198).
DO NOT call rotation.get_or_assign_sender for selection (Pitfall 1: its stale-CCA short-circuit returns the frozen sender).

Call-site current line anchors (verified 2026-06-24):
- queue.py PEER_FLOOD block 733-774; its db2.commit() at L754; _fail_item+return at 773-774.
- queue.py ACCOUNT_FROZEN block 776-812; its db2.commit() at L795; _fail_item+return 811-812.
- listener.py _handle_antispam_signal 881-957; session opened L919; flag UPDATE 936-944; session.commit() L946.
</interfaces>
</context>

<threat_model>
ASVS L1. Internal backend reassignment — no new endpoint, no user input (failover triggers internally on freeze). Threats:
- **PII leakage in logs (mitigated — FAIL-08):** the helper logs COUNT + source sender UUID + receiver sender UUIDs + campaign UUID ONLY, mirroring rebalance.py:209-213. NEVER recipient_phone or payloads. A dedicated test (test_failover_logs_count_no_pii) asserts no phone substring appears in logs. Block-worthy if violated; not expected.
- **Cross-workspace / cross-campaign row leakage (mitigated):** predicate is campaign-scoped (`mq.campaign_id = :cid`) and the healthy-pool query joins `campaigns c` with `s.workspace_id = c.workspace_id`. Backlog is grouped by campaign_id and the pool is resolved per campaign — a sender in another workspace can never become a receiver.
- **Integrity under concurrent worker (mitigated):** `FOR UPDATE OF mq SKIP LOCKED` + `status='pending'` guard (same discipline as rebalance.py:183 and the worker claim) — rows the worker already flipped to `processing` are excluded; locked rows are skipped. Second failover call moves 0.
No HIGH-severity threat. Cleared.
</threat_model>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Create app/services/failover.py::failover_cold_backlog</name>
  <files>app/services/failover.py</files>
  <read_first>
    - app/services/rebalance.py (WHOLE file 1-215) — the template: module docstring concurrency discipline, imports (logging/UUID/text/AsyncSession), `_COLD_PENDING_PREDICATE` constant (50-64), movable-row claim under FOR UPDATE SKIP LOCKED (169-186), lock-step dual UPDATE (191-205), COUNT-only log (209-213).
    - app/services/rotation.py:35-217 — `get_or_assign_sender` short-circuit (71-97) and its eligibility check at line 76 (active AND ok, IGNORES restriction_status — this is Pitfall 1, why we must NOT use it for selection); candidate filter incl. restriction_status='none' (112-124); `_pick_least_loaded` (198) which we DO reuse.
    - app/models/__init__.py:10-26 (enums), :108-127 (messages_log — outbound only), :190-273 (MessageQueue, Conversation), :533-571 (CampaignSender, CampaignContactAssignment).
    - migrations/017_phase5.sql — `messages` table columns (conversation_id, direction); confirm `messages` has NO recipient_phone (must join conversations).
    - app/database.py — how AsyncSessionLocal is imported (queue.py uses `from app.database import AsyncSessionLocal`).
    - tests/test_failover.py (from 09-01) — the contract these tests assert.
  </read_first>
  <action>
    Create app/services/failover.py mirroring rebalance.py's structure. Imports: `logging`, `from uuid import UUID`, `from sqlalchemy import text`, `from sqlalchemy.ext.asyncio import AsyncSession`, `from app.database import AsyncSessionLocal`, and `from app.services.rotation import _pick_least_loaded`. Module-level `logger = logging.getLogger(__name__)`.

    Define `_COLD_PENDING_PREDICATE` as the SQL string in the interfaces block above (extends rebalance's predicate with: `item_type='message'`, `IN ('sent','processing')`, and the `JOIN messages` empty-conversation widening anchored on the `messages` table by conversation_id — NOT messages_log).

    Implement `async def failover_cold_backlog(frozen_sender_id: UUID, db: AsyncSession | None = None) -> int`:
    1. Session ownership: if `db is None`, wrap the whole body in `async with AsyncSessionLocal() as db:` and `await db.commit()` at the end (queue.py callers). If a `db` is passed, run transaction-neutral — do NOT commit (listener antispam caller commits). Factor the work into an inner function taking the live session to avoid duplicating logic.
    2. Group the frozen sender's movable backlog by campaign_id: first SELECT DISTINCT `campaign_id` from message_queue where sender_id=frozen and status='pending' and item_type='message'. For each campaign_id (`cid`):
       a. Resolve the healthy pool via the candidate query in interfaces (restriction_status='none' excludes the frozen sender — Pitfall 1/3). If fewer than 1 healthy candidate, log "nowhere to move" (count + frozen UUID + cid) and continue (D-13 / FAIL-07) — do NOT touch the rows.
       b. Claim movable rows: `SELECT mq.id, mq.recipient_phone FROM message_queue mq WHERE {_COLD_PENDING_PREDICATE} AND mq.sender_id = :frozen_sid FOR UPDATE OF mq SKIP LOCKED` with `{"cid": cid, "frozen_sid": str(frozen_sender_id)}`.
       c. For EACH claimed row (D-09 per-row even spread): call `await _pick_least_loaded(db, candidate_ids)` to choose the receiver; then dual UPDATE in lock-step (rebalance.py:191-205) — `UPDATE message_queue SET sender_id = :new, scheduled_at = NOW() WHERE id = :rid` (the `scheduled_at = NOW()` is divergence EDIT 1 / Pitfall 2, mandatory because freeze pushed pending +24h) and `UPDATE campaign_contact_assignments SET sender_id = :new WHERE campaign_id = :cid AND contact_phone = :phone`. Track receivers in a set.
    3. Logging (FAIL-08, copy rebalance.py:209-213 discipline): per campaign with moves, `logger.info("failover: moved %d cold-pending rows off sender %s to %d receivers in campaign %s", n, frozen_sender_id, len(receivers), cid)`. NEVER log recipient_phone or payloads.
    4. Return the total rows moved across all campaigns (int).

    Constraints: async only, raw `text()` SQL, no time.sleep/print. DO NOT call rotation.get_or_assign_sender for selection (Pitfall 1). DO NOT add a migration (FAIL-09). DO NOT touch rate-limiter intervals or FloodWait logic.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - All 9 unit tests from 09-01 GREEN: `pytest tests/test_failover.py -k "not triggers_failover" -x` exits 0.
    - `test_failover_excludes_frozen_as_receiver` GREEN — proves get_or_assign_sender short-circuit is NOT used (Pitfall 1 avoided).
    - `test_failover_moves_empty_conversation` GREEN and `test_failover_leaves_engaged` GREEN — proves the JOIN messages predicate distinguishes empty (movable) from engaged (not).
    - `test_failover_cca_in_sync` GREEN — moved rows have scheduled_at <= NOW() and CCA.sender_id matches queue.sender_id.
    - `test_failover_no_receiver_keeps_paused` GREEN and `test_failover_idempotent` GREEN.
    - `grep -n "get_or_assign_sender" app/services/failover.py` returns nothing; `grep -n "_pick_least_loaded\|restriction_status = 'none'\|scheduled_at = NOW" app/services/failover.py` all present.
    - No new file under `migrations/` (`git status --porcelain migrations/` empty).
  </acceptance_criteria>
  <done>app/services/failover.py exists; all 09-01 unit tests green; frozen sender never a receiver; empty-conversation movable, engaged not; CCA in lock-step with scheduled_at=NOW; idempotent; no migration; rate-limiter untouched.</done>
</task>

<task type="auto">
  <name>Task 2: Wire failover into the three freeze call sites</name>
  <files>app/services/queue.py, app/services/listener.py</files>
  <read_first>
    - app/services/queue.py PEER_FLOOD block (~700-774): read the full `async with AsyncSessionLocal() as db2:` block, the pause-pending +24h UPDATE (~745), the `restriction_status='spam_limited'` UPDATE, `db2.commit()` (~754), and `_fail_item`+return (~773-774).
    - app/services/queue.py ACCOUNT_FROZEN block (~776-812): the identical db2 pattern, `db2.commit()` (~795), `_fail_item`+return (~811-812).
    - app/services/listener.py::_handle_antispam_signal (~881-957): session opened (~919), pause UPDATE (~924-931), flag UPDATE `restriction_status='spam_limited'` (~936-944), `session.commit()` (~946); read the self-check guard at the top of the function (preserve it).
    - app/services/failover.py (Task 1) — the signature and the two wiring modes.
  </read_first>
  <action>
    Insert three calls to `failover_cold_backlog` (D-02). Import locally inside each block (`from app.services.failover import failover_cold_backlog`) to avoid import cycles, matching how queue.py imports freeze helpers.

    1. queue.py PEER_FLOOD: AFTER the existing `await db2.commit()` (~L754, where restriction_status='spam_limited' is now persisted), BEFORE `_fail_item`/return, call `await failover_cold_backlog(sender.id)` with db=None (own committed session). Use the sender id variable already in scope in that block (verify it is the frozen sender's id).
    2. queue.py ACCOUNT_FROZEN: AFTER `await db2.commit()` (~L795) the same way: `await failover_cold_backlog(sender.id)` (db=None).
    3. listener.py _handle_antispam_signal: INSIDE the `async with ... as session:` block, AFTER the flag UPDATE (~L944) and BEFORE `session.commit()` (~L946), call `await failover_cold_backlog(sender_id, session)` — pass the session (transaction-neutral; pause+flag+failover land in ONE commit). The flag UPDATE MUST precede this call (Pitfall 3 — so the candidate SELECT sees restriction_status != 'none' and won't pick the frozen sender).

    Order discipline (Pitfall 3): at all three sites the `restriction_status` write must be committed/visible before the failover call. Do NOT change the pause +24h logic, the freeze UPDATEs, the self-check guard, the rate-limiter, or FloodWait retry. The failover call is additive only.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "failover_cold_backlog" app/services/queue.py` shows exactly 2 call sites (PEER_FLOOD + ACCOUNT_FROZEN), each AFTER a `db2.commit()`.
    - `grep -n "failover_cold_backlog" app/services/listener.py` shows 1 call inside _handle_antispam_signal, passing `session`, located before `session.commit()`.
    - FAIL-02 integration tests GREEN: `pytest tests/test_failover.py -k "triggers_failover" -x` exits 0 (PEER_FLOOD + ACCOUNT_FROZEN + antispam each invoke failover and move the cold backlog).
    - `git diff app/services/queue.py` shows NO change to rate-limiter constants, FloodWait retry, the pause +24h UPDATE, or `_fail_item` (additive call only).
  </acceptance_criteria>
  <done>All three freeze paths call failover_cold_backlog after the restriction flag is persisted; queue.py uses db=None, listener passes session before commit; FAIL-02 tests green; no empirical constants touched.</done>
</task>

<task type="auto">
  <name>Task 3: Add FAIL-02 call-site integration tests + full-suite green gate</name>
  <files>tests/test_failover.py</files>
  <read_first>
    - tests/test_failover.py (current state from 09-01 + Task 1).
    - tests/test_rebalance.py — for how call-site/integration flows are exercised against the worker freeze blocks (if a freeze-path harness exists, mirror it; else drive the failover via the public freeze handlers with a fixture sender flagged frozen).
    - app/services/queue.py PEER_FLOOD/ACCOUNT_FROZEN blocks and listener.py _handle_antispam_signal — to know what state to set up so each path reaches the failover call.
  </read_first>
  <action>
    Append the FAIL-02 integration tests to tests/test_failover.py (import-inside-body pattern preserved):
    - `test_peer_flood_triggers_failover`: drive the PEER_FLOOD freeze path for a sender with a healthy pool peer and cold backlog; assert the backlog moved off the frozen sender (reuse the unit-test assertion helpers). If invoking the full queue worker block is impractical in a unit test, assert at minimum that the PEER_FLOOD handler path calls failover (e.g. via a thin seam: confirm post-handler queue state shows the backlog reassigned). Prefer exercising the real path; document any seam used.
    - `test_account_frozen_triggers_failover`: same for the ACCOUNT_FROZEN path.
    - `test_antispam_signal_triggers_failover`: drive `_handle_antispam_signal` (transaction-neutral path) and assert pause+flag+failover landed in one commit and the cold backlog moved.
    Keep tests deterministic; no network/Telegram calls (mock the Telegram layer as test_rebalance/conftest already do where needed). All three must be RED-then-GREEN against Task 2's wiring.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_failover.py -x` (overlay) exits 0 — all unit + 3 FAIL-02 integration tests green.
    - Full suite green: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` exits 0 (~683+ collected, no regressions in test_rebalance / test_pool_endpoints).
    - `grep -c "def test_" tests/test_failover.py` >= 12 (9 unit + 3 integration).
    - FAIL-09 confirmed: `git status --porcelain migrations/` is empty (no new migration).
  </acceptance_criteria>
  <done>3 FAIL-02 integration tests added and green; full suite green with no regressions; no migration introduced.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x` exits 0 (all FAIL-01..FAIL-08 + FAIL-02 green).
- Full suite (overlay) green — no regressions in rebalance/pool/listener tests.
- `git status --porcelain migrations/` empty (FAIL-09).
- `grep -n "get_or_assign_sender" app/services/failover.py` empty (Pitfall 1 avoided — selection via _pick_least_loaded over restriction-aware candidate set).
- `git diff` shows no change to rate-limiter intervals (4/20/150), FloodWait retry, or the +24h pause logic.
</verification>

<success_criteria>
- app/services/failover.py implements failover_cold_backlog with the resolved D-06 predicate, per-row even spread (no get_or_assign_sender), lock-step queue+CCA UPDATE with scheduled_at=NOW, FOR UPDATE SKIP LOCKED idempotency, COUNT/UUID-only logging, D-13 best-effort fallback.
- All three freeze paths (PEER_FLOOD, ACCOUNT_FROZEN, antispam) call it inline after the restriction flag is persisted; correct session ownership per caller.
- FAIL-01..FAIL-09 all covered by green tests; full suite green; no migration; empirical constants untouched.
</success_criteria>

<output>
After completion, create `.planning/phases/09-cold-contact-failover/09-02-SUMMARY.md`.
</output>
