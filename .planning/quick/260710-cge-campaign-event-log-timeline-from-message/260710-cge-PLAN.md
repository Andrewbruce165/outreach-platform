---
phase: quick-260710-cge
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/routers/campaigns.py
  - app/schemas/__init__.py
  - tests/test_campaign_events.py
  - frontend/src/components/ui/timeline.tsx
  - frontend/src/routes/_authenticated/campaigns.$id.tsx
  - frontend/package.json
  - frontend/bun.lock
autonomous: true
requirements: [CGE-01]
must_haves:
  truths:
    - "GET /api/v1/campaigns/{id}/events returns a merged, newest-first event list from message_queue (sent/failed) and llm_calls tool_calls (mark_as_lead/transfer_to_manager/finish_conversation), workspace-scoped, cursor-paginated"
    - "A llm_calls row whose tool_calls column is JSON null (jsonb 'null', not SQL NULL) does not crash the endpoint and produces no event — guarded by jsonb_typeof(tool_calls) = 'array'"
    - "Campaign detail page shows a 'Лог кампании' section rendering the events as a reui Timeline with Badge status chips, Russian labels, loading/empty/error states, and a 'Показать ещё' load-more button"
    - "Backend and frontend changes land as two separate commits"
  artifacts:
    - path: "app/routers/campaigns.py"
      provides: "GET /{campaign_id}/events endpoint — two raw-SQL queries merged in Python, workspace-scoped via _load_campaign"
    - path: "app/schemas/__init__.py"
      provides: "CampaignEvent + CampaignEventsResponse Pydantic models"
    - path: "tests/test_campaign_events.py"
      provides: "pytest coverage: merge+order, jsonb-null guard, workspace isolation, cursor pagination"
    - path: "frontend/src/components/ui/timeline.tsx"
      provides: "reui base Timeline component (vendored from https://reui.io/r/timeline.json)"
      exports: ["Timeline", "TimelineItem", "TimelineHeader", "TimelineSeparator", "TimelineTitle", "TimelineIndicator", "TimelineContent", "TimelineDate"]
    - path: "frontend/src/routes/_authenticated/campaigns.$id.tsx"
      provides: "'Лог кампании' card section with Timeline rendering + load-more pagination"
  key_links:
    - from: "frontend/src/routes/_authenticated/campaigns.$id.tsx"
      to: "GET /api/v1/campaigns/{id}/events"
      via: "api() client from @/lib/api with query {limit, before}"
      pattern: "campaigns/.*/events"
    - from: "app/routers/campaigns.py events SQL"
      to: "llm_calls.tool_calls"
      via: "jsonb_typeof guard + jsonb_array_elements LATERAL"
      pattern: "jsonb_typeof"
---

<objective>
Campaign event log ("Лог кампании"): a chronological, newest-first timeline of what actually happened in a campaign, assembled read-only from existing tables — no new tables, no migrations.

Event sources (MVP scope, per user requirements):
- **message_queue** (campaign_id set, status `sent` / `failed`) → events `message_sent` / `message_failed` with recipient info and error_message
- **llm_calls.tool_calls** (campaign_id set) → events `lead` (mark_as_lead) / `handoff` (transfer_to_manager) / `dialog_finished` (finish_conversation) — tool_calls chosen over conversation.status because status gets overwritten; the audit log does not

Explicitly OUT of scope (no data exists without a new table — future task): campaign start/pause/resume/finish history, sender attach/detach history, restriction events tied to a campaign.

Output: one workspace-scoped paginated endpoint + a reui Timeline section on the campaign detail page. Two commits: backend first, frontend second.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@app/routers/campaigns.py
@app/routers/analytics.py
@frontend/src/routes/_authenticated/campaigns.$id.tsx
@frontend/src/lib/api.ts

# Verified facts (checked in code during planning — trust these):
#
# Built-in tool names (app/services/ai_engine.py:60):
#   BUILT_IN_TOOL_NAMES = {"mark_as_lead", "transfer_to_manager", "finish_conversation"}
#   Mapping (C-04): mark_as_lead→lead, transfer_to_manager→handoff, finish_conversation→finish
#
# llm_calls.tool_calls element shape (app/services/llm_logger.py:103-105):
#   [{"id": ..., "name": "mark_as_lead", "arguments": "<JSON string>"}, ...]
#   Column is JSONB nullable; some rows store JSON null (jsonb_typeof = 'null'), NOT SQL NULL.
#   Any jsonb_array_elements over it MUST be guarded by jsonb_typeof(tool_calls) = 'array'.
#
# MessageQueue columns (app/models/__init__.py:283): workspace_id, sender_id, campaign_id
#   (nullable, SET NULL), status enum pending|processing|sent|failed|cancelled,
#   recipient_phone, recipient_name, result_recipient_name, result_recipient_username,
#   error_message, created_at, finished_at.
#
# LLMCall columns (app/models/__init__.py:1002): workspace_id, conversation_id,
#   campaign_id (nullable), sender_id (nullable), tool_calls JSONB, created_at.
#
# Conversation columns: contact_phone, contact_name (nullable), contact_telegram_id.
#   NO contact_username column — username for queue events comes from
#   message_queue.result_recipient_username; for llm events it is null.
#
# Workspace scoping: reuse _load_campaign(db, ctx, campaign_id) helper
#   (app/routers/campaigns.py:86) — raises 404 for foreign-workspace campaigns.
#
# Raw-SQL pattern: analytics.py::_compute_cards — text() + bind params only,
#   never f-string values into SQL.
#
# Frontend: campaigns.$id.tsx uses inline styles + className="card" sections,
#   useQuery from @tanstack/react-query, api() from @/lib/api, mixed RU/EN labels
#   (Russian is established: "Кому пишем", "Правила кампании"). Generated types
#   live in @/types/api — do NOT regenerate; define local TS types for the new endpoint.
#
# reui Timeline: registry JSON verified fetchable at https://reui.io/r/timeline.json
#   (follow redirects: curl -sL). Single file timeline.tsx, exports Timeline,
#   TimelineContent, TimelineDate, TimelineHeader, TimelineIndicator, TimelineItem,
#   TimelineSeparator, TimelineTitle. Depends on "@base-ui/react" (mergeProps,
#   useRender) — NOT currently in frontend/package.json (project is radix-based),
#   must be added. shadcn badge.tsx and collapsible.tsx already exist in
#   frontend/src/components/ui/.
#
# Host has NO bun — all frontend install/build runs in docker: oven/bun:1
#   (see deploy-frontend.sh). deploy uses --frozen-lockfile, so bun.lock must be
#   regenerated and committed when package.json changes.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Backend — GET /campaigns/{id}/events merged event log endpoint + tests</name>
  <files>app/routers/campaigns.py, app/schemas/__init__.py, tests/test_campaign_events.py</files>
  <action>
Add a read-only, workspace-scoped, cursor-paginated event-log endpoint to app/routers/campaigns.py. No migrations, no new tables, no changes to queue worker / listener / rate limits.

**Schemas (app/schemas/__init__.py, near the existing Campaign* models):**
- `CampaignEvent`: `type` (Literal["message_sent","message_failed","lead","handoff","dialog_finished"]), `at` (datetime), `contact_name` (str|None), `contact_username` (str|None), `contact_phone` (str|None), `sender_slug` (str|None), `detail` (str|None).
- `CampaignEventsResponse`: `events: list[CampaignEvent]`, `next_before: datetime|None`, `has_more: bool`.

**Endpoint:** `@router.get("/{campaign_id}/events", response_model=CampaignEventsResponse)` with `ctx: AuthCtx = Depends(auth_dep)`, `db: AsyncSession = Depends(get_db)`, query params `before: datetime | None = None` (ISO cursor) and `limit: int = Query(50, ge=1, le=100)`. First call `await _load_campaign(db, ctx, campaign_id)` — existing helper enforces workspace ownership (404 otherwise). Default `before` to now (UTC) when omitted.

**Query 1 — message events** (raw SQL via `text()` + bind params, following analytics.py style):
select from `message_queue q JOIN senders s ON s.id = q.sender_id` where `q.campaign_id = :cid AND q.workspace_id = :wid AND q.status IN ('sent','failed')` and `COALESCE(q.finished_at, q.created_at) < :before`, ordered by that timestamp DESC, `LIMIT :lim` where lim = limit + 1. Select: timestamp as `at`, `q.status`, `COALESCE(q.result_recipient_name, q.recipient_name)` as contact_name, `q.result_recipient_username`, `q.recipient_phone`, `q.error_message`, `s.slug`. Map status sent→`message_sent`, failed→`message_failed`; `detail` = error_message for failed, None for sent. Skip pending/processing/cancelled rows — they are not events yet.

**Query 2 — AI signal events:**
select from `llm_calls lc JOIN conversations conv ON conv.id = lc.conversation_id LEFT JOIN senders s ON s.id = lc.sender_id CROSS JOIN LATERAL jsonb_array_elements(lc.tool_calls) AS tc` where `lc.campaign_id = :cid AND lc.workspace_id = :wid AND jsonb_typeof(lc.tool_calls) = 'array' AND tc->>'name' IN ('mark_as_lead','transfer_to_manager','finish_conversation') AND lc.created_at < :before` ordered by `lc.created_at DESC LIMIT :lim` (limit + 1). The `jsonb_typeof(lc.tool_calls) = 'array'` predicate is mandatory and must appear in the WHERE clause — rows store jsonb null and would blow up jsonb_array_elements otherwise (this bug bit the campaign-detail redesign before). Select: `lc.created_at`, `tc->>'name'`, `tc->>'arguments'`, `conv.contact_name`, `conv.contact_phone`, `s.slug`. Map name → type: mark_as_lead→`lead`, transfer_to_manager→`handoff`, finish_conversation→`dialog_finished`. `detail` = arguments string truncated to 200 chars (or None if empty); contact_username = None for these events.

**Merge in Python:** concatenate both result sets, sort by `(at DESC, stable tie-break)` — use a secondary sort key (e.g. event type + a row id string) so ordering is deterministic. `has_more = len(merged) > limit`; respond with `merged[:limit]`, `next_before` = `at` of the last returned event (None when list empty). Add a code comment that the strict `< :before` cursor can theoretically skip events sharing the exact same microsecond timestamp on a page boundary — accepted for MVP.

**Tests (tests/test_campaign_events.py)** — copy fixture/setup patterns from tests/test_campaign_router.py (workspace + auth ctx + campaign creation). Cover:
1. Merged ordering: insert a sent queue row, a failed queue row (with error_message), and an llm_calls row with `tool_calls=[{"id":"x","name":"mark_as_lead","arguments":"{}"}]` (plus its conversation) → response contains message_sent, message_failed (detail=error text) and lead events, newest first.
2. jsonb-null guard: insert an llm_calls row with tool_calls stored as JSON null → endpoint returns 200 and that row yields no event.
3. Workspace isolation: campaign of another workspace → 404.
4. Pagination: create limit+N events, request with small `limit` → `has_more=true`, then request again with `before=next_before` → older events, no duplicates.

Tests run ONLY via the docker test-overlay (conftest guard blocks anything else).

Commit this task separately as the backend commit (do not mix frontend files in): `feat(campaigns): add GET /campaigns/{id}/events campaign event log endpoint`.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_events.py -x -q</automated>
  </verify>
  <done>Endpoint returns merged newest-first events from both sources with cursor pagination; jsonb-null tool_calls rows are harmless; foreign-workspace campaign → 404; all new tests green via test-overlay; backend-only commit created.</done>
</task>

<task type="auto">
  <name>Task 2: Frontend — "Лог кампании" section with reui Timeline on campaign detail page</name>
  <files>frontend/src/components/ui/timeline.tsx, frontend/src/routes/_authenticated/campaigns.$id.tsx, frontend/package.json, frontend/bun.lock</files>
  <action>
**Vendor the reui Timeline (do NOT run the shadcn CLI):** the CLI previously overwrote the aimly.css token imports in frontend/src/styles.css (known regression). Instead fetch the component straight from the registry:

```
curl -sL https://reui.io/r/timeline.json | python3 -c "import json,sys; print(json.load(sys.stdin)['files'][0]['content'])" > frontend/src/components/ui/timeline.tsx
```

Verified during planning: this yields a single timeline.tsx exporting Timeline, TimelineItem, TimelineHeader, TimelineSeparator, TimelineTitle, TimelineIndicator, TimelineContent, TimelineDate, importing `cn` from `@/lib/utils` (exists) and `mergeProps`/`useRender` from `@base-ui/react` (NOT installed). Add `"@base-ui/react"` to frontend/package.json dependencies (latest stable), then regenerate the lockfile in docker (host has no bun): `docker run --rm -v "$PWD/frontend":/app -w /app oven/bun:1 bun install`. Commit the updated bun.lock — deploy-frontend.sh builds with `--frozen-lockfile` and will fail otherwise. After all edits, confirm `git diff frontend/src/styles.css` is empty (aimly.css import untouched).

**"Лог кампании" section in frontend/src/routes/_authenticated/campaigns.$id.tsx:** add a new `<section className="card" style={{ padding: 20 }}>` in the left column (after the existing Funnel/template sections), heading "Лог кампании", following the page's existing inline-style + card conventions.

Data: define local TS types for the response (do NOT regenerate @/types/api — the generated openapi types won't include this endpoint): `CampaignEvent { type; at; contact_name; contact_username; contact_phone; sender_slug; detail }` and `CampaignEventsResponse { events; next_before; has_more }`. Fetch with `useInfiniteQuery` (or useQuery + accumulated pages state — whichever is simpler given the file's existing patterns) keyed `["campaign-events", campaignId]`, calling `api<CampaignEventsResponse>(`/campaigns/${campaignId}/events`, { query: { limit: "50", ...(before ? { before } : {}) } })`, `getNextPageParam: (last) => last.has_more ? last.next_before : undefined`.

Rendering: reui `Timeline` with one `TimelineItem` per event — `TimelineHeader` + `TimelineSeparator` + `TimelineIndicator` + `TimelineTitle` + `TimelineContent`, plus shadcn `Badge` (already in ui/badge.tsx) as the status chip. No avatars (the reui CI-pipeline example's people avatars are explicitly not wanted). Russian labels per event type:
- message_sent → Badge "Отправлено"
- message_failed → Badge "Ошибка" (variant="destructive"), error text in TimelineContent
- lead → Badge "Лид"
- handoff → Badge "Передан менеджеру"
- dialog_finished → Badge "Диалог завершён"

Each item title shows the contact (contact_name, else @contact_username, else contact_phone) and a muted "via {sender_slug}" line; time via `new Date(at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })`. Day grouping (cheap version): iterate the flat event list and emit a muted day header (`toLocaleDateString("ru-RU", { day: "numeric", month: "long" })`) whenever the calendar day changes between consecutive events — skip entirely if the Timeline layout fights it.

States: loading "Загрузка…" (muted div, same as page's existing loading style), error via the page's existing `errMsg` helper, empty "Событий пока нет". Below the list, a "Показать ещё" button (className "btn btn--ghost btn--sm" like existing buttons) visible while `hasNextPage`, disabled while fetching.

Commit as a separate frontend-only commit: `feat(frontend): campaign event log timeline on campaign detail page`. Do not deploy (no ./deploy-frontend.sh) unless the user asks.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach/frontend && docker run --rm -v "$PWD":/app -w /app oven/bun:1 sh -c "bun install --frozen-lockfile && bun run build"</automated>
  </verify>
  <done>timeline.tsx vendored under components/ui with @base-ui/react added to package.json + regenerated bun.lock; campaign detail page renders "Лог кампании" via reui Timeline + Badge with Russian labels, day headers, loading/empty/error states and working "Показать ещё"; styles.css untouched (git diff clean on it); SPA docker build passes with frozen lockfile; frontend-only commit created.</done>
</task>

</tasks>

<verification>
- Backend: test-overlay pytest for tests/test_campaign_events.py green (merge/order, jsonb-null guard, isolation, pagination).
- Frontend: docker bun build with --frozen-lockfile succeeds (proves lockfile committed correctly and TS compiles).
- `git log --oneline -2` shows two separate commits: one backend (app/ + tests/), one frontend (frontend/).
- `git diff HEAD~2 -- frontend/src/styles.css` shows no changes to the aimly.css import.
</verification>

<success_criteria>
- User opens a campaign detail page and sees "Лог кампании": newest-first timeline of sends, send failures, leads, handoffs and finished dialogs with contact + sender info and Russian status badges, with load-more pagination.
- No new DB tables/migrations; queue worker, listener and rate limits untouched.
</success_criteria>

<output>
Create `.planning/quick/260710-cge-campaign-event-log-timeline-from-message/260710-cge-SUMMARY.md` when done.
</output>
