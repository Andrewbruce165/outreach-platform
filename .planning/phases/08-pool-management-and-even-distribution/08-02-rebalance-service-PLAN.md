---
phase: 08-pool-management-and-even-distribution
plan: 02
type: tdd
wave: 2
depends_on: [01]
files_modified:
  - app/services/rebalance.py
autonomous: true
requirements: [POOL-07, POOL-08, POOL-08b]
must_haves:
  truths:
    - "Attaching a sender to a running campaign with a skewed cold-pending backlog back-fills the NEWLY-ATTACHED sender to within ±1 of total/P (single-pass even-split guaranteed for the new sender only; multi-donor full evenness and the >BATCH_CAP case are out of v1 scope)"
    - "Calling rebalance again on an already-even pool moves 0 rows (idempotent)"
    - "Rebalance never moves a sent, processing, or engaged-dialog row"
    - "campaign_contact_assignments stays in sync with message_queue.sender_id for every moved row"
    - "Rebalance never races the queue worker (uses status='pending' + FOR UPDATE OF mq SKIP LOCKED)"
    - "Honors decisions D-07 (sticky enqueue is why a back-fill is needed), D-08 (light rebalance of un-sent cold pending only, active dialogs untouched), D-09 (campaign-scoped even-split pass; _pick_least_loaded NOT reused; BATCH_CAP; idempotent and safe under load)"
  artifacts:
    - path: app/services/rebalance.py
      provides: "rebalance_on_attach(campaign_id, new_sender_id, db) campaign-scoped even-split"
      exports: ["rebalance_on_attach"]
      min_lines: 60
  key_links:
    - from: app/services/rebalance.py
      to: message_queue + campaign_contact_assignments
      via: "single transaction: UPDATE message_queue.sender_id + UPDATE campaign_contact_assignments.sender_id keyed on recipient_phone"
      pattern: "FOR UPDATE OF mq SKIP LOCKED"
---

<objective>
Build the one genuinely-new piece of logic in Phase 8: a campaign-scoped even-split rebalance that, when a sender is attached to a RUNNING campaign, moves a fair share of un-sent cold-pending queue rows from overloaded senders onto the new sender — keeping `campaign_contact_assignments` in sync and never racing the worker. Implements D-08/D-09.

Purpose: enqueue is sticky (D-07), so least-loaded alone never back-fills a sender attached after the folder is fully enqueued. Without rebalance, a sender added to a running campaign would receive zero traffic.
Output: `app/services/rebalance.py::rebalance_on_attach`, green against tests/test_rebalance.py (POOL-07/08/08b).

This is a TDD plan: the tests already exist (Plan 01). Run them RED, implement, run GREEN.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/08-pool-management-and-even-distribution/08-RESEARCH.md
@.planning/phases/08-pool-management-and-even-distribution/08-PATTERNS.md

<interfaces>
<!-- SQL building blocks to COPY (not invent). Verified line numbers, this session. -->
Eligible candidate filter to copy verbatim (rotation.py:113-123) — so a spam_limited new sender does NOT receive moved rows:
  s.lifecycle_status='active' AND s.auth_status='ok' AND s.role='sender' AND s.restriction_status='none' AND s.workspace_id=:wid

Worker row-claim discipline to mirror (queue.py:294-313):
  ... WHERE mq.sender_id=:sid AND mq.status='pending' ... FOR UPDATE OF mq SKIP LOCKED
  (worker flips claimed rows to status='processing' and commits BEFORE Telegram — queue.py:351-357)

Sticky CCA upsert reference (rotation.py:150-163): UNIQUE idx_cca_campaign_phone (campaign_id, contact_phone).

Cold-pending predicate (movable iff), keyed on recipient_phone (RESEARCH §"never sent / no dialog"):
  mq.status='pending' AND mq.campaign_id=:cid
  AND NOT EXISTS (SELECT 1 FROM message_queue s WHERE s.campaign_id=mq.campaign_id AND s.recipient_phone=mq.recipient_phone AND s.status='sent')
  AND NOT EXISTS (SELECT 1 FROM conversations cv WHERE cv.workspace_id=mq.workspace_id AND cv.contact_phone=mq.recipient_phone)

Signature (consumed by Plan 03 attach endpoint): async def rebalance_on_attach(campaign_id, new_sender_id, db: AsyncSession) -> int  (returns moved-row count for idempotency assertions)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Implement rebalance_on_attach campaign-scoped even-split</name>
  <files>app/services/rebalance.py</files>
  <behavior>
    - POOL-07: running campaign, sender A has N cold-pending rows, sender B attached → after call, the NEWLY-ATTACHED sender B holds within ±1 of total/P (one-directional back-fill of B; pre-existing donors are not re-evened against each other, and a single pass caps at BATCH_CAP).
    - POOL-08: second call on an even pool returns 0 (moves nothing).
    - POOL-08b: rows that are sent / processing / belong to an engaged conversation are never moved.
    - CCA invariant: every moved recipient's campaign_contact_assignments.sender_id == new_sender_id.
  </behavior>
  <read_first>
    - app/services/rotation.py:113-123 (eligible candidate filter to copy) and :198-217 (_pick_least_loaded — read ONLY to understand why it is NOT reusable: global scope, single pick — do NOT call it)
    - app/services/queue.py:294-313 (FOR UPDATE OF mq SKIP LOCKED claim) and :351-357 (worker flips to processing before Telegram)
    - app/services/rotation.py:150-163 (CCA sticky upsert keyed (campaign_id, contact_phone))
    - app/models/__init__.py::MessageQueue (recipient_phone:204, status values), Conversation.contact_phone, CampaignContactAssignment, CampaignSender
    - migrations/016_phase4.sql:55-109 (campaign_senders, campaign_contact_assignments UNIQUE idx_cca_campaign_phone, message_queue composite index)
    - 08-RESEARCH.md §"Rebalance Algorithm" (full pseudocode + fair-share math) and §"Concurrency Safety"
    - 08-PATTERNS.md §"app/services/rebalance.py" (composed-from-two-analogs guidance)
  </read_first>
  <action>
    Create `app/services/rebalance.py` with `async def rebalance_on_attach(campaign_id, new_sender_id, db: AsyncSession) -> int`. All queries raw `text()` (codebase convention). Algorithm per RESEARCH §"Rebalance Algorithm":
    1. Resolve the eligible pool: SELECT senders in `campaign_senders` for `campaign_id` applying the rotation.py:113-123 candidate filter (lifecycle_status='active', auth_status='ok', role='sender', restriction_status='none', workspace_id). If `new_sender_id` is not in the eligible pool → return 0. If pool size P < 2 → return 0.
    2. Count current movable cold-pending load per sender (campaign-scoped) using the cold-pending predicate from the interfaces block (status='pending', NOT EXISTS sent, NOT EXISTS conversations). total = sum; if total == 0 → return 0.
    3. target = total // P; need = target - load[new_sender_id]; if need <= 0 → return 0 (idempotent). need = min(need, BATCH_CAP) with module-level `BATCH_CAP = 500` (Claude's discretion per D-09 — single pass is fine at v1 scale; leave a comment). NOTE: this single-pass back-fill guarantees the ±1-of-total/P even-split for the NEWLY-ATTACHED sender only — it does NOT re-balance pre-existing donors against each other, and `total/P > BATCH_CAP` would need a follow-up pass; both are intentionally out of v1 scope (matches the narrowed POOL-07 assertion in Plan 01).
    4. Select donor rows: movable cold-pending rows whose sender_id is a donor with load > target, `ORDER BY donor-load DESC, mq.scheduled_at DESC`, `LIMIT :need`, with `FOR UPDATE OF mq SKIP LOCKED` and `status='pending'` guard (this is what prevents racing the worker). If none → return 0.
    5. In the SAME transaction, for each moved row: `UPDATE message_queue SET sender_id=:new WHERE id=:row_id` AND `UPDATE campaign_contact_assignments SET sender_id=:new WHERE campaign_id=:cid AND contact_phone=:recipient_phone` (Pitfall 3 — keep CCA in sync). Single `await db.commit()`.
    6. `logger.info("rebalance: moved %d cold-pending rows to sender %s in campaign %s", n, new_sender_id, campaign_id)` — COUNT ONLY, never payloads (CLAUDE.md). Return n.
    Constraints: async/await everywhere; NO time.sleep/print/sync requests; do NOT call `_pick_least_loaded`; do NOT touch rate-limit constants or `scheduled_at` semantics; do NOT add a migration (016 covers schema).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_rebalance.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/rebalance.py` exports `rebalance_on_attach(campaign_id, new_sender_id, db) -> int`.
    - The donor SELECT contains `FOR UPDATE OF mq SKIP LOCKED` AND `status = 'pending'` (grep: `grep -n "SKIP LOCKED" app/services/rebalance.py` returns a match; `grep -n "status = 'pending'\|status='pending'" app/services/rebalance.py` returns a match).
    - The module does NOT import or call `_pick_least_loaded` (grep: `grep -n "_pick_least_loaded" app/services/rebalance.py` returns nothing).
    - The function UPDATEs both `message_queue` and `campaign_contact_assignments` (grep both table names present).
    - NO new file under migrations/ (grep: `git status --porcelain migrations/` shows no additions).
    - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_rebalance.py -x` → all 3 tests GREEN (POOL-07/08/08b).
  </acceptance_criteria>
  <done>tests/test_rebalance.py is fully green; rebalance is idempotent, worker-safe, CCA-synced, and never moves non-cold rows.</done>
</task>

</tasks>

<threat_model>
ASVS L1 surface for the rebalance service:
- **T1 — Cross-workspace / cross-campaign row movement (V4 Access Control / IDOR).** A rebalance must only move rows belonging to the target campaign AND its workspace. Mitigation: every query is scoped by `mq.campaign_id = :cid`; the eligible-pool filter includes `s.workspace_id = :wid`; the CCA UPDATE is scoped `campaign_id = :cid`. Executor MUST NOT write an UPDATE that omits the campaign_id scope. (Caller passes a campaign already loaded under the workspace via `_load_campaign` in Plan 03.)
- **T2 — Race with the queue worker causing double/lost send (V1 Architecture / data integrity).** Mitigation: donor SELECT uses `status='pending'` + `FOR UPDATE OF mq SKIP LOCKED` (identical discipline to queue.py:313); the worker flips to `processing` and commits before Telegram, so a mid-send row is excluded by the status guard. Reassign is one transaction so an observer never sees queue.sender_id and CCA.sender_id disagree.
- **T3 — Sending from an ineligible/restricted account (V1 business logic).** Moving rows onto a `spam_limited`/frozen new sender would route cold contacts to a restricted account. Mitigation: the eligible-pool filter copies rotation.py:113-123 (`restriction_status='none'` etc.); if `new_sender_id` is not eligible, return 0 (no moves). Verified by POOL-07 only running on an eligible sender.
- **T4 — Log leakage of contact data (V7 Logging).** Mitigation: log moved-row COUNT only, never recipient_phone/payloads.
All three threats map to assertions in tests/test_rebalance.py (POOL-08b covers T2/T3 non-cold exclusion).
</threat_model>

<verification>
- `pytest tests/test_rebalance.py -x` (overlay) all green.
- Grep gates from acceptance_criteria (SKIP LOCKED present, no _pick_least_loaded, both tables updated, no migration added).
- Full suite still green per wave-merge sampling (run before declaring done): `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -x`.
</verification>

<success_criteria>
- rebalance_on_attach is campaign-scoped, even-split, idempotent, worker-safe, CCA-synced.
- No migration added; rotation.py and queue.py untouched; rate-limit/scheduled_at semantics unchanged.
- POOL-07/08/08b green.
</success_criteria>

<output>
After completion, create `.planning/phases/08-pool-management-and-even-distribution/08-02-SUMMARY.md`
</output>
