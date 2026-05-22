---
phase: 05-inbox-analytics
verified: 2026-05-22T00:00:00Z
status: human_needed
score: 11/11 must-haves verified in code (6/6 success criteria backed by code; behavioral confirmation needs live deploy)
re_verification: null
human_verification:
  - test: "Run `pytest tests/test_phase5_*.py -x -q` server-side on VPS after deploy"
    expected: "All ~94 Phase 5 tests pass (migration_017: 11, inbox: 14, manager_mode: 5, send_takeover: 6, bot_filter: 12, analytics: 12, analytics_correctness: 8, llm_logger: 11, llm_logger_no_block_on_error: 3, llm_calls_endpoint: 8, plus regression in migration_016 / campaign_router / ai_engine)"
    why_human: "Mac dev box has no Python venv / Docker; tests run server-side on deploy per CLAUDE.md. All 3 SUMMARYs explicitly note this deviation."
  - test: "Apply migration 017 to fresh DB and verify `\\d+ conversations`, `\\d+ llm_calls`, and `\\d+ messages` schemas"
    expected: "conversations_status_check shows 7 values incl 'bot_ignored'; llm_calls has 15 columns + 2 indexes; 3 composite indexes on conversations present; messages table exists with workspace_id col"
    why_human: "Needs live PG; migration is idempotent so safe to re-run"
  - test: "From Lovable UI: open inbox, send a message from manager UI on an active dialog"
    expected: "Dialog flips to status='manual', ai_enabled=false, paused_reason='Manager sent message via UI'; any pending queue items for that recipient_phone become status='failed'; manager message arrives in Telegram"
    why_human: "End-to-end UX flow with real Telethon + UI — D-04 auto-takeover behavior"
  - test: "Send a Telegram message to a sender from a verified bot account (e.g. SpamBot at id=178220800 or any @BotFather bot)"
    expected: "Listener creates conversation status='bot_ignored', ai_enabled=false, stores inbound message, ai_engine.generate_response NOT invoked; for 178220800 specifically delegate to _handle_antispam_signal (D-08 safety net) pauses sender lifecycle and cancels ALL queue items for that sender"
    why_human: "Requires live Telegram bot interaction"
  - test: "Hit GET /api/v1/analytics/workspace with auth_headers on a workspace with seeded sent/replied/lead/finished data"
    expected: "Response {sent, replied:{conversation_count, message_count}, leads, finishes} reflects only that workspace's counts; bot_ignored conversations excluded; leads strict EQ (does NOT include finished)"
    why_human: "Behavioral verification of D-13 real-time COUNT + D-15 two figures + Pitfall 8/9; correctness tests exist but need live execution"
  - test: "Trigger AI response on a dialog, then hit GET /api/v1/conversations/{id}/llm-calls"
    expected: "Response shows 1 row per turn (or 2 if custom tools fired second OpenAI call); prompt JSONB has full request_params, response_text matches AI reply, prompt_tokens/completion_tokens/total_tokens populated, latency_ms ~= actual round-trip"
    why_human: "Requires live OpenAI call through generate_response; warmup-LLM calls must NOT appear (D-12)"
  - test: "Inspect application logs (docker logs api 2>&1 | grep -i prompt) after a trigger of generate_response with system prompt containing distinctive text"
    expected: "Distinctive prompt text NEVER appears in logs; only conversation_id + exception text on errors (T-05-03-PROMPT-LEAK)"
    why_human: "Grep verification of llm_logger.py shows 0 matches, but live log inspection confirms no leak path through pydantic validation or other indirect log channels"
---

# Phase 5: Inbox & Analytics Verification Report

**Phase Goal:** Клиент видит входящие диалоги с фильтром по кампании, переключает на ручник и смотрит метрики по уровням (workspace / campaign / agent / sender) + лог LLM-запросов на уровне диалога.

**Verified:** 2026-05-22
**Status:** human_needed (all code wired and structurally correct; behavioral confirmation requires live deploy + pytest run on VPS)
**Re-verification:** No — initial verification
**Score:** 11/11 must-haves verified in code; 6/6 success criteria backed by code artifacts

---

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| 1 | Inbox shows all workspace dialogs with message history and AI status (active / paused / manager / lead / finish) | VERIFIED | `app/routers/conversations.py:87-186` GET /api/v1/conversations + `:189-271` detail + messages history endpoints; status field present in `ConversationResponse` schema; migration 017 extends CHECK to 7 values |
| 2 | From inbox, can switch a dialog to manager mode (AI disabled for that dialog) | VERIFIED | `app/routers/conversations.py:313-350` POST /disable-ai (D-01: status='manual' + cancel queue D-02); `:379-488` POST /send auto-takeover D-04; `:353-376` enable-ai D-03 preserves status |
| 3 | Dialog filter by campaign, agent, TG-account is available | VERIFIED | `app/routers/conversations.py:88-94, 115-123` Query params campaign_id / agent_id / sender_id all wired into WHERE clauses; 3 composite indexes from migration 017 back them |
| 4 | AI does not reply to system bots (SpamBot etc.) — listener-side filter | VERIFIED | `app/services/listener.py:117` ANTISPAM_BOT_IDS module-level; `:606-621` proactive `getattr(sender, 'bot', False)` filter with ID delegation; `:925-1005` `_handle_bot_message` with Pitfall-3 UPDATE guard |
| 5 | Dashboard shows metric cards at 4 levels: workspace / campaign / agent / sender | VERIFIED | `app/routers/analytics.py:195-247` 4 endpoints all returning identical `AnalyticsCards`; `_compute_cards` helper at `:102-189` with 4 raw-SQL COUNTs |
| 6 | Each dialog has an LLM call log (prompt → response) for debugging | VERIFIED | `app/services/llm_logger.py:30-159` never-raise log_llm_call; `app/services/ai_engine.py:664-682, 803-827` wraps 2 OpenAI calls; `app/routers/conversations.py:510-558` GET /llm-calls read endpoint |

**Score:** 6/6 success criteria backed by code artifacts. All observable truths require live deploy for behavioral confirmation.

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `migrations/017_phase5.sql` | Idempotent migration: extend conversations.status CHECK to 7 values, create llm_calls (15 cols), 5 indexes, defensive messages table | VERIFIED | 79 lines, BEGIN/COMMIT, `DROP CONSTRAINT IF EXISTS` + `CREATE TABLE IF NOT EXISTS` everywhere; CHECK literal includes 'bot_ignored'; 2 llm_calls indexes + 3 conversation composite indexes + 2 messages indexes |
| `app/models/__init__.py` LLMCall | ORM class with 15 columns, FK CASCADE on workspace/conversation, SET NULL on campaign/agent/sender | VERIFIED | `class LLMCall(Base)` at line 530 |
| `app/schemas/__init__.py` 11 Phase 5 schemas | ConversationResponse/List/Update + MessageResponse/List + SendMessageFromUI Req/Resp + AnalyticsReplied/Cards + LLMCallResponse/List | VERIFIED | All 11 classes + CONVERSATION_STATUSES set present; lines 594-748 |
| `app/routers/conversations.py` | Full rewrite, 8+1 endpoints under Depends(auth_dep), workspace-scoped | VERIFIED | 558 lines, 9 endpoints, 10 `Depends(auth_dep)` (one per endpoint + recursive call); 0 references to legacy `is_active` or `verify_api_key` |
| `app/routers/analytics.py` | 4 endpoints + _compute_cards + 3 prechecks + _ALLOWED_SCOPE_COLUMNS whitelist | VERIFIED | 247 lines; 4 `@router.get`; 4 `Depends(auth_dep)`; `_ensure_*_in_workspace` for campaign/agent/sender; `_ALLOWED_SCOPE_COLUMNS = {"campaign_id", "ai_context_id", "sender_id"}` |
| `app/services/llm_logger.py` | log_llm_call coroutine, isolated AsyncSessionLocal, try/except SQLAlchemyError + bare Exception, never-raise | VERIFIED | 168 lines; `async def log_llm_call`; `async with AsyncSessionLocal()`; both exception handlers present; `_safe_jsonify` uses ensure_ascii=False + default=str |
| `app/services/ai_engine.py` wraps | 2 OpenAI calls wrapped in time.perf_counter + try/except/finally + inline await log_llm_call | VERIFIED | `from app.services.llm_logger import log_llm_call` at line 31; 2 `await log_llm_call` calls at lines 674 + 819; 2 `time.perf_counter()` calls; both in finally blocks |
| `app/services/listener.py` bot filter | Proactive bot filter via `getattr(sender, 'bot', False)`, delegation by ANTISPAM_BOT_IDS, isolated _handle_bot_message | VERIFIED | ANTISPAM_BOT_IDS module-level at line 117; proactive check at lines 606-621; _handle_bot_message at line 925 with Pitfall-3 UPDATE guard (only downgrade from 'active') |
| `app/services/queue.py` pre-send guard | SELECT ai_enabled BEFORE Telethon send; SKIP send + flip queue item to 'failed' if false | VERIFIED | Lines 528-567 inside `__send_item_inner`; one SELECT + one UPDATE; empirical rate-limit constants untouched per CLAUDE.md |
| `app/main.py` includes | `include_router(conversations.router)` + `include_router(analytics.router)` registered | VERIFIED | Lines 101-102 in main.py; both registered after campaigns |

### Key Link Verification

| From | To | Via | Status |
| ---- | -- | --- | ------ |
| `app/main.py` | `app/routers/conversations.py` | `app.include_router(conversations.router)` | WIRED (line 101) |
| `app/main.py` | `app/routers/analytics.py` | `app.include_router(analytics.router)` | WIRED (line 102) |
| `app/routers/conversations.py POST /send` | `app/services/telegram.py send_message_by_telegram_id` | direct call after workspace + lifecycle check | WIRED (line 452) |
| `app/routers/conversations.py POST /send + /disable-ai` | `message_queue` table | `UPDATE message_queue SET status='failed' ... 'Conversation taken over manually'` | WIRED (lines 337-347, 437-447) |
| `app/services/listener.py handle_incoming_message` | `_handle_bot_message` OR `_handle_antispam_signal` | `getattr(sender,'bot',False) is True` + ID-based delegation to ANTISPAM_BOT_IDS | WIRED (lines 606-621) |
| `app/services/queue.py __send_item_inner` | conversations table | `SELECT ai_enabled FROM conversations WHERE workspace_id AND sender_id AND contact_phone` | WIRED (lines 538-548) |
| `app/services/ai_engine.py generate_response` (point #1) | `app/services/llm_logger.py log_llm_call` | inline `await log_llm_call(...)` in finally block at line 674 | WIRED |
| `app/services/ai_engine.py generate_response` (point #2 tool result summary) | `app/services/llm_logger.py log_llm_call` | inline `await log_llm_call(...)` in finally block at line 819 | WIRED |
| `app/services/llm_logger.py log_llm_call` | llm_calls + conversations tables | SELECT conversations FK denormalisation + INSERT llm_calls via raw SQL with `::jsonb` casts | WIRED (lines 62-145) |
| `app/routers/conversations.py GET /llm-calls` | llm_calls table | SELECT WHERE conversation_id AND workspace_id (defence-in-depth after _load_conversation_or_404) | WIRED (lines 510-558) |
| `app/routers/analytics.py /compute_cards` | conversations + messages tables | 4 raw-SQL COUNTs with workspace-first WHERE + scope_clause whitelist | WIRED (lines 135-176) |

### Data-Flow Trace (Level 4)

Backend project — data flows through HTTP → router → SQL → DB. No JSX/React rendering layer in this repo (frontend is separate Lovable). Each endpoint's data source is a parameterised raw SQL query against PostgreSQL with workspace_id binding; no static fallbacks observed.

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| GET /conversations | `rows`, `total` | `list_query` + `count_query` raw SQL with LATERAL JOINs against conversations + messages | Real (DB-bound; no static `[]` fallback — empty result returns Pydantic list with `total=0`) | FLOWING |
| GET /conversations/{id}/llm-calls | `rows`, `total` | SELECT FROM llm_calls WHERE workspace_id AND conversation_id ORDER BY created_at DESC | Real (writes flow from ai_engine wraps; reads come back via same workspace filter) | FLOWING |
| GET /analytics/workspace | `AnalyticsCards` fields | 4 raw COUNT queries from `_compute_cards` against messages+conversations | Real (no fallbacks; `or 0` only protects against NULL scalar from empty COUNT) | FLOWING |
| POST /conversations/{id}/send | `result` from telegram_service | `await telegram_service.send_message_by_telegram_id(...)` real Telethon call | Real (post-commit Telethon; INSERT messages with sent_by='human' on success) | FLOWING |
| log_llm_call INSERT | llm_calls row | Reads response.choices[0].message.content/tool_calls/usage from OpenAI response; serializes request_params dict | Real on success, NULL on OpenAI error (response=None + error text set) | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Conversations router includes 9 endpoints | `grep -c "^@router\\." app/routers/conversations.py` | 9 | PASS |
| Analytics router includes 4 endpoints | `grep -c "^@router\\." app/routers/analytics.py` | 4 | PASS |
| All endpoints scoped under Depends(auth_dep) | `grep -c "Depends(auth_dep)" app/routers/conversations.py app/routers/analytics.py` | 10+4 (>= endpoint count) | PASS |
| No legacy is_active references in new router | `grep -c "is_active" app/routers/conversations.py` | 0 | PASS |
| No legacy verify_api_key in new router | `grep -c "verify_api_key" app/routers/conversations.py` | 0 | PASS |
| log_llm_call wired in 2 places in ai_engine | `grep -c "await log_llm_call" app/services/ai_engine.py` | 2 | PASS |
| Warmup NOT wrapped (D-12) | `grep -c "log_llm_call\\|llm_logger" app/services/warmup.py` | 0 | PASS |
| Prompt-leak guard (T-05-03-PROMPT-LEAK) | `grep -E "logger\\.(info\|warning\|error\|debug).*\\bprompt\\b" app/services/llm_logger.py app/services/ai_engine.py` | 0 matches | PASS |
| ANTISPAM_BOT_IDS module-level | `grep -c "^ANTISPAM_BOT_IDS = {" app/services/listener.py` | 1 | PASS |
| Bot filter checks `getattr(sender, 'bot', False)` | `grep -c "getattr(sender, 'bot', False)" app/services/listener.py` | 1 | PASS |
| Pre-send guard SELECT in queue.py | `grep "SELECT ai_enabled FROM conversations" app/services/queue.py` | line 539 | PASS |
| Migration 017 idempotent guards | `grep -c "IF NOT EXISTS\\|IF EXISTS" migrations/017_phase5.sql` | 9 | PASS |
| 7-value status CHECK | `grep "CHECK (status IN" migrations/017_phase5.sql` | active/manual/paused/lead/handoff/finished/bot_ignored | PASS |
| 3 composite indexes for analytics | `grep "idx_conversations_workspace_" migrations/017_phase5.sql` | 3 indexes (campaign/agent/sender) | PASS |
| llm_calls 15 columns | `grep -c "^    " migrations/017_phase5.sql` (col defs in llm_calls block) | 15 | PASS |
| Two-figure replied (D-15) single SELECT | `grep -c "COUNT(DISTINCT m.conversation_id)" app/routers/analytics.py` | 2 (one query, helper docstring) | PASS |
| Pitfall 8 bot_ignored exclusion | `grep -c "c.status != 'bot_ignored'" app/routers/analytics.py` | 6 (one per of 4 COUNTs + 2 in leads/finishes for uniformity) | PASS |
| Test files exist (10 files) | `ls tests/test_phase5_*.py` | 10 files | PASS |
| Commits exist (8 commits for plans + meta) | `git log --oneline` | ba0b57f / ada61fb / 1e6b369 / 1b6979d / 2e41600 / 1a90116 / 798ad9a / b2680c3 + 2 docs commits | PASS |

**Note:** pytest itself cannot be run locally on Mac dev box (no Python venv, no Docker — per CLAUDE.md the project runs on VPS). All 3 SUMMARYs flag this; tests will execute server-side on next deploy. See `human_verification` items above.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| INBX-01 | 05-01 | Пользователь видит все входящие диалоги своего workspace | SATISFIED | `conversations.py:list_conversations` workspace-scoped LIST endpoint with `c.workspace_id = :wid` clause |
| INBX-02 | 05-01 | В каждом диалоге видна история сообщений | SATISFIED | `conversations.py:get_conversation` + `get_messages` workspace-scoped detail + paginated message history |
| INBX-03 | 05-01 | Виден статус AI диалога (active/paused/manager/lead/finish) | SATISFIED | Migration 017 extends CHECK to 7 values; `status` field present in `ConversationResponse`; PATCH endpoint validates against `CONVERSATION_STATUSES` set |
| INBX-04 | 05-01 | Ручное переключение в режим менеджера | SATISFIED | POST /disable-ai (D-01) + POST /enable-ai (D-03 preserves historic status) + POST /send auto-takeover (D-04) all implemented |
| INBX-05 | 05-01 | Фильтр по кампании / агенту / TG-аккаунту | SATISFIED | `list_conversations` accepts `campaign_id` / `agent_id` / `sender_id` Query params; 3 composite indexes from migration 017 back them |
| AIRC-04 | 05-01 | AI не отвечает системным ботам — listener-side фильтр | SATISFIED | `listener.py` proactive `getattr(sender, 'bot', False)` filter at line 606; ANTISPAM_BOT_IDS delegation preserves D-08 safety net; new `_handle_bot_message` with Pitfall-3 UPDATE guard |
| ANLX-01 | 05-02 | Метрики workspace | SATISFIED | `analytics.py:workspace_analytics` → `_compute_cards(scope=None)` returns AnalyticsCards |
| ANLX-02 | 05-02 | Метрики кампании | SATISFIED | `analytics.py:campaign_analytics` with `_ensure_campaign_in_workspace` precheck + scope=("campaign_id", id) |
| ANLX-03 | 05-02 | Метрики TG-аккаунта (sender) | SATISFIED | `analytics.py:sender_analytics` with `_ensure_sender_in_workspace` precheck + scope=("sender_id", id). Note: per D-16, sender errors lifecycle/auth data lives on `/api/v1/senders` (Phase 2), not duplicated here; analytics endpoint returns identical AnalyticsCards shape per spec |
| ANLX-04 | 05-02 | Метрики агента | SATISFIED | `analytics.py:agent_analytics` with `_ensure_agent_in_workspace` precheck + scope=("ai_context_id", id) |
| ANLX-05 | 05-03 | Лог запросов в OpenAI на уровне диалога | SATISFIED | `llm_logger.py` never-raise log_llm_call; `ai_engine.py` wraps 2 OpenAI calls; `conversations.py:get_llm_calls` reads with defence-in-depth workspace isolation |

**Orphaned requirements:** None. All 11 IDs declared in PLAN frontmatters match REQUIREMENTS.md phase mapping.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `app/services/listener.py` | 626-637 | Backup keyword detection for antispam (after Phase 5 proactive bot filter) | Info | Intentional — comment marks it as "Backup path: keyword detection ловит ботов которые не выставляют event.sender.bot=True". Belt-and-suspenders for D-08 safety net |
| `migrations/017_phase5.sql` | 19-30 | Defensive CREATE TABLE messages — DDL not from migrations 001-016 | Info | Documented in SUMMARY 05-01 as Deviation #1 (auto-fixed). Idempotent `IF NOT EXISTS` makes it no-op on production; required for fresh test DBs |
| `app/routers/analytics.py` | 165, 174 | `c.status != 'bot_ignored'` redundant in leads/finishes WHERE (they already filter status='lead'/'finished') | Info | Plan 05-02 SUMMARY explicitly notes: "kept for uniformity — keeps the exclusion semantic explicit on every aggregate, making future copy-paste safer" |
| All Phase 5 tests | n/a | Cannot run pytest locally on Mac dev box | Warning | Process deviation noted in all 3 SUMMARYs. Tests must be executed on VPS post-deploy. AST-parse + grep acceptance criteria all pass |

No blocker anti-patterns. No TODO/FIXME placeholders. No stubs. No `return null`/`return []` with disconnected callers. No `console.log`-only handlers.

### Human Verification Required

See `human_verification` block in frontmatter. Seven items:

1. **Run `pytest tests/test_phase5_*.py -x -q` on VPS** — verify ~94 Phase 5 tests all green
2. **Apply migration 017 to fresh DB** — verify 7-value status CHECK, 15-column llm_calls, 3 composite indexes
3. **End-to-end manager send via Lovable UI** — verify D-04 auto-takeover + queue cancellation
4. **Real bot interaction (SpamBot id=178220800 or any @BotFather bot)** — verify proactive bot filter creates bot_ignored conversation, AI not called, antispam delegation for known IDs
5. **GET /api/v1/analytics/workspace correctness with seeded data** — verify D-13 real-time + D-15 two figures + Pitfall 8/9
6. **Real OpenAI call → GET /conversations/{id}/llm-calls** — verify 1-2 rows per turn with correct tokens/latency; warmup NOT logged (D-12)
7. **Inspect docker logs for prompt content** — verify T-05-03-PROMPT-LEAK no-leak in live application logs

### Gaps Summary

**No code-level gaps found.** All 11 must-haves and all 6 success criteria have working code artifacts with correct wiring. Migration is idempotent. Workspace isolation is defence-in-depth on every endpoint. Bot filter delegates to antispam safety net per D-08. Queue pre-send guard protects against D-04 race. LLM logger is never-raise with T-05-03-PROMPT-LEAK guard verified by grep returning 0 matches. Analytics _compute_cards uses parameterised raw SQL with `_ALLOWED_SCOPE_COLUMNS` whitelist (no SQL injection via scope column name).

**The verification gap is purely behavioral confirmation:** the Mac dev box has no Python/Docker, so pytest cannot be executed locally. All 3 plan SUMMARYs flag this as Deviation #4 (auto-fixed by trusting server-side pytest run on next deploy). Acceptance criteria verified via `ast.parse` + ~90 grep checks across 3 task commits per plan; all green.

**Recommended action:** Deploy to VPS with `cd /root/apps/outreach-platform && git pull && docker compose up -d --build api && docker compose up -d --build listener && pytest tests/test_phase5_*.py -x -q`. After successful test run, perform the 7 human verification items.

---

_Verified: 2026-05-22_
_Verifier: Claude (gsd-verifier, static-analysis only — no live Python env on dev box)_
