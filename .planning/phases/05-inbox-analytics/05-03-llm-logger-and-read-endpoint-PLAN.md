---
phase: 05-inbox-analytics
plan: 03
type: execute
wave: 2
depends_on: ["05-01"]
files_modified:
  - app/services/llm_logger.py
  - app/services/ai_engine.py
  - app/routers/conversations.py
  - app/schemas/__init__.py
  - tests/test_phase5_llm_logger.py
  - tests/test_phase5_llm_logger_no_block_on_error.py
  - tests/test_phase5_llm_calls_endpoint.py
autonomous: true
requirements: [ANLX-05]
requirements_addressed: [ANLX-05]
gap_closure: false

must_haves:
  truths:
    - "Новый модуль app/services/llm_logger.py содержит `async def log_llm_call(*, workspace_id, conversation_id, model, prompt, response, latency_ms, error=None)` — НИКОГДА не raise (try/except SQLAlchemyError + bare Exception оба ловятся, logger.warning, return None)"
    - "log_llm_call использует isolated `async with AsyncSessionLocal() as session:` — НЕ принимает session извне (защита от commit-conflict с основным flow); pattern из listener._handle_antispam_signal:840-880"
    - "log_llm_call резолвит denormalised cols (campaign_id, agent_id=ai_context_id, sender_id) одним SELECT из conversations если workspace_id=None; иначе использует переданный workspace_id"
    - "log_llm_call извлекает response.usage.{prompt_tokens, completion_tokens, total_tokens} и response.choices[0].message.{content, tool_calls} с defensive AttributeError/IndexError guards (response может быть None при OpenAI error)"
    - "log_llm_call INSERT в llm_calls со всеми 15 колонками через text() с :prompt::jsonb / :tool_calls::jsonb binding; prompt сериализуется через json.dumps(ensure_ascii=False, default=str)"
    - "ai_engine.generate_response (точка #1 — первый OpenAI call) обёрнут в timestamp + try/except/finally; на finally вызывается inline `await log_llm_call(...)` с полным request_params (messages + tools + temperature + model) в prompt и captured response/error/latency_ms"
    - "ai_engine.generate_response (точка #2 — второй OpenAI call для tool result summarisation, ~line 780) обёрнут аналогично — даёт 2 rows в llm_calls per turn когда есть custom tools"
    - "log_llm_call вызывается ТОЛЬКО из ai_engine.generate_response (listener-driven). Warmup-LLM calls (app/services/warmup.py) НЕ оборачиваются (D-12) — audit cost-tracking warmup отложен в v2"
    - "При ошибке INSERT (SQLAlchemyError или любой Exception) — exception проглатывается, warning логируется, ai_engine.generate_response возвращает response клиенту нормально (Pitfall 5)"
    - "Новый endpoint GET /api/v1/conversations/{id}/llm-calls добавлен в conversations.py: workspace-scoped, pagination limit/offset, sorted DESC created_at, возвращает LLMCallListResponse — для inbox-debug UI"
    - "Phase 5 schemas extended: LLMCallResponse + LLMCallListResponse в app/schemas/__init__.py"
    - "Open Question #3 (inline await vs create_task): inline await — детерминированный, тестируется через прямой SELECT после AI response; +1-3ms latency приемлемо для v1 (per Recommendation в RESEARCH §Pattern 5)"
    - "Sensitive prompt data — НЕ попадает в application logs (logger.info/warning не печатает prompt content); хранится ТОЛЬКО в llm_calls.prompt JSONB column (T-05-03-PROMPT-LEAK mitigation)"
  artifacts:
    - path: "app/services/llm_logger.py"
      provides: "log_llm_call() helper — never-raise INSERT в llm_calls + denormalisation resolve + OpenAI response extraction"
      contains: "async def log_llm_call, async with AsyncSessionLocal, INSERT INTO llm_calls, try/except SQLAlchemyError"
    - path: "app/services/ai_engine.py"
      provides: "Wrap вокруг 2 OpenAI calls (chat.completions.create) — timestamp + try/except/finally + inline await log_llm_call"
      contains: "import time as _time, _start_ts = _time.perf_counter(), await log_llm_call"
    - path: "app/routers/conversations.py"
      provides: "GET /api/v1/conversations/{id}/llm-calls — workspace-scoped read endpoint для inbox-debug UI"
      contains: "@router.get(\"/{conversation_id}/llm-calls\""
    - path: "app/schemas/__init__.py"
      provides: "Phase 5 LLMCall schemas"
      contains: "class LLMCallResponse, class LLMCallListResponse"
  key_links:
    - from: "app/services/ai_engine.py generate_response (точка #1, line ~660)"
      to: "app/services/llm_logger.py log_llm_call"
      via: "inline `await log_llm_call(workspace_id=None, conversation_id=conversation_id, model=request_params['model'], prompt=request_params, response=response, latency_ms=_latency_ms, error=_log_error)` в finally block"
      pattern: "await log_llm_call"
    - from: "app/services/ai_engine.py generate_response (точка #2, line ~780)"
      to: "app/services/llm_logger.py log_llm_call"
      via: "тот же inline await для second OpenAI call (tool result summarisation)"
      pattern: "await log_llm_call"
    - from: "app/services/llm_logger.py log_llm_call"
      to: "llm_calls table (INSERT) + conversations table (SELECT для denormalisation)"
      via: "SELECT workspace_id, campaign_id, ai_context_id, sender_id FROM conversations WHERE id = :cid; INSERT INTO llm_calls VALUES (...)"
      pattern: "INSERT INTO llm_calls"
    - from: "app/routers/conversations.py GET /llm-calls"
      to: "llm_calls table"
      via: "SELECT * FROM llm_calls WHERE conversation_id = :cid AND workspace_id = :wid ORDER BY created_at DESC LIMIT/OFFSET"
      pattern: "SELECT.*FROM llm_calls.*workspace_id"

threat_model:
  - id: T-05-03-WS-ISOLATION
    threat: "Cross-workspace LLM log leak — user A читает llm_calls workspace B через GET /conversations/{id}/llm-calls с подменой UUID"
    mitigation: "GET endpoint имеет двойную защиту: (1) _load_conversation_or_404 prequery с workspace_id check; (2) SELECT llm_calls дополнительно фильтрует WHERE workspace_id=:wid AND conversation_id=:cid (даже если conversation_id из чужого workspace utterly прошёл бы prequery). 404 на cross-workspace ДО любого SELECT llm_calls."
    verification: "pytest tests/test_phase5_llm_calls_endpoint.py::test_cross_workspace_llm_calls_404 -x — seed llm_calls в workspace B → GET /api/v1/conversations/{workspace-B-conv-id}/llm-calls auth'нут как workspace A → 404"
  - id: T-05-03-PROMPT-LEAK
    threat: "llm_calls.prompt JSONB содержит чувствительные данные клиента (system prompt с PROJECT.md контекстом, FAQ, тон, диалог с реальными именами/телефонами). Утечка через application logs или error tracebacks разрушает privacy."
    mitigation: "logger.warning в llm_logger НЕ принимает prompt как параметр (только conversation_id, exception text). exc_info=True для exception, но НЕ для prompt. Также CLAUDE.md guard: «API_KEY не в логах» расширяется до «sensitive prompt data не в логах»."
    verification: "grep -E 'logger\\.(info|warning|error|debug).*prompt\\b' app/services/llm_logger.py app/services/ai_engine.py — 0 matches (no logger calls reference prompt variable)"
  - id: T-05-03-LOG-FAIL-DOS
    threat: "Если log_llm_call raise — ai_engine.generate_response пропускает return → клиент не получает AI-ответ → service DoS через ошибку логирования"
    mitigation: "log_llm_call ЛОВИТ all exceptions (SQLAlchemyError + bare Exception). НЕ re-raise. logger.warning возникает асинхронно с return response (response уже captured в finally — внешняя функция возвращает response в обычном flow). Pitfall 5."
    verification: "pytest tests/test_phase5_llm_logger_no_block_on_error.py::test_db_failure_does_not_break_ai -x — monkeypatch AsyncSessionLocal raises SQLAlchemyError → generate_response всё равно returns valid response"
  - id: T-05-03-CASCADE-AUDIT-LOSS
    threat: "DELETE conversation → llm_calls CASCADE delete = audit trail loss (per Open Question #4 in RESEARCH, CONTEXT.md D-09 verbatim)"
    mitigation: "Поведение задокументировано в migration 017 (Plan 05-01) — CONTEXT.md D-09 explicit `ON DELETE CASCADE`. v2 может пересмотреть на SET NULL + soft delete. DELETE /conversations/{id} response в Plan 05-01 не возвращает count удалённых llm_calls — это deferred (можно добавить позже)."
    verification: "Acknowledged в design; covered by test_phase5_migration_017.py::test_llm_calls_cascade_on_conversation_delete (Plan 05-01 Task 1)"

---

<objective>
Создать новый модуль `app/services/llm_logger.py` с `log_llm_call()` корутиной (never-raise INSERT в llm_calls с denormalisation resolve и OpenAI response extraction). Wrap'нуть 2 OpenAI client.chat.completions.create вызова в `app/services/ai_engine.py` (точка #1 = первый call ~line 660, точка #2 = tool result summarisation ~line 780) в timestamp + try/except/finally с inline `await log_llm_call`. Добавить `GET /api/v1/conversations/{id}/llm-calls` endpoint в conversations.py (Plan 05-01 файл). Extend app/schemas с LLMCallResponse + LLMCallListResponse.

Purpose: Закрывает последнее требование Phase 5 — ANLX-05. Per-conversation LLM audit trail для inbox-debug UI: «почему AI ответил так — посмотри полный prompt + response». 100% покрытие listener-driven LLM-calls; warmup-calls НЕ логируем (D-12). Зависит от Plan 05-01 (миграция 017 создаёт llm_calls таблицу).

Output: 1 новый сервис (llm_logger.py), modify ai_engine.py (две точки wrap), modify conversations.py (1 endpoint), extend schemas, 3 файла тестов (logger payload + no-block-on-error + read endpoint).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@.planning/phases/05-inbox-analytics/05-CONTEXT.md
@.planning/phases/05-inbox-analytics/05-RESEARCH.md
@.planning/phases/05-inbox-analytics/05-PATTERNS.md
@.planning/phases/05-inbox-analytics/05-VALIDATION.md
@.planning/phases/05-inbox-analytics/05-01-migration-inbox-manager-bot-filter-PLAN.md
@.planning/codebase/ARCHITECTURE.md
@.planning/codebase/CONCERNS.md
@CLAUDE.md
@migrations/017_phase5.sql
@app/database.py
@app/models/__init__.py
@app/schemas/__init__.py
@app/services/ai_engine.py
@app/services/listener.py
@app/services/webhook_notify.py
@app/routers/conversations.py
@app/utils/auth.py
@tests/conftest.py
@tests/test_ai_engine.py
@tests/test_phase5_inbox.py

<interfaces>
<!-- llm_calls table schema (created in Plan 05-01 Task 1, migration 017) -->
```sql
CREATE TABLE llm_calls (
    id                UUID PRIMARY KEY,
    workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    campaign_id       UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    agent_id          UUID REFERENCES ai_contexts(id) ON DELETE SET NULL,
    sender_id         UUID REFERENCES senders(id) ON DELETE SET NULL,
    model             VARCHAR(50) NOT NULL,
    prompt            JSONB NOT NULL,
    response_text     TEXT,
    tool_calls        JSONB,
    prompt_tokens     INT,
    completion_tokens INT,
    total_tokens      INT,
    latency_ms        INT,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

<!-- ORM model from Plan 05-01 Task 1 -->
```python
from app.models import LLMCall  # already registered after Plan 05-01
```

<!-- AsyncSessionLocal pattern for isolated transaction (from app/database.py) -->
```python
from app.database import AsyncSessionLocal
async with AsyncSessionLocal() as session:
    await session.execute(text("INSERT ..."))
    await session.commit()
```

<!-- OpenAI response object shape (from existing ai_engine.py:660-670) -->
```python
response = await client.chat.completions.create(**request_params)
# response.choices[0].message.content              -> str | None
# response.choices[0].message.tool_calls          -> List[ToolCall] | None
#     each ToolCall has .id, .function.name, .function.arguments (str)
# response.usage.prompt_tokens                     -> int
# response.usage.completion_tokens                 -> int
# response.usage.total_tokens                      -> int
```

<!-- ai_engine.generate_response signature context (from app/services/ai_engine.py) -->
```python
async def generate_response(
    session: AsyncSession,  # main flow session — DO NOT pass to log_llm_call (isolated session)
    conversation_id: UUID | str,
    ...
) -> str | None:
    ...
    request_params = {"model": "gpt-5-mini-2025-08-07", "messages": messages, ...}
    if all_tools:
        request_params["tools"] = all_tools
        request_params["tool_choice"] = "auto"
    # === WRAP POINT #1 ===
    response = await client.chat.completions.create(**request_params)
    # ... process response, possibly second call for tool result summary ...
    # === WRAP POINT #2 (line ~780) ===
    response2 = await client.chat.completions.create(model=..., messages=..., max_completion_tokens=2000)
```

<!-- Phase 5 inbox schemas already defined in Plan 05-01 Task 1 -->
- ConversationResponse, ConversationListResponse, ConversationUpdate, MessageResponse, ...

<!-- Phase 5 D-09 column order for INSERT (must match exactly) -->
prompt (JSONB) — full request_params dict, serialized via json.dumps(ensure_ascii=False, default=str)
tool_calls (JSONB) — list[dict] из msg.tool_calls (NULL если пустой)

<!-- Hardcoded model id (from app/services/ai_engine.py — KNOWN BUG per CONCERNS.md, but assume fixed -->
model = "gpt-5-mini-2025-08-07"   # source for llm_calls.model column

<!-- Open Question #3 resolution: inline await (deterministic, testable) -->
<!-- Open Question #4 resolution: CASCADE per CONTEXT.md D-09 verbatim — covered in Plan 05-01 Task 1 -->
</interfaces>
</context>

<tasks>

<task type="execute" tdd="true">
  <name>Task 1: llm_logger.py service module + LLMCallResponse/LLMCallListResponse schemas</name>
  <files>
    app/services/llm_logger.py,
    app/schemas/__init__.py,
    tests/test_phase5_llm_logger.py,
    tests/test_phase5_llm_logger_no_block_on_error.py
  </files>
  <wave>1</wave>
  <depends_on>[]</depends_on>
  <read_first>
    - app/services/listener.py (строки 823-889 — `_handle_antispam_signal` exact pattern для isolated AsyncSessionLocal + try/except + logger.warning; copy structure for llm_logger)
    - app/services/webhook_notify.py (полностью — fire-and-forget try/except guard pattern)
    - app/database.py (полностью — verify AsyncSessionLocal export point)
    - app/services/ai_engine.py (строки 660-670 — pattern для response.usage / response.choices[0].message.content / .tool_calls extraction)
    - app/schemas/__init__.py (полностью — pattern для LLMCallResponse: ConfigDict(from_attributes=True), UUID + datetime + Optional fields)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §D-09, §D-10, §D-11, §D-12 (точная shape llm_calls + retention policy + scope to listener-only)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"Example 4: llm_logger.log_llm_call" (строки 915-1034 — почти полный код модуля), §"Pitfall 5: llm_calls.workspace_id derive race condition" (строки 575-583), §"Open Question #3" (inline await rationale), §"Pattern 5: LLM logger wrap" (строки 301-351)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`app/services/llm_logger.py`" (строки 239-312 — analog references + code skeleton)
    - migrations/017_phase5.sql (verify column names + types для INSERT)
    - tests/test_ai_engine.py (pattern для mock OpenAI response — controlled .choices / .usage / tool_calls)
  </read_first>
  <behavior>
    - Test 1 (happy path): log_llm_call(workspace_id=W, conversation_id=C, model='gpt-4o-mini', prompt={'messages':[...], 'tools':[]}, response=mock_response_with_usage, latency_ms=150, error=None) → INSERT 1 row в llm_calls; row.workspace_id=W, row.conversation_id=C, row.model='gpt-4o-mini', row.prompt JSONB содержит messages key, row.response_text matches mock content, row.prompt_tokens/completion_tokens/total_tokens из mock.usage, row.latency_ms=150
    - Test 2 (denormalisation resolve): log_llm_call(workspace_id=None, conversation_id=C где conversation linked к campaign+agent+sender) → INSERT row.campaign_id, agent_id, sender_id заполнены из conversations SELECT; workspace_id также resolved
    - Test 3 (response with tool_calls): mock response.choices[0].message.tool_calls = [{id:'x', function:Mock(name='mark_as_lead', arguments='{}')}] → row.tool_calls JSONB = [{"id": "x", "name": "mark_as_lead", "arguments": "{}"}]; row.response_text = mock.content (может быть None)
    - Test 4 (response is None on error): log_llm_call(response=None, error='RateLimitError') → row inserted с response_text=NULL, tool_calls=NULL, prompt_tokens/completion_tokens/total_tokens=NULL, error='RateLimitError'
    - Test 5 (Pitfall 5 — never raise on DB error): monkeypatch AsyncSessionLocal так чтобы session.execute raise SQLAlchemyError → log_llm_call возвращает None (НЕ raise); warning логируется
    - Test 6 (never raise on unexpected error): monkeypatch чтобы любой Exception (не SQLAlchemyError) raise → log_llm_call возвращает None
    - Test 7 (conversation not found — Pitfall 5): log_llm_call(workspace_id=None, conversation_id=<non-existent UUID>) → НЕ raise; warning «workspace_id unresolved» логируется; row НЕ insert'ится (return early)
    - Test 8 (workspace_id explicitly provided — no SELECT needed): log_llm_call(workspace_id=W, conversation_id=<non-existent UUID>) → row все равно insert'ится с workspace_id=W (но campaign_id/agent_id/sender_id NULL — SELECT вернёт пусто)
    - Test 9 (sensitive prompt NOT in logs): test logger output (caplog fixture) — после log_llm_call с prompt={'messages':[{'role':'system', 'content':'SECRET FAQ'}]} → caplog.records НЕ содержат 'SECRET FAQ'
    - Test 10 (schema LLMCallResponse): from_attributes пропускает ORM row → response model serialises корректно (тест через ORM SELECT + LLMCallResponse.model_validate)
    - Test 11 (defensive response extraction): mock response.choices[0].message без .tool_calls attribute → log_llm_call не падает (AttributeError defensive guard); response_text сохраняется
  </behavior>
  <action>
Создать новый файл `app/services/llm_logger.py`:

```python
"""LLM call audit logger — Phase 5 ANLX-05.

Wraps openai_client.chat.completions.create result into an llm_calls INSERT.
Per D-09..D-12: only listener-driven generate_response logged; warmup-LLM
calls NOT logged. Critical contract: THIS FUNCTION MUST NEVER RAISE — failure
to log MUST NOT bubble up to ai_engine.generate_response caller (Pitfall 5).
"""

import json
import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def log_llm_call(
    *,
    workspace_id: Optional[UUID | str],
    conversation_id: UUID | str,
    model: str,
    prompt: dict,
    response: Any,
    latency_ms: int,
    error: Optional[str] = None,
) -> None:
    """Insert one llm_calls row. NEVER raises.

    Args:
        workspace_id: If None, resolved from conversations.workspace_id.
        conversation_id: FK NOT NULL — required.
        model: e.g. "gpt-5-mini-2025-08-07".
        prompt: Full request_params dict (messages + tools + temperature + model).
        response: OpenAI ChatCompletion object OR None on error.
        latency_ms: Total round-trip time of OpenAI call.
        error: Exception text if OpenAI call failed (truncated to 500 chars).
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Resolve denormalised cols + workspace_id if not provided
            ws_id = workspace_id
            campaign_id = None
            agent_id = None
            sender_id = None

            row = (await session.execute(text("""
                SELECT workspace_id, campaign_id, ai_context_id, sender_id
                FROM conversations
                WHERE id = :cid
            """), {"cid": str(conversation_id)})).first()

            if row is not None:
                if ws_id is None:
                    ws_id = row.workspace_id
                campaign_id = row.campaign_id
                agent_id = row.ai_context_id
                sender_id = row.sender_id

            if ws_id is None:
                # Conversation deleted before log fired (Pitfall 5) — skip silently
                logger.warning(
                    "llm_calls: workspace_id unresolved for conv=%s — skipping",
                    conversation_id,
                )
                return

            # 2. Extract response fields (defensive — response может быть None при error)
            response_text: Optional[str] = None
            tool_calls_json: Optional[list[dict]] = None
            prompt_tokens: Optional[int] = None
            completion_tokens: Optional[int] = None
            total_tokens: Optional[int] = None

            if response is not None:
                try:
                    msg = response.choices[0].message
                    response_text = getattr(msg, "content", None)
                    tcs = getattr(msg, "tool_calls", None)
                    if tcs:
                        tool_calls_json = [
                            {
                                "id": tc.id,
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                            for tc in tcs
                        ]
                    usage = getattr(response, "usage", None)
                    if usage is not None:
                        prompt_tokens = getattr(usage, "prompt_tokens", None)
                        completion_tokens = getattr(usage, "completion_tokens", None)
                        total_tokens = getattr(usage, "total_tokens", None)
                except (AttributeError, IndexError) as e:
                    # Note: do NOT log prompt or response content here (T-05-03-PROMPT-LEAK)
                    logger.warning(
                        "llm_calls: response extraction failed for conv=%s: %s",
                        conversation_id, e,
                    )

            # 3. INSERT row
            await session.execute(text("""
                INSERT INTO llm_calls (
                    workspace_id, conversation_id, campaign_id, agent_id, sender_id,
                    model, prompt, response_text, tool_calls,
                    prompt_tokens, completion_tokens, total_tokens, latency_ms, error
                )
                VALUES (
                    :wid, :cid, :camp, :agent, :sender,
                    :model, :prompt::jsonb, :response_text, :tool_calls::jsonb,
                    :pt, :ct, :tt, :latency, :error
                )
            """), {
                "wid": str(ws_id),
                "cid": str(conversation_id),
                "camp": str(campaign_id) if campaign_id else None,
                "agent": str(agent_id) if agent_id else None,
                "sender": str(sender_id) if sender_id else None,
                "model": model,
                "prompt": _safe_jsonify(prompt),
                "response_text": response_text,
                "tool_calls": _safe_jsonify(tool_calls_json) if tool_calls_json else None,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "tt": total_tokens,
                "latency": latency_ms,
                "error": error,
            })
            await session.commit()

    except SQLAlchemyError as e:
        # T-05-03-PROMPT-LEAK: НЕ логируем prompt — только conversation_id + exception text
        logger.warning(
            "llm_calls INSERT failed for conv=%s: %s",
            conversation_id, e,
        )
    except Exception as e:
        # Catch-all — log_llm_call MUST NEVER raise (Pitfall 5 / T-05-03-LOG-FAIL-DOS)
        logger.warning(
            "llm_calls log unexpected error for conv=%s: %s",
            conversation_id, e,
        )


def _safe_jsonify(obj: Any) -> str:
    """Serialize dict/list to JSON string for PG JSONB binding.

    `ensure_ascii=False` сохраняет Russian text в prompt (FAQ, persona) как есть.
    `default=str` для UUID/datetime objects encountered в request_params.
    """
    return json.dumps(obj, ensure_ascii=False, default=str)
```

Добавить в `app/schemas/__init__.py` (после Phase 5 analytics schemas из Plan 05-02):

```python
# === Phase 5 LLM call schemas ===

class LLMCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    campaign_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None
    sender_id: Optional[UUID] = None
    model: str
    prompt: dict  # full request_params (messages + tools + temp + model)
    response_text: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime


class LLMCallListResponse(BaseModel):
    llm_calls: list[LLMCallResponse]
    total: int
```

Создать `tests/test_phase5_llm_logger.py` — тесты 1-4, 7, 8, 10, 11 из <behavior>:

```python
import pytest
from unittest.mock import MagicMock
from uuid import uuid4
from sqlalchemy import text

from app.services.llm_logger import log_llm_call

pytestmark = pytest.mark.asyncio


def _mock_openai_response(content="AI reply", tool_calls=None,
                          prompt_tokens=100, completion_tokens=50):
    resp = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    resp.choices = [MagicMock(message=msg)]
    if prompt_tokens is not None:
        resp.usage = MagicMock(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
    else:
        resp.usage = None
    return resp


async def test_log_llm_call_happy_path(
    async_db_session, test_conversation_factory, test_workspace
):
    conv = await test_conversation_factory()
    response = _mock_openai_response(content="Hello!", prompt_tokens=200, completion_tokens=30)
    prompt = {"model": "gpt-4o-mini",
              "messages": [{"role": "system", "content": "You are an assistant"},
                           {"role": "user", "content": "Hi"}],
              "temperature": 0.7}

    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt=prompt,
        response=response,
        latency_ms=150,
        error=None,
    )

    # Verify row inserted
    row = (await async_db_session.execute(text("""
        SELECT workspace_id, conversation_id, model, response_text,
               prompt_tokens, completion_tokens, total_tokens, latency_ms, error
        FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row is not None
    assert str(row.workspace_id) == str(conv["workspace_id"])
    assert row.model == "gpt-4o-mini"
    assert row.response_text == "Hello!"
    assert row.prompt_tokens == 200
    assert row.completion_tokens == 30
    assert row.total_tokens == 230
    assert row.latency_ms == 150
    assert row.error is None


async def test_log_llm_call_resolves_denormalised_cols(
    async_db_session, test_conversation_factory, test_campaign_factory,
    test_agent_factory, test_sender_factory
):
    """Test 2 — when workspace_id=None, resolve from conversations + populate camp/agent/sender."""
    sender = await test_sender_factory()
    agent = await test_agent_factory()
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(
        sender=sender, campaign_id=camp["id"], ai_context_id=agent["id"],
    )

    await log_llm_call(
        workspace_id=None,  # MUST be resolved from conversation
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={},
        response=_mock_openai_response(),
        latency_ms=10,
    )

    row = (await async_db_session.execute(text("""
        SELECT workspace_id, campaign_id, agent_id, sender_id
        FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert str(row.workspace_id) == str(conv["workspace_id"])
    assert str(row.campaign_id) == str(camp["id"])
    assert str(row.agent_id) == str(agent["id"])
    assert str(row.sender_id) == str(sender["id"])


async def test_log_llm_call_with_tool_calls(
    async_db_session, test_conversation_factory
):
    conv = await test_conversation_factory()
    tc = MagicMock()
    tc.id = "call_x"
    tc.function = MagicMock(name="mark_as_lead", arguments='{"reason":"interested"}')
    tc.function.name = "mark_as_lead"  # explicit set
    response = _mock_openai_response(content=None, tool_calls=[tc])

    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={},
        response=response,
        latency_ms=100,
    )

    row = (await async_db_session.execute(text("""
        SELECT response_text, tool_calls FROM llm_calls
        WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text is None
    assert row.tool_calls is not None
    assert row.tool_calls[0]["name"] == "mark_as_lead"


async def test_log_llm_call_with_none_response_and_error(
    async_db_session, test_conversation_factory
):
    """Test 4 — when OpenAI raised, response=None, error string captured."""
    conv = await test_conversation_factory()
    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={"messages": []},
        response=None,
        latency_ms=50,
        error="RateLimitError: 429",
    )
    row = (await async_db_session.execute(text("""
        SELECT response_text, tool_calls, prompt_tokens, completion_tokens,
               total_tokens, error
        FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text is None
    assert row.tool_calls is None
    assert row.prompt_tokens is None
    assert row.completion_tokens is None
    assert row.total_tokens is None
    assert row.error == "RateLimitError: 429"


async def test_log_llm_call_conversation_not_found_skips_silently(
    async_db_session, caplog
):
    """Test 7 — conversation_id doesn't exist + workspace_id=None → skip (no raise, warning log)."""
    nonexistent = uuid4()
    await log_llm_call(
        workspace_id=None,
        conversation_id=nonexistent,
        model="gpt-4o-mini",
        prompt={},
        response=_mock_openai_response(),
        latency_ms=10,
    )
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(nonexistent)})).scalar()
    assert cnt == 0
    assert any("workspace_id unresolved" in r.message for r in caplog.records)


async def test_log_llm_call_explicit_workspace_id_with_unknown_conv(
    async_db_session, test_workspace
):
    """Test 8 — workspace_id explicitly provided; conv doesn't exist → row still inserts (FK violation? — actually it WILL fail FK)."""
    # Note: this test actually verifies that FK on conversation_id NOT NULL prevents
    # insertion when conversation doesn't exist — log_llm_call must NOT raise.
    nonexistent_conv = uuid4()
    await log_llm_call(
        workspace_id=test_workspace["id"],
        conversation_id=nonexistent_conv,
        model="gpt-4o-mini",
        prompt={},
        response=_mock_openai_response(),
        latency_ms=10,
    )
    # Row insert should fail (FK violation on conversation_id) — but log_llm_call
    # must not raise. SQLAlchemyError handler catches it. Row count = 0.
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(nonexistent_conv)})).scalar()
    assert cnt == 0


async def test_response_extraction_defensive_missing_tool_calls_attr(
    async_db_session, test_conversation_factory
):
    """Test 11 — response.choices[0].message has no tool_calls attribute → no AttributeError."""
    conv = await test_conversation_factory()
    response = MagicMock()
    msg = MagicMock(spec=["content"])  # spec=[] — no tool_calls attr
    msg.content = "Just text"
    response.choices = [MagicMock(message=msg)]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={},
        response=response,
        latency_ms=10,
    )
    row = (await async_db_session.execute(text("""
        SELECT response_text, tool_calls FROM llm_calls
        WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text == "Just text"
    assert row.tool_calls is None


async def test_llm_call_response_pydantic_schema(
    async_db_session, test_conversation_factory
):
    """Test 10 — LLMCallResponse.model_validate from ORM row works."""
    from app.models import LLMCall
    from app.schemas import LLMCallResponse
    from sqlalchemy import select

    conv = await test_conversation_factory()
    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={"x": 1},
        response=_mock_openai_response(),
        latency_ms=20,
    )

    row = (await async_db_session.execute(
        select(LLMCall).where(LLMCall.conversation_id == conv["id"])
    )).scalar_one()
    schema = LLMCallResponse.model_validate(row)
    assert schema.model == "gpt-4o-mini"
    assert schema.latency_ms == 20
```

Создать `tests/test_phase5_llm_logger_no_block_on_error.py` — тесты 5, 6, 9:

```python
import pytest
import logging
from unittest.mock import patch, AsyncMock
from sqlalchemy.exc import SQLAlchemyError

from app.services.llm_logger import log_llm_call

pytestmark = pytest.mark.asyncio


async def test_db_error_does_not_raise(test_conversation_factory, caplog):
    """Test 5 — SQLAlchemyError on INSERT swallowed, warning logged, no raise."""
    conv = await test_conversation_factory()

    with patch(
        "app.services.llm_logger.AsyncSessionLocal",
        side_effect=SQLAlchemyError("simulated DB failure"),
    ):
        # MUST NOT raise
        await log_llm_call(
            workspace_id=conv["workspace_id"],
            conversation_id=conv["id"],
            model="gpt-4o-mini",
            prompt={},
            response=None,
            latency_ms=10,
        )

    assert any(
        "llm_calls INSERT failed" in r.message or "unexpected error" in r.message
        for r in caplog.records
    )


async def test_unexpected_error_does_not_raise(test_conversation_factory, caplog):
    """Test 6 — any Exception type swallowed."""
    conv = await test_conversation_factory()
    with patch(
        "app.services.llm_logger.AsyncSessionLocal",
        side_effect=RuntimeError("unexpected"),
    ):
        await log_llm_call(
            workspace_id=conv["workspace_id"],
            conversation_id=conv["id"],
            model="gpt-4o-mini",
            prompt={},
            response=None,
            latency_ms=10,
        )
    assert any("unexpected error" in r.message for r in caplog.records)


async def test_sensitive_prompt_content_not_in_logs(
    test_conversation_factory, caplog
):
    """Test 9 — T-05-03-PROMPT-LEAK: prompt content MUST NOT appear in application logs."""
    conv = await test_conversation_factory()
    secret_prompt = {
        "messages": [
            {"role": "system", "content": "SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG"},
            {"role": "user", "content": "SECRET_USER_PRIVATE_INFO_ABC"},
        ],
    }
    # Trigger error path so a warning is emitted
    with patch(
        "app.services.llm_logger.AsyncSessionLocal",
        side_effect=SQLAlchemyError("triggered"),
    ):
        with caplog.at_level(logging.WARNING):
            await log_llm_call(
                workspace_id=conv["workspace_id"],
                conversation_id=conv["id"],
                model="gpt-4o-mini",
                prompt=secret_prompt,
                response=None,
                latency_ms=10,
            )
    all_log_text = " ".join(r.message for r in caplog.records)
    assert "SECRET_FAQ_FRAGMENT_XYZ_DO_NOT_LOG" not in all_log_text
    assert "SECRET_USER_PRIVATE_INFO_ABC" not in all_log_text
```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_llm_logger.py tests/test_phase5_llm_logger_no_block_on_error.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `test -f app/services/llm_logger.py` returns 0 (file exists)
    - `grep -q "async def log_llm_call" app/services/llm_logger.py` returns 0
    - `grep -q "async with AsyncSessionLocal()" app/services/llm_logger.py` returns 0 (isolated session)
    - `grep -q "INSERT INTO llm_calls" app/services/llm_logger.py` returns 0
    - `grep -c "except SQLAlchemyError" app/services/llm_logger.py` >= 1
    - `grep -c "except Exception" app/services/llm_logger.py` >= 1 (catch-all per Pitfall 5)
    - `grep -E "logger\.(info|warning|error|debug).*prompt\b" app/services/llm_logger.py` returns nothing (T-05-03-PROMPT-LEAK guard — no logger refs to prompt variable in logging calls)
    - `grep -q "ensure_ascii=False" app/services/llm_logger.py` returns 0 (Russian preserved per CLAUDE.md)
    - `grep -q "class LLMCallResponse" app/schemas/__init__.py` returns 0
    - `grep -q "class LLMCallListResponse" app/schemas/__init__.py` returns 0
    - `pytest tests/test_phase5_llm_logger.py -x -q` — все 8 tests pass (1, 2, 3, 4, 7, 8, 10, 11)
    - `pytest tests/test_phase5_llm_logger_no_block_on_error.py -x -q` — все 3 tests pass (5, 6, 9)
    - **T-05-03-PROMPT-LEAK verification:** `pytest tests/test_phase5_llm_logger_no_block_on_error.py::test_sensitive_prompt_content_not_in_logs -x` passes
  </acceptance_criteria>
  <done>
    app/services/llm_logger.py создан с log_llm_call() корутиной (never-raise, isolated AsyncSessionLocal, denormalisation SELECT, defensive OpenAI response extraction). LLMCallResponse + LLMCallListResponse в schemas. 11 тестов в 2 файлах зелёные. Sensitive prompt content НЕ попадает в application logs (T-05-03-PROMPT-LEAK).
  </done>
</task>

<task type="execute" tdd="true">
  <name>Task 2: ai_engine.generate_response wrap (timestamp + try/except/finally + inline await log_llm_call для 2 OpenAI calls)</name>
  <files>
    app/services/ai_engine.py,
    tests/test_phase5_llm_logger.py
  </files>
  <wave>1</wave>
  <depends_on>["Task 1"]</depends_on>
  <read_first>
    - app/services/ai_engine.py (полностью — особенно строки 640-700 для точки #1 (первый chat.completions.create), и строки 770-810 для точки #2 (tool result summarisation second call). Если внутренняя структура отличается от RESEARCH §Example 5 — адаптировать имя переменных под реальный код)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"Pattern 5: LLM logger wrap" (строки 301-351 — exact wrap code), §"Open Question #3" (inline await vs create_task — выбрали inline для детерминизма)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`app/services/ai_engine.py`" (строки 396-453)
    - tests/test_ai_engine.py (полностью — pattern для mock OpenAI client в integration tests)
    - app/services/llm_logger.py (созданный в Task 1 — точная signature log_llm_call для inline вызова)
  </read_first>
  <behavior>
    - Test 1 (точка #1 wrap): generate_response с mocked openai client → llm_calls.row.count == 1 для conversation_id; row.model = настройка из ai_engine, row.prompt JSONB содержит "messages" key, row.response_text == mocked content, latency_ms ~= elapsed
    - Test 2 (точка #2 wrap — second call): generate_response с mocked tool_calls в первом ответе → второй OpenAI call (для tool result summarisation) триггерится → llm_calls.row.count == 2 для conversation_id (один за first call, один за second)
    - Test 3 (OpenAI error capture): mock client.chat.completions.create raise OpenAIError → row inserted с response_text=NULL, error содержит exception text, latency_ms заполнен; sender flow продолжается (внешний except handles RateLimitError)
    - Test 4 (no block on log error — integration): monkeypatch log_llm_call raise → generate_response всё равно возвращает response клиенту (acceptance — НЕ выполняется потому что log_llm_call сам never-raises; verify через assertion что log_llm_call is called regardless of internal errors)
    - Test 5 (warmup не логирует — D-12): patch warmup.py to call ai_engine.client.chat.completions.create directly (НЕ через generate_response) → assert llm_calls.row.count == 0 для warmup-вызовов. (Note: this test confirms wrap is scoped to generate_response only, not the bare OpenAI client.)
    - Test 6 (D-09 column completeness): после generate_response — row содержит все 15 колонок NOT NULL fields (workspace_id, conversation_id, model, prompt, created_at) и опциональные (response_text, tool_calls, usage tokens) заполнены корректно
  </behavior>
  <action>
Модифицировать `app/services/ai_engine.py`.

**Точка #1 — первый OpenAI call (~line 660):**

ДО:
```python
# Existing code (~line 647-665)
request_params = {
    "model": "gpt-5-mini-2025-08-07",
    "messages": messages,
    "max_completion_tokens": 2000,
}
if all_tools:
    request_params["tools"] = all_tools
    request_params["tool_choice"] = "auto"

response = await client.chat.completions.create(**request_params)
```

ПОСЛЕ (добавляем wrap):
```python
import time as _time
# Phase 5 ANLX-05: import llm_logger (placed at top of file with other imports;
# if already imported — don't duplicate)
from app.services.llm_logger import log_llm_call

# ... existing code ...
request_params = {
    "model": "gpt-5-mini-2025-08-07",
    "messages": messages,
    "max_completion_tokens": 2000,
}
if all_tools:
    request_params["tools"] = all_tools
    request_params["tool_choice"] = "auto"

# === Phase 5 ANLX-05: wrap OpenAI call for llm_calls logging ===
_start_ts = _time.perf_counter()
_log_error: Optional[str] = None
response = None
try:
    response = await client.chat.completions.create(**request_params)
except Exception as e:
    _log_error = str(e)[:500]
    raise  # re-raise — external RateLimitError/APIError handler catches это
finally:
    _latency_ms = int((_time.perf_counter() - _start_ts) * 1000)
    # Inline await — deterministic, testable (Open Question #3 resolution).
    # log_llm_call NEVER raises — safe to await unconditionally.
    await log_llm_call(
        workspace_id=None,  # llm_logger resolves from conversations
        conversation_id=conversation_id,
        model=request_params["model"],
        prompt=request_params,
        response=response,
        latency_ms=_latency_ms,
        error=_log_error,
    )
# === End Phase 5 wrap ===
```

**Точка #2 — second OpenAI call (~line 780, tool result summarisation):**

ДО:
```python
# Existing code (~line 780)
response2 = await client.chat.completions.create(
    model="gpt-5-mini-2025-08-07",
    messages=messages,
    max_completion_tokens=2000,
)
```

ПОСЛЕ — analogous wrap:
```python
# === Phase 5 ANLX-05: wrap second OpenAI call ===
_start_ts_2 = _time.perf_counter()
_log_error_2: Optional[str] = None
response2 = None
_second_params = {
    "model": "gpt-5-mini-2025-08-07",
    "messages": messages,
    "max_completion_tokens": 2000,
}
try:
    response2 = await client.chat.completions.create(**_second_params)
except Exception as e:
    _log_error_2 = str(e)[:500]
    raise
finally:
    _latency_ms_2 = int((_time.perf_counter() - _start_ts_2) * 1000)
    await log_llm_call(
        workspace_id=None,
        conversation_id=conversation_id,
        model=_second_params["model"],
        prompt=_second_params,
        response=response2,
        latency_ms=_latency_ms_2,
        error=_log_error_2,
    )
# === End wrap ===
```

**Critical — НЕ оборачивать другие OpenAI client calls:**
- `app/services/warmup.py` (если там вызывается openai_client) — D-12: НЕ логируем
- Любые OpenAI calls вне `generate_response` — оставляем как есть

**Если внутри `generate_response` есть >2 OpenAI calls** (например, в каком-то рекурсивном rolling-window flow) — обернуть ВСЕ вызовы которые относятся к "генерация ответа клиенту" (по которым inbox-debug нужен).

Расширить `tests/test_phase5_llm_logger.py` integration-тестами через `generate_response` (тесты 1-6 из <behavior>). Mock client.chat.completions.create через monkeypatch:

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_generate_response_writes_llm_call_row(
    async_db_session, test_conversation_factory, monkeypatch
):
    """Test 1 — generate_response → llm_calls row inserted."""
    from app.services import ai_engine
    conv = await test_conversation_factory()

    mock_response = MagicMock()
    msg = MagicMock()
    msg.content = "Mocked AI reply"
    msg.tool_calls = None
    mock_response.choices = [MagicMock(message=msg)]
    mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    create_mock = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(ai_engine.client.chat.completions, "create", create_mock)

    # Call generate_response (signature dependent — adapt per real signature)
    result = await ai_engine.generate_response(
        session=async_db_session,
        conversation_id=conv["id"],
        # ... other required args based on actual signature
    )

    # Verify llm_calls row
    row = (await async_db_session.execute(text("""
        SELECT model, response_text, prompt_tokens FROM llm_calls
        WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row is not None
    assert row.response_text == "Mocked AI reply"
    assert row.prompt_tokens == 100


async def test_openai_error_captured_in_llm_calls(
    async_db_session, test_conversation_factory, monkeypatch
):
    """Test 3 — OpenAI raises → error captured, response_text=NULL."""
    from app.services import ai_engine
    from openai import OpenAIError
    conv = await test_conversation_factory()

    create_mock = AsyncMock(side_effect=OpenAIError("RateLimitError"))
    monkeypatch.setattr(ai_engine.client.chat.completions, "create", create_mock)

    with pytest.raises(OpenAIError):
        await ai_engine.generate_response(
            session=async_db_session,
            conversation_id=conv["id"],
        )

    row = (await async_db_session.execute(text("""
        SELECT response_text, error FROM llm_calls
        WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text is None
    assert "RateLimitError" in row.error
```

Если точная signature `generate_response` сложна для прямой моки — допустимо использовать lower-level integration test, который вызывает только wrap'нутую часть (i.e., directly patch client.chat.completions.create в реальном flow listener'а).
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_llm_logger.py -x -q && pytest tests/test_ai_engine.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "from app.services.llm_logger import log_llm_call" app/services/ai_engine.py` returns 0
    - `grep -c "await log_llm_call" app/services/ai_engine.py` >= 2 (one for point #1, one for point #2)
    - `grep -c "_time.perf_counter()" app/services/ai_engine.py` >= 2 (timestamp capture for both wraps)
    - `grep -B 3 -A 15 "await log_llm_call" app/services/ai_engine.py | grep -c "finally:"` >= 2 (each wrap in finally block)
    - `grep -B 2 -A 12 "await log_llm_call" app/services/ai_engine.py | grep -c "try:"` >= 2
    - `grep -q "_log_error = str(e)" app/services/ai_engine.py` returns 0 (error capture pattern)
    - `grep -q ":500" app/services/ai_engine.py` returns 0 (error truncation to 500 chars)
    - **D-12 verification:** `grep "await log_llm_call" app/services/warmup.py 2>/dev/null` returns nothing (warmup NOT logged)
    - **D-12 verification:** `grep "from app.services.llm_logger" app/services/warmup.py 2>/dev/null` returns nothing
    - `pytest tests/test_phase5_llm_logger.py -x -q` — все integration tests pass
    - `pytest tests/test_ai_engine.py -x -q` — existing tests still pass (regression — wrap не сломал основной flow)
  </acceptance_criteria>
  <done>
    ai_engine.generate_response обёрнут в timestamp + try/except/finally + inline `await log_llm_call(...)` для обоих OpenAI calls (точка #1 в line ~660, точка #2 в line ~780). Warmup НЕ обёрнут (D-12). Все 6 интеграционных тестов test_phase5_llm_logger.py зелёные.
  </done>
</task>

<task type="execute" tdd="true">
  <name>Task 3: GET /api/v1/conversations/{id}/llm-calls read endpoint (workspace-scoped)</name>
  <files>
    app/routers/conversations.py,
    tests/test_phase5_llm_calls_endpoint.py
  </files>
  <wave>1</wave>
  <depends_on>["Task 1"]</depends_on>
  <read_first>
    - app/routers/conversations.py (созданный в Plan 05-01 Task 2 — добавляем 9-й endpoint, тот же pattern auth_dep + workspace-scope + LIMIT/OFFSET pagination)
    - app/schemas/__init__.py (LLMCallResponse + LLMCallListResponse из Task 1)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §"Lovable UI contract" §lovable рендерит LLM-debug панель (canonical_refs section) — confirms endpoint shape
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §C-03 LLMCallListResponse shape
    - tests/test_phase5_inbox.py (созданный в Plan 05-01 Task 2 — pattern для auth + workspace isolation tests)
  </read_first>
  <behavior>
    - Test 1 (auth): GET /api/v1/conversations/{id}/llm-calls без credentials → 401
    - Test 2 (cross-workspace 404): user workspace A → GET /conversations/{workspace-B-conv-id}/llm-calls → 404 ДО любого SELECT llm_calls
    - Test 3 (happy path): seed conv + 3 llm_calls rows → GET endpoint возвращает {llm_calls: [...3 items], total: 3}
    - Test 4 (sorted DESC created_at): seed 3 llm_calls с разными timestamps → response.llm_calls[0].created_at > [1] > [2]
    - Test 5 (pagination): seed 5 llm_calls → GET ?limit=2&offset=1 → 2 rows, total=5
    - Test 6 (cross-workspace defence-in-depth): seed conv в workspace B + 1 llm_call для этого conv → user workspace A передаёт workspace-B-conv-id → 404; llm_call row не утекает
    - Test 7 (prompt JSONB returned): seed llm_call с prompt={"messages":[...]} → response llm_calls[0].prompt deserialized as dict
    - Test 8 (empty list): conv без llm_calls → response {llm_calls: [], total: 0}
  </behavior>
  <action>
Добавить в `app/routers/conversations.py` (созданный в Plan 05-01 Task 2) новый 9-й endpoint после `delete_conversation`:

```python
# Import — добавить если ещё не импортирован
from app.schemas import (
    # ... existing Phase 5 schemas ...
    LLMCallResponse,
    LLMCallListResponse,
)


@router.get("/{conversation_id}/llm-calls", response_model=LLMCallListResponse)
async def get_llm_calls(
    conversation_id: UUID,
    limit: int = Query(50, le=100, ge=1),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> LLMCallListResponse:
    """ANLX-05 — LLM call audit log per conversation (inbox-debug UI).

    Workspace-scoped: defense-in-depth (precheck conversation + WHERE workspace_id in llm_calls SELECT).
    Sorted DESC created_at (newest first).
    """
    # Defense-in-depth #1: prequery conversation in workspace
    await _load_conversation_or_404(db, ctx, conversation_id)

    # Defense-in-depth #2: SELECT llm_calls also filters workspace_id
    rows = (await db.execute(text("""
        SELECT id, workspace_id, conversation_id, campaign_id, agent_id, sender_id,
               model, prompt, response_text, tool_calls,
               prompt_tokens, completion_tokens, total_tokens, latency_ms, error,
               created_at
        FROM llm_calls
        WHERE conversation_id = :cid
          AND workspace_id = :wid
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id),
           "limit": limit, "offset": offset})).fetchall()

    total = (await db.execute(text("""
        SELECT COUNT(*) FROM llm_calls
        WHERE conversation_id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).scalar() or 0

    return LLMCallListResponse(
        llm_calls=[LLMCallResponse(**dict(r._mapping)) for r in rows],
        total=total,
    )
```

Создать `tests/test_phase5_llm_calls_endpoint.py` с 8 тестами из <behavior>:

```python
import pytest
from sqlalchemy import text
from uuid import uuid4

pytestmark = pytest.mark.asyncio


async def _seed_llm_call(async_db_session, conv, model="gpt-4o-mini",
                         response_text="reply", created_at=None):
    extra_ts = "" if created_at is None else ", created_at = :ts"
    params = {
        "wid": str(conv["workspace_id"]), "cid": str(conv["id"]),
        "model": model, "rt": response_text,
    }
    if created_at is not None:
        params["ts"] = created_at
    await async_db_session.execute(text(f"""
        INSERT INTO llm_calls (workspace_id, conversation_id, model, prompt, response_text)
        VALUES (:wid, :cid, :model, '{{"messages":[]}}'::jsonb, :rt)
    """), params)
    await async_db_session.commit()


async def test_llm_calls_endpoint_auth_required(async_client, test_conversation_factory):
    conv = await test_conversation_factory()
    resp = await async_client.get(f"/api/v1/conversations/{conv['id']}/llm-calls")
    assert resp.status_code == 401


async def test_llm_calls_cross_workspace_404(
    async_client, auth_headers_factory, test_conversation_factory,
    test_workspace_factory, async_db_session,
):
    """T-05-03-WS-ISOLATION — workspace A user can't read workspace B llm_calls."""
    workspace_b = await test_workspace_factory()
    # conversation belongs to workspace_b
    # use a different conversation_factory variant that takes workspace param OR mock differently
    # (depends on conftest exact API)
    workspace_a_headers = auth_headers_factory(workspace_id=...)  # adapt
    # ...
    resp = await async_client.get(
        f"/api/v1/conversations/{conv_in_b['id']}/llm-calls",
        headers=workspace_a_headers,
    )
    assert resp.status_code == 404


async def test_llm_calls_happy_path(
    async_client, auth_headers, test_conversation_factory, async_db_session,
):
    conv = await test_conversation_factory()
    await _seed_llm_call(async_db_session, conv, response_text="reply 1")
    await _seed_llm_call(async_db_session, conv, response_text="reply 2")
    await _seed_llm_call(async_db_session, conv, response_text="reply 3")
    resp = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["llm_calls"]) == 3


async def test_llm_calls_pagination(
    async_client, auth_headers, test_conversation_factory, async_db_session,
):
    conv = await test_conversation_factory()
    for i in range(5):
        await _seed_llm_call(async_db_session, conv, response_text=f"r{i}")
    resp = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls?limit=2&offset=1",
        headers=auth_headers,
    )
    data = resp.json()
    assert data["total"] == 5
    assert len(data["llm_calls"]) == 2


async def test_llm_calls_prompt_jsonb_returned_as_dict(
    async_client, auth_headers, test_conversation_factory, async_db_session,
):
    conv = await test_conversation_factory()
    await _seed_llm_call(async_db_session, conv)
    resp = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=auth_headers,
    )
    data = resp.json()
    assert isinstance(data["llm_calls"][0]["prompt"], dict)
    assert "messages" in data["llm_calls"][0]["prompt"]


async def test_llm_calls_empty_list(
    async_client, auth_headers, test_conversation_factory,
):
    conv = await test_conversation_factory()
    resp = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=auth_headers,
    )
    data = resp.json()
    assert data == {"llm_calls": [], "total": 0}
```

Адаптировать test setup под реальную structure conftest (test_workspace_factory, auth_headers_factory, etc. — должны быть из Plan 05-01 conftest extensions).
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_llm_calls_endpoint.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "@router.get(\"/{conversation_id}/llm-calls\"" app/routers/conversations.py` returns 0
    - `grep -q "response_model=LLMCallListResponse" app/routers/conversations.py` returns 0
    - In get_llm_calls handler: BOTH `await _load_conversation_or_404` AND `WHERE conversation_id = :cid AND workspace_id = :wid` in SELECT (defense-in-depth)
    - `grep -A 20 "async def get_llm_calls" app/routers/conversations.py | grep -q "ORDER BY created_at DESC"` returns 0
    - `grep -A 25 "async def get_llm_calls" app/routers/conversations.py | grep -q "LIMIT :limit OFFSET :offset"` returns 0
    - `pytest tests/test_phase5_llm_calls_endpoint.py -x -q` — все 8 tests pass
    - Regression: `pytest tests/test_phase5_inbox.py -x -q` зелёные (новый endpoint не сломал existing inbox endpoints)
  </acceptance_criteria>
  <done>
    GET /api/v1/conversations/{id}/llm-calls endpoint добавлен в conversations.py с workspace-scope defense-in-depth (precheck + WHERE workspace_id), pagination, ORDER BY created_at DESC. 8 тестов test_phase5_llm_calls_endpoint.py зелёные.
  </done>
</task>

</tasks>

<verification>
**Plan-level checks:**

1. **llm_logger contract — never raises:**
   - `pytest tests/test_phase5_llm_logger_no_block_on_error.py -x -q` зелёный
   - Manual: drop llm_calls table → call generate_response → assert AI response still returned + warning log

2. **2 OpenAI calls wrapped:**
   - `pytest tests/test_phase5_llm_logger.py::test_generate_response_writes_llm_call_row -x -q` зелёный
   - Manual: одна реальная generate_response (если есть тестовый OpenAI key) → 1 или 2 rows в llm_calls (1 для simple ответа, 2 если tool_call'и)

3. **Warmup NOT logged (D-12):**
   - `grep "log_llm_call" app/services/warmup.py` пусто
   - Manual: trigger warmup → 0 rows в llm_calls

4. **Read endpoint workspace isolation:**
   - `pytest tests/test_phase5_llm_calls_endpoint.py -x -q` зелёный
   - Manual: 2 workspaces, попытка cross → 404

5. **Sensitive prompt NOT in app logs (T-05-03-PROMPT-LEAK):**
   - `pytest tests/test_phase5_llm_logger_no_block_on_error.py::test_sensitive_prompt_content_not_in_logs -x -q` зелёный
   - Manual: trigger DB error → check uvicorn stderr — нет prompt content

6. **Schema compliance:**
   - LLMCallResponse.model_validate работает с ORM LLMCall instance
   - All 15 column fields доступны в JSON response

7. **Regression:**
   - `pytest tests/test_ai_engine.py tests/test_phase5_inbox.py tests/test_listener.py -x -q` все зелёные
</verification>

<success_criteria>
**Plan 05-03 complete when:**

- [ ] `app/services/llm_logger.py` создан с `async def log_llm_call(...)` — never-raise, isolated AsyncSessionLocal, denormalisation resolve (workspace_id + campaign_id + agent_id + sender_id из conversations), defensive OpenAI response extraction (.choices[0].message.content / .tool_calls / .usage с try/except AttributeError/IndexError)
- [ ] Try/except blocks ловят SQLAlchemyError + bare Exception — функция никогда не raise
- [ ] T-05-03-PROMPT-LEAK guard: logger.warning calls НЕ принимают prompt как параметр; только conversation_id + exception text
- [ ] `app/services/ai_engine.py` — оба OpenAI calls (chat.completions.create) обёрнуты в timestamp + try/except/finally + inline `await log_llm_call(...)` с прочей передачей request_params в prompt argument
- [ ] D-12 verification: warmup.py НЕ импортирует log_llm_call и НЕ вызывает его
- [ ] `LLMCallResponse` + `LLMCallListResponse` Pydantic schemas добавлены в app/schemas/__init__.py
- [ ] `GET /api/v1/conversations/{id}/llm-calls` endpoint добавлен в conversations.py: workspace-scope defense-in-depth (prequery + WHERE workspace_id в SELECT), pagination LIMIT/OFFSET, ORDER BY created_at DESC
- [ ] `tests/test_phase5_llm_logger.py` (~10 tests) — все зелёные (happy path, denormalisation, tool_calls, response None, conv not found, defensive guards, schema validation, integration via generate_response)
- [ ] `tests/test_phase5_llm_logger_no_block_on_error.py` (~3 tests) — все зелёные (SQLAlchemyError, bare Exception, sensitive prompt NOT in logs)
- [ ] `tests/test_phase5_llm_calls_endpoint.py` (~8 tests) — все зелёные (auth, cross-workspace 404, happy path, sorted DESC, pagination, prompt JSONB, empty list)
- [ ] Regression: test_ai_engine.py + test_phase5_inbox.py + test_listener.py зелёные
- [ ] ANLX-05 закрыто полностью: inbox UI может GET /conversations/{id}/llm-calls и видеть полный prompt + response для debug
</success_criteria>

<output>
After completion, create `.planning/phases/05-inbox-analytics/05-03-SUMMARY.md` следуя `.claude/get-shit-done/templates/summary.md`.

Include:
- New module app/services/llm_logger.py — never-raise log_llm_call() helper
- 2 wrap points в ai_engine.generate_response (first OpenAI call + tool result summarisation)
- D-12 confirmed: warmup НЕ обёрнут
- Open Question #3 resolution: inline await (deterministic, testable)
- Defense-in-depth в read endpoint (precheck conversation + WHERE workspace_id в SELECT)
- T-05-03-PROMPT-LEAK mitigation verified (sensitive prompt NOT in app logs)
- Test coverage: ~21 tests across 3 files
- ANLX-05 closed — inbox-debug UI flow доступен
</output>
