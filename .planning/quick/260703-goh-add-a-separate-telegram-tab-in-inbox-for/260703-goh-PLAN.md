---
phase: quick-260703-goh
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/046_telegram_service_status.sql
  - app/schemas/__init__.py
  - app/services/listener.py
  - app/routers/conversations.py
  - tests/test_phase5_inbox.py
  - lovable-handoff/openapi.json
autonomous: true
requirements: [TGTAB-01, TGTAB-02]
must_haves:
  truths:
    - "Auth-code messages from Telegram's service account (id 777000 / +42777) are persisted to conversations + messages instead of being dropped"
    - "These conversations carry a distinct status='telegram_service' so the inbox can render a separate 'Telegram' tab"
    - "The default inbox list hides status='telegram_service' (like bot_ignored); GET /api/v1/conversations?status=telegram_service returns exactly those rows"
    - "The AI answerer never fires on Telegram service messages"
  artifacts:
    - path: "migrations/046_telegram_service_status.sql"
      provides: "Extends conversations_status_check to allow 'telegram_service'"
      contains: "telegram_service"
    - path: "app/services/listener.py"
      provides: "Persistence of 777000 service messages as status='telegram_service'"
    - path: "app/routers/conversations.py"
      provides: "Default-hide of telegram_service + explicit filter"
  key_links:
    - from: "app/services/listener.py"
      to: "conversations table (status='telegram_service')"
      via: "_handle_telegram_service_message INSERT/UPDATE"
      pattern: "telegram_service"
    - from: "app/routers/conversations.py list_conversations"
      to: "?status=telegram_service"
      via: "WHERE c.status filter"
      pattern: "telegram_service"
---

<objective>
Currently the listener explicitly DROPS every message from Telegram's official service account (`sender.id == 777000` / phone `+42777`) at `app/services/listener.py:736-740` with an early `return` — BEFORE anything is written to the `conversations`/`messages` tables. That is exactly why "we can't even receive these messages in the UI right now": the login/auth-code notifications never reach the database, so no UI surface can show them.

This plan makes the backend PERSIST and CLASSIFY those service messages under a distinct conversation `status='telegram_service'`, and exposes them through the existing inbox API (`GET /api/v1/conversations?status=telegram_service`) while keeping them out of the default inbox list. This is the minimal, tab-shaped data-model concept — the inbox already builds its tabs by filtering `status`, so a new status value is all the frontend needs to render a "Telegram" tab.

Purpose: let managers see Telegram login/auth codes inside the product UI (the account 42777 sends them), without polluting the normal contact inbox and without triggering the AI answerer.
Output: migration 046, a new listener handler, an extended status vocabulary, and an inbox filter — plus a documented API contract for the frontend team to build the actual tab.

Scope: BACKEND ONLY (`/root/apps/aimly/tg-outreach`). The frontend repo (`/root/apps/aimly/aimly-tg-outreach`) is a separate git repo generated via Lovable and is OUT OF SCOPE — this plan only exposes the contract; the SUMMARY documents what the frontend must do.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md

<interfaces>
<!-- Extracted from codebase — executor should use these directly, no exploration needed. -->

CURRENT ROOT CAUSE — app/services/listener.py (handle_incoming_message), lines 736-740:
```python
# Пропускаем системные сообщения Telegram (+42777, id=777000)
TELEGRAM_SERVICE_PHONES = {"+42777", "42777"}
if phone in TELEGRAM_SERVICE_PHONES or sender.id == 777000:
    logger.info(f"📨 Пропускаем сервисное сообщение Telegram от {phone} (id={sender.id})")
    return   # <-- messages dropped here, never persisted
```
Note: this block runs AFTER the internal-warmup short-circuit (lines 727-734) and BEFORE
the antispam/bot branches. Account 777000 is never a workspace sender, so leaving the
internal check above it is safe.

EXISTING PERSISTENCE PATTERN to mirror — app/services/listener.py:_handle_bot_message (lines 1150-1229):
- Opens its own `async with AsyncSessionLocal() as session:` (isolated so a failure
  doesn't poison the listener loop)
- `SELECT id, status FROM conversations WHERE sender_id=:sid AND contact_telegram_id=:tid`
- If none: INSERT conversation (ai_enabled=false, a distinct status, paused_at=NOW(), paused_reason)
- Then INSERT into messages: direction='inbound', sent_by='contact', telegram_message_id=event.id,
  with `ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING`
- Wrapped in try/except logging on failure

Conversation model — app/models/__init__.py:305 (status is String(20), free-form CHECK-constrained):
  status = Column(String(20), default="active", server_default="active")
  # active|manual|paused|lead|handoff|finished|bot_ignored|no_reply

Status vocabulary — app/schemas/__init__.py:909 CONVERSATION_STATUSES (used by PATCH validation):
  {"active","manual","paused","lead","handoff","finished","bot_ignored"}
  (note: 'no_reply' exists in the DB CHECK via mig 045 but is NOT in this set — pre-existing;
   do not touch it, just add 'telegram_service')

CHECK-constraint migration pattern — migrations/045_follow_up.sql:31-33 (idempotent DROP+ADD):
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply'));

Inbox list default-hide — app/routers/conversations.py:112-117:
  if status is None:
      where_clauses.append("c.status != 'bot_ignored'")
  else:
      where_clauses.append("c.status = :status")
      params["status"] = status

Test factory + patterns — tests/test_phase5_inbox.py:
  test_conversation_factory(contact_phone=..., status=..., ai_enabled=...) fixture exists.
  test_list_hides_bot_ignored_by_default (line 83) and test_list_status_bot_ignored_explicit
  (line 103) are the exact templates to copy for telegram_service.
  Run tests ONLY via test-overlay (CLAUDE.md): 
    docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Persist and classify Telegram service (777000) messages as status='telegram_service'</name>
  <files>migrations/046_telegram_service_status.sql, app/schemas/__init__.py, app/services/listener.py</files>
  <action>
Stop dropping Telegram service-account messages; persist them under a new distinct status.

1. Create `migrations/046_telegram_service_status.sql` — idempotent, following the exact
   pattern of migrations/045_follow_up.sql:31-33. Extend the conversations status CHECK to add
   'telegram_service' while PRESERVING every existing value (including 'no_reply' and 'bot_ignored'):
   ```sql
   -- 046: conversations.status gains 'telegram_service' — Telegram login/auth-code
   -- notifications from the service account (id 777000 / +42777) get their own inbox tab.
   DO $$ BEGIN
     ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
     ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
       CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply','telegram_service'));
   END $$;
   ```
   (The applier auto-runs it on api start; must be idempotent — DROP CONSTRAINT IF EXISTS makes re-run safe.)

2. In `app/schemas/__init__.py`, add `"telegram_service"` to the CONVERSATION_STATUSES set
   (line ~909) so PATCH /conversations/{id} validation accepts it.

3. In `app/services/listener.py`, REPLACE the early-return block at lines 736-740. Instead of
   `return`, keep the same detection condition and delegate to a NEW method
   `_handle_telegram_service_message(sender_info, sender, event, name, phone)`, then `return`
   (no AI dispatch, no antispam/bot branches). Detection stays:
   `phone in TELEGRAM_SERVICE_PHONES or sender.id == 777000`.

4. Add the `_handle_telegram_service_message` method to the listener class, modeled closely on
   `_handle_bot_message` (lines 1150-1229) but with status='telegram_service':
   - Isolated `async with AsyncSessionLocal() as session:` + try/except logging (never poison the loop).
   - SELECT existing conversation by (sender_id, contact_telegram_id).
   - If none: INSERT conversation with ai_enabled=false, status='telegram_service',
     paused_at=NOW(), paused_reason='Telegram service account (login/auth codes)',
     contact_name = name (usually "Telegram"), contact_phone = phone.
   - If exists: UPDATE to status='telegram_service' + ai_enabled=false ONLY when current
     status='active' (Pitfall-3 guard — don't clobber lead/handoff/finished/manual history).
   - Always INSERT the inbound message: direction='inbound', sent_by='contact',
     message_text = event.text or '<media>', telegram_message_id = event.id,
     `ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING`.
   - Log e.g. "📥 Telegram service message stored: conv=…".
   CRITICAL: this method must NOT call the AI engine, must NOT enqueue anything, and must NOT
   touch sender restriction/lifecycle status. It only records the message so the UI can show it.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_inbox.py -q</automated>
  </verify>
  <done>
Migration 046 exists and is idempotent; 'telegram_service' is in CONVERSATION_STATUSES;
listener no longer early-returns on 777000/+42777 but persists a conversation
(status='telegram_service', ai_enabled=false) + the inbound message via the new handler, with
no AI dispatch. Existing inbox test suite still passes.
  </done>
</task>

<task type="auto">
  <name>Task 2: Expose the Telegram tab via inbox API + regenerate openapi contract</name>
  <files>app/routers/conversations.py, tests/test_phase5_inbox.py, lovable-handoff/openapi.json</files>
  <action>
Make the new status behave like a dedicated tab through the existing inbox list endpoint, and
publish the contract for the frontend team.

1. In `app/routers/conversations.py` list_conversations (lines 112-117), extend the default-hide
   so status=None hides BOTH 'bot_ignored' AND 'telegram_service' (keep service messages out of
   the normal inbox), while an explicit `?status=telegram_service` returns exactly those rows.
   Change:
   ```python
   if status is None:
       where_clauses.append("c.status NOT IN ('bot_ignored', 'telegram_service')")
   else:
       where_clauses.append("c.status = :status")
       params["status"] = status
   ```
   Update the D-17 docstring comment to mention telegram_service is also hidden by default.
   (No change needed to the warmup-pair exclude — 777000 is never a workspace sender.)

2. In `tests/test_phase5_inbox.py`, add two tests mirroring test_list_hides_bot_ignored_by_default
   (line 83) and test_list_status_bot_ignored_explicit (line 103):
   - `test_list_hides_telegram_service_by_default`: seed one 'active' + one 'telegram_service'
     conversation via test_conversation_factory; assert the default GET /api/v1/conversations
     returns only the active one (telegram_service absent).
   - `test_list_status_telegram_service_explicit`: seed a 'telegram_service' conversation;
     assert GET /api/v1/conversations?status=telegram_service returns it.

3. Regenerate the handoff contract OFFLINE (Phase 19-05 precedent — do NOT un-gate prod).
   Since conversation `status` is a free-form `str` in both the query param and ConversationResponse,
   the openapi.json diff will likely be empty; that is expected. Regenerate anyway to keep it in
   sync, using app.openapi() inside the test container (no prod api boot):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm -T api \
     python -c "import json,app.main as m; print(json.dumps(m.app.openapi(), ensure_ascii=False, indent=2))" \
     > lovable-handoff/openapi.json
   ```
   If the file is unchanged, leave it as-is (no-op commit is fine). Do NOT hand-edit openapi.json.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_inbox.py -q</automated>
  </verify>
  <done>
Default GET /api/v1/conversations hides telegram_service; GET
/api/v1/conversations?status=telegram_service returns exactly those rows; both new tests pass;
openapi.json regenerated offline. Frontend contract (a "Telegram" tab that calls
?status=telegram_service) is documented in the SUMMARY.
  </done>
</task>

</tasks>

<verification>
- `grep -n "telegram_service" migrations/046_telegram_service_status.sql app/schemas/__init__.py app/services/listener.py app/routers/conversations.py` shows the value wired end-to-end.
- Listener no longer contains a bare `return` on the 777000/+42777 branch — it delegates to `_handle_telegram_service_message`.
- Full targeted test run green: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_inbox.py tests/test_listener.py -q`
- Migration is idempotent (DROP CONSTRAINT IF EXISTS) so api start-up applier is safe on re-run.
</verification>

<success_criteria>
- Telegram service-account (777000 / +42777) messages are persisted to conversations + messages instead of being dropped.
- Those conversations carry status='telegram_service', ai_enabled=false, and never trigger the AI answerer.
- Default inbox list excludes telegram_service; explicit ?status=telegram_service returns them → the frontend can render a "Telegram" tab.
- openapi.json regenerated offline; contract documented for the frontend team.
- All existing + new inbox tests pass.
</success_criteria>

<output>
After completion, create `.planning/quick/260703-goh-add-a-separate-telegram-tab-in-inbox-for/260703-goh-SUMMARY.md`.

In the SUMMARY, include a "Frontend handoff" section (this repo cannot touch the sibling frontend repo) stating:
- Add a new inbox tab labeled "Telegram".
- The tab lists conversations from `GET /api/v1/conversations?status=telegram_service` (workspace-scoped, auth required).
- Messages within a conversation load as usual via `GET /api/v1/conversations/{id}/messages`.
- These conversations are AI-disabled by design (ai_enabled=false); they are login/auth-code notifications from Telegram's official service account, not real contacts.
- Contract source: `lovable-handoff/openapi.json` (regenerated by this plan).
</output>
