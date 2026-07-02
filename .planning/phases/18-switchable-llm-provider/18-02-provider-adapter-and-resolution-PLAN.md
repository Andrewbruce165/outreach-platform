---
phase: 18-switchable-llm-provider
plan: 02
type: execute
wave: 2
depends_on: ["18-01"]
files_modified:
  - app/services/llm/__init__.py
  - app/services/llm/base.py
  - app/services/llm/capabilities.py
  - app/services/llm/openai_provider.py
  - app/services/llm/anthropic_provider.py
  - app/services/llm/resolve.py
  - app/services/encryption.py
autonomous: true
requirements: [LLMP-03, LLMP-04, LLMP-06, LLMP-09, LLMP-10, LLMP-11]
must_haves:
  truths:
    - "An internal messages+tools representation translates correctly to both OpenAI and Anthropic native shapes"
    - "Both providers normalize their response into a single LLMResult{text, tool_calls, finish_reason, usage}"
    - "Temperature is never sent to OpenAI reasoning models; max_tokens for reasoning models is clamped to >=4000"
    - "resolve_llm_config returns platform default when no llm_settings row exists"
    - "is_key_level_error is True only for 401/403/insufficient_quota/402 — never transient 429/5xx"
  artifacts:
    - path: "app/services/llm/base.py"
      provides: "LLMProvider protocol + LLMResult/ToolCall normalized types"
      contains: "class LLMResult"
    - path: "app/services/llm/openai_provider.py"
      provides: "OpenAI translation + response normalization"
      contains: "chat.completions.create"
    - path: "app/services/llm/anthropic_provider.py"
      provides: "Anthropic translation + response normalization"
      contains: "messages.create"
    - path: "app/services/llm/capabilities.py"
      provides: "capability gating + clamp + effort->budget mapping"
      contains: "def clamp_max_tokens"
    - path: "app/services/llm/resolve.py"
      provides: "workspace config resolution + fallback decision + is_key_level_error"
      contains: "def is_key_level_error"
  key_links:
    - from: "app/services/llm/resolve.py"
      to: "app/services/encryption.py"
      via: "decrypt the BYO api key"
      pattern: "decrypt_api_key|decrypt_session"
    - from: "app/services/llm/openai_provider.py::complete"
      to: "app/services/llm/base.py::LLMResult"
      via: "returns normalized result"
      pattern: "LLMResult"
---

<objective>
Build the `app/services/llm/` provider-adapter package: a `LLMProvider` protocol with `OpenAIProvider` + `AnthropicProvider`, each translating one internal `system+messages+tools` representation into the provider's native shape and normalizing the response into a plain `LLMResult`. Add the pure capability/clamp helpers (D-09/D-10), the workspace-config resolver + fallback classifier (D-03/D-06), and Fernet key helpers (D-04).

Purpose: This is the phase's core abstraction — a single choke point so the answerer + warmup route through any provider without per-call `if provider` branching. It is pure/testable in isolation (no listener changes here).
Output: 6 new files under `app/services/llm/` + `encrypt_api_key`/`decrypt_api_key` aliases in `encryption.py`. Turns the RED tests `test_llm_capabilities`, `test_llm_fallback`, `test_llm_provider` GREEN.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/18-switchable-llm-provider/18-CONTEXT.md
@.planning/phases/18-switchable-llm-provider/18-RESEARCH.md

<interfaces>
<!-- Extracted from codebase + RESEARCH translation map. Executor uses these directly. -->

app/services/ai_engine.py:61 (existing reasoning gate — reuse/extend, do not duplicate logic):
```python
def _is_reasoning_model(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))
```

app/services/ai_engine.py:71-95 (existing OpenAI param assembly — becomes OpenAIProvider translation):
```python
params = {"model": model, "messages": messages, "max_completion_tokens": budget}
if _is_reasoning_model(model):
    params["reasoning_effort"] = effort   # 'low' first, 'minimal' retry
if tools:
    params["tools"] = tools; params["tool_choice"] = "auto"
# temperature ONLY for non-reasoning OpenAI models (else 400 unsupported_value)
```

app/services/encryption.py (Fernet helper to reuse — same ENCRYPTION_KEY):
```python
def encrypt_session(session_string: str) -> str: ...
def decrypt_session(encrypted: str) -> str: ...
```

RESEARCH translation map (OpenAI chat.completions  vs  Anthropic messages):
| Concept | OpenAI | Anthropic |
| System | messages[0]={role:"system"} | top-level system= param (NOT a message) |
| Max out | max_completion_tokens (reasoning) / max_tokens | max_tokens (REQUIRED always) |
| Tools | tools=[{type:"function", function:{name,description,parameters}}] | tools=[{name,description,input_schema}] |
| Tool call out | message.tool_calls[].{id, function.name, function.arguments(str JSON)} | content block {type:"tool_use", id, name, input(dict)} |
| Tool result back | {role:"tool", tool_call_id, content} | user content block {type:"tool_result", tool_use_id, content} |
| Reply text | message.content | concat content blocks {type:"text", text} |
| Finish | finish_reason (stop/length/tool_calls) | stop_reason (end_turn/max_tokens/tool_use) |
| Temperature | 0.0-2.0; rejected by reasoning models | 0.0-1.0 |
| Usage | usage.{prompt_tokens, completion_tokens, total_tokens} | usage.{input_tokens, output_tokens} |

is_key_level_error taxonomy (RESEARCH Code Examples — copy verbatim):
```python
import openai, anthropic
def is_key_level_error(e) -> bool:
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return True
    if isinstance(e, openai.RateLimitError) and getattr(e, "code", None) == "insufficient_quota":
        return True
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    if isinstance(e, anthropic.APIStatusError) and getattr(e, "status_code", None) == 402:
        return True
    return False
```

reasoning_effort -> Claude thinking budget (RESEARCH recommended table):
```python
EFFORT_TO_BUDGET = {"minimal": 0, "low": 2000, "medium": 8000, "high": 16000}
# budget=0 => omit thinking; always clamp budget = min(budget, max_tokens - 512) (Pitfall 2)
# For effort-capable Claude 5-series: pass reasoning_effort through / thinking={"type":"adaptive"}
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: base types + capabilities (pure helpers) + encryption aliases</name>
  <read_first>
    - app/services/ai_engine.py lines 49-95 (AI_MAX_COMPLETION_TOKENS=4000/RETRY=6000, AI_REASONING_EFFORT, _is_reasoning_model, _build_completion_params — the existing constants and gate to preserve/reuse)
    - app/services/encryption.py (full file — add aliases, don't change existing)
    - tests/test_llm_capabilities.py (the RED assertions this task must satisfy)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Provider Capability Matrix + § Pitfall 2/3/4
  </read_first>
  <behavior>
    - is_reasoning_model("gpt-5-mini")==True, ("gpt-4o-mini")==False, ("o3-mini")==True, ("claude-sonnet-4-5")==False
    - temperature_allowed("gpt-5-mini")==False, ("gpt-4o")==True, ("claude-sonnet-4-5")==True
    - clamp_max_tokens(500, model="gpt-5-mini") >= 4000 (D-10 reasoning floor); clamp_max_tokens(500, model="gpt-4o")==500 (non-reasoning may be lower); clamp of a huge value caps at a ceiling (e.g. 32000)
    - effort_to_budget("minimal", max_tokens=4000)==0; effort_to_budget("high", max_tokens=4000) < 4000 (always < max_tokens - 512)
    - LLMResult(text=..., tool_calls=[...], finish_reason=..., usage=...) is a dataclass with those fields
  </behavior>
  <action>
    Create `app/services/llm/__init__.py` (empty package marker + re-export `get_provider` later — for now just `# Phase 18 provider-adapter package`).

    Create `app/services/llm/base.py` with normalized dataclasses and the provider protocol:
    ```python
    from dataclasses import dataclass, field
    from typing import Any, Optional, Protocol

    @dataclass
    class ToolCall:
        id: str
        name: str
        arguments: str        # JSON string (OpenAI-native shape; Anthropic dict is json.dumps'd on normalize)

    @dataclass
    class LLMResult:
        text: Optional[str]
        tool_calls: list[ToolCall] = field(default_factory=list)
        finish_reason: Optional[str] = None      # normalized: 'stop'|'length'|'tool_calls'
        usage: dict = field(default_factory=dict)  # {prompt_tokens, completion_tokens, total_tokens}
        raw: Any = None

    class LLMProvider(Protocol):
        async def complete(self, *, system: str, messages: list, tools: Optional[list],
                           model: str, max_tokens: int, temperature: Optional[float],
                           reasoning_effort: Optional[str]) -> LLMResult: ...
    ```
    Normalize finish_reason so Anthropic `max_tokens` -> `'length'`, `tool_use` -> `'tool_calls'`, `end_turn`/`stop` -> `'stop'` (so the existing empty-guard `finish_reason=='length'` retry logic works unchanged across providers).

    Create `app/services/llm/capabilities.py` — PURE functions, no I/O:
    ```python
    REASONING_MAX_TOKENS_FLOOR = 4000     # D-10, 2026-07-02 ghosted-contact incident
    MAX_TOKENS_CEILING = 32000            # green-corridor ceiling (discretion)
    EFFORT_TO_BUDGET = {"minimal": 0, "low": 2000, "medium": 8000, "high": 16000}

    def is_reasoning_model(model: str) -> bool:
        m = (model or "").lower()
        return m.startswith(("gpt-5", "o1", "o3", "o4"))

    def temperature_allowed(model: str) -> bool:
        # OpenAI reasoning models reject temperature (400 unsupported_value).
        # Non-reasoning OpenAI + all Claude accept it.
        return not is_reasoning_model(model)

    def clamp_max_tokens(value: Optional[int], *, model: str) -> int:
        # D-10 hard clamp. Reasoning models floor at 4000 (incident). Ceiling for all.
        if value is None:
            value = REASONING_MAX_TOKENS_FLOOR if is_reasoning_model(model) else 4000
        if is_reasoning_model(model):
            value = max(value, REASONING_MAX_TOKENS_FLOOR)
        return min(value, MAX_TOKENS_CEILING)

    def effort_to_budget(effort: Optional[str], *, max_tokens: int) -> int:
        # Claude manual-thinking budget; always < max_tokens (Pitfall 2).
        budget = EFFORT_TO_BUDGET.get((effort or "").lower(), 0)
        if budget <= 0:
            return 0
        return min(budget, max_tokens - 512)
    ```
    Import `is_reasoning_model` from here into `ai_engine` later (do NOT duplicate — plan 18-04 replaces `ai_engine._is_reasoning_model`'s body with a re-export). For THIS plan, just define it here.

    In `app/services/encryption.py`, add two aliases at the bottom (reuse the exact same Fernet — one key, one code path, D-04):
    ```python
    def encrypt_api_key(api_key: str) -> str:
        return encrypt_session(api_key)

    def decrypt_api_key(encrypted: str) -> str:
        return decrypt_session(encrypted)
    ```
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_capabilities.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/llm/base.py` contains `class LLMResult` and `class ToolCall` and `class LLMProvider(Protocol)`
    - `app/services/llm/capabilities.py` contains `def clamp_max_tokens`, `def temperature_allowed`, `def effort_to_budget`, `REASONING_MAX_TOKENS_FLOOR = 4000`
    - `app/services/encryption.py` contains `def encrypt_api_key` and `def decrypt_api_key`
    - `tests/test_llm_capabilities.py` passes (exit 0)
  </acceptance_criteria>
  <done>Pure capability/clamp helpers + normalized types + Fernet key aliases exist; capability tests GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: OpenAIProvider + AnthropicProvider translation & normalization</name>
  <read_first>
    - app/services/ai_engine.py lines 1420-1500 (how the current code reads response.choices[0].message.content / .tool_calls / finish_reason — the OpenAI response shape to normalize)
    - app/services/llm/base.py (LLMResult/ToolCall/LLMProvider from Task 1)
    - app/services/llm/capabilities.py (is_reasoning_model, temperature_allowed, clamp_max_tokens, effort_to_budget)
    - tests/test_llm_provider.py (the RED translation + normalization assertions)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Pattern 1 translation map + Anthropic native call example + § Pitfall 1/2
  </read_first>
  <behavior>
    - OpenAIProvider.complete builds params: messages[0].role=='system', max_completion_tokens=clamped (reasoning) present, reasoning_effort only for reasoning model, temperature only when temperature_allowed and not None, tools kept in {type:'function', function:{...}} shape, tool_choice='auto' when tools.
    - AnthropicProvider.complete builds native call: system= is top-level (system NOT in messages), max_tokens=clamped (required), tools reshaped to {name, description, input_schema}, thinking added only when effort_to_budget>0 (manual) — omitted when 0, temperature 0.0-1.0 only when not None.
    - AnthropicProvider normalizes a Message with content blocks: text = "".join text blocks; tool_calls from tool_use blocks (arguments = json.dumps(block.input)); finish_reason 'max_tokens'->'length', 'tool_use'->'tool_calls', else 'stop'; usage mapped input_tokens->prompt_tokens, output_tokens->completion_tokens.
    - OpenAIProvider normalizes ChatCompletion: text=choices[0].message.content; tool_calls from message.tool_calls; finish_reason passed through ('length' stays 'length'); usage passed through.
  </behavior>
  <action>
    Create `app/services/llm/openai_provider.py`:
    - `class OpenAIProvider` holding an `AsyncOpenAI` client (constructed from a passed api_key).
    - `async def complete(...)` assembles params exactly like `_build_completion_params` (RESEARCH Code Example): `max_completion_tokens = clamp_max_tokens(max_tokens, model=model)`; `reasoning_effort` only when `is_reasoning_model(model)`; `temperature` only when `temperature_allowed(model) and temperature is not None`; `tools` + `tool_choice='auto'` when tools. Calls `await self.client.chat.completions.create(**params)` then `return self._normalize(resp)`.
    - `_normalize(resp) -> LLMResult`: read `choices[0].message.content`, map `.tool_calls[]` to `ToolCall(id, function.name, function.arguments)`, `finish_reason` pass-through, `usage.{prompt_tokens,completion_tokens,total_tokens}`.

    Create `app/services/llm/anthropic_provider.py`:
    - `from anthropic import AsyncAnthropic` + import the typed exceptions used by resolve.
    - `class AnthropicProvider` holding an `AsyncAnthropic` client.
    - `async def complete(...)`: build `params = {"model": model, "max_tokens": clamp_max_tokens(max_tokens, model=model), "system": system, "messages": messages_without_system}`; add `tools=[{"name":..,"description":..,"input_schema":..}]` translated from the OpenAI-shape tools (strip the `type:'function'`/`function:{}` wrapper, `parameters`->`input_schema`); add `temperature` only when not None (0.0-1.0); compute `budget = effort_to_budget(reasoning_effort, max_tokens=params["max_tokens"])` and add `thinking={"type":"enabled","budget_tokens":budget}` ONLY when `budget>0` (Pitfall 1/2 — omit for 5-series/adaptive; the capability-aware path can be refined in wiring but manual-budget is the safe default and is gated by budget>0). Also translate any incoming `{role:'tool', tool_call_id, content}` messages into Anthropic user `tool_result` content blocks, and any assistant tool_call turns into `tool_use` content blocks (needed for the second-pass call). Call `await self.client.messages.create(**params)` then `return self._normalize(resp)`.
    - `_normalize(resp) -> LLMResult`: `text = "".join(b.text for b in resp.content if getattr(b,'type',None)=='text')`; `tool_calls = [ToolCall(id=b.id, name=b.name, arguments=json.dumps(b.input, ensure_ascii=False)) for b in resp.content if getattr(b,'type',None)=='tool_use']`; map `stop_reason` ('max_tokens'->'length', 'tool_use'->'tool_calls', else 'stop'); `usage = {'prompt_tokens': resp.usage.input_tokens, 'completion_tokens': resp.usage.output_tokens, 'total_tokens': input+output}`.

    Do NOT make Anthropic emulate OpenAI's object graph — normalize into the plain `LLMResult` (Anti-Pattern in RESEARCH).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_provider.py -x -q 2>&1 | tail -6</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/llm/openai_provider.py` contains `class OpenAIProvider` and calls `self.client.chat.completions.create`
    - `app/services/llm/anthropic_provider.py` contains `class AnthropicProvider` and calls `self.client.messages.create`
    - Anthropic path builds `system=` as a top-level param (grep: `"system": system` or `system=`) and `max_tokens` (required)
    - Anthropic tool translation produces `input_schema` (grep `input_schema`), never `type":"function"`
    - Both providers return `LLMResult` (grep `LLMResult` in both files)
    - `tests/test_llm_provider.py` passes (exit 0)
  </acceptance_criteria>
  <done>OpenAIProvider + AnthropicProvider translate the internal representation to native and normalize responses to LLMResult; provider tests GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: resolve.py — workspace config, provider factory, fallback classifier</name>
  <read_first>
    - app/services/llm/base.py + capabilities.py + openai_provider.py + anthropic_provider.py (Tasks 1-2)
    - app/models/__init__.py::LLMSettings (from 18-01)
    - app/config.py lines 18, 54 (encryption_key, openai_model — the platform default)
    - app/services/encryption.py (decrypt_api_key)
    - tests/test_llm_fallback.py (is_key_level_error taxonomy assertions)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Pattern 2 + § Pitfall 6 + Code Examples (is_key_level_error, build_client)
  </read_first>
  <behavior>
    - resolve_llm_config(session, workspace_id) with NO llm_settings row -> LLMConfig(provider='openai', model=settings.openai_model, key_source='platform', decrypted_key=platform OPENAI_API_KEY, temperature=None, reasoning_effort=code default, max_tokens=None)
    - with a byok row (api_key_status='valid') -> provider/model/knobs from the row, key_source='byok', decrypted_key=decrypt of api_key_encrypted
    - is_key_level_error(openai.AuthenticationError)==True; (openai.RateLimitError code='insufficient_quota')==True; (plain openai.RateLimitError)==False; (anthropic APIStatusError 402)==True; (500 APIStatusError)==False; (APIConnectionError)==False
    - get_provider(config) returns an OpenAIProvider for provider=='openai' and AnthropicProvider for provider=='anthropic'
  </behavior>
  <action>
    Create `app/services/llm/resolve.py`:
    - `@dataclass class LLMConfig`: `provider: str`, `model: str`, `decrypted_key: Optional[str]`, `key_source: str` ('platform'|'byok'|'fallback'), `temperature: Optional[float]`, `reasoning_effort: Optional[str]`, `max_tokens: Optional[int]`.
    - `PLATFORM_DEFAULT(settings)` helper returning an `LLMConfig` with `provider='openai'`, `model=settings.openai_model`, `decrypted_key=os.environ.get("OPENAI_API_KEY")`, `key_source='platform'`, knobs None (D-02 — byte-identical to today; reasoning_effort left None so ai_engine's existing AI_REASONING_EFFORT constant applies).
    - `async def resolve_llm_config(session, workspace_id) -> LLMConfig`: SELECT the llm_settings row for workspace_id. Absent row OR `api_key_status != 'valid'` OR `api_key_encrypted IS NULL` -> return PLATFORM_DEFAULT (D-02/D-03: switching requires a valid key). Present valid byok row -> decrypt key via `decrypt_api_key`, return byok LLMConfig with the row's model/temperature/reasoning_effort/max_tokens. Never log the decrypted key.
    - `def platform_fallback_config(settings) -> LLMConfig`: same as PLATFORM_DEFAULT but `key_source='fallback'` (used by ai_engine on D-06 key error).
    - `def is_key_level_error(e) -> bool`: copy VERBATIM from the interfaces block above (401/403/insufficient_quota/402 only — Pitfall 6: transient 429/5xx return False).
    - `def get_provider(config: LLMConfig) -> LLMProvider`: `AnthropicProvider(api_key=config.decrypted_key)` when provider=='anthropic' else `OpenAIProvider(api_key=config.decrypted_key or os.environ["OPENAI_API_KEY"])`.
    - Wire `app/services/llm/__init__.py` to re-export `get_provider`, `resolve_llm_config`, `platform_fallback_config`, `is_key_level_error`, `LLMConfig`.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_fallback.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/llm/resolve.py` contains `def is_key_level_error`, `async def resolve_llm_config`, `def get_provider`, `def platform_fallback_config`, `class LLMConfig`
    - `is_key_level_error` returns False for a plain `RateLimitError` without `code=='insufficient_quota'` (Pitfall 6) — asserted by `tests/test_llm_fallback.py::test_transient_errors_false`
    - `resolve_llm_config` returns platform default when no row / no valid key (D-02/D-03)
    - `app/services/llm/__init__.py` re-exports `get_provider` and `resolve_llm_config`
    - `tests/test_llm_fallback.py` passes (exit 0)
    - No occurrence of the decrypted key in any `logger.` call in resolve.py (grep: no `logger.*decrypted_key`)
  </acceptance_criteria>
  <done>resolve.py resolves workspace config (default-off D-02, byok requires valid key D-03), classifies key-level errors (D-06 Pitfall 6), and builds the right provider; fallback tests GREEN.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_capabilities.py tests/test_llm_provider.py tests/test_llm_fallback.py -x` — all GREEN
- Adapter is pure (no listener/ai_engine edits in this plan)
- Anthropic path: system top-level, max_tokens required, input_schema tools, budget<max_tokens
- OpenAI path: temperature gated off reasoning models, reasoning max_tokens floor >=4000
</verification>

<success_criteria>
- 6 files under app/services/llm/ + encryption aliases
- test_llm_capabilities, test_llm_provider, test_llm_fallback all GREEN
- No PROTECTED queue constant touched; no changes to ai_engine/warmup/listener (deferred to 18-04)
</success_criteria>

<output>
After completion, create `.planning/phases/18-switchable-llm-provider/18-02-SUMMARY.md`
</output>
