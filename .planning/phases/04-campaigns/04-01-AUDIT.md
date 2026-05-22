---
phase: 04-campaigns
plan: 01
created: 2026-05-22
type: audit
---

# Phase 4 Plan 01: Pre-Implementation Audit

Final audit of the live codebase before Phase 4 implementation begins. Downstream
planners (04-02 / 04-03 / 04-04 / 04-05) MUST add this file to their `<context>`
via `@.planning/phases/04-campaigns/04-01-AUDIT.md`.

Verified against working-tree as of 2026-05-22 (HEAD = `60cfaeb`, latest applied
migration = `015_phase3.sql`).

## 1. TODO(phase-4) Inventory

`grep -nrE "TODO\(phase-4\)" app/ --include="*.py"` returns 10 markers
(matches CONTEXT.md expectation). Distribution:

| # | File:Line | Marker text (truncated) | Closure plan | Closure task |
|---|-----------|--------------------------|--------------|--------------|
| 1 | `app/routers/agents.py:49` | `campaign_count via SELECT COUNT(*) FROM campaigns WHERE agent_id = ai_contexts.id` (docstring) | 04-02 | Real COUNT in `_agent_to_response` |
| 2 | `app/routers/agents.py:246` | `also block on active campaign attachment (D-09)` | 04-02 | 409 if `SELECT 1 FROM campaigns WHERE agent_id=? AND status='running'` |
| 3 | `app/routers/folders.py:248` | `also block on active campaign attachment` (inside 409 detail block) | 04-02 | Append `active_campaigns` to 409 payload + block DELETE if running |
| 4 | `app/services/queue.py:708` | `pull from conversation.campaign_id JOIN` (inside `_upsert_conversation` INSERT) | 04-04 | Replace `extra_data.ai_context_id` lookup with direct `campaign_id` column on `message_queue` |
| 5 | `app/services/queue.py:849` | `apply same ai_context_id propagation as enqueue_message` (inside `enqueue_file`) | 04-04 | Propagate `campaign_id` to file-path enqueue (B1 revision per plan-checker) |
| 6 | `app/services/rotation.py:180` | `selection по campaign_id когда появится Campaign.sender_lock` | 04-04 | Change `get_or_assign_sender` signature `context_id` → `campaign_id`; pool source switches from `senders` global to `campaign_senders` through-table |
| 7 | `app/services/ai_engine.py:88` | `max_message_length moves to Campaign; webhook_functions moves to Campaign tools (CAMP-15)` | 04-05 | Rewrite `get_context` → `get_context_for_conversation(conv_id)` reading from `campaigns` via JOIN |
| 8 | `app/services/listener.py:250` | `pull ai_context_id from conversation.campaign_id via JOIN` (inside `_send_to_ai` docstring) | 04-05 | After 04-04 lands `conversations.campaign_id` — JOIN to derive agent_id |
| 9 | `app/services/listener.py:350` | `pull ai_context_id from conversation.campaign_id via JOIN` (inside `get_active_senders`) | 04-05 | Same JOIN pattern; remove dead `ai_context_id` plumbing from sender_info |
| 10 | `app/services/listener.py:707` | `pull document_webhook_url from conversation.campaign_id` | 04-05 | **DO NOT restore** — document_webhook_url is dropped per Phase 3 D-01 (CONTEXT.md item 6). Replace the entire TODO comment with permanent note: feature is part of `campaigns.tools` if a client wants it. Close marker by deletion + comment update. |

**Total: 10 markers. All accounted for.**

Note on `app/routers/senders.py`: searched separately —
**no `TODO(phase-4)` markers present** (Phase 3 cleaned this path). The
"block DELETE/PATCH sender при active campaign" requirement from 04-02 Task 3
adds a **new** check, it does NOT close an existing TODO. Planner 04-02 must
add it without relying on a marker.

## 2. Webhook + Tools Code Inventory

| Function / Class | File:Line | What it does now | Phase 4 action |
|------------------|-----------|------------------|----------------|
| `AIEngine.build_tools(webhook_functions)` | `app/services/ai_engine.py:165-199` | Converts internal `{name, description, parameters:[{name,type,description,required}]}` array into OpenAI tools format (`{type:"function", function:{name, description, parameters:{type:"object", properties:{...}, required:[...]}}}`) | REUSE without API change. **Source switches** from `context["webhook_functions"]` (currently always `[]` per `get_context:91`) to `campaign.tools` JSONB via JOIN. Plan 04-05 Task 2. |
| `AIEngine.execute_webhook(func_config, func_args, conversation_context)` | `app/services/ai_engine.py:201-265` | Fire-and-forget(-ish) POST via `httpx.AsyncClient(timeout=10.0)`. Returns webhook response body as string (or error message) for second LLM call. Payload shape: `{arguments, callId, agentId, context, timestamp}` ("BlackBox-compatible" — see line 215). Includes JSON decode of response, plus full error taxonomy (Timeout/Connect/HTTP/generic). | REUSE without API change for custom tools (CAMP-15). Source `func_config` is read from `campaign.tools` items in the new code path. Plan 04-05 Task 2. |
| `AIEngine.generate_response(session, conversation_id, context_id, contact_name, new_message, conversation_context)` | `app/services/ai_engine.py:267-430` | Two-pass OpenAI call: (1) chat.completions with optional `tools` from `build_tools`; (2) if `response_message.tool_calls` is non-empty — iterates each tool_call, looks up `func_config` in `webhook_functions`, calls `execute_webhook`, re-issues `chat.completions.create` with tool results to obtain final reply text. | EXTEND: signature changes to accept `campaign_id` (or be replaced by `get_context_for_conversation(conversation_id)` that resolves campaign via JOIN). Tool-call loop adds dispatch branch for **built-in** tools (D-12: `mark_as_lead`/`transfer_to_manager`/`finish_conversation`) — these do NOT return a tool result to the LLM; second `chat.completions.create` is skipped when handoff/finish fired. Plan 04-05 Task 3. |
| `AIEngine.get_context(session, context_id)` | `app/services/ai_engine.py:50-100` | SELECTs `system_prompt, tone_of_voice, rules, company_info` from `ai_contexts` by id. Hardcodes `max_message_length=500` and `webhook_functions=[]` since Phase 3 D-01 dropped both columns. 5-min TTL in-memory cache (`_context_cache`). | REWRITE as `get_context_for_conversation(conversation_id)` that JOINs `conversations.campaign_id → campaigns` and reads `tools`, `lead_trigger_hint`, `handoff_trigger_hint`, `finish_trigger_hint`, `lead_webhook_url`, `handoff_webhook_url`, `finish_webhook_url`, plus agent fields via `campaigns.agent_id → ai_contexts`. Keep TTL cache pattern. Plan 04-05 Task 1. |
| `QueueWorker._fire_callback(url, queue_id, status, sender_slug, recipient_phone, ...)` | `app/services/queue.py:731-766` | Fire-and-forget POST via `httpx.AsyncClient(timeout=10.0)`, payload `{queue_id, status, sender_slug, recipient_phone, recipient_name, recipient_telegram_id, recipient_username, message_id, error, extra_data}`. Never raises; logs warn on failure. Used for per-queue-item delivery callbacks (existing `callback_url` column on `message_queue`). | KEEP. **Recommendation for Plan 04-05:** lift the httpx pattern (NOT this exact function — different payload shape) into `app/services/webhook_notify.py` for the 3 new campaign-level webhooks (lead/handoff/finish). The existing `_fire_callback` stays focused on per-queue-item delivery webhooks. |
| `QueueWorker._upsert_conversation(db, sender, item, result)` | `app/services/queue.py:672-729` | INSERTs new conversation row when no `(sender_id, contact_telegram_id)` match. Currently fills `ai_context_id` from `item.extra_data["ai_context_id"]` (Phase 3 D-06). | EXTEND: add `campaign_id` to INSERT, source = `item.campaign_id` (new NOT NULL column on `message_queue` per D-16 / Q1 resolution). Closes TODO #4. Plan 04-04. |
| `TelegramListener._send_to_ai` | `app/services/listener.py:247-305` | Buffers AI generation; calls `ai_engine.generate_response(context_id=ai_context_id, ...)` from `context["ai_context_id"]`. | EXTEND: `context["ai_context_id"]` becomes derived from `conversation.campaign_id` JOIN (or call `get_context_for_conversation(conversation_id)` and pass campaign_id to ai_engine). Closes TODO #8. Plan 04-05. |
| `TelegramListener._handle_antispam_signal(sender_info, bot_name, bot_id, message_text)` | `app/services/listener.py:813-879` | On SpamBot-like message: UPDATE `conversations.ai_enabled=false`, `paused_at=NOW()`, `paused_reason=...` for **all** active conversations of the sender; UPDATE `message_queue.status='failed'` for all pending/processing of that sender. Logs `🚨 ANTISPAM`. | KEEP as-is. Safety net runs **in parallel** with D-12 built-in `transfer_to_manager` — they do not conflict (antispam acts on sender-wide signal, built-in acts per-conversation). Plan 04-05 must NOT touch this. |
| `TelegramListener.send_document_to_webhook(file_path, file_name, ..., webhook_url)` | `app/services/listener.py:306-339` | Reads file, base64-encodes, POSTs to webhook_url with `{file_name, file_type, file_base64, conversation_id, contact_name, contact_telegram_id, timestamp}`. | KEEP function (might be reused if document handling returns later), but call-sites are dead in Phase 3 (D-01 dropped `document_webhook_url`). Plan 04-05 leaves it alone — comment at line 704-711 is permanent. |

## 3. Schedule Constants Inventory (queue.py)

**WILL BE REMOVED in Plan 04-03 (per-campaign rewrite):**

| Constant / helper | Location | Replaced by |
|--------------------|----------|-------------|
| `MOSCOW_TZ = zoneinfo.ZoneInfo("Europe/Moscow")` | `app/services/queue.py:63` | Per-campaign `zoneinfo.ZoneInfo(campaign.timezone)` resolved at tick time |
| `WORK_HOUR_START = 9` | `app/services/queue.py:64` | `campaigns.work_hour_start` column (default 9, D-09) |
| `WORK_HOUR_END = 20` | `app/services/queue.py:65` | `campaigns.work_hour_end` column (default 20, D-09) |
| `QueueWorker._is_working_hours()` | `app/services/queue.py:111-115` | Per-campaign check inside `_tick` (JOIN to `campaigns`, compute `(work_days_mask & (1 << weekday)) != 0` and `work_hour_start <= hour < work_hour_end` in the campaign's tz) |
| `QueueWorker._next_working_window()` | `app/services/queue.py:117-125` | Removed; soft-skip semantics — items just stay pending until next eligible tick (no rescheduling required) |

**MUST NOT BE TOUCHED in Phase 4 (CLAUDE.md "эмпирически подобраны"):**

| Constant | Location | Why protected |
|----------|----------|---------------|
| `MIN_SEND_INTERVAL = 20` | `app/services/queue.py:38` | Empirical inter-message delay floor |
| `MAX_SEND_INTERVAL = 55` | `app/services/queue.py:39` | Empirical inter-message delay ceiling |
| `SEND_INTERVAL_FATIGUE = 0.5` | `app/services/queue.py:41` | Empirical fatigue multiplier |
| `MAX_NEW_CONTACTS_PER_HOUR = 15` | `app/services/queue.py:47` | Empirical new-contact throttle |
| `MAX_ATTEMPTS = 3`, `RETRY_DELAY_SECONDS = 60` | `app/services/queue.py:48-49` | Empirical retry policy |
| `LONG_PAUSE_EVERY_MIN = 12` | `app/services/queue.py:53` | Empirical human-pause cadence floor |
| `LONG_PAUSE_EVERY_MAX = 25` | `app/services/queue.py:54` | Empirical human-pause cadence ceiling |
| `LONG_PAUSE_MIN_SECS = 180` | `app/services/queue.py:55` | Empirical human-pause duration floor |
| `LONG_PAUSE_MAX_SECS = 600` | `app/services/queue.py:56` | Empirical human-pause duration ceiling |
| `FLOOD_HARD_THRESHOLD = 300` | `app/services/queue.py:60` | Empirical FloodWait escalation point |
| Per-sender `rate_per_min/hour/day` (DB columns) | `senders` table | Already moved to per-sender storage in Phase 2 D-13 — NOT in code constants. Phase 4 does NOT touch defaults. |

**RECOMMENDATION for Plan 04-03:** When rewriting `_tick`, keep the comment at
`queue.py:45-46` (current 4-line block "NB: Other rate constants ... — NOT
TOUCHED per CLAUDE.md") and **update line 46** to remove `WORK_HOUR_*` from the
"not touched" list (since 04-03 explicitly DOES touch them) while keeping
`MIN_SEND_INTERVAL`, `LONG_PAUSE_*`, `FLOOD_HARD_THRESHOLD`.

## 4. Recovered `ai_contexts.webhook_functions` Shape

Migration 015 (`migrations/015_phase3.sql:13`) dropped the column:

```sql
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS webhook_functions;
```

The column was added in the initial import commit (`54430ec init: base from
telegram-api (internal v1)`). Recovered ORM declaration (pre-Phase 3, from
`git show 54430ec:app/models/__init__.py`):

```python
class AIContext(Base):
    __tablename__ = "ai_contexts"
    # ...
    webhook_functions = Column(JSONB, default=[])  # implied — column was JSONB
```

The Python code path was the source of truth for the **shape**. Recovered from
`git show 54430ec:app/services/ai_engine.py`:

```python
# In get_context() — column read as-is:
"webhook_functions": row[5] or []

# In build_tools(webhook_functions: list) — iteration shape:
for func in webhook_functions:
    for param in func.get("parameters", []):
        param_type = param.get("type", "string")
        properties[param["name"]] = {
            "type": param_type,
            "description": param.get("description", "")
        }
        if param.get("required", False):
            required.append(param["name"])
    # tool name + description:
    func["name"]
    func.get("description", "")

# In execute_webhook(func_config: dict, ...) — webhook delivery shape:
webhook_url = func_config.get("webhook_url")
func_name   = func_config.get("name", "unknown")
# (webhook_method не читался — всегда POST через httpx_client.post)
```

**Recovered canonical shape (this is the baseline for `campaigns.tools` JSONB
in Plan 04-02 migration + Pydantic `ToolSpec` in Plan 04-02 schemas):**

```json
[
  {
    "name": "save_lead",
    "description": "Save lead info to CRM",
    "parameters": [
      {
        "name": "phone",
        "type": "string",
        "description": "Contact phone in E.164",
        "required": true
      },
      {
        "name": "volume",
        "type": "number",
        "description": "Order volume in kg",
        "required": false
      }
    ],
    "webhook_url": "https://example.com/crm/leads",
    "webhook_method": "POST"
  }
]
```

**Important nuances for Plan 04-02 and 04-05 to honour:**

1. **`parameters` is an ARRAY of param-spec objects**, NOT an OpenAI-flavoured
   JSON Schema (`{type: "object", properties: {...}, required: [...]}`). The
   conversion to OpenAI's shape happens inside `build_tools` — `campaigns.tools`
   stores the **internal** array form. Pydantic `ToolSpec` (C-10) must validate
   the array shape, not the JSON-schema shape.
2. **`webhook_method`** was read by neither `build_tools` nor `execute_webhook`
   (the latter always uses `httpx_client.post(...)`). It is purely advisory in
   the legacy shape. **Decision for Plan 04-05:** drop `webhook_method` from the
   `ToolSpec` Pydantic model (always POST) OR include it as `Literal["POST"]`
   with a default for future-proofing — recommend the latter, no extra work.
3. **`webhook_url`** is OPTIONAL in the legacy code (`func_config.get("webhook_url")`
   returns `None` and logs a warning — does not raise). Pydantic validator
   should require it on create (422 if missing) so we surface errors at the API
   layer, not at LLM call time.
4. **No nested `parameters.properties`** in storage — flat array of `{name,
   type, description, required}`. Param `type` values seen in legacy: string,
   number (loosely OpenAI JSON Schema primitive types). No enum / array /
   object types were used in production webhook functions per CONCERNS.md
   absence of complaints. Plan 04-02 should accept the canonical JSON Schema
   primitive type set as `Literal["string","number","integer","boolean"]` — or
   keep it as free-form string for forward compatibility (Lovable already ships
   the editor matching this set). Recommend `Literal[...]` for v1 strictness.

## 5. Signal Handling Inventory

Current signal flow (how AI gets paused today, pre-Phase 4):

| Signal source | Mechanism | Files | State after action |
|---------------|-----------|-------|---------------------|
| **SpamBot / antispam-like sender** | `_handle_antispam_signal` is invoked from `handle_incoming_message` when message comes from a known antispam bot (heuristic, exact whitelist lives in listener) | `app/services/listener.py:813-879` | UPDATE `conversations.ai_enabled=false`, `paused_at=NOW()`, `paused_reason='Auto-disabled: antispam signal...'` for ALL active convos of that sender. UPDATE `message_queue.status='failed'`, `error_message='Auto-cancelled: antispam...'` for ALL pending/processing of that sender. |
| **Manual operator pause** | UI sets `conversations.ai_enabled=false` directly (Phase 5 INBX-04 will own this) | `app/models/__init__.py:228-232` | Same flag, no automatic queue cancellation. |
| **`conversations.status` column** | `String(20)` default `'active'`, server_default `'active'`. Currently used values: `'active'`, `'manual'`, `'paused'`. No CHECK constraint in DB. | `app/models/__init__.py:230` | Listener `handle_incoming_message:759` gates AI: `if conv["ai_enabled"] and conv["status"] == "active"`. Anything other than `'active'` silences AI. |
| **`paused_at`, `paused_reason`** | TIMESTAMPTZ + TEXT, both nullable | `app/models/__init__.py:231-232` | Written by `_handle_antispam_signal`. Phase 4 D-12 will write them on `transfer_to_manager` / `finish_conversation` built-in tool calls. |

**Phase 4 additions (per D-12 / C-13):**

- `conversations.status` extended with values `'lead'`, `'handoff'`, `'finished'`.
  Per Pitfall 2 (RESEARCH.md) — implemented as **`String(20)` + CHECK constraint**
  in migration 016, NOT as PostgreSQL ENUM type. CHECK clause:
  `status IN ('active','manual','paused','lead','handoff','finished')`.
  Reasoning: ALTER TYPE ADD VALUE cannot run inside a transaction block; CHECK
  constraint can be replaced with `DROP CONSTRAINT IF EXISTS … ADD CONSTRAINT
  … CHECK …` idempotently in one migration script. **This is a direct
  override of CONTEXT.md D-04 "SQLEnum, не String" — see Section 6 Q6 below.**
- New write paths from `ai_engine.generate_response` built-in tool dispatch:
  - `mark_as_lead(reason)` → `status='lead'`, `ai_enabled` UNCHANGED (lead is a marker, conversation continues), POST `lead_webhook_url` if set.
  - `transfer_to_manager(reason)` → `status='handoff'`, `ai_enabled=false`, `paused_at=NOW()`, `paused_reason=reason`, POST `handoff_webhook_url` if set.
  - `finish_conversation(reason)` → `status='finished'`, `ai_enabled=false`, `paused_at=NOW()`, `paused_reason=reason`, POST `finish_webhook_url` if set.
- `_handle_antispam_signal` (existing) **remains untouched** — runs in parallel
  with the new built-in flow. It is the last-line safety net for sender-wide
  attack scenarios; the new built-in tools operate at per-conversation
  granularity.

## 6. Open Question Resolutions

The 5 Open Questions from RESEARCH.md `## Open Questions` plus one D-override
discovered while writing this audit. **All locked-in for downstream planners:**

| # | Question | Resolution | Affects plan |
|---|----------|------------|--------------|
| **Q1** | `message_queue.campaign_id` NULL vs NOT NULL | **NULLable** (overrides CONTEXT.md D-16 "NOT NULL"). Rationale: D-07 hard delete of `done` campaigns with FK `ON DELETE SET NULL` requires NULLable on the child column. The alternative (NO ACTION / RESTRICT with a 409 if any queue items exist) blocks hard delete forever in practice — every `done` campaign accumulates historical queue rows. UX breaks. The CONTEXT.md "БД чистая → NOT NULL применим сразу" argument is true on day-one but wrong for the lifetime of the data. **Decision: NULLable + `ON DELETE SET NULL`.** Composite index per D-16 stays: `(workspace_id, campaign_id, status, scheduled_at)` — works fine with NULL values (or use a partial index `WHERE campaign_id IS NOT NULL` to keep it small). | **04-02** (migration 016) |
| **Q2** | Include `POST /campaigns/{id}/duplicate` in Phase 4? | **YES**, in Plan 04-02. Semantics (per C-11): copy `campaigns` row + `campaign_senders` rows. New name = `"{name} (copy)"` (retry-on-IntegrityError pattern from Phase 3 `duplicate_agent`). `status='draft'`. **Do NOT** copy `message_queue` items, **do NOT** copy `campaign_contact_assignments` (those are runtime rotation state, not template). | **04-02** |
| **Q3** | LLM returning `text_content + tool_call` simultaneously when calling `finish_conversation` — send the text or skip it? | **Send the `text_content` to the contact BEFORE flipping `ai_enabled=false`**. This delivers the LLM's farewell line and produces a clean UX. Order: (1) deliver `response_message.content` via `client.send_message`, (2) UPDATE conversations.status='finished' + ai_enabled=false + paused_at, (3) fire webhook. If `response_message.content` is empty/None — skip step 1 silently. Same rule for `transfer_to_manager`. For `mark_as_lead` — always send text (conversation continues). | **04-05** |
| **Q4** | Workspace-isolation guarantee on `campaign_senders` | **API-level validation + DB-level NOT NULL workspace_id**: `POST /campaigns` (and `PATCH /campaigns/{id}` if senders[] is mutable) validates that every `sender_id` in payload satisfies `senders.workspace_id == ctx.workspace_id` before INSERT into `campaign_senders` (Phase 1 D-04 pattern). Defence-in-depth: `campaign_senders.workspace_id UUID NOT NULL FK CASCADE`, and the INSERT always fills it from `campaigns.workspace_id`. Cross-workspace sender attach attempts → 422 with explicit error. | **04-02** |
| **Q5** | `CampaignEnqueueWorker` partial failure semantics — what if INSERT into `campaign_contact_assignments` succeeds but INSERT into `message_queue` fails? | **One database transaction per contact**. Both INSERTs go inside `async with db.begin():`. If anything raises — full rollback. On the next tick the worker re-selects the same contact (it's still NOT IN `campaign_contact_assignments`) and retries. UNIQUE `(campaign_id, contact_phone)` protects against concurrent worker races. Batch size = 500 per campaign per tick (env-configurable per D-17). | **04-04** |
| **Q6** (new — overrides CONTEXT.md D-04) | `campaigns.status` type: PostgreSQL `SQLEnum` or `VARCHAR(20)` + CHECK constraint? | **`VARCHAR(20)` + CHECK constraint** (overrides CONTEXT.md D-04 "SQLEnum, не String"). Rationale: (a) PostgreSQL `ALTER TYPE ... ADD VALUE` cannot run inside a transaction block (RESEARCH.md Pitfall 2) — every future status addition would break migration idempotency. (b) `conversations.status` already follows this pattern (Phase 3 String) and gets the same treatment in migration 016 for the new `lead`/`handoff`/`finished` values — homogeneity. (c) Lovable UI works with raw status strings — no API-layer benefit from SQLEnum. (d) CONCERNS.md C-13 explicitly lists "String + CHECK" as a valid option. (e) `senders.role` already follows String+CHECK (Phase 2 D-21 carry-over) — convention is established. **Decision: `VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','running','paused','done'))`**. If a v2 refactor wants SQLEnum across the board, a single migration step (`CREATE TYPE … AS ENUM; ALTER TABLE … TYPE …`) can do it for all three columns at once. | **04-02** (migration 016) |

## 7. Anti-Patterns Defence (DO NOT TOUCH)

Hard-locked list — Plans 04-02..04-05 MUST NOT modify any of these, even if
they appear to overlap with Phase 4 changes:

- **Empirical rate constants in `app/services/queue.py`** (CLAUDE.md hard rule):
  `MIN_SEND_INTERVAL`, `MAX_SEND_INTERVAL`, `SEND_INTERVAL_FATIGUE`,
  `MAX_NEW_CONTACTS_PER_HOUR`, `MAX_ATTEMPTS`, `RETRY_DELAY_SECONDS`,
  `LONG_PAUSE_EVERY_MIN`, `LONG_PAUSE_EVERY_MAX`, `LONG_PAUSE_MIN_SECS`,
  `LONG_PAUSE_MAX_SECS`, `FLOOD_HARD_THRESHOLD`. Lines 38-60 of queue.py.
- **`FloodWaitError` / `PEER_FLOOD` retry logic** in `_send_item` (queue.py) —
  CLAUDE.md "Retry-логика FloodWait: не ломать без явной просьбы".
- **`DEFAULT_SYSTEM_PROMPT`** in `app/services/ai_engine.py:30-41` —
  AGS Foods brand-leak per CONCERNS.md. NOT a Phase 4 concern. Defer to a
  separate brand-cleanup phase or v2.
- **`gpt-5-mini-2025-08-07` model ID** at `app/services/ai_engine.py:307`
  and `:379` — CONCERNS.md "Known Bugs". Phase 4 assumes this is fixed via
  `app/config.py` before execution; Plan 04-05 may surface a TODO comment but
  must NOT rewrite the model string as part of campaign wiring.
- **`_handle_antispam_signal`** in `app/services/listener.py:813-879` —
  remains as safety net in parallel with the new D-12 built-in tools. Plan
  04-05 references it as a pattern for `transfer_to_manager` but **does not
  modify** the antispam handler itself.
- **`warmup.py` MOSCOW_TZ / MOSCOW_OFFSET** — CONCERNS.md "Hardcoded Moscow
  timezone (two separate places)" — Phase 4 only touches `queue.py`. Warmup
  is a separate inter-sender feature (warmup messages don't have a campaign
  context); deferred indefinitely or to v2.
- **`process.env.SUPABASE_*`, `OPENAI_API_KEY`** — environment variables, not
  changed.
- **Telethon abstraction (`telegram_service.send_message`)** — Plan 04-04
  uses it without modification. Sender lifecycle, encryption, FloodWait
  handling all preserved.
- **`app/services/encryption.py`** (Fernet session encryption) — not touched.
- **`docker-compose.yml` core services** — no new containers in Phase 4.
  `CampaignEnqueueWorker` is an in-process worker inside the API container.
  Listener container does NOT need to be rebuilt unless Plan 04-05 changes
  listener code (it does — minor JOIN tweaks — so `docker compose up -d
  --build api listener` is the final deploy command).

## 8. Decisions for Plans 04-02..04-05

| Plan | Scope this plan OWNS | Sections from this AUDIT it reads |
|------|----------------------|-----------------------------------|
| **04-02** (model + lifecycle + CRUD) | • Migration 016 (entire DDL): `campaigns`, `campaign_senders`, `campaign_contact_assignments`, `+conversations.campaign_id` (NULLable), `+message_queue.campaign_id` (NULLable per Q1), DROP `context_contact_assignments`, extend `conversations.status` CHECK constraint with `lead/handoff/finished`, **`campaigns.status VARCHAR(20)+CHECK` per Q6 not SQLEnum**. <br>• ORM models: `Campaign`, `CampaignSender`, `CampaignContactAssignment`; remove `ContextContactAssignment`; update `Conversation.status` comment to reflect new values; update `MessageQueue` (+campaign_id). <br>• Pydantic: `CampaignCreate`, `CampaignUpdate`, `CampaignResponse`, `CampaignListResponse`, `CampaignStatusUpdate`, `ToolSpec` (per Section 4 — array param shape, not JSON Schema). <br>• Router `app/routers/campaigns.py`: CRUD + `POST /start /pause /resume /finish /duplicate`. Sender lock check on `/start` and `/resume` per RESEARCH Pattern 5. Workspace-scoped per Q4. <br>• `is_exhausted` computed boolean in `CampaignResponse`. <br>• Closure of TODO #1 (real campaign_count COUNT), #2 (block agent DELETE on active campaign), #3 (block folder DELETE on active campaign). <br>• Micro-edit of REQUIREMENTS.md CAMP-14 (already done at CONTEXT.md commit per D-13). | **§1, §2, §4, §6 (Q1, Q2, Q4, Q6)** |
| **04-03** (schedule per-campaign) | • Schedule columns on campaigns (`timezone`, `work_hour_start/end`, `work_days_mask`, `start_date`, `stop_date`) — these may be folded into Plan 04-02's migration 016 per CONTEXT.md C-07. Planner 04-03 decides at start whether to write its own 016b or extend 016. **Recommendation: fold into 04-02's 016** — schedule is +6 columns and CHECK constraints on the same table. <br>• Rewrite `_tick` and `_process_next_for_sender` to per-campaign JOIN (read `campaigns.timezone, work_hour_start/end, work_days_mask, stop_date`). <br>• Remove `MOSCOW_TZ`, `WORK_HOUR_START/END`, `_is_working_hours()`, `_next_working_window()` per Section 3. <br>• `pyproject.toml`-level pytest job for the new schedule fixtures. | **§3, §6** |
| **04-04** (queue rewrite + enqueue worker + send.py) | • `app/services/campaign_enqueue.py` — `CampaignEnqueueWorker` per RESEARCH Pattern 2 (singleton in module, start/stop in lifespan). <br>• `app/services/template.py` — `render_template(template, contact)` per D-18/D-19 with Mustache regex (RESEARCH C-03). <br>• `app/services/rotation.py` — `get_or_assign_sender` signature `context_id` → `campaign_id`, pool source = `campaign_senders` not workspace-wide. Closes TODO #6. <br>• `app/services/queue.py` — `enqueue_message` accepts `campaign_id` (drops `ai_context_id` parameter per D-16; agent_id is derivable via JOIN). `_upsert_conversation` adds `campaign_id` to INSERT. Closes TODO #4 + #5. <br>• `enqueue_file` — same `campaign_id` propagation (B1 revision). Closes TODO #5. <br>• `app/routers/send.py` — rewrite body schema: `campaign_id: UUID` instead of `ai_context_id: UUID`. agent_id derived from `campaigns.agent_id` via JOIN. <br>• Workspace isolation guard per Q4 / Pitfall 8 in CampaignEnqueueWorker SELECT and INSERTs. <br>• Race handling per Q5 (one transaction per contact, UNIQUE protects). | **§1, §2, §5, §6 (Q1, Q5)** |
| **04-05** (signals + webhooks + tools) | • `app/services/ai_engine.py` — `get_context` → `get_context_for_conversation(conversation_id)` that JOINs to campaigns. Reads `tools`, `lead/handoff/finish_trigger_hint`, `lead/handoff/finish_webhook_url`. <br>• `build_tools` extended: prepend 3 built-in tool specs (RESEARCH Pattern 4); descriptions sourced from campaign trigger_hints with fallback restrictive default per RESEARCH Pitfall 7. <br>• `generate_response` tool_call loop: dispatch by name — built-in → new branch (UPDATE conversation + fire webhook, **no second LLM call** if handoff/finish fired, see Q3); custom → existing `execute_webhook`. Parallel tool_calls handled per RESEARCH Pitfall 1 (priority: finish > handoff > lead). <br>• `app/services/webhook_notify.py` — new fire-and-forget helper for the 3 campaign-level webhooks (separate from `_fire_callback` which stays focused on per-queue-item delivery). Payload shape per RESEARCH C-01 (planner decides exact fields). <br>• Closes TODO #7 (ai_engine.py:88), #8 (listener.py:250), #9 (listener.py:350), #10 (listener.py:707 — delete TODO + permanent comment, do NOT restore document_webhook_url). | **§2, §4, §5, §6 (Q3)** |

## 9. Acceptance Notes for Downstream

- All numeric file:line references in this audit are verified against
  working-tree HEAD `60cfaeb` on 2026-05-22. Downstream planners must
  re-verify if more than ~5 commits land on `main` before their execution.
- The `webhook_functions` shape in Section 4 is the **internal storage**
  shape, not the OpenAI API shape. Pydantic `ToolSpec` and migration 016
  JSONB validation must mirror Section 4's array form.
- Sections 6 Q1 and Q6 contain **explicit overrides** of CONTEXT.md decisions
  (D-16 NOT NULL → NULLable; D-04 SQLEnum → VARCHAR+CHECK). Plan 04-02 MUST
  follow this audit's resolution, not the CONTEXT.md baseline, when there is
  conflict. The CONTEXT.md document is not edited (no retroactive rewriting);
  this AUDIT.md is the authoritative source for these two points.
- Open Question resolutions in Section 6 are intentionally short. Detailed
  arguments live in RESEARCH.md (Pitfalls 2, 4, 7, 8) — planners should
  cross-read RESEARCH for full rationale.

---

*Phase: 04-campaigns*
*Audit completed: 2026-05-22*
*Downstream plans must read this file via `@.planning/phases/04-campaigns/04-01-AUDIT.md` in their `<context>`.*
