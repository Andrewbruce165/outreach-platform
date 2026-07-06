---
phase: quick-260706-fcq
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/schemas/__init__.py
  - app/routers/campaigns.py
  - app/routers/senders.py
  - tests/test_pool_preflight.py
  - lovable-handoff/openapi.json
  - lovable-handoff/error-codes.md
autonomous: true
requirements: [PFH-01, PFH-02, PFH-03]

must_haves:
  truths:
    - "Attaching a sender that has a restriction event in the last 7 days returns 200 with a non-empty attach_warnings[] carrying code RECENT_RESTRICTION"
    - "Attaching a role='checker' sender WITHOUT force returns 409 CHECKER_ROLE_CONFLICT with a clear message"
    - "Attaching a role='checker' sender WITH force=true returns 200 and carries a CHECKER_FORCE_ATTACHED warning"
    - "Attaching a clean role='sender' with no recent restriction returns 200 with an empty attach_warnings[]"
    - "PATCH /senders/{slug} flipping role to 'checker' while the sender is in a running campaign, without force, returns 409 CHECKER_ROLE_CONFLICT"
  artifacts:
    - path: "app/schemas/__init__.py"
      provides: "SenderAttachWarning model, attach_warnings field on CampaignResponse, force field on CampaignSenderAttachRequest and SenderUpdate"
      contains: "class SenderAttachWarning"
    - path: "app/routers/campaigns.py"
      provides: "Pre-flight restriction warning + checker force-guard in attach_sender"
      contains: "CHECKER_ROLE_CONFLICT"
    - path: "app/routers/senders.py"
      provides: "Reverse-direction role->checker force-guard in update_sender"
      contains: "CHECKER_ROLE_CONFLICT"
    - path: "tests/test_pool_preflight.py"
      provides: "Pre-flight attach + reverse-guard test coverage"
      contains: "test_"
  key_links:
    - from: "app/routers/campaigns.py::attach_sender"
      to: "sender_restriction_events"
      via: "SELECT last 7 days for the attached sender"
      pattern: "sender_restriction_events"
    - from: "app/routers/campaigns.py::attach_sender"
      to: "senders.role"
      via: "checker-role detection on the loaded sender"
      pattern: "role.*checker"
    - from: "app/routers/senders.py::update_sender"
      to: "campaign_senders / campaigns"
      via: "running-campaign membership check when role flips to checker"
      pattern: "status = 'running'"
---

<objective>
Add a pre-flight health check when a sender is attached to a campaign pool, and separate the checker/sender roles so a contact-checking account is not silently burned as a campaign sender.

Purpose: A campaign was launched on ca-account-1 — the ONLY working checker (already flagged with an antispam signal on 30.06). A PEER_FLOOD on its 2nd outreach message removed it from BOTH sending AND contact-checking (restriction-gated selection excludes restricted checkers). The attach path had zero safety net: no restriction-history warning, no role guard. This plan closes that gap.

Output:
- attach_sender returns advisory warnings (attach_warnings[]) when the incoming sender has a restriction event in the last 7 days ("зелёный коридор").
- Attaching a checker-pool account (role='checker') as a campaign sender requires explicit force=true, else 409 CHECKER_ROLE_CONFLICT.
- The reverse direction — flipping an in-running-campaign sender to role='checker' via PATCH /senders/{slug} — is guarded the same way (force=true).
- No new schema/migration (role + sender_restriction_events already exist).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md

<interfaces>
<!-- Extracted from the codebase. Executor should use these directly — no exploration needed. -->

Checker role is a SINGLE column on senders (no separate flag/table):
  app/models/__init__.py:92
    role = Column(String(20), nullable=False, server_default='sender')  # 'sender' or 'checker'
  The contact-check worker's pool-selection (app/services/contact_check_worker.py) filters
  `role = 'checker' AND restriction_status = 'none' AND lifecycle_status <> 'paused'`.
  => "active checker pool membership" == role == 'checker'. No migration needed to detect it.

Restriction history lives in sender_restriction_events (Phase 10, populated since 2026-06-24):
  columns: workspace_id, sender_id, category, event_type, source, restricted_until, created_at
  event_type ∈ {spam_limited, frozen, cleared, extension, recipient_privacy, blocked, flood_wait}
  NOTE: 'cleared' is a RECOVERY event, not a restriction — exclude it from the warning.
  Established query pattern (app/routers/senders.py::get_block_rate):
    WHERE e.sender_id = :sid AND e.workspace_id = :wid AND e.created_at > now() - interval '7 days'

attach_sender endpoint (app/routers/campaigns.py:1081), returns CampaignResponse:
  @router.post("/{campaign_id}/senders", response_model=CampaignResponse)
  async def attach_sender(campaign_id, payload: CampaignSenderAttachRequest, ctx, db)
  - _load_campaign (workspace-scoped 404)
  - _validate_workspace_owns_senders(db, ctx, [payload.sender_id])  # 404 SENDER_NOT_FOUND
  - idempotency: PK (campaign_id, sender_id) → no-op if already attached (returns _campaign_to_response)
  - insert CampaignSender → flush → _check_sender_lock (409 SENDER_LOCK_CONFLICT) → rollback on conflict
  - if c.status == "running": rebalance_on_attach(...)
  - commit; return await _campaign_to_response(db, ctx, c)

CampaignResponse is built by _campaign_to_response(db, ctx, campaign) (campaigns.py:317) and
returned by MANY endpoints (get/patch/start/pause/attach/detach). Adding an OPTIONAL
`attach_warnings: List[...] = []` field is backward-compatible — only attach_sender populates it.

CampaignSenderAttachRequest (app/schemas/__init__.py:715) — thin body, currently:
    sender_id: UUID

update_sender (app/routers/senders.py:615) applies role change at line 647-648:
    if request.role is not None:
        sender.role = request.role
  SenderUpdate (app/schemas/__init__.py:107) already has `role: Optional[Literal["sender","checker"]]`.
  Existing helper _check_sender_not_in_running_campaign(db, ctx, sender_id) (senders.py:382) runs the
  running-campaign SELECT and raises 409 SENDER_USED_BY_RUNNING_CAMPAIGN. Its inner query is the model
  for the reverse-guard EXISTS check (do NOT reuse the raise — we need a distinct code + force bypass).

WarningItem (schemas/__init__.py:82) is int-shaped (field/value/recommended_max) — NOT reusable here.
Create a new SenderAttachWarning model instead.

Existing live @SpamBot check: GET /senders/{slug}/spambot-check (senders.py:830) + telegram_service.check_spambot.
  OUT OF SCOPE for this plan (do NOT invoke @SpamBot inline in the synchronous attach path — FloodWait/latency risk).
  It stays the manual "зелёный коридор" verification the UI can call on demand; live inline pre-flight is a follow-up.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Schemas + attach-sender pre-flight (restriction warning + checker force-guard)</name>
  <files>app/schemas/__init__.py, app/routers/campaigns.py, tests/test_pool_preflight.py</files>
  <behavior>
    New test file tests/test_pool_preflight.py (mirror tests/test_pool_endpoints.py setup:
    async_client, valid_supabase_jwt, async_db_session, test_workspace, test_campaign_factory,
    test_sender_factory, the _bind() helper + distinct per-test JWT sub). Assert:
    - test_attach_clean_sender_no_warnings: attach a fresh role='sender' → 200,
      response JSON attach_warnings == [].
    - test_attach_recent_restriction_warns: insert a sender_restriction_events row
      (event_type='spam_limited', category='restriction', created_at=now()) for the sender,
      attach → 200, attach_warnings has one item with code == "RECENT_RESTRICTION"
      and event_type == "spam_limited"; campaign_senders row IS created (warning, not block).
    - test_attach_old_restriction_no_warn: restriction event created_at = now() - 10 days →
      attach → 200, attach_warnings == [] (7-day window).
    - test_attach_cleared_event_no_warn: only a 'cleared' event in-window → attach_warnings == []
      ('cleared' is recovery, excluded).
    - test_attach_checker_without_force_409: attach a role='checker' sender (no force) →
      409, detail.code == "CHECKER_ROLE_CONFLICT"; NO campaign_senders row created.
    - test_attach_checker_with_force_ok: same but body {"sender_id":..., "force":true} → 200,
      campaign_senders row created, attach_warnings contains an item code == "CHECKER_FORCE_ATTACHED".
  </behavior>
  <action>
    Implements PFH-01 (recent-restriction warning) + PFH-02 (checker force-guard).

    1. app/schemas/__init__.py:
       - Add `class SenderAttachWarning(BaseModel)` with fields:
           code: str          # "RECENT_RESTRICTION" | "CHECKER_FORCE_ATTACHED"
           sender_id: UUID
           message: str
           event_type: Optional[str] = None
           restricted_until: Optional[datetime] = None
           last_event_at: Optional[datetime] = None
       - Add to CampaignResponse (near attached_senders):
           attach_warnings: List[SenderAttachWarning] = Field(default_factory=list)
         (Optional, default empty → backward-compatible for every other endpoint.)
       - Add to CampaignSenderAttachRequest:
           force: bool = False
       - Add to SenderUpdate (used by Task 2):
           force: bool = False

    2. app/routers/campaigns.py — new module-level helper:
         async def _recent_restriction_warnings(db, ctx, sender_id) -> List[SenderAttachWarning]:
           SELECT event_type, restricted_until, created_at
             FROM sender_restriction_events
            WHERE sender_id = :sid AND workspace_id = :wid
              AND event_type <> 'cleared'
              AND created_at > now() - interval '7 days'
            ORDER BY created_at DESC LIMIT 1
           → [] if no row, else one SenderAttachWarning(code="RECENT_RESTRICTION",
             message="Account had a restriction event (<event_type>) in the last 7 days —
             attaching it may re-trigger anti-spam. Verify via @SpamBot before sending.",
             event_type=..., restricted_until=..., last_event_at=created_at).
         Use text() + bind params (str(ctx.workspace_id), str(sender_id)) per the get_block_rate pattern.

    3. app/routers/campaigns.py::attach_sender — pre-flight, added AFTER
       _validate_workspace_owns_senders and BEFORE the idempotency SELECT:
       - Load the Sender row (SELECT senders WHERE id=payload.sender_id AND workspace_id=ctx.workspace_id;
         it is already workspace-validated, so a simple fetch of role is enough).
       - if sender.role == 'checker' and not payload.force:
           raise HTTPException(409, detail={"code":"CHECKER_ROLE_CONFLICT",
             "message":"This account is in the checker pool (role='checker'); attaching it as a "
             "campaign sender will consume it for contact-checking and can PEER_FLOOD it out of both "
             "roles. Pass force=true to override.", "sender_id": str(payload.sender_id)})
       - Keep the existing idempotency short-circuit as-is (an already-attached sender returns the
         campaign with attach_warnings defaulting to []).
       - After the successful commit, build warnings:
           warnings = await _recent_restriction_warnings(db, ctx, payload.sender_id)
           if sender.role == 'checker':  # reached only with force=true
               warnings.append(SenderAttachWarning(code="CHECKER_FORCE_ATTACHED",
                 sender_id=payload.sender_id,
                 message="Checker account force-attached as a campaign sender — it will be pulled "
                 "out of the contact-check pool once it sends."))
           resp = await _campaign_to_response(db, ctx, c)
           resp.attach_warnings = warnings
           return resp

    Do NOT invoke @SpamBot inline (out-of-scope per plan header). Do NOT touch empirical
    queue intervals or rate limits (CLAUDE.md guard). No migration.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_preflight.py -x</automated>
  </verify>
  <done>
    All test_pool_preflight.py tests for attach GREEN: clean attach → empty attach_warnings;
    in-window non-cleared restriction → RECENT_RESTRICTION warning (still attaches);
    old/cleared events → no warning; checker without force → 409 CHECKER_ROLE_CONFLICT (no row);
    checker with force=true → 200 + CHECKER_FORCE_ATTACHED warning.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Reverse-direction guard — role->checker on an in-running-campaign sender requires force</name>
  <files>app/routers/senders.py, tests/test_pool_preflight.py</files>
  <behavior>
    Extend tests/test_pool_preflight.py:
    - test_flip_to_checker_in_running_campaign_without_force_409: create a running campaign,
      attach a role='sender' sender to it, then PATCH /senders/{slug} with {"role":"checker"} (no force)
      → 409, detail.code == "CHECKER_ROLE_CONFLICT"; sender.role in DB is still 'sender'.
    - test_flip_to_checker_in_running_campaign_with_force_ok: same PATCH with {"role":"checker","force":true}
      → 200; sender.role in DB is now 'checker'.
    - test_flip_to_checker_not_in_running_campaign_ok: a sender NOT attached to any running campaign,
      PATCH {"role":"checker"} (no force) → 200 (no force needed when idle).
    (Use the same _bind + distinct JWT sub convention. A running campaign needs a folder + template;
    reuse test_campaign_factory(status="running") the way test_pool_endpoints.py does, or set
    status='running' on a factory campaign and attach the sender via the attach endpoint / direct insert.)
  </behavior>
  <action>
    Implements PFH-03 (symmetric guard on the explicit role-assignment path — PATCH /senders/{slug}).

    In app/routers/senders.py::update_sender, BEFORE the existing `if request.role is not None:` block
    (line ~647), add a guard for the sender-becoming-checker transition:

      if request.role == 'checker' and sender.role != 'checker' and not request.force:
          in_running = (await db.execute(text("""
              SELECT EXISTS (
                SELECT 1 FROM campaign_senders cs
                JOIN campaigns c ON c.id = cs.campaign_id
                WHERE cs.sender_id = :sid
                  AND c.workspace_id = :wid
                  AND c.status = 'running'
              )
          """), {"sid": str(sender.id), "wid": str(ctx.workspace_id)})).scalar()
          if in_running:
              raise HTTPException(status_code=409, detail={
                  "code": "CHECKER_ROLE_CONFLICT",
                  "message": "Sender is attached to a running campaign — flipping it to the checker "
                             "pool would pull it out of sending. Pause/finish the campaign or pass "
                             "force=true to override.",
                  "sender_id": str(sender.id),
              })

    Rationale for the inline EXISTS (not reusing _check_sender_not_in_running_campaign): that helper
    raises a DIFFERENT code (SENDER_USED_BY_RUNNING_CAMPAIGN) and has no force bypass; we need the
    CHECKER_ROLE_CONFLICT code + force=true escape hatch and NO guard when the sender is idle.
    Leave the rest of update_sender untouched; role is still applied by the existing setter below.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_preflight.py -x</automated>
  </verify>
  <done>
    Reverse-guard tests GREEN: role->checker while in a running campaign without force → 409
    CHECKER_ROLE_CONFLICT (role unchanged); with force=true → 200 (role flips); idle sender → 200
    without force. Full test_pool_preflight.py suite GREEN.
  </done>
</task>

<task type="auto">
  <name>Task 3: Regenerate openapi handoff + document CHECKER_ROLE_CONFLICT for the frontend</name>
  <files>lovable-handoff/openapi.json, lovable-handoff/error-codes.md</files>
  <action>
    Wire the new contract for the (cross-repo) Lovable frontend so it can render the "зелёный коридор"
    warning and the force=true confirmation. This task is the ONLY consumer-facing wiring in this plan;
    building the actual React UI lives in the sibling aimly-tg-outreach repo and is an explicit follow-up.

    1. Regenerate the OpenAPI handoff so `attach_warnings`, the `force` field on both request bodies,
       and the SenderAttachWarning schema appear:
         bash scripts/export-handoff.sh
       (This is the established offline app.openapi() export — see prior phases' export-handoff usage.
       If the script rebuilds/needs the api image, follow its own prompts; it must NOT deploy prod.)
       Confirm lovable-handoff/openapi.json now contains "attach_warnings" and "SenderAttachWarning".

    2. Append a row to lovable-handoff/error-codes.md documenting:
         CHECKER_ROLE_CONFLICT (409) — sender is a checker (attach) or is being flipped to checker
         while in a running campaign (PATCH /senders); resolve by pausing the campaign or resending the
         request with force=true. Also note the advisory RECENT_RESTRICTION / CHECKER_FORCE_ATTACHED
         entries returned in CampaignResponse.attach_warnings[] (non-blocking, show as amber banner).

    Do NOT hand-edit openapi.json beyond what the exporter produces. Frontend rendering is out of scope
    (sibling repo). Inline @SpamBot pre-flight also remains a documented follow-up.
  </action>
  <verify>
    <automated>grep -q "attach_warnings" lovable-handoff/openapi.json && grep -q "SenderAttachWarning" lovable-handoff/openapi.json && grep -q "CHECKER_ROLE_CONFLICT" lovable-handoff/error-codes.md && echo OK</automated>
  </verify>
  <done>
    lovable-handoff/openapi.json exposes attach_warnings + SenderAttachWarning + the force fields;
    error-codes.md documents CHECKER_ROLE_CONFLICT + the two advisory warning codes.
  </done>
</task>

</tasks>

<verification>
- Full targeted suite: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_preflight.py` → all GREEN.
- Regression: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py tests/test_pool_health.py` → still GREEN (attach_warnings default [] must not break existing attach/detach contract).
- No migration added (ls migrations/ shows highest still 049); role + sender_restriction_events reused.
- Empirical rate-limit/queue intervals untouched (CLAUDE.md guard).
</verification>

<success_criteria>
- Attaching a sender with a non-cleared restriction event in the last 7 days returns 200 with attach_warnings=[{code:"RECENT_RESTRICTION", ...}] (warning, NOT a block) — matches acceptance "returns a warning in the API response".
- Attaching a role='checker' account without force returns 409 CHECKER_ROLE_CONFLICT; with force=true it succeeds and carries CHECKER_FORCE_ATTACHED — matches acceptance "attach of an active checker requires explicit force=true".
- Flipping a running-campaign sender to role='checker' without force returns 409 CHECKER_ROLE_CONFLICT (symmetric direction on the explicit role-assignment path).
- @SpamBot live pre-flight NOT invoked inline; existing GET /senders/{slug}/spambot-check remains the manual verification, inline pre-flight documented as follow-up.
- OpenAPI handoff + error-codes.md updated so the frontend can build the amber warning + force confirmation.
</success_criteria>

<output>
After completion, create `.planning/quick/260706-fcq-pre-flight-health-check-on-sender-attach/260706-fcq-SUMMARY.md`
</output>
