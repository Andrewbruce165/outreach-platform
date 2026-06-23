---
phase: 08-pool-management-and-even-distribution
plan: 03
type: execute
wave: 2
depends_on: [01, 02]
files_modified:
  - app/routers/campaigns.py
  - app/schemas/__init__.py
autonomous: true
requirements: [POOL-01, POOL-02, POOL-03, POOL-04, POOL-05, POOL-06, POOL-06b]
must_haves:
  truths:
    - "A workspace can attach a sender to a draft/paused/running campaign and it appears in attached_senders"
    - "Attaching a sender locked by another running campaign returns 409 SENDER_LOCK_CONFLICT with the same conflicts[] contract as /start"
    - "Attaching a sender not owned by the workspace returns 404 SENDER_NOT_FOUND"
    - "Detaching the last sender of a running campaign returns 409 MIN_POOL_GUARD"
    - "Detaching a sender that still has un-sent cold pending returns 409 DETACH_BLOCKED_PENDING"
    - "Detaching a sender whose only remaining work is engaged dialogs succeeds (engaged dialogs do not block detach)"
    - "Attach to a running campaign triggers rebalance_on_attach; draft/paused does not"
  artifacts:
    - path: app/routers/campaigns.py
      provides: "POST /campaigns/{id}/senders and DELETE /campaigns/{id}/senders/{sid}"
      contains: "senders"
    - path: app/schemas/__init__.py
      provides: "CampaignSenderAttachRequest{sender_id: UUID}"
      contains: "CampaignSenderAttachRequest"
  key_links:
    - from: app/routers/campaigns.py (attach_sender)
      to: app/services/rebalance.py::rebalance_on_attach
      via: "called only when campaign.status == 'running' after insert + lock check pass"
      pattern: "rebalance_on_attach"
    - from: app/routers/campaigns.py (attach_sender)
      to: _check_sender_lock
      via: "insert campaign_senders → flush → _check_sender_lock → rollback+409 on conflict"
      pattern: "_check_sender_lock"
---

<objective>
Add the two pool-management endpoints to the existing campaigns router, reusing the start/resume validation chain byte-for-byte. Implements D-01..D-06 (POOL-01..06b).

Purpose: today every campaign is locked to exactly one sender at create time; PATCH ignores sender_ids by design. These endpoints make the pool mutable on draft/paused/running with the same isolation, lock, and guard invariants the rest of the campaign lifecycle already enforces.
Output: `POST /campaigns/{id}/senders`, `DELETE /campaigns/{id}/senders/{sid}` in app/routers/campaigns.py; `CampaignSenderAttachRequest` in app/schemas/__init__.py; tests/test_pool_endpoints.py green.
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

@app/routers/campaigns.py

<interfaces>
<!-- Helpers to reuse verbatim. Verified line numbers this session. -->
From app/routers/campaigns.py:
- _load_campaign(db, ctx, campaign_id) -> Campaign           # :68  404 CAMPAIGN_NOT_FOUND if not in workspace
- _validate_workspace_owns_senders(db, ctx, sender_ids)      # :141 raises 404 {code:"SENDER_NOT_FOUND", missing_sender_ids:[...]}
- _build_attached_senders(db, ctx, campaign_id)              # :194 returns locked_by_campaign_id/name shape
- _campaign_to_response(db, ctx, campaign)                   # :228 full CampaignResponse incl. attached_senders
- _check_sender_lock(db, ctx, campaign_id) -> list[dict]     # :275 [{sender_id,campaign_id,campaign_name}]; checks ALL senders in campaign_senders for that campaign
- start_campaign 409 pattern                                  # :621-627 raise HTTPException(409, detail={"code":"SENDER_LOCK_CONFLICT","conflicts":conflicts})
- min-pool count idiom (start uses)                           # :610-613 select(sql_func.count()).select_from(CampaignSender).where(...)

From app/services/rebalance.py (Plan 02):
- async def rebalance_on_attach(campaign_id, new_sender_id, db) -> int

From app/schemas/__init__.py:
- CampaignSenderAttach (:566) is the RESPONSE sub-object inside attached_senders[] — do NOT reuse it as the request body
- CampaignResponse (:656) — response_model for both endpoints
- CampaignUpdate note (:622-627) — stale "delete→create" docstring to update (D-12: PATCH still ignores sender_ids)

Cold-pending guard SQL (detach, scoped to detached sender) — RESEARCH §"Detach Guards", keyed recipient_phone:
  EXISTS(SELECT 1 FROM message_queue mq WHERE mq.campaign_id=:cid AND mq.sender_id=:sid AND mq.status='pending'
    AND NOT EXISTS(SELECT 1 FROM message_queue s WHERE s.campaign_id=mq.campaign_id AND s.recipient_phone=mq.recipient_phone AND s.status='sent')
    AND NOT EXISTS(SELECT 1 FROM conversations cv WHERE cv.workspace_id=mq.workspace_id AND cv.contact_phone=mq.recipient_phone))
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add CampaignSenderAttachRequest schema + fix CampaignUpdate docstring</name>
  <files>app/schemas/__init__.py</files>
  <read_first>
    - app/schemas/__init__.py:566-574 (CampaignSenderAttach — the response sub-object; confirm it is NOT the request body)
    - app/schemas/__init__.py:577-627 (CampaignCreate.sender_ids default [], CampaignUpdate note at :622-627)
    - 08-PATTERNS.md §"app/schemas/__init__.py"
  </read_first>
  <action>
    Add a thin request schema `class CampaignSenderAttachRequest(BaseModel): sender_id: UUID` near the existing campaign schemas (keeps the OpenAPI body clean — Claude's discretion per RESEARCH §OpenAPI). Update the stale `CampaignUpdate` docstring/comment (:622-627) that says sender_ids is "удали → создай новую": replace it with a note that the pool is managed via `POST/DELETE /campaigns/{id}/senders` and that PATCH still intentionally ignores `sender_ids` (D-12). Do NOT wire sender_ids into PATCH.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.schemas import CampaignSenderAttachRequest; print(CampaignSenderAttachRequest.model_fields.keys())"</automated>
  </verify>
  <acceptance_criteria>
    - `CampaignSenderAttachRequest` importable from app.schemas with a single `sender_id: UUID` field.
    - CampaignUpdate docstring no longer says "удали → создай"; references the attach/detach endpoints.
    - PATCH still ignores sender_ids (no new field added to CampaignUpdate).
  </acceptance_criteria>
  <done>Request schema exists; docstring corrected; PATCH unchanged.</done>
</task>

<task type="auto">
  <name>Task 2: Add POST /campaigns/{id}/senders (attach) — POOL-01/02/03 + rebalance hook</name>
  <files>app/routers/campaigns.py</files>
  <read_first>
    - app/routers/campaigns.py:578-633 (start_campaign — validate→insert→_check_sender_lock→409 contract to copy)
    - app/routers/campaigns.py:304-400 (create_campaign — CampaignSender insert pattern + _validate_workspace_owns_senders call)
    - app/routers/campaigns.py:141-162 (_validate_workspace_owns_senders), :275-298 (_check_sender_lock), :228-272 (_campaign_to_response)
    - 08-RESEARCH.md §"Attach Validation Reuse" (validation order steps 1-7) and §"Code Examples" (attach skeleton)
    - 08-PATTERNS.md §"attach_sender"
  </read_first>
  <action>
    Add `@router.post("/{campaign_id}/senders", response_model=CampaignResponse)` `async def attach_sender(campaign_id: UUID, payload: CampaignSenderAttachRequest, ctx=Depends(auth_dep), db=Depends(get_db))`. Use the SAME auth dependency as the existing campaigns endpoints. Validation order (RESEARCH §"Attach Validation Reuse"):
    1. `c = await _load_campaign(db, ctx, campaign_id)` (404 CAMPAIGN_NOT_FOUND).
    2. `await _validate_workspace_owns_senders(db, ctx, [payload.sender_id])` (404 SENDER_NOT_FOUND).
    3. Idempotency: pre-check `select(CampaignSender).where(campaign_id==c.id, sender_id==payload.sender_id)`; if already attached → skip insert (no-op) and return current `_campaign_to_response` (avoids PK violation on (campaign_id, sender_id)).
    4. Else `db.add(CampaignSender(campaign_id=c.id, sender_id=payload.sender_id, workspace_id=ctx.workspace_id))` then `await db.flush()`.
    5. `conflicts = await _check_sender_lock(db, ctx, c.id)`; if non-empty → `await db.rollback()` and `raise HTTPException(409, detail={"code":"SENDER_LOCK_CONFLICT","conflicts":conflicts})` — byte-identical to start_campaign:621-627. (insert-then-check-then-rollback so the incoming sender is in scope — Pitfall 8.)
    6. If `c.status == "running"`: `await rebalance_on_attach(c.id, payload.sender_id, db)` (import from app.services.rebalance). Skip for draft/paused (D-07).
    7. `await db.commit(); await db.refresh(c); return await _campaign_to_response(db, ctx, c)`.
    NO status-transition block — attach is allowed on draft/paused/running (D-01); only _load_campaign 404 gates it. Add `from app.services.rebalance import rebalance_on_attach` at module top.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py::test_attach_adds_sender tests/test_pool_endpoints.py::test_attach_locked_sender_409 tests/test_pool_endpoints.py::test_attach_foreign_sender_404 -x</automated>
  </verify>
  <acceptance_criteria>
    - POST /campaigns/{id}/senders exists with response_model=CampaignResponse and the same auth dependency as other campaigns endpoints (grep the Depends matches start_campaign's).
    - 409 body for lock is exactly `{"code":"SENDER_LOCK_CONFLICT","conflicts":[...]}` (reuses _check_sender_lock; not a new code).
    - rebalance_on_attach is called ONLY inside an `if c.status == "running":` branch (grep shows the guard).
    - Attaching an already-attached sender is a no-op 200 (no PK violation).
    - test_attach_adds_sender, test_attach_locked_sender_409, test_attach_foreign_sender_404 all GREEN.
  </acceptance_criteria>
  <done>Attach endpoint green for POOL-01/02/03; rebalance fired only on running.</done>
</task>

<task type="auto">
  <name>Task 3: Add DELETE /campaigns/{id}/senders/{sid} (detach) — POOL-04/05/06/06b guards</name>
  <files>app/routers/campaigns.py</files>
  <read_first>
    - app/routers/campaigns.py:578-633 (start_campaign 409 envelope + count idiom :610-613) and :86-102 (_cancel_pending_queue raw-SQL UPDATE-on-message_queue convention)
    - app/routers/campaigns.py:228-272 (_campaign_to_response)
    - 08-RESEARCH.md §"Detach Guards" (min-pool D-03 + cold-pending D-04 SQL + D-05 engaged-exclusion rationale) and §"Code Examples" (detach skeleton)
    - 08-PATTERNS.md §"detach_sender"
  </read_first>
  <action>
    Add `@router.delete("/{campaign_id}/senders/{sender_id}", response_model=CampaignResponse)` `async def detach_sender(campaign_id: UUID, sender_id: UUID, ctx=Depends(auth_dep), db=Depends(get_db))`, same auth dependency. Steps:
    1. `c = await _load_campaign(db, ctx, campaign_id)`.
    2. Min-pool guard (D-03): `cnt = select(sql_func.count()).select_from(CampaignSender).where(CampaignSender.campaign_id==c.id)`; if `c.status == "running" and cnt <= 1` → `raise HTTPException(409, detail={"code":"MIN_POOL_GUARD","message":"Cannot detach the last sender of a running campaign. Pause it first."})`. draft/paused may go to 0 (do NOT guard those).
    3. Cold-pending guard (D-04/D-05): run the EXISTS query from the interfaces block via raw `text()` with params `{"cid": str(c.id), "sid": str(sender_id)}`; if true → `raise HTTPException(409, detail={"code":"DETACH_BLOCKED_PENDING","message":"This sender still has un-sent contacts in the campaign. Pause the campaign or wait for the queue to drain, then detach."})`. The `NOT EXISTS conversations` clause is what excludes engaged dialogs so they do NOT block detach (D-05/POOL-06b).
    4. `await db.execute(delete(CampaignSender).where(CampaignSender.campaign_id==c.id, CampaignSender.sender_id==sender_id))`.
    5. `await db.commit(); await db.refresh(c); return await _campaign_to_response(db, ctx, c)`.
    Add `delete` to the sqlalchemy import if not already present. Do NOT touch conversations rows or active dialogs (D-05/D-06 — no auto-reassign of cold backlog in Phase 8).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py::test_detach_removes_sender tests/test_pool_endpoints.py::test_detach_last_running_409 tests/test_pool_endpoints.py::test_detach_cold_pending_409 tests/test_pool_endpoints.py::test_detach_engaged_only_ok -x</automated>
  </verify>
  <acceptance_criteria>
    - DELETE /campaigns/{id}/senders/{sid} exists, response_model=CampaignResponse, same auth dependency.
    - Min-pool guard only fires when `status == "running"` (draft/paused can reach 0 senders).
    - Cold-pending guard uses the `NOT EXISTS sent` + `NOT EXISTS conversations` predicate keyed on recipient_phone (grep both NOT EXISTS clauses).
    - Detach does NOT auto-reassign or move any queue rows (grep: detach_sender body contains no UPDATE message_queue / no rebalance call).
    - test_detach_removes_sender, test_detach_last_running_409, test_detach_cold_pending_409, test_detach_engaged_only_ok all GREEN.
  </acceptance_criteria>
  <done>Detach endpoint green for POOL-04/05/06/06b; guards correct; engaged dialogs untouched.</done>
</task>

</tasks>

<threat_model>
ASVS L1 surface for the new endpoints (security enforcement ON, block on HIGH):
- **T1 — IDOR on attach/detach: a workspace attaches/detaches another workspace's sender (V4 Access Control).** Mitigation: attach calls `_validate_workspace_owns_senders` (404 SENDER_NOT_FOUND, no leak) and `CampaignSender.workspace_id = ctx.workspace_id`; both endpoints start with `_load_campaign(db, ctx, ...)` which scopes the campaign to the workspace (404 if not owned). Verified by test_attach_foreign_sender_404 (POOL-03). Executor MUST NOT add a code path that loads a campaign or sender without the ctx/workspace scope.
- **T2 — Cross-campaign sender hijack: attaching a sender locked to another running campaign (V1 business logic / integrity).** Mitigation: `_check_sender_lock` after insert → 409 SENDER_LOCK_CONFLICT (same contract as /start, prevents one account driving two running campaigns). Verified by test_attach_locked_sender_409 (POOL-02).
- **T3 — Missing/weak auth on the new endpoints (V2 Auth).** Mitigation: both endpoints use the identical `Depends(auth_dep)` as every other campaigns.py endpoint — no anonymous access, JWT verified upstream. Acceptance criteria require the Depends to match start_campaign's.
- **T4 — Detach causing a stuck running campaign or stranded cold backlog (availability / data integrity).** Mitigation: MIN_POOL_GUARD (no zero-sender running campaign) and DETACH_BLOCKED_PENDING (no orphaned cold pending — Phase 8 guards, Phase 9 will auto-reassign). Verified by POOL-05/POOL-06.
- **T5 — Insert-then-rollback leaving a partial campaign_senders row on lock conflict (integrity).** Mitigation: the conflict path calls `await db.rollback()` before raising 409, so no orphan row persists; commit happens only on the success path.
All threats map to POOL tests in tests/test_pool_endpoints.py.
</threat_model>

<verification>
- `pytest tests/test_pool_endpoints.py -x` (overlay) → all 7 green.
- Full-suite wave-merge sampling before done: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -x` (must catch no regressions in test_sender_lock.py / test_campaign_router.py / test_rotation_campaign.py).
- Deploy note for executor: after merge, `docker compose up -d --build api` (restart does NOT pick up code changes) — but only as a smoke step, not part of automated verify.
</verification>

<success_criteria>
- Two endpoints live, reusing _load_campaign / _validate_workspace_owns_senders / _check_sender_lock / _campaign_to_response verbatim.
- Lock 409 byte-identical to /start; MIN_POOL_GUARD + DETACH_BLOCKED_PENDING correct; engaged dialogs do not block detach.
- rebalance fired only on running attach; no migration added; no auto-reassign on detach.
- POOL-01..06b green.
</success_criteria>

<output>
After completion, create `.planning/phases/08-pool-management-and-even-distribution/08-03-SUMMARY.md`
</output>
