---
phase: quick-260709-hgn
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/routers/conversations.py
  - tests/test_phase5_inbox_manager_mode.py
  - frontend/src/routes/_authenticated/inbox.tsx
autonomous: true
requirements: [INBX-UI-LEAD, INBX-UI-SENDER-PHONE]
must_haves:
  truths:
    - "Sender-filter dropdown on Inbox shows each sender's name + phone (falls back to @slug when phone empty), not the sender id"
    - "A 'Lead' button in the conversation detail pane sets conversation.status='lead' and fires the campaign lead webhook (mirrors the auto-lead flow)"
    - "Marking a lead does NOT change ai_enabled (lead is a marker, the conversation continues)"
    - "The Lead button shows an active/disabled state when status is already 'lead'"
  artifacts:
    - path: "app/routers/conversations.py"
      provides: "POST /api/v1/conversations/{id}/mark-lead endpoint (workspace-scoped, status='lead' + notify_signal lead webhook)"
      contains: "mark-lead"
    - path: "frontend/src/routes/_authenticated/inbox.tsx"
      provides: "Sender dropdown name+phone label + Lead button/mutation"
  key_links:
    - from: "frontend/src/routes/_authenticated/inbox.tsx"
      to: "/api/v1/conversations/{id}/mark-lead"
      via: "useMutation api() POST"
      pattern: "mark-lead"
    - from: "app/routers/conversations.py"
      to: "notify_signal"
      via: "fire-and-forget after commit"
      pattern: "notify_signal\\("
---

<objective>
Two Inbox UI improvements + the backend endpoint one of them needs:

1. Sender-filter dropdown shows the sender's **name + phone** instead of `name (@slug)`.
2. A **"Lead"** button in the conversation detail pane marks the contact as a lead
   directly from the UI — mirroring the automatic auto-lead flow (status='lead'
   AND fire the campaign lead webhook). The existing `PATCH /{id}` only sets
   status and does NOT fire the webhook, so a NEW dedicated backend endpoint is
   required.

Purpose: Give managers a one-click manual "this is a lead" action that behaves
exactly like the AI's `mark_as_lead` signal (downstream n8n/webhook consumers
must see the same event), and make the sender filter human-readable.

Output: `POST /api/v1/conversations/{id}/mark-lead` endpoint + one targeted test;
frontend dropdown label change + Lead button with mutation.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@/root/apps/aimly/tg-outreach/CLAUDE.md
@/root/CLAUDE.md

Backend:
@app/routers/conversations.py
@app/services/ai_engine.py
@app/services/webhook_notify.py

Frontend:
@frontend/src/routes/_authenticated/inbox.tsx

<interfaces>
<!-- Canonical auto-lead flow (app/services/ai_engine.py:_handle_builtin_signal, mark_as_lead branch) -->
<!-- UPDATE conversations SET status='lead', updated_at=NOW() WHERE id=:cid;  await db.commit(); -->
<!-- then: await notify_signal(event_type="lead", campaign=..., conversation_id=..., contact=..., reason=..., db=...) -->
<!-- ai_enabled is intentionally NOT touched — lead is a marker, conversation continues. -->

<!-- notify_signal signature (app/services/webhook_notify.py) -->
async def notify_signal(*, event_type: str, campaign: dict, conversation_id: UUID,
                        contact: dict, reason: str, db: AsyncSession) -> None
<!-- campaign dict needs: id, name, workspace_id, lead_webhook_url, webhook_url
     (falls back webhook_url when lead_webhook_url is None; if BOTH None → logs + returns, no HTTP call).
     contact dict needs: phone, telegram_id, name(full_name|name), username, source, custom. -->

<!-- Existing workspace-scope helper (already in conversations.py) -->
async def _load_conversation_or_404(db, ctx, conversation_id) -> dict   # raises 404 cross-workspace
async def get_conversation(conversation_id, ctx, db) -> ConversationResponse  # reuse to build response

<!-- Conversation model has NO contact_id FK; link to contacts is by (workspace_id, phone).
     conversations: contact_phone, contact_name, contact_telegram_id, campaign_id, status, ai_enabled
     contacts:      phone, username, full_name, source, custom (JSONB)  — LEFT JOIN on workspace_id+phone
     campaigns:     id, name, workspace_id, lead_webhook_url, webhook_url -->

<!-- Frontend: SenderResponse has `.phone` (used already at inbox.tsx:2569 `sender?.phone`);
     Conversation type = components["schemas"]["ConversationResponse"] (has `.status`, `.ai_enabled`).
     Existing mutation pattern: disableAiMut (inbox.tsx:1238) — api<Conversation>(url,{method:"POST"})
     + onSuccess invalidate ["conversation", conversationId] and ["conversations"]. -->
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Backend POST /{id}/mark-lead endpoint + test</name>
  <files>app/routers/conversations.py, tests/test_phase5_inbox_manager_mode.py</files>
  <behavior>
    - POST /api/v1/conversations/{id}/mark-lead on an active conversation → 200,
      body.status == "lead", body.ai_enabled unchanged (still true).
    - Campaign with no webhook URLs (lead_webhook_url NULL and webhook_url NULL) →
      notify_signal short-circuits (no HTTP), endpoint still returns 200 — this is
      what the test asserts (no httpx mock needed).
    - Cross-workspace / unknown id → 404 CONVERSATION_NOT_FOUND (via _load_conversation_or_404).
  </behavior>
  <action>
    Add a new endpoint in app/routers/conversations.py near the other mutating
    endpoints (after `update_conversation` / `disable_ai`, ~L801+). Mirror the
    canonical auto-lead flow in ai_engine.py `_handle_builtin_signal` (mark_as_lead):

    ```python
    @router.post("/{conversation_id}/mark-lead", response_model=ConversationResponse)
    async def mark_lead(
        conversation_id: UUID,
        ctx: AuthCtx = Depends(auth_dep),
        db: AsyncSession = Depends(get_db),
    ) -> ConversationResponse:
        """Manual 'mark as lead' from the inbox UI.

        Mirrors ai_engine._handle_builtin_signal(mark_as_lead): set status='lead'
        (ai_enabled UNCHANGED — lead is a marker, conversation continues) and fire
        the campaign lead webhook (fire-and-forget). 404 if not in this workspace.
        """
        await _load_conversation_or_404(db, ctx, conversation_id)

        # UPDATE status only — never touch ai_enabled (matches auto-lead).
        await db.execute(text("""
            UPDATE conversations SET status='lead', updated_at=NOW()
            WHERE id = :cid AND workspace_id = :wid
        """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
        await db.commit()

        # Lean SELECT of just the webhook + contact fields notify_signal needs.
        # No contact_id FK: LEFT JOIN contacts on (workspace_id, phone).
        row = (await db.execute(text("""
            SELECT c.campaign_id,
                   camp.id AS camp_id, camp.name AS camp_name,
                   camp.workspace_id AS camp_wid,
                   camp.lead_webhook_url, camp.webhook_url,
                   c.contact_phone, c.contact_telegram_id, c.contact_name,
                   ct.full_name AS ct_full_name, ct.username AS ct_username,
                   ct.source AS ct_source, ct.custom AS ct_custom
            FROM conversations c
            LEFT JOIN campaigns camp ON camp.id = c.campaign_id
            LEFT JOIN contacts ct
                ON ct.workspace_id = c.workspace_id AND ct.phone = c.contact_phone
            WHERE c.id = :cid AND c.workspace_id = :wid
        """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()

        campaign = {}
        contact = {}
        if row is not None:
            if row.camp_id is not None:
                campaign = {
                    "id": row.camp_id,
                    "name": row.camp_name,
                    "workspace_id": row.camp_wid,
                    "lead_webhook_url": row.lead_webhook_url,
                    "webhook_url": row.webhook_url,
                }
            contact = {
                "phone": row.contact_phone,
                "telegram_id": row.contact_telegram_id,
                "full_name": row.ct_full_name or row.contact_name,
                "username": row.ct_username,
                "source": row.ct_source,
                "custom": row.ct_custom or {},
            }

        # Fire-and-forget AFTER commit (never await webhook inside a txn).
        # notify_signal itself no-ops when both URLs are None.
        await notify_signal(
            event_type="lead",
            campaign=campaign,
            conversation_id=conversation_id,
            contact=contact,
            reason="Marked as lead manually via UI",
            db=db,
        )

        return await get_conversation(conversation_id, ctx, db)
    ```

    Add the import at the top of conversations.py (it is NOT imported yet — only
    ai_engine imports it):
    `from app.services.webhook_notify import notify_signal`

    No migration: status='lead' is already in the conversations.status CHECK
    (app/models/__init__.py:358). Do NOT add a migration.

    Then add a test in tests/test_phase5_inbox_manager_mode.py mirroring
    `test_disable_ai_cancels_pending_queue` (same fixtures/_bind/_auth_headers):
    create a campaign (default factory — no webhook URLs), a sender, and an active
    conversation; POST /mark-lead; assert 200, body["status"]=="lead", and
    body["ai_enabled"] is True (unchanged). No httpx mock is needed because the
    factory campaign has no webhook URL so notify_signal returns before any HTTP.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_inbox_manager_mode.py -k "mark_lead" -x -q</automated>
  </verify>
  <done>New POST /{id}/mark-lead returns 200 with status='lead' and ai_enabled unchanged; notify_signal fired (or no-op'd) after commit; workspace-scoped 404; targeted test passes.</done>
</task>

<task type="auto">
  <name>Task 2: Frontend — sender dropdown name+phone label + Lead button</name>
  <files>frontend/src/routes/_authenticated/inbox.tsx</files>
  <action>
    Two edits in the same file:

    (A) Sender-filter dropdown label (~L719-721). Change the visible option label
    from `{s.name} (@{s.slug})` to name + phone, with a fallback to @slug when
    phone is empty. The `<option value>` stays `s.id` (unchanged):

    ```tsx
    {senders.map((s) => (
      <option key={s.id} value={s.id}>
        {s.phone ? `${s.name} (${s.phone})` : `${s.name} (@${s.slug})`}
      </option>
    ))}
    ```

    (B) Lead button in the ConversationDetail header (~L1452-1472, beside the
    Take over / Hand back to AI buttons). Add a mutation next to `disableAiMut`
    (~L1238), following the same pattern:

    ```tsx
    const markLeadMut = useMutation({
      mutationFn: () =>
        api<Conversation>(`/api/v1/conversations/${conversationId}/mark-lead`, {
          method: "POST",
        }),
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
        void qc.invalidateQueries({ queryKey: ["conversations"] });
      },
      onError: (e) => toast.error(errMsg(e)),
    });
    ```

    Then render a "Lead" button in the header button group (near the Take over /
    Hand back to AI buttons, ~L1452). Match existing `btn btn--sm` styling and use
    the `Flag` icon (already imported — used in the lead banner at L1512). Disable
    it (and show active state) when the conversation is already status='lead':

    ```tsx
    {conv && (
      <button
        type="button"
        className="btn btn--sm"
        style={
          conv.status === "lead"
            ? { background: "var(--success, #4dcd5e)", color: "white" }
            : undefined
        }
        onClick={() => markLeadMut.mutate()}
        disabled={markLeadMut.isPending || conv.status === "lead"}
        title={conv.status === "lead" ? "Already marked as lead" : "Mark as lead"}
      >
        <Flag size={14} /> {conv.status === "lead" ? "Lead" : "Mark lead"}
      </button>
    )}
    ```

    Place it before the Take over / Hand back to AI conditional so ordering is
    Lead → AI-toggle → details. Keep `toast` / `errMsg` usage consistent with the
    file (both already imported).
  </action>
  <verify>
    <automated>cd frontend && bun run build 2>&1 | tail -20</automated>
  </verify>
  <done>Dropdown shows "Name (phone)" (or "Name (@slug)" fallback); Lead button appears in the detail header, calls /mark-lead, invalidates queries on success, and is disabled+active-styled when status==='lead'; frontend build passes with no type errors.</done>
</task>

</tasks>

<verification>
- Backend: `POST /api/v1/conversations/{id}/mark-lead` returns 200 with
  `status='lead'`, `ai_enabled` unchanged; targeted pytest green.
- Frontend: `bun run build` compiles clean; dropdown label = name + phone
  (fallback @slug); Lead button present, wired, and status-aware.
</verification>

<success_criteria>
- New workspace-scoped mark-lead endpoint mirrors the auto-lead flow (status +
  webhook, ai_enabled untouched) and is reachable at same-origin /api/v1/...
- Sender filter is human-readable (name + phone).
- Manager can mark a lead in one click; the status pill / lead banner update.
- No migration added; queue/rate-limit code untouched.
</success_criteria>

<output>
After completion, create `.planning/quick/260709-hgn-inbox-sender-phone-lead-button/260709-hgn-SUMMARY.md`
</output>
