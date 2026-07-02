---
phase: 18-switchable-llm-provider
plan: 04
type: execute
wave: 3
depends_on: ["18-02", "18-03"]
files_modified:
  - app/services/ai_engine.py
  - app/services/warmup.py
  - app/services/llm_logger.py
  - tests/test_ai_engine_empty_retry.py
  - tests/test_llm_logger_provider.py
  - tests/test_llm_isolation.py
autonomous: true
requirements: [LLMP-06, LLMP-07, LLMP-11, LLMP-12]
must_haves:
  truths:
    - "The chat answerer's three LLM calls all route through the resolved workspace provider/model/knobs"
    - "The second (tool-summarization) pass appends a provider-neutral assistant turn (not a raw OpenAI SDK object) so both providers can translate it"
    - "Warmup routes through the same workspace-aware provider factory (single tone everywhere)"
    - "On a key-level error the answerer falls back to the platform OpenAI default and continues; key flagged invalid"
    - "llm_logger records provider + key_source on every logged call"
    - "Whisper transcription and KB embeddings still use the platform OpenAI singleton regardless of provider choice"
  artifacts:
    - path: "app/services/ai_engine.py"
      provides: "answerer routed through provider adapter with D-06 fallback + provider-neutral second-pass turn"
      contains: "resolve_llm_config"
    - path: "app/services/warmup.py"
      provides: "warmup routed through the workspace-aware provider factory"
      contains: "get_provider"
    - path: "app/services/llm_logger.py"
      provides: "provider + key_source persisted per call"
      contains: "key_source"
  key_links:
    - from: "app/services/ai_engine.py::generate_response"
      to: "app/services/llm/resolve.py::resolve_llm_config"
      via: "load workspace LLM config on the existing context path"
      pattern: "resolve_llm_config"
    - from: "app/services/ai_engine.py"
      to: "app/services/llm/resolve.py::is_key_level_error"
      via: "D-06 fallback trigger on key-level error only"
      pattern: "is_key_level_error"
    - from: "app/services/ai_engine.py::transcribe_audio"
      to: "module-level client (platform AsyncOpenAI)"
      via: "Whisper stays on platform key (D-12)"
      pattern: "client.audio"
---

<objective>
Route the chat answerer (three `chat.completions.create` sites in `generate_response`) and warmup through the provider adapter using the per-workspace resolved config, preserving the empty-response guard/retry across both providers. Rebuild the second-pass (tool-summarization) message list to be provider-neutral so Anthropic can translate the assistant tool-call turn. Implement the D-06 key-level fallback (platform OpenAI + flag key invalid). Extend `llm_logger` with provider + key_source (D-07). Keep Whisper + KB embeddings pinned to the platform OpenAI singleton (D-12).

Purpose: This is where the switch takes effect at runtime. Highest-risk plan — touches the hot answerer path in the listener. Sequenced last (Wave 3) so the adapter (18-02) and settings API (18-03) are already merged.
Output: adapter-wired ai_engine + warmup + logger; test_llm_logger_provider, test_llm_isolation GREEN; updated test_ai_engine_empty_retry works across providers.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/18-switchable-llm-provider/18-CONTEXT.md
@.planning/phases/18-switchable-llm-provider/18-RESEARCH.md

<interfaces>
<!-- Exact seams to modify — extracted from source. -->

app/services/ai_engine.py:41 (platform singleton — STAYS for Whisper + embeddings, D-12):
```python
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))  # platform, embeddings/whisper ONLY
```

app/services/ai_engine.py:1279 (generate_response signature — DO NOT change; it already has session + conversation_id):
```python
async def generate_response(self, session, conversation_id, context_id, contact_name, new_message, conversation_context=None) -> Optional[str]:
```

app/services/ai_engine.py:1308 (workspace resolution already on the path):
```python
campaign_context = await get_context_for_conversation(conversation_id, session)
# campaign_context["workspace_id"] is available (see get_context_for_conversation returns conv_wid)
```

The three current call sites (all `await client.chat.completions.create(**params)`):
- initial:      line ~1424  (params = _build_completion_params(messages, tools=all_tools))
- empty-retry:  line ~1478  (retry_params = _build_completion_params(messages, tools=all_tools, retry=True))
- second-pass:  line ~1652  (_second_params = _build_completion_params(messages))

app/services/ai_engine.py:1635-1642 (second-pass message assembly — BLOCKER: appends the RAW OpenAI SDK object):
```python
# Second LLM call to summarise tool results into a final reply.
messages.append(response_message)                    # <-- raw OpenAI ChatCompletionMessage SDK object
for tool_call, _name, _args in custom_calls:
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": tool_results.get(tool_call.id, "Функция выполнена"),
    })
```
`response_message` here is the raw OpenAI SDK object from the FIRST call. That object cannot be
translated by AnthropicProvider into `tool_use` content blocks — Claude tool flows (mark_as_lead /
custom webhook tools) would crash or mis-translate on the second pass. It MUST become a
provider-neutral plain dict built from the normalized LLMResult before appending.

Empty-guard (line ~1460): `if not tool_calls and not text_content_clean and finish_reason=='length': retry`.
This must keep working: LLMResult.finish_reason is normalized so Anthropic 'max_tokens' -> 'length'.

app/services/llm/resolve.py (from 18-02): resolve_llm_config, get_provider, is_key_level_error, platform_fallback_config, LLMConfig.
app/services/llm/base.py: LLMResult{text, tool_calls:[ToolCall{id,name,arguments}], finish_reason, usage}.
app/services/llm/anthropic_provider.py (from 18-02): translates incoming assistant tool-call turns of shape
`{"role":"assistant","tool_calls":[{"id","name","arguments"}]}` into `tool_use` content blocks, and
`{"role":"tool","tool_call_id","content"}` into `tool_result` blocks. The second-pass list MUST use
that neutral shape so both providers translate it.

app/services/warmup.py:99 (own client -> replace) + :640-658 (_generate_message call):
```python
self._openai = AsyncOpenAI()          # -> remove; use workspace factory
response = await self._openai.chat.completions.create(model=settings.openai_model, messages=messages)
```
_generate_message signature (:626): (self, topic, history, from_sender_id, system_prompt=None). from_sender is a dict with "workspace_id".

app/services/llm_logger.py:31 log_llm_call(*, workspace_id, conversation_id, model, prompt, response, latency_ms, error) — ADD provider + key_source kwargs.
Its response extraction (lines 92-111) reads response.choices[0].message — must also accept a normalized LLMResult (branch on type).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: llm_logger — accept + persist provider/key_source, handle LLMResult shape</name>
  <read_first>
    - app/services/llm_logger.py (full file — the log_llm_call signature + response extraction block lines 85-138)
    - app/models/__init__.py::LLMCall (now has provider + key_source columns from 18-01)
    - app/services/llm/base.py::LLMResult (the normalized shape the logger must also read)
    - tests/test_llm_logger_provider.py (RED assertion: row has provider + key_source)
  </read_first>
  <behavior>
    - log_llm_call(..., provider='anthropic', key_source='byok', response=<LLMResult>) writes an llm_calls row with provider=='anthropic', key_source=='byok', response_text=LLMResult.text, tool_calls from LLMResult.tool_calls, tokens from LLMResult.usage.
    - log_llm_call(..., provider='openai', key_source='platform', response=<ChatCompletion>) still reads the OpenAI object graph as today.
    - Both provider/key_source default None (backward-compatible) and never raise.
  </behavior>
  <action>
    In `app/services/llm_logger.py`:
    - Add two kwargs to `log_llm_call`: `provider: Optional[str] = None`, `key_source: Optional[str] = None`.
    - In the response-extraction block, branch on the response type: if it is an `LLMResult` (import from `app.services.llm.base`; use `isinstance` or duck-type on `.text`/`.tool_calls` attributes), read `response_text = response.text`, `tool_calls_json = [{"id":tc.id,"name":tc.name,"arguments":tc.arguments} for tc in response.tool_calls]`, and `usage = response.usage` dict -> prompt_tokens/completion_tokens/total_tokens. Otherwise keep the existing `response.choices[0].message` path (OpenAI object) unchanged.
    - Pass `provider=provider, key_source=key_source` into the `LLMCall(...)` constructor.
    - Preserve the NEVER-RAISE contract and the no-prompt-in-logs guard (do not add any logger call carrying prompt/response content or the key).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_logger_provider.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - `log_llm_call` signature includes `provider` and `key_source`
    - `LLMCall(...)` constructor call includes `provider=provider` and `key_source=key_source`
    - Logger reads both a normalized `LLMResult` (`.text`/`.tool_calls`/`.usage`) and the legacy OpenAI `.choices[0].message` shape
    - `tests/test_llm_logger_provider.py` passes (exit 0)
    - No new `logger.` line carries the prompt/response content or an api key (grep-review)
  </acceptance_criteria>
  <done>llm_logger records provider + key_source and reads the normalized LLMResult; logger-provider test GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Route generate_response through the adapter + provider-neutral second-pass turn + D-06 fallback</name>
  <read_first>
    - app/services/ai_engine.py lines 1279-1700 (generate_response — the three call sites, empty-guard, tool dispatch, second pass at 1635-1667, error handlers)
    - app/services/ai_engine.py lines 1635-1642 (the RAW `messages.append(response_message)` + tool-result appends — the BLOCKER to fix on the APPEND side)
    - app/services/ai_engine.py lines 41, 61-95 (platform singleton + _is_reasoning_model + _build_completion_params)
    - app/services/llm/__init__.py + resolve.py + base.py (from 18-02)
    - app/services/llm/anthropic_provider.py (from 18-02 — how it translates a neutral assistant tool-call turn into tool_use blocks; the second-pass dict must match that shape)
    - app/services/llm/capabilities.py::is_reasoning_model (single source; ai_engine._is_reasoning_model becomes a re-export)
    - tests/test_ai_engine_empty_retry.py (the patch seam that must keep working through the adapter)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Pattern 2/3 + § Pitfall 4/6/7
  </read_first>
  <behavior>
    - generate_response resolves LLMConfig once via resolve_llm_config(session, workspace_id) where workspace_id comes from campaign_context.
    - All three LLM calls go through provider.complete(...) (initial with tools, empty-retry with a larger budget + minimal effort, second-pass no tools). The empty-guard still fires on normalized finish_reason=='length'.
    - Tool dispatch reads LLMResult.tool_calls (ToolCall.name, ToolCall.arguments str-JSON) — the existing built-in/custom split works unchanged (same field names).
    - SECOND-PASS APPEND is provider-neutral: instead of appending the raw OpenAI SDK `response_message`, the code appends a plain dict `{"role":"assistant","content": result.text or "", "tool_calls":[{"id":tc.id,"name":tc.name,"arguments":tc.arguments} for tc in result.tool_calls]}` built from the normalized LLMResult, followed by the existing `{"role":"tool", tool_call_id, content}` results. Both OpenAI (which accepts the assistant+tool_calls dict) and Anthropic (which translates the neutral assistant turn into tool_use blocks, 18-02) can consume this messages list.
    - On is_key_level_error during a byok call: log the failure, flip llm_settings.api_key_status='invalid' for that workspace, rebuild the provider from platform_fallback_config, retry the SAME call once on platform OpenAI (key_source='fallback'), and continue. Transient 429/5xx are NOT fallback — they hit the existing RateLimitError/APIConnectionError/APIStatusError handlers returning None (unchanged).
    - The module-level `client` (platform AsyncOpenAI) is untouched and still used ONLY by transcribe_audio + embeddings paths (D-12).
  </behavior>
  <action>
    In `app/services/ai_engine.py`:
    - Import `from app.services.llm import resolve_llm_config, get_provider, is_key_level_error, platform_fallback_config` and `from app.services.llm.capabilities import is_reasoning_model as _cap_is_reasoning_model`.
    - Replace the body of `_is_reasoning_model` with `return _cap_is_reasoning_model(model)` (single source; keep the function name so existing callers still work), OR leave it and have both delegate — do NOT keep two divergent implementations.
    - In `generate_response`, after `campaign_context` is resolved, add: `ws_id = (campaign_context or {}).get("workspace_id")`; `llm_cfg = await resolve_llm_config(session, ws_id) if ws_id else platform_fallback_config(settings)`. (Legacy path with no campaign_context -> platform default, D-02.)
    - Build a helper INSIDE generate_response (or a small module fn) `async def _complete(messages, tools, retry, cfg) -> LLMResult` that: `provider = get_provider(cfg)`; computes `max_tokens` = cfg.max_tokens or (AI_MAX_COMPLETION_TOKENS_RETRY if retry else AI_MAX_COMPLETION_TOKENS); `reasoning_effort` = cfg.reasoning_effort or (AI_REASONING_EFFORT_RETRY if retry else AI_REASONING_EFFORT); `temperature`=cfg.temperature; extracts the `system` string from messages[0] and passes the rest; calls `await provider.complete(system=..., messages=..., tools=tools, model=cfg.model, max_tokens=max_tokens, temperature=temperature, reasoning_effort=reasoning_effort)`. Wrap the call: on `except Exception as e:` if `is_key_level_error(e)` and `cfg.key_source=='byok'`: log loudly, `await _flag_key_invalid(session, ws_id)` (small UPDATE llm_settings SET api_key_status='invalid'), rebuild `cfg2 = platform_fallback_config(settings)`, retry ONCE via `get_provider(cfg2).complete(...)`, and use cfg2 for logging; else re-raise (existing handlers catch RateLimitError/APIConnectionError/APIStatusError -> None).
    - Replace the three `await client.chat.completions.create(**params)` sites with `await _complete(...)`. Update the surrounding code to read from `LLMResult` instead of `response.choices[0].message`:
      - `text_content = result.text`; `text_content_clean = text_content.strip() if text_content else None`.
      - `response_message.tool_calls` -> `result.tool_calls` (list of ToolCall). The built-in/custom split loop uses `tool_call.name` and `json.loads(tool_call.arguments or "{}")` — ToolCall exposes `.name` and `.arguments` (str JSON), same as before via `.function.name`/`.function.arguments`. Adjust the two attribute reads accordingly.
      - Empty-guard checks `result.finish_reason == "length"` (normalized).
    - FIX THE SECOND-PASS APPEND (BLOCKER): at line ~1636, REPLACE `messages.append(response_message)` (which appends the raw OpenAI ChatCompletionMessage SDK object) with a provider-neutral assistant turn built from the first-call LLMResult. Construct a plain dict:
      ```python
      assistant_turn = {
          "role": "assistant",
          "content": result.text or "",
          "tool_calls": [
              {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
              for tc in result.tool_calls
          ],
      }
      messages.append(assistant_turn)
      ```
      (Use the LLMResult from the FIRST/initial call — the one whose tool_calls were dispatched, not a fresh object.) Do NOT append the raw SDK `response_message` anymore. Keep the existing `{"role":"tool", tool_call_id, content}` appends after it. AnthropicProvider (18-02) translates this neutral assistant turn into `tool_use` content blocks and the tool messages into `tool_result` blocks, so the SAME messages list works for both providers on the second pass. Then call the second pass via `await _complete(messages, tools=None, retry=False, cfg=...)`.
    - Keep `log_llm_call(...)` at all three sites, now passing `provider=cfg.provider, key_source=cfg.key_source` (or cfg2's on fallback) and `model=cfg.model`. `response=` becomes the LLMResult (logger handles it — Task 1).
    - Add a small `async def _flag_key_invalid(session, workspace_id)` helper doing an idempotent `UPDATE llm_settings SET api_key_status='invalid', updated_at=NOW() WHERE workspace_id=:ws` (best-effort, swallow errors — must never break the reply).
    - Do NOT touch `transcribe_audio` (Whisper) or any embedding call — they keep the module-level platform `client` (D-12).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine_empty_retry.py tests/test_llm_provider.py -x -q 2>&1 | tail -8</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/ai_engine.py` imports `resolve_llm_config`, `get_provider`, `is_key_level_error`, `platform_fallback_config`
    - `generate_response` calls `resolve_llm_config` (grep) and no longer calls `client.chat.completions.create` in the answerer path (grep: the three answerer call sites now go through `_complete`/`provider.complete`; the ONLY remaining `client.` uses are transcribe_audio/embeddings)
    - Second-pass append is provider-neutral: `app/services/ai_engine.py` NO LONGER contains `messages.append(response_message)` (grep must NOT match it) and DOES construct an assistant turn dict from the LLMResult before appending (grep: `"role": "assistant"` AND `"tool_calls"` in the second-pass block, built from `result.tool_calls` / `tc.arguments`)
    - D-06 fallback: grep `is_key_level_error` AND `api_key_status='invalid'` (or `_flag_key_invalid`) present in ai_engine.py
    - `_is_reasoning_model` delegates to `capabilities.is_reasoning_model` (no duplicated startswith list)
    - `log_llm_call` calls in ai_engine pass `provider=` and `key_source=`
    - `transcribe_audio` still references the module-level `client` (grep `client.audio` unchanged)
    - `tests/test_ai_engine_empty_retry.py` passes (updated to patch the adapter path — see Task 4) and `tests/test_llm_provider.py` still GREEN
  </acceptance_criteria>
  <done>The answerer routes all three LLM calls through the workspace-resolved provider with the empty-guard preserved, the second pass appends a provider-neutral assistant turn (no raw SDK object), and D-06 fallback is wired; Whisper untouched.</done>
</task>

<task type="auto">
  <name>Task 3: Route warmup through the workspace-aware provider factory (D-11)</name>
  <read_first>
    - app/services/warmup.py lines 90-140 (WarmupWorker.__init__ with self._openai = AsyncOpenAI())
    - app/services/warmup.py lines 620-670 (_generate_message — the chat.completions.create call + from_sender_id)
    - app/services/warmup.py (find where _generate_message is CALLED — the from_sender dict with workspace_id is available there; grep `_generate_message(`)
    - app/services/llm/__init__.py + resolve.py (resolve_llm_config, get_provider, platform_fallback_config)
    - .planning/phases/18-switchable-llm-provider/18-CONTEXT.md D-11 (warmup uses the same chosen LLM — single tone)
  </read_first>
  <action>
    In `app/services/warmup.py`:
    - Remove `self._openai = AsyncOpenAI()` from `__init__` (and the now-unused `AsyncOpenAI` import if nothing else uses it — check first).
    - In `_generate_message`, accept a resolved config or the workspace_id so it can build the provider. Simplest: add a `workspace_id: Optional[str] = None` param; inside, open a short-lived session (mirror the `async with AsyncSessionLocal() as db` pattern already in warmup) OR reuse a passed session, call `cfg = await resolve_llm_config(db, workspace_id) if workspace_id else platform_fallback_config(settings)`, then `provider = get_provider(cfg)`, and `result = await provider.complete(system=messages[0]["content"], messages=messages[1:], tools=None, model=cfg.model, max_tokens=cfg.max_tokens or 1024, temperature=cfg.temperature, reasoning_effort=cfg.reasoning_effort)`; `return result.text.strip() if result.text else None`. Warmup history is built the same direction->role way as the answerer, so consecutive same-role turns are possible — AnthropicProvider's role-coalescing (18-02) handles this transparently, warmup needs no extra merge logic.
    - At the call site of `_generate_message`, pass the workspace_id from the `from_sender` dict (`from_sender["workspace_id"]`) so warmup uses that workspace's chosen provider (D-11).
    - Keep the existing APIError/Exception handling (return None on failure — warmup must degrade gracefully, never crash the tick). Warmup LLM calls are NOT logged to llm_calls (unchanged — D-09..D-12 from Phase 5).
    - Note: warmup does NOT get the D-06 fallback (that is answerer-specific); a warmup key error just returns None for that message (acceptable — warmup is best-effort, not a live customer dialog).
  </action>
  <verify>
    <automated>grep -q "get_provider\|resolve_llm_config" app/services/warmup.py && ! grep -q "self._openai = AsyncOpenAI()" app/services/warmup.py && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/warmup.py` no longer contains `self._openai = AsyncOpenAI()`
    - `_generate_message` builds the provider via `get_provider` and resolves config via `resolve_llm_config` (or `platform_fallback_config` when no workspace_id)
    - `_generate_message` returns `result.text` from the normalized `LLMResult`
    - The call site passes `from_sender["workspace_id"]` (grep the call site)
    - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/ -k warmup -x -q` still GREEN (no regressions in warmup tests)
  </acceptance_criteria>
  <done>Warmup routes through the same workspace-aware provider factory (D-11); the standalone AsyncOpenAI is gone.</done>
</task>

<task type="auto">
  <name>Task 4: Update empty-retry test for the adapter + finalize isolation guard, run full suite</name>
  <read_first>
    - tests/test_ai_engine_empty_retry.py (full file — the current patch of `ai_engine.client.chat.completions.create`)
    - tests/test_llm_isolation.py (from 18-01 — the Whisper/embeddings platform-singleton guard)
    - app/services/ai_engine.py (post-Task-2 state — how _complete/provider is invoked so the test can patch the right seam)
  </read_first>
  <action>
    Update `tests/test_ai_engine_empty_retry.py` so the empty-guard/retry test patches the NEW seam: instead of patching `ai_engine.client.chat.completions.create`, patch the provider's `complete` (e.g. `patch.object(OpenAIProvider, "complete", new=AsyncMock(side_effect=[<empty LLMResult finish_reason='length'>, <non-empty LLMResult>]))`) so the two-call empty-then-retry path is exercised through the adapter. Keep the assertion that a retry happens and the second (non-empty) result is returned. If mocking `resolve_llm_config` is simpler, patch it to return a platform-default LLMConfig so the test does not need a DB row.

    Confirm `tests/test_llm_isolation.py` passes now that ai_engine keeps the platform `client` only for Whisper/embeddings (it should already be GREEN after Task 2 — if it references a helper name that changed, align the assertion to the final source, but do NOT weaken it: it MUST still assert Whisper/embeddings use the platform singleton and NOT the new factory).

    Run the FULL suite via test-overlay and confirm no regressions (~865 baseline + new tests all green).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q 2>&1 | tail -8</automated>
  </verify>
  <acceptance_criteria>
    - `tests/test_ai_engine_empty_retry.py` patches the adapter/provider seam (grep: `OpenAIProvider` or `resolve_llm_config` or `.complete`), NOT `client.chat.completions.create`
    - `tests/test_llm_isolation.py` passes and still asserts Whisper/embeddings use the platform singleton
    - Full suite via test-overlay is GREEN (exit 0) — no regressions in the ~865 baseline
  </acceptance_criteria>
  <done>Empty-retry test exercises the adapter path; isolation guard confirmed; full suite GREEN.</done>
</task>

</tasks>

<verification>
- Full suite GREEN via test-overlay: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q`
- Answerer + warmup route through the provider factory; three answerer call sites no longer call the raw OpenAI client
- Second pass appends a provider-neutral assistant turn built from LLMResult (no `messages.append(response_message)` raw SDK object) — Claude tool flows survive the second pass
- D-06 fallback triggers only on key-level errors; transient errors keep existing None-degrade
- Whisper + embeddings still on the platform singleton (D-12) — test_llm_isolation GREEN
- llm_logger records provider + key_source
</verification>

<success_criteria>
- ai_engine + warmup + llm_logger wired to the adapter
- second-pass message list is provider-neutral (both providers translate it)
- test_llm_logger_provider, test_llm_isolation, test_ai_engine_empty_retry GREEN; full suite GREEN
- No PROTECTED queue constant touched; empty-response guard preserved across providers
</success_criteria>

<output>
After completion, create `.planning/phases/18-switchable-llm-provider/18-04-SUMMARY.md`
</output>
</output>
