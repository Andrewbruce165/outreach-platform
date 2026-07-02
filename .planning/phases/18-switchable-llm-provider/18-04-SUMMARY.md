---
phase: 18-switchable-llm-provider
plan: 04
subsystem: api
tags: [llm, anthropic, openai, ai-engine, warmup, llm-logger, provider-adapter, fallback]

# Dependency graph
requires:
  - phase: 18-switchable-llm-provider (plan 02)
    provides: app/services/llm/ adapter (resolve_llm_config/get_provider/is_key_level_error/platform_fallback_config, OpenAIProvider/AnthropicProvider, LLMResult)
  - phase: 18-switchable-llm-provider (plan 03)
    provides: llm_settings row shape + api_key_status persistence (flagged 'invalid' by the D-06 fallback here)
  - phase: 05-inbox-analytics
    provides: log_llm_call never-raise logger (extended here with provider/key_source)
  - phase: 15-warmup
    provides: WarmupWorker._generate_message call path (rerouted here through the provider factory)
provides:
  - ai_engine.generate_response routed through the workspace-resolved provider (all three LLM calls) with the empty-guard preserved across providers
  - provider-neutral second-pass assistant turn (no raw OpenAI SDK object) — Claude tool flows survive the second pass
  - D-06 key-level fallback (flag api_key_status='invalid' + retry once on platform OpenAI)
  - warmup routed through the same workspace-aware provider factory (D-11, single tone)
  - llm_logger persists provider + key_source and reads the normalized LLMResult
  - OpenAIProvider/AnthropicProvider neutral→native translation of the second-pass tool turns
affects: [18-05-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Nested _complete(msgs, tools, retry, cfg) helper inside generate_response: builds provider from resolved cfg, computes model-aware budget/effort (falls back to the existing ai_engine constants when the workspace didn't override), logs the call, and does the D-06 key-fallback in one place"
    - "Seam preservation: the OpenAI platform/fallback path REUSES the module-level ai_engine.client (provider.client = client) so existing tests patching ai_engine.client.chat.completions.create keep working and prod shares one client; BYOK OpenAI keeps the provider's own client"
    - "Provider-neutral second-pass turn {role:assistant, content, tool_calls:[{id,name,arguments}]} + {role:tool, tool_call_id, content}; each provider translates it to its native shape (OpenAI function-wrapper, Anthropic tool_use/tool_result blocks)"
    - "finish_reason_normalized=='length' drives the empty-response retry for BOTH providers (Anthropic max_tokens -> length)"

key-files:
  created:
    - .planning/phases/18-switchable-llm-provider/deferred-items.md
  modified:
    - app/services/llm_logger.py
    - app/services/ai_engine.py
    - app/services/warmup.py
    - app/services/llm/openai_provider.py
    - app/services/llm/anthropic_provider.py
    - tests/test_ai_engine_empty_retry.py

key-decisions:
  - "OpenAI platform/fallback path reuses the module-level ai_engine.client (not a fresh AsyncOpenAI) — preserves every existing test seam (test_builtin_tools/test_custom_tools_wiring/test_ai_engine_kb_tool/test_phase5_llm_logger patch ai_engine.client.chat.completions.create) AND keeps prod on one client. BYOK keeps its own client."
  - "complete() takes NO model kwarg — the model comes from the provider ctor via get_provider(cfg). The plan's sketch passed model=cfg.model; the actual 18-02 provider signature omits it, so model is set at construction (documented deviation)."
  - "Added neutral→native translation to BOTH providers (OpenAIProvider._to_openai_messages, AnthropicProvider._to_anthropic_messages) — the 18-02 providers passed messages through without reshaping the neutral second-pass tool turns, which would break the OpenAI second pass (real API needs the function wrapper). Rule-3 blocking fix so the neutral shape actually works for both providers as the plan requires."
  - "_flag_key_invalid best-effort swallow-all — flagging the BYO key must NEVER break the live reply (D-06)."

patterns-established:
  - "Single choke point for all answerer LLM calls: three call sites collapse to _complete(...); provider/key_source flow into every log_llm_call"

requirements-completed: [LLMP-06, LLMP-07, LLMP-11, LLMP-12]

# Metrics
duration: 22min
completed: 2026-07-02
---

# Phase 18 Plan 04: Wire Answerer, Warmup and Logger Summary

**The runtime switch takes effect: `ai_engine.generate_response` resolves the per-workspace LLM config once and routes all three answerer LLM calls (initial, empty-retry, tool-summarization second pass) through the provider adapter with the empty-response guard preserved via `finish_reason_normalized`; the second pass now appends a PROVIDER-NEUTRAL assistant tool-call turn (no raw OpenAI SDK object) that each provider translates to its native shape; the D-06 key-level fallback flags `api_key_status='invalid'` and retries once on platform OpenAI; warmup routes through the same factory (D-11); and `llm_logger` persists provider + key_source and reads the normalized LLMResult — Whisper + KB embeddings stay pinned to the platform singleton (D-12).**

## Performance
- **Duration:** 22 min
- **Started:** 2026-07-02T11:30:25Z
- **Completed:** 2026-07-02T11:52Z
- **Tasks:** 4
- **Files modified:** 7 (6 modified + 1 created deferred-items note)

## Accomplishments
- **llm_logger (Task 1):** `log_llm_call` gained `provider`/`key_source` kwargs threaded into the `LLMCall(...)` constructor; response extraction branches on a normalized `LLMResult` (`.text`/`.tool_calls`/`.usage`) vs the legacy OpenAI `.choices[0].message` graph via a `_is_llm_result` duck-type. NEVER-RAISE + no-prompt-leak contract intact.
- **Answerer (Task 2):** `generate_response` resolves `LLMConfig` once (`resolve_llm_config(session, ws_id)` on the campaign path, `platform_fallback_config` on the legacy path). A nested `_complete(...)` helper builds the provider, computes model-aware budget/effort (workspace overrides or the existing constants), logs the call, and wraps the D-06 fallback. All three `client.chat.completions.create` sites are gone from the answerer; the ONLY remaining `client.` uses are `transcribe_audio` (Whisper) + KB embeddings (D-12). Tool dispatch reads `ToolCall.name`/`.arguments` (str-JSON for OpenAI, dict for Anthropic).
- **Second-pass BLOCKER fixed:** `messages.append(response_message)` (raw OpenAI SDK object) replaced by a provider-neutral assistant turn built from the first-call `LLMResult`. Added neutral→native translation to `OpenAIProvider` (function-wrapper) and `AnthropicProvider` (tool_use/tool_result blocks) so the SAME messages list drives both providers.
- **D-06 fallback:** on `is_key_level_error(e)` against a `byok` call → `_flag_key_invalid` (idempotent `UPDATE llm_settings SET api_key_status='invalid'`) + one retry via `platform_fallback_config` (key_source='fallback'). Transient 429/5xx/conn errors re-raise into the unchanged `RateLimitError/APIConnectionError/APIStatusError` handlers (return None).
- **Warmup (Task 3):** dropped `self._openai = AsyncOpenAI()` + the `AsyncOpenAI` import; `_generate_message` resolves cfg via `resolve_llm_config` (or `platform_fallback_config`) and calls `get_provider(cfg).complete(...)`, returning `result.text`. Call site passes `db` + `from_sender["workspace_id"]` (D-11). Graceful None-degrade preserved; warmup LLM calls still NOT logged (D-09..D-12).
- **Tests (Task 4):** `test_ai_engine_empty_retry.py` now patches `OpenAIProvider.complete` with `LLMResult` side-effects (adapter seam), asserting the retry escalates `max_tokens`/`reasoning_effort`. `test_llm_isolation` confirmed GREEN (Whisper/embeddings on the platform singleton). `_is_reasoning_model` delegates to `capabilities.is_reasoning_model` (single source).

## Task Commits
1. **Task 1: llm_logger persists provider/key_source + reads LLMResult** - `d5fb08b` (feat)
2. **Task 2: route answerer through adapter + neutral second pass + D-06 fallback** - `c34205a` (feat)
3. **Task 3: route warmup through the workspace-aware provider factory (D-11)** - `458870d` (feat)
4. **Task 4: empty-retry test exercises the adapter seam** - `07c9ed4` (test)

**Plan metadata:** _final docs commit (this SUMMARY + STATE + ROADMAP)_

## Files Created/Modified
- `app/services/llm_logger.py` - `provider`/`key_source` kwargs + LLMResult branch + `_is_llm_result`.
- `app/services/ai_engine.py` - provider imports, `_is_reasoning_model` delegation, `_flag_key_invalid`, `_complete` helper, three call sites rerouted, provider-neutral second-pass turn, D-06 fallback.
- `app/services/warmup.py` - removed standalone AsyncOpenAI; `_generate_message` routes through `get_provider`/`resolve_llm_config`; call site passes db + workspace_id.
- `app/services/llm/openai_provider.py` - `_to_openai_messages` neutral→native tool-call translation.
- `app/services/llm/anthropic_provider.py` - `_to_anthropic_messages` neutral→tool_use/tool_result translation before coalescing.
- `tests/test_ai_engine_empty_retry.py` - integration tests patch `OpenAIProvider.complete`.
- `.planning/phases/18-switchable-llm-provider/deferred-items.md` - documents the pre-existing WARM-14 failure.

## Decisions Made
- **Reuse the module-level `ai_engine.client` for the OpenAI platform/fallback path** so the answerer routes through `provider.complete` (plan requirement) WITHOUT breaking the four existing test files that patch `ai_engine.client.chat.completions.create` and without spawning a second prod client. BYOK gets its own client.
- **`complete()` takes no `model` kwarg** — model set at provider construction via `get_provider(cfg)` (18-02's actual signature; the plan sketch's `model=` would have crashed).
- **Neutral→native translation added to both providers** (Rule-3 blocking) — the 18-02 providers passed messages through untranslated; the OpenAI second pass needs the `function` wrapper and Anthropic needs tool_use/tool_result blocks, so the neutral shape only works for both once each provider reshapes it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Providers didn't translate the neutral second-pass tool turns**
- **Found during:** Task 2.
- **Issue:** The plan prescribes appending a provider-neutral assistant turn `{role:assistant, content, tool_calls:[{id,name,arguments}]}` and relies on "OpenAI accepts the assistant+tool_calls dict / Anthropic translates it into tool_use blocks." But the 18-02 `OpenAIProvider`/`AnthropicProvider` pass `messages` straight to the SDK without reshaping — OpenAI's real API needs `tool_calls:[{id,type:function,function:{name,arguments}}]` and Anthropic needs `tool_use`/`tool_result` content blocks, so the neutral shape would fail the second pass.
- **Fix:** Added `OpenAIProvider._to_openai_messages` (neutral → function-wrapper, arguments json.dumps'd if a dict) and `AnthropicProvider._to_anthropic_messages` (assistant tool_calls → tool_use blocks; role='tool' → user tool_result block) applied inside each provider's `build_params`.
- **Files modified:** `app/services/llm/openai_provider.py`, `app/services/llm/anthropic_provider.py`.
- **Verification:** `test_custom_tools_wiring.py` + `test_ai_engine_kb_tool.py` (both exercise the OpenAI second pass) GREEN through the adapter.
- **Commit:** `c34205a`.

**2. [Plan-vs-source signature skew] complete() has no model kwarg**
- **Found during:** Task 2.
- **Issue:** The plan's action sketch passed `model=cfg.model` to `provider.complete(...)`; the committed 18-02 provider `complete()` signature takes model at `__init__` only (via `get_provider(cfg)`), so passing `model=` would raise `TypeError`.
- **Fix:** Removed the `model=` kwarg from the `_complete` call; model is set at construction. Documented inline.
- **Files modified:** `app/services/ai_engine.py`.
- **Commit:** `c34205a`.

**Total deviations:** 1 auto-fixed (Rule 3 blocking) + 1 plan-vs-source signature reconciliation. No scope creep — every decision the plan encoded (D-11 answerer+warmup single provider, D-06 fallback, D-07 logger, D-12 Whisper/embeddings isolation, provider-neutral second pass) is preserved.

## Issues Encountered
- **Existing tests patch `ai_engine.client.chat.completions.create`** (test_builtin_tools/test_custom_tools_wiring/test_ai_engine_kb_tool/test_phase5_llm_logger). Resolved by the seam-preservation decision (reuse the module client for the OpenAI platform path) rather than editing those out-of-scope test files — all four stay GREEN unchanged.

## Deferred Issues
- **tests/test_warmup_worker.py::test_restricted_sender_excluded (WARM-14)** — a pre-existing RED scaffold from a parallel/uncommitted Phase 15 warmup effort (pool-selection restriction clause not yet added). Unrelated to Phase 18 (does not touch `_generate_message`/the provider factory). Documented in `deferred-items.md` and project memory. Left untouched per scope boundary — belongs to Phase 15.

## User Setup Required
None for this plan (tests run via test-overlay).

**Runtime deploy note (for phase verification, NOT this plan):** the answerer runs in the `listener` container and warmup in the `api` container — deploying Phase 18 requires rebuilding BOTH: `docker compose up -d --build api listener` (so the anthropic SDK is present in the listener image too). Not done here per the plan's no-deploy instruction.

## Next Phase Readiness
- The runtime switch is wired end-to-end: answerer + warmup + logger all route through the workspace-resolved provider; a valid BYO Anthropic/OpenAI config now changes the model that answers. 18-05 (frontend) can build the Settings → LLM page against the 18-03 endpoints; the runtime already honours a saved+validated config.
- No blockers. No PROTECTED queue constant or empirical rate-limit touched. Empty-response guard preserved across both providers.

---
*Phase: 18-switchable-llm-provider*
*Completed: 2026-07-02*

## Self-Check: PASSED

- All 4 task commits verified in git history (d5fb08b, c34205a, 458870d, 07c9ed4).
- Modified files verified present on disk (llm_logger.py, ai_engine.py, warmup.py, openai_provider.py, anthropic_provider.py, test_ai_engine_empty_retry.py) + deferred-items.md + this SUMMARY.
- Full suite via test-overlay: 897 passed / 1 skipped / 1 failed (the failure is the documented out-of-scope WARM-14 Phase-15 scaffold) — no regressions in the Phase 18 or answerer/tool paths.
