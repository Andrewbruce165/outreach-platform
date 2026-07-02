---
phase: 18-switchable-llm-provider
plan: 02
subsystem: api
tags: [anthropic, openai, llm, provider-adapter, fernet, capabilities, fallback]

# Dependency graph
requires:
  - phase: 18-switchable-llm-provider (plan 01)
    provides: anthropic SDK in requirements.txt, LLMSettings ORM (PK workspace_id), RED tests test_llm_capabilities/test_llm_provider/test_llm_fallback
  - phase: 01-multitenancy
    provides: Fernet encryption (encrypt_session/decrypt_session, single ENCRYPTION_KEY) reused for BYO key
provides:
  - app/services/llm/ provider-adapter package (base + capabilities + openai_provider + anthropic_provider + resolve)
  - LLMResult/ToolCall normalized types + LLMProvider protocol (single choke point, no per-call `if provider`)
  - OpenAIProvider + AnthropicProvider (native translation both ways + response normalization; Anthropic role coalescing for the debounce alternation constraint)
  - pure capability helpers (is_reasoning_model/supports_temperature/clamp_max_tokens/effort_to_budget) — D-09/D-10 green corridor
  - resolve_llm_config (D-02/D-03 default-off) + platform_fallback_config + is_key_level_error (D-06) + get_provider factory
  - encrypt_api_key/decrypt_api_key Fernet aliases (D-04)
affects: [18-03-settings-api, 18-04-wire-answerer-warmup-logger, 18-05-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "One internal representation (system + OpenAI-shape messages[] + OpenAI-shape tools[]) translated to each provider's native shape by the adapter; callers only see LLMResult (no provider object graph leaks)"
    - "Anthropic role coalescing (_coalesce_roles merges consecutive same-role plain-text turns with '\\n\\n') runs before messages.create so debounced multi-turn dialogs never 400 on the alternation constraint"
    - "Pure capability module (no I/O, no SDK import) as the single source of truth for reasoning gate + max_tokens clamp + effort→budget"
    - "Absence-of-valid-key resolves to the platform default byte-identical to today (default-off provider switch, D-02/D-03)"
    - "normalize_response returns provider-NATIVE values (Anthropic tool input stays dict, stop_reason pass-through); LLMResult.finish_reason_normalized gives the cross-provider view for the empty-response 'length' retry"

key-files:
  created:
    - app/services/llm/__init__.py
    - app/services/llm/base.py
    - app/services/llm/capabilities.py
    - app/services/llm/openai_provider.py
    - app/services/llm/anthropic_provider.py
    - app/services/llm/resolve.py
  modified:
    - app/services/encryption.py

key-decisions:
  - "Interface names follow the RED tests verbatim (source of truth): supports_temperature (not temperature_allowed), clamp_max_tokens(model, value) positional, effort_to_budget(effort, max_tokens) positional, provider ctors take model=, providers expose build_params/complete/normalize_response"
  - "normalize_response is provider-native (Anthropic ToolCall.arguments is the parsed dict, finish_reason is the raw stop_reason); cross-provider normalization lives in LLMResult.finish_reason_normalized so the existing empty-guard 'length' retry works for both providers without forcing Anthropic to emulate OpenAI"
  - "ToolCall.arguments typed Any — OpenAI yields a JSON string (function.arguments), Anthropic yields a dict (tool_use.input); both representable so neither provider is forced to emulate the other (research anti-pattern avoided)"
  - "resolve.py has zero logging — the decrypted BYO key can never leak to app logs (stronger than the plan's 'never log the key')"

patterns-established:
  - "Provider adapter: build_params (pure translation, unit-testable without a network call) + complete (build_params → native create → normalize_response) + normalize_response (native → LLMResult)"
  - "Green-corridor clamp enforced in the backend regardless of UI input (reasoning max_tokens floor >=4000, ceiling 32000, thinking budget < max_tokens)"

requirements-completed: [LLMP-03, LLMP-04, LLMP-06, LLMP-09, LLMP-10, LLMP-11]

# Metrics
duration: 7min
completed: 2026-07-02
---

# Phase 18 Plan 02: Provider Adapter and Resolution Summary

**A pure `app/services/llm/` provider-adapter package — LLMResult/ToolCall/LLMProvider normalized types, OpenAIProvider + AnthropicProvider that translate one internal system+messages+tools representation into each provider's native shape (with Anthropic role coalescing so debounced multi-turn dialogs never 400 on the alternation constraint), capability/clamp helpers (reasoning max_tokens floor >=4000, D-10), and the workspace-config resolver + key-level-error classifier (default-off D-02/D-03, Pitfall-6-safe D-06) — turning test_llm_capabilities/test_llm_provider/test_llm_fallback GREEN.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-07-02T11:12:13Z
- **Completed:** 2026-07-02T11:19:00Z
- **Tasks:** 3
- **Files modified:** 7 (6 created under app/services/llm/ + 1 modified encryption.py)

## Accomplishments
- `app/services/llm/base.py`: normalized `LLMResult`/`ToolCall` dataclasses + `LLMProvider` protocol + `LLMResult.finish_reason_normalized` cross-provider stop-reason view — the single choke point so the answerer/warmup route through any provider without per-call `if provider` branching.
- `app/services/llm/capabilities.py`: PURE helpers — `is_reasoning_model` (gpt-5*/o1/o3/o4), `supports_temperature` (reasoning models rejected), `clamp_max_tokens` (D-10 reasoning floor >=4000 from the 2026-07-02 ghosted-contact incident + 32000 ceiling), `effort_to_budget` (Claude manual-thinking budget always < max_tokens, Pitfall 2).
- `app/services/llm/openai_provider.py`: `build_params` mirrors the existing `ai_engine._build_completion_params` shape (system as messages[0], `max_completion_tokens` clamped, `reasoning_effort`/`temperature` capability-gated, function-wrapper tools + `tool_choice='auto'`); `normalize_response` → LLMResult (arguments = OpenAI JSON string, finish_reason/usage pass-through).
- `app/services/llm/anthropic_provider.py`: `build_params` (system TOP-LEVEL, `max_tokens` required, `input_schema` tools, `thinking` budget gated on effort>0); module-level `_coalesce_roles` merges consecutive same-role plain-text turns with `"\n\n"` guaranteeing strict user/assistant alternation before `messages.create`; `normalize_response` → LLMResult (tool_use input kept as dict, stop_reason pass-through, input/output_tokens → prompt/completion).
- `app/services/llm/resolve.py`: `resolve_llm_config` (absent row / non-'valid' status / NULL key → PLATFORM_DEFAULT, D-02/D-03), `platform_fallback_config` (key_source='fallback', D-06), `is_key_level_error` (verbatim 401/403/insufficient_quota/402 taxonomy; transient 429/5xx/conn False, Pitfall 6), `get_provider` factory; `__init__` re-exports the public surface.
- `app/services/encryption.py`: `encrypt_api_key`/`decrypt_api_key` aliases reuse the exact same Fernet (one ENCRYPTION_KEY, D-04).

## Task Commits

Each task was committed atomically:

1. **Task 1: base types + capabilities (pure helpers) + encryption aliases** - `9665e2c` (feat)
2. **Task 2: OpenAIProvider + AnthropicProvider translation & normalization (incl. role coalescing)** - `66e72cc` (feat)
3. **Task 3: resolve.py — workspace config, provider factory, fallback classifier** - `6d6542e` (feat)

**Plan metadata:** _final docs commit (this SUMMARY + STATE + ROADMAP)_

_TDD note: the RED test scaffold was already committed in 18-01 (509f2e6); this plan is the GREEN half — no separate per-task test commit was needed since the failing tests pre-existed._

## Files Created/Modified
- `app/services/llm/__init__.py` - package marker + re-exports of the public surface (LLMProvider/LLMResult/ToolCall/LLMConfig/get_provider/is_key_level_error/platform_fallback_config/resolve_llm_config).
- `app/services/llm/base.py` - `LLMResult`/`ToolCall` dataclasses, `LLMProvider` protocol, `finish_reason_normalized` property.
- `app/services/llm/capabilities.py` - pure `is_reasoning_model`/`supports_temperature`/`clamp_max_tokens`/`effort_to_budget` + `REASONING_MAX_TOKENS_FLOOR=4000`/`MAX_TOKENS_CEILING=32000`/`EFFORT_TO_BUDGET`.
- `app/services/llm/openai_provider.py` - `OpenAIProvider` (build_params/complete/normalize_response); calls `self.client.chat.completions.create`.
- `app/services/llm/anthropic_provider.py` - `AnthropicProvider` (build_params/complete/normalize_response) + `_coalesce_roles` + `_translate_tools`; calls `self.client.messages.create`.
- `app/services/llm/resolve.py` - `LLMConfig`, `PLATFORM_DEFAULT`, `platform_fallback_config`, `resolve_llm_config`, `is_key_level_error`, `get_provider`.
- `app/services/encryption.py` - added `encrypt_api_key`/`decrypt_api_key` aliases (reuse `encrypt_session`/`decrypt_session`).

## Decisions Made
- **Interface names follow the RED tests verbatim (tests = source of truth).** The plan's action sketch used `temperature_allowed`, keyword-only `clamp_max_tokens(value, *, model=)`, `effort_to_budget(effort, *, max_tokens=)`, and providers without a `model` ctor arg. The committed 18-01 RED tests instead demand `supports_temperature`, positional `clamp_max_tokens(model, value)`, positional `effort_to_budget(effort, max_tokens)`, `OpenAIProvider(api_key=, model=)`, and `build_params`/`normalize_response` methods. Followed the tests (a plan-vs-test naming skew, documented as a deviation below).
- **`normalize_response` returns provider-NATIVE stop_reason + native tool arguments.** The test asserts `AnthropicProvider.normalize_response(...).finish_reason == "tool_use"` and `tool_calls[0].arguments == {"reason": "interested"}` (a dict). So normalization does NOT rewrite finish_reason or json.dumps the arguments; the cross-provider mapping (`max_tokens`→`length`, `tool_use`→`tool_calls`) lives in `LLMResult.finish_reason_normalized` instead, keeping the plan's intent (the existing empty-response `'length'` retry works across providers) without breaking the test contract.
- **`resolve.py` has zero logging** — the decrypted BYO key can never appear in an app-log call at all (the acceptance grep `logger.*decrypted_key` returns 0 because there is no logger in the module).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Rebuilt the api test image to install the anthropic SDK**
- **Found during:** Task 2 (AnthropicProvider)
- **Issue:** `import anthropic` raised `ModuleNotFoundError` under the test-overlay — `anthropic>=0.69,<1.0` was declared in requirements.txt by plan 18-01 but the existing `tg-outreach-api` image predated that add, so the SDK was not installed.
- **Fix:** `docker compose -f docker-compose.yml -f docker-compose.test.yml build api` to reinstall requirements. (No requirements change — the pin was already present.)
- **Files modified:** none (image rebuild only).
- **Verification:** `tests/test_llm_provider.py` (4 anthropic/openai translation tests) went from 1 failed / import-error to 4 passed.
- **Committed in:** N/A (image build, no source change).

**2. [Plan-vs-test naming skew — followed the committed RED tests] Interface names + call signatures**
- **Found during:** Tasks 1–3.
- **Issue:** The plan's `<action>` sketch named several symbols differently from the 18-01 RED tests that this plan must green (`temperature_allowed` vs `supports_temperature`; keyword-only clamp/budget args vs positional; providers lacking a `model=` ctor and `build_params`/`normalize_response` methods).
- **Fix:** Implemented to the tests (the binding contract), not the sketch. Kept every semantic the plan intended (D-09/D-10 gating + clamp, D-02/D-03 default-off, D-06 taxonomy, Anthropic coalescing).
- **Files modified:** all six new files under app/services/llm/.
- **Verification:** All 10 target-file tests pass; the D-12 isolation guard + ai_engine empty-retry regression still pass (20/20).
- **Committed in:** `9665e2c`, `66e72cc`, `6d6542e`.

---

**Total deviations:** 1 auto-fixed (Rule 3 blocking) + 1 plan-vs-test naming reconciliation.
**Impact on plan:** No scope creep. The SDK rebuild was a pure environment fix; the naming reconciliation preserved every decision the plan encoded. The adapter remains pure — no ai_engine/warmup/listener/queue changes (deferred to 18-04).

## Issues Encountered
- **Transient `socket.gaierror` on the combined 3-file run.** The freshly-started `tg-outreach-db-test-1` container had not yet registered in Docker DNS when the session-scoped `_setup_database` conftest fixture tried to connect. Re-running once the container reported `healthy` passed 10/10. Not a code issue (each file passed individually); root cause was a container-networking race, unrelated to the adapter. (Multiple parallel-agent `agent-*-db-test-1` containers are also present on the host — not ours, left untouched.)

## User Setup Required
None for this plan (tests run via the test-overlay, which builds the api image). 

**Runtime deploy note for 18-04:** the answerer runs in the `listener` container, so wiring the adapter into the live path will require rebuilding BOTH `api` and `listener` (`docker compose up -d --build api listener`) so the anthropic SDK is present in the listener image too. Not needed for this pure-adapter plan.

## Next Phase Readiness
- The provider abstraction is complete and pure. 18-03 (settings API) can persist/mask BYO keys and call `is_key_level_error` for test-connection; 18-04 (wire answerer/warmup/logger) can call `resolve_llm_config` → `get_provider(config).complete(...)` and read `LLMResult` (+ `finish_reason_normalized` for the empty-response retry).
- No blockers. No PROTECTED queue constant touched; no changes to ai_engine/warmup/listener.
- Uncommitted parallel-agent work is present in the tree (`app/routers/onboarding.py`, `tests/test_onboarding_plainflow_reauth.py`, `.planning/debug/reauth-verify-2fa-500.md`) — deliberately NOT touched or committed by this plan (careful-commit discipline with a parallel agent).

---
*Phase: 18-switchable-llm-provider*
*Completed: 2026-07-02*

## Self-Check: PASSED

- All 6 created files under `app/services/llm/` + the SUMMARY verified present on disk.
- `encrypt_api_key`/`decrypt_api_key` aliases verified in `app/services/encryption.py`.
- All 3 task commits verified in git history (9665e2c, 66e72cc, 6d6542e).
- All 10 target tests GREEN via test-overlay (test_llm_capabilities 4, test_llm_provider 4, test_llm_fallback 2); D-12 isolation guard + ai_engine empty-retry regression still GREEN (20/20 combined).
