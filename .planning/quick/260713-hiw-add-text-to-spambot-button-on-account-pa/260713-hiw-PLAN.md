---
phase: quick-260713-hiw
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/062_conversations_status_spambot.sql
  - app/services/listener.py
  - app/routers/senders.py
  - app/routers/conversations.py
  - tests/test_spambot_conversation.py
  - frontend/src/routes/_authenticated/accounts.tsx
  - frontend/src/components/SpambotChatPanel.tsx
autonomous: true
requirements: [QUICK-260713-hiw]
must_haves:
  truths:
    - "A manager can click 'Text to SpamBot' on an account card and a side panel opens showing a live chat with @SpamBot from that specific sender account"
    - "Messages the manager sends go out from that sender's Telegram session to @SpamBot (id 178220800) and appear in the panel"
    - "@SpamBot replies received by the listener are persisted and render in the panel"
    - "The SpamBot conversation NEVER appears in the normal Inbox conversation list"
    - "Existing antispam restriction handling still fires on real SpamBot restriction messages during a manual chat (unchanged)"
  artifacts:
    - path: "migrations/062_conversations_status_spambot.sql"
      provides: "conversations.status CHECK extended with 'spambot' value"
      contains: "conversations_status_check"
    - path: "app/routers/senders.py"
      provides: "POST /api/v1/senders/{slug}/spambot-conversation get-or-create endpoint"
      contains: "spambot-conversation"
    - path: "app/services/listener.py"
      provides: "_persist_spambot_message — persists inbound @SpamBot messages to a status='spambot' conversation"
      contains: "_persist_spambot_message"
    - path: "frontend/src/components/SpambotChatPanel.tsx"
      provides: "Side-panel (Sheet) chat with @SpamBot: message list + composer"
    - path: "frontend/src/routes/_authenticated/accounts.tsx"
      provides: "'Text to SpamBot' button in account actions menu"
      contains: "spambot-conversation"
  key_links:
    - from: "frontend accounts.tsx button"
      to: "POST /api/v1/senders/{slug}/spambot-conversation"
      via: "api() mutation, opens SpambotChatPanel with returned conversation_id"
    - from: "SpambotChatPanel composer"
      to: "POST /api/v1/conversations/{id}/send"
      via: "reused send endpoint (existing)"
    - from: "listener antispam branch (sender.id == 178220800)"
      to: "conversations/messages (status='spambot')"
      via: "_persist_spambot_message before _handle_antispam_signal"
---

<objective>
Add a "Text to SpamBot" affordance to the account (sender) detail card. Clicking it opens a side panel (shadcn Sheet slide-over) that is a live 1:1 chat with Telegram's official @SpamBot (id `178220800`), sending/receiving from that specific sender account. Managers use it to manually check or negotiate account restriction status, beyond the existing one-shot automated `spambot-check` probe.

Purpose: Give managers an interactive channel to @SpamBot per account without the SpamBot chat polluting the normal Inbox.
Output: One backend migration + listener persistence + a new get-or-create endpoint + a frontend button and chat panel. Sending reuses the existing `POST /conversations/{id}/send`.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/quick/260713-hiw-add-text-to-spambot-button-on-account-pa/260713-hiw-CONTEXT.md
@CLAUDE.md

<interfaces>
<!-- Verified against the codebase 2026-07-13. Use directly — no exploration needed. -->

SpamBot Telegram user id = 178220800 (already in `ANTISPAM_BOT_IDS = {178220800, 777000}` at app/services/listener.py:119).

Existing conversations.status CHECK (migration 046) — legal values:
  ('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply','telegram_service')
(migration 061 also added 'lead_pending' — DO NOT drop it; read the LIVE constraint before rewriting, since migrations 045/046/061 layered onto it. Safest: DROP CONSTRAINT IF EXISTS then re-add the FULL current set PLUS 'spambot'.)

Migration pattern (app/services/listener.py sibling — see migrations/046_telegram_service_status.sql):
```sql
BEGIN;
DO $$ BEGIN
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN (... FULL current set ..., 'spambot'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
COMMIT;
```

conversations columns: contact_phone VARCHAR(40) NOT NULL (needs a sentinel, not NULL);
  contact_name VARCHAR(100) NULL; contact_telegram_id BIGINT NULL; ai_enabled BOOL; status.

Listener persistence pattern to MIRROR — `_handle_telegram_service_message`
(app/services/listener.py:1319-1400): isolated `AsyncSessionLocal()`, SELECT existing
by (sender_id, contact_telegram_id), INSERT conversation with ai_enabled=false +
dedicated status if absent, then INSERT into messages with
`ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING`.

Listener antispam dispatch (app/services/listener.py):
  - bot-flag branch ~812-826: `if getattr(sender,'bot',False): if sender.id in ANTISPAM_BOT_IDS: await self._handle_antispam_signal(...); return`
  - keyword-backup branch ~837-843: same `_handle_antispam_signal(...)`; return
  - Note: id 777000 (Telegram service) is intercepted earlier (~790-795) and returns,
    so the only ANTISPAM_BOT_IDS member reaching these branches is 178220800 (@SpamBot).
  - `_handle_antispam_signal` (1114+) has early-returns for selfcheck + clean replies;
    it does NOT persist to conversations/messages. Persistence must be ADDITIVE and run
    for ALL @SpamBot messages (incl. "free" replies), so persist BEFORE calling it.

Auth/ownership pattern for the new endpoint — `GET /senders/{slug}/spambot-check`
(app/routers/senders.py:938-1079): `ctx: AuthCtx = Depends(auth_dep)`,
`sender = await _load_sender_by_slug(db, ctx, slug)` (helper at senders.py:432).
Router prefix = `/api/v1`.

Send endpoint (REUSE as-is) — `POST /conversations/{id}/send`
(app/routers/conversations.py:1097-1207): persists outbound message with sent_by='human',
resolves entity via `send_message_by_telegram_id` (has get_dialogs cold-start fallback).
  ⚠ Its auto-takeover UPDATE (lines 1144-1152) flips status→'manual' — this would surface
  the SpamBot conversation in the Inbox. Add a guard so it does NOT clobber 'spambot'
  (see Task 1). Its queue-cancel matches on the conversation's contact_phone — a sentinel
  contact_phone that matches no real recipient makes it a harmless no-op.
  Gate: `s.lifecycle_status='active' AND s.auth_status='ok'` — frozen/spam_limited senders
  keep auth_status='ok' so they can still text SpamBot (intended). Genuinely paused/banned
  senders can't (acceptable — no live session anyway).

Inbox list query — `GET /conversations` (app/routers/conversations.py:236-341):
  line ~261 `if status is None: where_clauses.append("c.status NOT IN ('bot_ignored', 'telegram_service')")`.
  Add 'spambot' to this exclusion.

Messages fetch (REUSE) — `GET /conversations/{id}/messages` (conversations.py:384) →
  MessageListResponse {messages: [{id, direction, message_text, sent_by, created_at, ...}], total}.
  Inbox Thread polls with `refetchInterval: 10_000`, queryKey `["messages", conversationId]`
  (frontend inbox.tsx:1245-1250).

Frontend account actions menu (frontend/src/routes/_authenticated/accounts.tsx):
  `SenderCard` at 399; `spambotMut` (the automated Check Spam Bot) at 406-415; its button
  at 611-624 (`ShieldAlert` icon). Helpers: `api<T>()`, `ApiError`, `apiBaseUrl` from
  `@/lib/api`; `useMutation`/`useQuery`/`useQueryClient` from @tanstack/react-query;
  `toast` from sonner. shadcn `Sheet` exists at frontend/src/components/ui/sheet.tsx.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Backend — spambot conversation status, listener persistence, get-or-create endpoint</name>
  <files>migrations/062_conversations_status_spambot.sql, app/services/listener.py, app/routers/senders.py, app/routers/conversations.py, tests/test_spambot_conversation.py</files>
  <action>
Implement the backend so a per-sender @SpamBot conversation exists, is populated in both directions, and stays out of the Inbox. Per CONTEXT decisions (dedicated status, keep antispam parsing active, persist mirroring telegram_service, reuse send endpoint).

1. **Migration `migrations/062_conversations_status_spambot.sql`** (idempotent raw SQL, mirror migration 046):
   - Read the LIVE `conversations_status_check` constraint first (migrations 045/046/061 layered values). Re-add the FULL current legal set PLUS `'spambot'`. Current known set to preserve: `('active','manual','paused','lead','handoff','finished','bot_ignored','no_reply','telegram_service','lead_pending')` — verify against DB, then append `'spambot'`.
   - Wrap in `BEGIN; DO $$ BEGIN ALTER TABLE ... DROP CONSTRAINT IF EXISTS ...; ALTER TABLE ... ADD CONSTRAINT ...; EXCEPTION WHEN duplicate_object THEN NULL; END $$; COMMIT;`

2. **`app/services/listener.py` — add `_persist_spambot_message(self, sender_info, sender, event, name, phone)`**: copy the structure of `_handle_telegram_service_message` (1319-1400) but use `status='spambot'`, `ai_enabled=false`, `paused_reason='SpamBot manual chat'`. On INSERT of a NEW conversation set `contact_phone` to a sentinel that matches no real recipient (e.g. `'spambot:178220800'` — NOT NULL, column is NOT NULL), `contact_name='@SpamBot'`, `contact_telegram_id=178220800`. For an existing row DO NOT downgrade status (no Pitfall-3 flip needed — just reuse the existing conv id). Then INSERT the inbound message with `sent_by='contact'`, `ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING`. Isolated `AsyncSessionLocal()`, wrapped in try/except that only logs (never break the loop). NEVER touch restriction_status/lifecycle_status here.

3. **`app/services/listener.py` — wire persistence into the antispam branches**: in BOTH the bot-flag branch (~813) and the keyword-backup branch (~837), when `sender.id == 178220800`, call `await self._persist_spambot_message(sender_info, sender, event, name, phone)` BEFORE the existing `await self._handle_antispam_signal(...)`. Do NOT modify `_handle_antispam_signal` itself — restriction parsing/paths stay exactly as today (CONTEXT: keep antispam active, do not special-case).

4. **`app/routers/senders.py` — new endpoint `POST /api/v1/senders/{slug}/spambot-conversation`**: mirror `check_spambot`'s auth/ownership (`ctx: AuthCtx = Depends(auth_dep)`, `sender = await _load_sender_by_slug(db, ctx, slug)`). Get-or-create the conversation for `(sender.id, contact_telegram_id=178220800)`: SELECT newest existing (ORDER BY created_at DESC LIMIT 1); if none, INSERT with `workspace_id=sender.workspace_id`, `ai_enabled=false`, `status='spambot'`, sentinel `contact_phone='spambot:178220800'`, `contact_name='@SpamBot'`, `contact_telegram_id=178220800`. Return `{"conversation_id": str(id), "status": "spambot"}`. No Telethon call needed here (entity cold-start is handled by the send path's get_dialogs fallback).

5. **`app/routers/conversations.py` — two minimal guards**:
   - Inbox list (`list_conversations`, ~261): add `'spambot'` to `c.status NOT IN ('bot_ignored', 'telegram_service')` → `('bot_ignored', 'telegram_service', 'spambot')`.
   - Send endpoint auto-takeover UPDATE (`send_message_from_ui`, ~1144-1152): add `AND status <> 'spambot'` to the WHERE clause so sending to @SpamBot does NOT flip the dedicated status to 'manual' (which would leak it into the Inbox). Everything else in the send path is reused as-is.

6. **`tests/test_spambot_conversation.py`** — targeted tests (reuse fixtures/patterns from tests/test_senders.py):
   - POST `/api/v1/senders/{slug}/spambot-conversation` returns 200 with `status=='spambot'` and a `conversation_id`; calling twice returns the SAME conversation_id (get-or-create idempotency).
   - The created conversation has `ai_enabled=false` and does NOT appear in `GET /api/v1/conversations` (status=None default list).
   - Endpoint is workspace-scoped: a slug from another workspace → 404 (mirror _load_sender_by_slug behavior).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_conversation.py tests/test_spambot_selfcheck.py -x -q</automated>
  </verify>
  <done>Migration 062 present + idempotent; listener persists @SpamBot inbound to a status='spambot' conversation without altering antispam handling; POST /senders/{slug}/spambot-conversation get-or-creates and is workspace-scoped; SpamBot conversation excluded from Inbox list; send endpoint no longer clobbers 'spambot' status; new tests green.</done>
</task>

<task type="auto">
  <name>Task 2: Frontend — 'Text to SpamBot' button + side-panel chat</name>
  <files>frontend/src/components/SpambotChatPanel.tsx, frontend/src/routes/_authenticated/accounts.tsx</files>
  <action>
Add the manager-facing UI. Per CONTEXT: button near the existing "Check Spam Bot" action; side panel is a slimmed Thread (message list + composer only — no AI-trace tabs/tags/RightPane).

1. **`frontend/src/components/SpambotChatPanel.tsx`** — a new component using the existing shadcn `Sheet` (frontend/src/components/ui/sheet.tsx), side='right'. Props: `{ slug: string; senderLabel: string; open: boolean; onOpenChange: (v: boolean) => void }`.
   - On open, resolve the conversation id via `useQuery` (enabled while open) hitting `POST /api/v1/senders/${slug}/spambot-conversation` through `api<{conversation_id: string; status: string}>()`. (POST is fine inside queryFn — mirror how accounts.tsx calls api.)
   - Once `conversation_id` is known, `useQuery(["messages", conversation_id])` → `api<MessageList>('/api/v1/conversations/${id}/messages')` with `refetchInterval: 10_000` (mirror inbox.tsx:1245-1250). Render a simple message list: outbound (sent_by 'human') right-aligned, inbound (@SpamBot) left-aligned — reuse the visual style/classes of inbox `MessageBubble` (~2037); a lightweight inline bubble is acceptable, do NOT import the full Thread/RightPane.
   - Composer: a textarea + send button; on send call `POST /api/v1/conversations/${conversation_id}/send` with `{ message }` via `api()`; on success clear input and `qc.invalidateQueries({ queryKey: ["messages", conversation_id] })`. Handle `ApiError` with `toast.error`. Disable send while pending / when no conversation_id.
   - Use `MessageList` type via `components["schemas"]["MessageListResponse"]` (see inbox.tsx type alias at line 65) from `@/types/api`.

2. **`frontend/src/routes/_authenticated/accounts.tsx`** — in `SenderCard`:
   - Add `const [spambotChatOpen, setSpambotChatOpen] = useState(false);`
   - Add a new button in the actions menu right after the existing "Check Spam Bot" button (~624): label "Написать SpamBot" (RU, to match neighbours) with a chat icon already imported or add one (e.g. `MessageSquare` from lucide-react — extend the existing lucide import block); onClick `{ setOpen(false); setSpambotChatOpen(true); }`.
   - Render `<SpambotChatPanel slug={sender.slug} senderLabel={sender.name || sender.phone} open={spambotChatOpen} onOpenChange={setSpambotChatOpen} />` alongside the existing modals (near where `SpambotResultModal`/history are rendered).
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach/frontend && bunx tsc --noEmit</automated>
  </verify>
  <done>Account card shows a "Написать SpamBot" button that opens a right-side Sheet; the panel resolves/creates the conversation, polls messages every 10s, and sends via the reused send endpoint; TypeScript compiles with no errors.</done>
</task>

</tasks>

<verification>
- Migration 062 applies on api start (auto-applier) without failing; conversations.status accepts 'spambot' and still accepts all prior values.
- Listener: a message from id 178220800 creates/updates a status='spambot' conversation and inserts the inbound message; restriction_status behavior for a real "limited/frozen" SpamBot reply is unchanged.
- POST /senders/{slug}/spambot-conversation is idempotent and workspace-scoped; the conversation is absent from GET /conversations default list.
- Sending via POST /conversations/{id}/send to the SpamBot conversation keeps status='spambot' (no Inbox leak) and persists the outbound message.
- Frontend: button opens the Sheet, messages render both directions, send works; tsc clean.
</verification>

<success_criteria>
- Backend targeted pytest (test_spambot_conversation.py + test_spambot_selfcheck.py) green.
- Frontend `bunx tsc --noEmit` clean.
- All 5 must_haves truths observably satisfied.
- No changes to queue rate limits, working hours, FloodWait retry logic, or `_handle_antispam_signal` internals.
</success_criteria>

<output>
After completion, create `.planning/quick/260713-hiw-add-text-to-spambot-button-on-account-pa/260713-hiw-SUMMARY.md`.
</output>
