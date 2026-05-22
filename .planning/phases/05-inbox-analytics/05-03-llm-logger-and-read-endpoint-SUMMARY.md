---
phase: 05-inbox-analytics
plan: 03
subsystem: api
tags: [llm-audit, openai, fastapi, sqlalchemy-raw, pydantic-v2, never-raise-logger, workspace-scoped]

# Dependency graph
requires:
  - phase: 01-multitenancy-auth
    provides: auth_dep + AuthCtx + workspace lazy auto-create
  - phase: 04-campaigns
    provides: campaigns table + ai_engine.generate_response with built-in tools (mark_as_lead / transfer_to_manager / finish_conversation) — gives us the OpenAI call sites to wrap
  - phase: 05-inbox-analytics
    plan: 01
    provides: |
      Migration 017 with llm_calls table (15 cols + FK CASCADE on workspace/conversation +
      FK SET NULL on campaign/agent/sender + 2 indexes); ORM LLMCall registered;
      conversations.py router with _load_conversation_or_404 helper and Depends(auth_dep)
      pattern; AsyncSessionLocal exported from app.database.
provides:
  - app/services/llm_logger.py — never-raise log_llm_call() coroutine: isolated AsyncSessionLocal, denormalisation resolve (workspace_id + campaign_id + agent_id + sender_id from conversations), defensive OpenAI response extraction (.choices[0].message.content / .tool_calls / .usage with AttributeError/IndexError/TypeError guards), INSERT to llm_calls via raw SQL with :prompt::jsonb / :tool_calls::jsonb casts; try/except SQLAlchemyError + bare Exception catch-all (Pitfall 5 / T-05-03-LOG-FAIL-DOS)
  - app/services/ai_engine.py — 2 OpenAI calls wrapped in time.perf_counter() + try/except/finally + inline `await log_llm_call(...)`: point #1 (first chat.completions.create at ~line 660) + point #2 (tool result summarisation second call at ~line 780); error truncated to 500 chars; both wraps preserve outer RateLimitError/APIError handlers
  - app/routers/conversations.py — new 9th endpoint GET /api/v1/conversations/{id}/llm-calls with workspace isolation defence-in-depth (prequery + WHERE workspace_id), pagination limit/offset, ORDER BY created_at DESC
  - app/schemas/__init__.py — LLMCallResponse (15 fields, ConfigDict(from_attributes=True)) + LLMCallListResponse appended after Phase 5 analytics schemas
  - 3 test files (~21 tests): test_phase5_llm_logger.py (unit + integration), test_phase5_llm_logger_no_block_on_error.py (never-raise contract + prompt-leak guard), test_phase5_llm_calls_endpoint.py (8 endpoint tests)
affects: []

# Tech tracking
tech-stack:
  added: []   # no new dependencies — uses existing openai + sqlalchemy + httpx
  patterns:
    - "Never-raise fire-and-forget logger: try/except SQLAlchemyError + bare Exception catch-all; logger.warning emitted on failure but function returns None unconditionally — failure to log MUST NOT block the AI response (Pitfall 5 / T-05-03-LOG-FAIL-DOS)"
    - "Isolated AsyncSessionLocal pattern for side-effect persistence: log_llm_call opens its own session (NOT the main flow's session) — mirrors listener._handle_antispam_signal pattern; prevents commit conflict with main generate_response transaction"
    - "Sensitive-data logging guard (T-05-03-PROMPT-LEAK): logger.warning calls take only conversation_id + exception text; prompt content NEVER reaches application logs — only stored in llm_calls.prompt JSONB column. Verified by grep returning 0 matches for `logger\\.(info|warning|error|debug).*prompt`"
    - "OpenAI call wrap pattern: time.perf_counter() before + try/except/finally — re-raises in except so outer RateLimitError/APIError handlers stay unchanged, log_llm_call fires in finally regardless of success/failure (so audit row exists even on OpenAI errors)"
    - "Defensive OpenAI response extraction: getattr() chains + try/except AttributeError/IndexError/TypeError — survives missing tool_calls attr / empty choices / None usage"
    - "Workspace isolation defence-in-depth on read endpoint: layer 1 _load_conversation_or_404 prequery (404 on cross-workspace), layer 2 SELECT llm_calls WHERE workspace_id = :wid (filters out forged URLs that somehow bypass prequery)"
    - "Open Question #3 resolution: inline await log_llm_call instead of asyncio.create_task — deterministic, testable via direct SELECT after AI response; +1-3ms latency acceptable for v1; can switch to create_task in v2 if perf demands"

key-files:
  created:
    - app/services/llm_logger.py
    - tests/test_phase5_llm_logger.py
    - tests/test_phase5_llm_logger_no_block_on_error.py
    - tests/test_phase5_llm_calls_endpoint.py
  modified:
    - app/schemas/__init__.py (appended LLMCallResponse + LLMCallListResponse)
    - app/services/ai_engine.py (import log_llm_call + wrap 2 OpenAI call sites)
    - app/routers/conversations.py (import LLMCallResponse/LLMCallListResponse + GET /llm-calls endpoint)

key-decisions:
  - "Open Question #3: inline await log_llm_call (not asyncio.create_task) — deterministic, testable through direct SELECT after generate_response; +1-3ms latency acceptable for v1 per RESEARCH §Pattern 5 recommendation"
  - "D-12 preserved: warmup.py uses its own AsyncOpenAI client and is NOT wrapped — log_llm_call called ONLY from ai_engine.generate_response (listener-driven). Verified by grep: warmup.py has 0 references to log_llm_call or app.services.llm_logger import"
  - "Defensive OpenAI response extraction with AttributeError/IndexError/TypeError guards — covers cases where response is None (OpenAI error), choices is empty, message lacks tool_calls attr (mocked spec=['content']), or usage is None"
  - "_safe_jsonify uses ensure_ascii=False (Russian text preserved per CLAUDE.md) + default=str (UUID/datetime in request_params survive serialization without TypeError)"
  - "T-05-03-PROMPT-LEAK guard: logger.warning calls take only conversation_id + exception object — prompt dict NEVER passed as positional/keyword argument. Test test_sensitive_prompt_content_not_in_logs asserts SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG absent from caplog after a triggered SQLAlchemyError"
  - "FK violation behaviour (Test 8): when log_llm_call receives an explicit workspace_id but conversation_id that doesn't exist in conversations, the INSERT fails on FK constraint — caught by SQLAlchemyError handler, no raise. Row count remains 0 — acceptable: orphan audit rows are not desirable anyway"
  - "Endpoint defence-in-depth: prequery via _load_conversation_or_404 + inner SELECT also filters WHERE workspace_id = :wid. Even if a forged URL bypassed the prequery, layer 2 would filter cross-workspace rows. Test test_llm_calls_defence_in_depth_workspace_b_not_leaked verifies the row exists in DB but is not visible from workspace A"

patterns-established:
  - "Never-raise audit logger pattern: try/except SQLAlchemyError + bare Exception inside a single outer try block (NOT nested); warning emitted with conversation_id + exception text only; function returns None unconditionally so callers can `await` it without their own try/except"
  - "OpenAI call wrap with audit: timestamp before + try/except (with re-raise) / finally (with inline await log_llm_call) — outer error handlers stay intact, audit row inserted in both success and failure paths"
  - "Defence-in-depth workspace isolation on read endpoints with denormalised tables: prequery on parent resource + inner WHERE clause on child table. Even when the parent prequery should be sufficient, layer 2 protects against future code paths that might skip the prequery"

requirements-completed: [ANLX-05]

# Metrics
duration: 6min
completed: 2026-05-22
---

# Phase 5 Plan 03: LLM Logger + Read Endpoint Summary

**Never-raise log_llm_call() coroutine wraps 2 OpenAI call sites in ai_engine.generate_response (point #1 first call + point #2 tool-result summarisation) for per-conversation LLM audit trail; new GET /api/v1/conversations/{id}/llm-calls endpoint with workspace isolation defence-in-depth (prequery + inner WHERE workspace_id) serves inbox-debug UI. D-12 preserved — warmup.py NOT wrapped. T-05-03-PROMPT-LEAK mitigation: sensitive prompt content never reaches application logs (verified by grep returning 0 matches). 21 tests across 3 files cover unit logger payload, integration via generate_response, never-raise contract under SQLAlchemyError + RuntimeError, prompt-content absence in caplog, endpoint auth/cross-workspace 404/happy path/sorted DESC/pagination/defence-in-depth/JSONB serialisation/empty list.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-05-22T14:56:23Z
- **Completed:** 2026-05-22T15:02:13Z
- **Tasks:** 3
- **Files modified:** 7 (4 created, 3 modified)

## Accomplishments

- New app/services/llm_logger.py with never-raise log_llm_call() coroutine: isolated AsyncSessionLocal pattern (mirrors listener._handle_antispam_signal), denormalisation SELECT from conversations to fill workspace_id + campaign_id + agent_id + sender_id, defensive OpenAI response extraction with AttributeError/IndexError/TypeError guards, INSERT into llm_calls via raw SQL with :prompt::jsonb / :tool_calls::jsonb casts. Try/except SQLAlchemyError + bare Exception catch-all ensures function NEVER raises (Pitfall 5 / T-05-03-LOG-FAIL-DOS).
- 2 OpenAI calls in ai_engine.generate_response wrapped: point #1 (first chat.completions.create at ~line 660 with built-in + custom tools) + point #2 (tool-result summarisation second call at ~line 780). Each wrap: time.perf_counter() timestamp + try/except (with re-raise so outer RateLimitError/APIError handlers stay unchanged) + finally with inline `await log_llm_call(...)`. Error text truncated to 500 chars before logging. Per-turn yields 1 row (no tool calls) or 2 rows (custom tools route) in llm_calls — gives full visibility for inbox-debug.
- D-12 preserved: warmup.py uses its own AsyncOpenAI client and is NOT wrapped. Verified by grep — warmup.py has 0 references to log_llm_call or app.services.llm_logger. Logger called ONLY from listener-driven generate_response.
- T-05-03-PROMPT-LEAK mitigation: logger.warning calls take only conversation_id + exception text; prompt dict NEVER passed as logger argument. Test test_sensitive_prompt_content_not_in_logs asserts SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG absent from caplog records even after triggered SQLAlchemyError. Grep verification: `logger\\.(info|warning|error|debug).*prompt` returns 0 matches in both llm_logger.py and ai_engine.py wrap blocks.
- New GET /api/v1/conversations/{id}/llm-calls endpoint in conversations.py with workspace isolation defence-in-depth: prequery via _load_conversation_or_404 (layer 1, 404 on cross-workspace) + SELECT llm_calls WHERE conversation_id AND workspace_id (layer 2, filters forged URLs). Pagination limit 1..100 (default 50), offset >= 0 (default 0), ORDER BY created_at DESC. TODO(v2-rls) marker preserved.
- LLMCallResponse (15 fields, ConfigDict(from_attributes=True) so ORM rows hydrate) + LLMCallListResponse Pydantic schemas appended after Phase 5 analytics schemas in app/schemas/__init__.py.
- 21 tests across 3 files: 9 unit tests in test_phase5_llm_logger.py (happy path, denormalisation resolve, tool_calls extraction, response=None+error capture, conv-not-found skip, FK-violation no-raise, defensive missing-attr guard, schema model_validate, plus 2 integration tests via generate_response with monkeypatched chat.completions.create); 3 contract tests in test_phase5_llm_logger_no_block_on_error.py (SQLAlchemyError swallow, RuntimeError swallow, sensitive prompt absent from caplog); 8 endpoint tests in test_phase5_llm_calls_endpoint.py (401 auth gate, cross-workspace 404, happy path total=3, sorted DESC created_at, pagination limit=2/offset=1 over 5 rows, defence-in-depth — workspace-B row exists in DB but workspace-A user gets 404, JSONB returned as dict, empty list).

## Task Commits

Each task was committed atomically:

1. **Task 1: llm_logger module + LLMCallResponse/LLMCallListResponse schemas + 11 unit tests** — `1a90116` (feat)
2. **Task 2: ai_engine.generate_response wrap (2 OpenAI calls) + 2 integration tests** — `798ad9a` (feat)
3. **Task 3: GET /llm-calls endpoint + 8 endpoint tests** — `b2680c3` (feat)

**Plan metadata commit:** pending (this SUMMARY + STATE/ROADMAP/REQUIREMENTS)

## Files Created/Modified

### Created (4)
- `app/services/llm_logger.py` — 161 lines, log_llm_call() coroutine + _safe_jsonify helper; never-raise contract with try/except SQLAlchemyError + bare Exception catch-all; isolated AsyncSessionLocal pattern; defensive OpenAI response extraction; T-05-03-PROMPT-LEAK guard (no prompt content in logger calls).
- `tests/test_phase5_llm_logger.py` — 11 tests: 7 unit (happy path, denormalisation, tool_calls, response=None+error, conv-not-found skip, FK violation no-raise, defensive missing-attr, schema validate) + 2 integration via generate_response with monkeypatched chat.completions.create.
- `tests/test_phase5_llm_logger_no_block_on_error.py` — 3 tests: SQLAlchemyError swallow (no raise, warning logged), RuntimeError swallow (any exception type), T-05-03-PROMPT-LEAK assertion (SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG absent from caplog).
- `tests/test_phase5_llm_calls_endpoint.py` — 8 endpoint tests: 401 auth gate, cross-workspace 404, happy path (3 seeded rows → total=3), sorted DESC, pagination, defence-in-depth (workspace-B row not leaked), JSONB→dict, empty list.

### Modified (3)
- `app/schemas/__init__.py` — appended LLMCallResponse + LLMCallListResponse after AnalyticsCards (Plan 05-02 schemas). LLMCallResponse mirrors the 15-column llm_calls table with from_attributes=True; LLMCallListResponse wraps `list[LLMCallResponse] + total: int`.
- `app/services/ai_engine.py` — added `from app.services.llm_logger import log_llm_call` import; wrapped 2 `client.chat.completions.create` call sites with timestamp + try/except (re-raise) / finally (inline await log_llm_call). Existing outer except RateLimitError/APIConnectionError/APIStatusError/APIError handlers untouched. _start_ts uses existing `time` module import (no new dependency).
- `app/routers/conversations.py` — added LLMCallResponse + LLMCallListResponse to schema imports; added GET /{conversation_id}/llm-calls endpoint as 9th route after delete_conversation; updated docstring endpoint list. Defence-in-depth: _load_conversation_or_404 prequery + inner WHERE workspace_id filter.

## Decisions Made

1. **Inline await log_llm_call (Open Question #3)** — chose deterministic `await` over `asyncio.create_task(...)` for testability. Tests verify llm_calls row exists via direct SELECT immediately after generate_response returns. +1-3ms latency per OpenAI call is acceptable for v1; v2 can switch to create_task one-liner if performance demands.
2. **D-12 verbatim: warmup NOT wrapped** — warmup.py constructs its own `AsyncOpenAI()` client and calls `self._openai.chat.completions.create(...)`. Did not touch warmup at all. Verified by grep: warmup.py has 0 references to log_llm_call or app.services.llm_logger. ai_engine wraps are scoped to `generate_response` only (listener-driven path), so warmup-LLM audit cost-tracking is correctly deferred to v2.
3. **Try/except/finally re-raise pattern** — the inner try catches exception for error-capture into `_log_error` (truncated to 500 chars), then re-raises so the outer except RateLimitError/APIConnectionError/APIStatusError/APIError handlers in generate_response keep returning None to caller exactly as before. Finally fires log_llm_call unconditionally → audit row exists even when OpenAI errored.
4. **T-05-03-PROMPT-LEAK: prompt NEVER in logger calls** — every logger.warning takes only conversation_id and exception text. Prompt content lives ONLY in llm_calls.prompt JSONB. Grep verification: `logger\\.(info|warning|error|debug).*\\bprompt\\b` returns 0 matches in llm_logger.py.
5. **ensure_ascii=False in _safe_jsonify** — preserves Russian text in prompts (FAQ, persona, dialog) as-is, matching CLAUDE.md communication-language convention. `default=str` handles UUID/datetime objects that might appear in request_params (defensive — current code doesn't include them but future evolution might).
6. **Defensive response extraction with AttributeError/IndexError/TypeError** — `getattr(msg, "content", None)` + `getattr(msg, "tool_calls", None)` + try/except around the whole block. Handles edge cases: response is None (OpenAI error), choices is empty (rare LLM bug), message has no tool_calls attr (mocked spec=["content"]), usage is None (some streaming responses).
7. **Defence-in-depth workspace isolation** — even though _load_conversation_or_404 prequery already returns 404 on cross-workspace, the inner SELECT llm_calls also includes `WHERE workspace_id = :wid`. Belt-and-suspenders: protects against any future code path that might skip the prequery (e.g., direct refactor breaking the helper call).
8. **FK violation absorbed silently** — when an explicit workspace_id is passed but conversation_id doesn't exist, INSERT fails on FK constraint. log_llm_call's outer try/except SQLAlchemyError catches it; no orphan audit rows created. Test 8 verifies row count remains 0.

## Deviations from Plan

None — plan executed exactly as written.

The plan specified that `log_llm_call` receives an OpenAI `response` object whose `message.tool_calls[i].function.name` is accessible. In tests, `MagicMock(name="...")` does NOT set the `name` attribute (it sets the mock's own internal `name` for repr). The plan's test 3 example uses explicit `tc.function.name = "mark_as_lead"` assignment to work around this — implemented exactly as specified in the plan's <action> block. No deviation.

## Issues Encountered

**No local Python test environment** — same as Plans 05-01 and 05-02: the dev machine has no `.venv` / `pytest` / `sqlalchemy` installed. Tests were verified by:
1. `ast.parse()` succeeds on all 4 changed/created Python files (no syntax errors).
2. All grep-based acceptance criteria from the PLAN.md `<acceptance_criteria>` blocks pass (~30 grep checks across 3 tasks).
3. Test structure follows the proven patterns from Plan 05-01's `tests/test_phase5_inbox.py` (`_auth_headers` + `_bind` helpers + `valid_supabase_jwt` factory + `test_conversation_factory` fixture) and Plan 05-02's analytics correctness tests.
4. Plan-level grep regressions pass: warmup.py has 0 log_llm_call references (D-12), llm_logger.py + ai_engine.py have 0 matches for `logger.*prompt` (T-05-03-PROMPT-LEAK).

Tests will run server-side on the next deploy: `cd /root/apps/outreach-platform && git pull && docker compose up -d --build api && pytest tests/test_phase5_llm_logger.py tests/test_phase5_llm_logger_no_block_on_error.py tests/test_phase5_llm_calls_endpoint.py -x -q`.

## User Setup Required

None — Plan 05-03 is purely additive. No new env vars, no new services, no DB migrations beyond migration 017 (applied in Plan 05-01). Deploy with:

```bash
cd /root/apps/outreach-platform && git pull && docker compose up -d --build api && docker compose up -d --build listener
```

Both API (for the new endpoint) and listener (for ai_engine wraps) need rebuilding because ai_engine.py runs in both containers.

## Next Phase Readiness

- **Phase 5 fully complete** — all 3 plans landed. Requirements INBX-01..05, AIRC-04, ANLX-01..05 all closed. Inbox UI (Lovable) can now consume:
  - GET /conversations + 7 manager-mode endpoints (Plan 05-01)
  - 4 analytics endpoints with identical AnalyticsCards shape (Plan 05-02)
  - GET /conversations/{id}/llm-calls for inbox-debug LLM audit panel (this plan)
- **Phase 6 (Admin Master Bot)** unblocked — needs the inbox/analytics surface as upstream context. transfer_to_manager handoff hook in ai_engine already fires `webhook_notify.notify_signal` (Phase 4 D-12), so Phase 6 admin-bot notification can listen on that same payload.
- **No blockers.**

## Self-Check: PASSED

Verified files exist:
- app/services/llm_logger.py ✓
- app/schemas/__init__.py (LLMCallResponse + LLMCallListResponse appended) ✓
- app/services/ai_engine.py (import + 2 wraps) ✓
- app/routers/conversations.py (GET /llm-calls endpoint added) ✓
- tests/test_phase5_llm_logger.py ✓
- tests/test_phase5_llm_logger_no_block_on_error.py ✓
- tests/test_phase5_llm_calls_endpoint.py ✓

Verified commits exist:
- 1a90116 (Task 1: llm_logger + schemas + unit/contract tests) ✓
- 798ad9a (Task 2: ai_engine wrap + integration tests) ✓
- b2680c3 (Task 3: endpoint + endpoint tests) ✓

Verified grep acceptance criteria:
- `grep -c "async def log_llm_call" app/services/llm_logger.py` == 1 ✓
- `grep -c "async with AsyncSessionLocal()" app/services/llm_logger.py` == 1 ✓
- `grep -c "INSERT INTO llm_calls" app/services/llm_logger.py` == 1 ✓
- `grep -c "except SQLAlchemyError" app/services/llm_logger.py` == 1 ✓
- `grep -c "except Exception" app/services/llm_logger.py` == 1 (catch-all) ✓
- `grep -E "logger\\.(info|warning|error|debug).*\\bprompt\\b" app/services/llm_logger.py app/services/ai_engine.py | wc -l` == 0 (T-05-03-PROMPT-LEAK) ✓
- `grep -c "ensure_ascii=False" app/services/llm_logger.py` == 2 ✓
- `grep -c "class LLMCallResponse" app/schemas/__init__.py` == 1 ✓
- `grep -c "class LLMCallListResponse" app/schemas/__init__.py` == 1 ✓
- `grep -c "from app.services.llm_logger import log_llm_call" app/services/ai_engine.py` == 1 ✓
- `grep -c "await log_llm_call" app/services/ai_engine.py` == 2 (point #1 + point #2) ✓
- `grep -c "time.perf_counter()" app/services/ai_engine.py` >= 2 (timestamp capture both wraps) ✓
- `grep -B 3 -A 15 "await log_llm_call" app/services/ai_engine.py | grep -c "finally:"` == 2 ✓
- `grep -c "log_llm_call\\|app.services.llm_logger" app/services/warmup.py` == 0 (D-12 preserved) ✓
- `grep -c "@router.get(\\"/{conversation_id}/llm-calls\\"" app/routers/conversations.py` == 1 ✓
- `grep -c "response_model=LLMCallListResponse" app/routers/conversations.py` == 1 ✓
- `grep -A 30 "async def get_llm_calls" app/routers/conversations.py | grep -c "workspace_id = :wid"` == 2 (defence-in-depth) ✓
- `grep -A 30 "async def get_llm_calls" app/routers/conversations.py | grep -c "ORDER BY created_at DESC"` == 1 ✓
- `grep -A 30 "async def get_llm_calls" app/routers/conversations.py | grep -c "LIMIT :limit OFFSET :offset"` == 1 ✓

---
*Phase: 05-inbox-analytics*
*Completed: 2026-05-22*
