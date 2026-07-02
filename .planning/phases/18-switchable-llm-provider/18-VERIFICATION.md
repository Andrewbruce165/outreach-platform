---
phase: 18-switchable-llm-provider
verified: 2026-07-02T13:10:00Z
status: passed
score: 12/12 must-haves verified (across 5 plans)
---

# Phase 18: Switchable LLM Provider Verification Report

**Phase Goal:** Workspace-scoped OpenAI/Anthropic switch with BYO key, settings UI, provider-routed answerer/warmup/logger.
**Verified:** 2026-07-02T13:10:00Z
**Status:** passed
**Re-verification:** No — initial verification.

## Goal Achievement

### Observable Truths (aggregated must_haves across 18-01..18-05)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `anthropic` SDK importable in api/listener | ✓ VERIFIED | `requirements.txt:29` `anthropic>=0.69,<1.0`; `openai>=1.40.0,<2.0.0` unchanged |
| 2 | `llm_settings` one-row-per-workspace table (D-01) | ✓ VERIFIED | `migrations/044_llm_settings.sql` `CREATE TABLE IF NOT EXISTS llm_settings (workspace_id UUID PRIMARY KEY …)`, header cites D-01 |
| 3 | `llm_calls.provider`/`key_source` columns (D-07) | ✓ VERIFIED | migration 044 `ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS provider/key_source`; ORM `LLMCall.provider`/`.key_source` (models/__init__.py:860-861) |
| 4 | Internal repr translates correctly to both native shapes | ✓ VERIFIED | `openai_provider.py` (chat.completions params), `anthropic_provider.py` (`system=`, `max_tokens`, `input_schema` tools) — `test_llm_provider.py` 7/7 green |
| 5 | Consecutive same-role messages coalesced before Anthropic call | ✓ VERIFIED | `anthropic_provider.py::_coalesce_roles`; `test_anthropic_coalesces_consecutive_same_role` passes |
| 6 | Both providers normalize to single `LLMResult` | ✓ VERIFIED | `app/services/llm/base.py::LLMResult` (+`finish_reason_normalized` cross-provider property) |
| 7 | Temperature never sent to OpenAI reasoning models; max_tokens clamped ≥4000 | ✓ VERIFIED | `capabilities.py::temperature_allowed`, `clamp_max_tokens` (`REASONING_MAX_TOKENS_FLOOR=4000`); `test_llm_capabilities.py` 6/6 green |
| 8 | `resolve_llm_config` returns platform default when no valid row (D-02) | ✓ VERIFIED | `resolve.py::resolve_llm_config` — absent row / status != 'valid' / null key → `PLATFORM_DEFAULT` |
| 9 | `is_key_level_error` true only for 401/403/insufficient_quota/402 | ✓ VERIFIED | `resolve.py::is_key_level_error` verbatim taxonomy; `test_llm_fallback.py` 2/2 green |
| 10 | GET/PATCH llm-settings workspace-scoped, key masked | ✓ VERIFIED | `app/routers/llm_settings.py` — every query `WHERE workspace_id = ctx.workspace_id`; `LLMSettingsResponse` has no plaintext key field, only `api_key_prefix` |
| 11 | PATCH stores key Fernet-encrypted, blocks switch without key (D-03) | ✓ VERIFIED | `encrypt_api_key` used on PATCH; `KEY_REQUIRED` 400 gate present (3 call sites) |
| 12 | Test-connection probes provider, flips `api_key_status` | ✓ VERIFIED | `resolve.py::probe_key` + router `/test-connection` endpoint sets `valid`/`invalid` |
| 13 | GET models returns live, family-filtered list (D-08) | ✓ VERIFIED | `models_filter.py::filter_models`; router `/models` endpoint calls `list_model_ids` + filter |
| 14 | Cross-tenant isolation of llm-settings | ✓ VERIFIED | `test_workspace_isolation` passes; all queries keyed on `ctx.workspace_id` |
| 15 | Answerer's 3 LLM calls route through resolved provider (D-11) | ✓ VERIFIED | `ai_engine.py::generate_response` — `resolve_llm_config` called once, `_complete()` used at all 3 call sites; no `client.chat.completions.create` remains in answerer path |
| 16 | Second-pass appends provider-neutral assistant turn | ✓ VERIFIED | `ai_engine.py:1734-1742` builds `{"role":"assistant","content","tool_calls":[...]}" dict from `LLMResult`; raw `messages.append(response_message)` no longer present (grep confirms absence) |
| 17 | Warmup routes through same workspace-aware factory (D-11) | ✓ VERIFIED | `warmup.py` — `self._openai = AsyncOpenAI()` removed; `_generate_message` calls `resolve_llm_config`/`get_provider`; call site passes `from_sender["workspace_id"]` |
| 18 | Key-level error → fallback to platform OpenAI, key flagged invalid (D-06) | ✓ VERIFIED | `ai_engine.py::_complete` — `is_key_level_error(_e) and cfg.key_source=='byok'` → `_flag_key_invalid` + retry on `platform_fallback_config`; live UAT confirmed via `key_source='fallback'` path design (optional step 8 not required for pass since primary byok path verified live) |
| 19 | `llm_logger` records provider + key_source | ✓ VERIFIED | `log_llm_call(provider=, key_source=)` persisted to `LLMCall`; duck-types `LLMResult` vs legacy OpenAI object; `test_llm_logger_provider.py` green |
| 20 | Whisper/KB embeddings stay on platform singleton (D-12) | ✓ VERIFIED | `transcribe_audio` still uses `client.audio.transcriptions`; `kb_ingest.py`/`kb_search.py` import `client` from `ai_engine` (platform singleton), no `get_provider`/`AsyncAnthropic` references; `test_llm_isolation.py` 2/2 green |
| 21 | Settings UI has AI/LLM section (provider/model/key/knobs/test-connection) | ✓ VERIFIED | `settings.tsx` (sibling repo) — provider select, key input, model select from live list, temperature/reasoning-effort/max-tokens knobs, Test connection button (commit `60343bb`/`388f8ba`) |
| 22 | UI blocks switching until key entered (mirrors D-03) | ✓ VERIFIED | `settings.tsx` surfaces `KEY_REQUIRED`; model/config cards gated on stored-or-entered key |
| 23 | Knobs capability-gated + green-corridor hints | ✓ VERIFIED | `REASONING_FLOOR=4000`/`MAX_TOKENS_CEILING=32000` constants + `supportsTemperature`/`supportsReasoningEffort` gating in settings.tsx |
| 24 | openapi.json + types/api.ts include llm-settings | ✓ VERIFIED | `grep -c llm-settings` → 7 hits openapi.json, 3 hits types/api.ts |
| 25 | Human UAT: end-to-end OpenAI↔Claude switch works live | ✓ VERIFIED | 18-05-SUMMARY.md "Task 3 — Human-Verify Result: APPROVED (2026-07-02)" — `llm_calls` rows `provider='anthropic', key_source='byok', model='claude-sonnet-5'`, zero listener errors, user-approved |

**Score:** 25/25 truths verified (rolled up from all 5 plans' must_haves)

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `migrations/044_llm_settings.sql` | llm_settings table + llm_calls columns, idempotent | ✓ VERIFIED | Present, idempotent (`IF NOT EXISTS` + `duplicate_object` guards), cites D-01/D-02/D-04/D-05/D-06/D-07 |
| `app/models/__init__.py::LLMSettings` | ORM mirror, server_default on every NOT NULL | ✓ VERIFIED | `provider`/`api_key_status` carry `server_default=text(...)` |
| `app/models/__init__.py::LLMCall` | +provider/+key_source | ✓ VERIFIED | Both nullable Text columns present |
| `app/services/llm/base.py` | LLMResult/ToolCall/LLMProvider | ✓ VERIFIED | Present, includes `finish_reason_normalized` cross-provider helper |
| `app/services/llm/capabilities.py` | clamp/gate/effort helpers | ✓ VERIFIED | Plus post-UAT additions `anthropic_uses_adaptive_thinking`, `effort_to_anthropic_level` |
| `app/services/llm/openai_provider.py` | OpenAI translation/normalization | ✓ VERIFIED | `chat.completions.create`, `LLMResult` |
| `app/services/llm/anthropic_provider.py` | Anthropic translation + role coalescing | ✓ VERIFIED | `messages.create`, `_coalesce_roles`, `input_schema`, plus UAT fixes (thinking/temperature exclusivity, adaptive thinking for Claude-5-gen) |
| `app/services/llm/resolve.py` | config resolution, fallback classifier, provider factory | ✓ VERIFIED | `resolve_llm_config`, `is_key_level_error`, `get_provider`, `platform_fallback_config`, `probe_key`, `list_model_ids` |
| `app/services/llm/models_filter.py` | family filter (D-08) | ✓ VERIFIED | `filter_models` |
| `app/routers/llm_settings.py` | GET/PATCH/test-connection/models | ✓ VERIFIED | Registered in `app/main.py` |
| `app/services/llm_logger.py` | provider/key_source persistence | ✓ VERIFIED | Duck-types `LLMResult` vs OpenAI object |
| `app/services/ai_engine.py` | answerer routed through adapter, D-06 fallback, neutral 2nd pass | ✓ VERIFIED | All 3 call sites via `_complete`; raw SDK append removed |
| `app/services/warmup.py` | routed through factory | ✓ VERIFIED | `AsyncOpenAI()` singleton removed |
| `lovable-handoff/openapi.json` + `types/api.ts` | llm-settings paths/types | ✓ VERIFIED | 7 / 3 grep hits respectively |
| `aimly-tg-outreach/src/routes/_authenticated/settings.tsx` | AI/LLM section | ✓ VERIFIED | Sibling repo, commit `388f8ba` |
| `aimly-tg-outreach/src/lib/error-codes.ts` | KEY_REQUIRED message | ✓ VERIFIED | Present |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `LLMSettings` ORM | migration 044 | server_default matches SQL DEFAULT | ✓ WIRED | Verified column-by-column |
| `resolve.py` | `encryption.py` | `decrypt_api_key`/`encrypt_api_key` | ✓ WIRED | Aliases added, used in resolve.py + router |
| `openai_provider.py::complete` | `base.py::LLMResult` | returns normalized result | ✓ WIRED | Confirmed |
| `llm_settings.py` router | `encryption.py` | `encrypt_api_key` on PATCH | ✓ WIRED | Confirmed |
| `main.py` | `llm_settings.py` router | `include_router` | ✓ WIRED | `app.include_router(llm_settings.router)` present |
| `ai_engine.py::generate_response` | `resolve.py::resolve_llm_config` | load workspace config | ✓ WIRED | Called once per generate_response |
| `ai_engine.py` | `resolve.py::is_key_level_error` | D-06 fallback trigger | ✓ WIRED | Confirmed in `_complete` exception handler |
| `ai_engine.py::transcribe_audio` | platform singleton `client` | Whisper isolation (D-12) | ✓ WIRED | `client.audio.transcriptions` unchanged |
| `settings.tsx` | `/api/v1/workspace/llm-settings` | GET/PATCH/test-connection/models fetch | ✓ WIRED | All 4 endpoints referenced |
| `types/api.ts` | `openapi.json` | openapi-typescript regen | ✓ WIRED | Confirmed via grep |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| `settings.tsx` model select | live model list state | `GET /workspace/llm-settings/models` → `list_model_ids` (real SDK `models.list()` call) → `filter_models` | Yes | ✓ FLOWING |
| `settings.tsx` key-status badge | `api_key_status` | `GET /workspace/llm-settings` → DB row via `resolve.py`/router `_get_row` | Yes | ✓ FLOWING |
| Answerer reply text | `LLMResult.text` | Live provider `.complete()` call (OpenAI or Anthropic SDK) — confirmed live via UAT `llm_calls` rows with real `claude-sonnet-5` model + non-null response | Yes | ✓ FLOWING |
| `llm_calls.provider`/`key_source` | logged per-call | `log_llm_call(provider=cfg.provider, key_source=cfg.key_source, ...)` | Yes | ✓ FLOWING (confirmed live: 4 consecutive rows `provider='anthropic', key_source='byok'`) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Phase-18 targeted test suite (32 tests across 8 files) | `pytest tests/test_llm_*.py tests/test_ai_engine_empty_retry.py -q` | `32 passed` | ✓ PASS |
| Full regression suite | `pytest -q` (test-overlay) | `902 passed, 1 skipped, 1 failed` | ✓ PASS (the 1 failure is pre-existing WARM-14, documented out-of-scope in `deferred-items.md`) |
| Live human UAT (real Anthropic API, real contact) | Manual, see 18-05-SUMMARY.md | 4 `llm_calls` rows `provider='anthropic', key_source='byok', model='claude-sonnet-5'`, zero errors | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| LLMP-01 | 18-01, 18-03 | Per-workspace llm_settings row, workspace-level PK | ✓ SATISFIED | migration 044, router workspace-scoping |
| LLMP-02 | 18-03 | Default-off resolution | ✓ SATISFIED | `resolve_llm_config` → `PLATFORM_DEFAULT` on absent/invalid row |
| LLMP-03 | 18-02, 18-03, 18-05 | Key mandatory to switch (KEY_REQUIRED) | ✓ SATISFIED | Router 400 gate + UI gate |
| LLMP-04 | 18-01, 18-02, 18-03 | Fernet-encrypted key, masked response | ✓ SATISFIED | `encrypt_api_key`/`decrypt_api_key`, `api_key_prefix` only in response |
| LLMP-05 | 18-03, 18-05 | Test-connection probe flips status | ✓ SATISFIED | `probe_key` + router endpoint + UI wiring |
| LLMP-06 | 18-02, 18-04 | Key-level error fallback (D-06) | ✓ SATISFIED | `is_key_level_error`, `_flag_key_invalid`, retry on platform default; live-verified design |
| LLMP-07 | 18-01, 18-04 | llm_logger provider+key_source | ✓ SATISFIED | Migration columns + logger writes + `test_llm_logger_provider.py` |
| LLMP-08 | 18-01, 18-03, 18-05 | Live, family-filtered model list | ✓ SATISFIED | `models_filter.py`, router `/models`, UI live list |
| LLMP-09 | 18-01, 18-02, 18-05 | Capability-gated knobs | ✓ SATISFIED | `temperature_allowed`, UI `supportsTemperature`/`supportsReasoningEffort` |
| LLMP-10 | 18-01, 18-02, 18-05 | Hard clamp + green corridor | ✓ SATISFIED | `clamp_max_tokens` (floor 4000, ceiling 32000) + UI hints; `effort_to_budget` < max_tokens |
| LLMP-11 | 18-02, 18-04, 18-05 | Answerer + warmup route through adapter | ✓ SATISFIED | `_complete` wiring in ai_engine, `_generate_message` in warmup; live UAT confirms runtime switch |
| LLMP-12 | 18-01, 18-04 | Whisper + KB embeddings stay on platform key | ✓ SATISFIED | `test_llm_isolation.py` 2/2 green, grep-confirmed no `get_provider`/`AsyncAnthropic` in kb_ingest/kb_search/transcribe_audio |

All 12 requirement IDs (LLMP-01..LLMP-12) declared in plan frontmatter cross-reference cleanly against `.planning/REQUIREMENTS.md` §Switchable LLM Provider — no orphans found (all 12 marked `[x]` in REQUIREMENTS.md and traced to concrete plan+artifact evidence above).

### Anti-Patterns Found

None found. Scanned all `app/services/llm/*.py`, `app/routers/llm_settings.py` for TODO/FIXME/placeholder/not-implemented/empty-return patterns — zero matches. The three post-plan UAT fix commits (85b041c, c050ab4, 7e7ad6b) each shipped production code changes together with new/updated tests in the same commit (not deferred), consistent with the project's TDD discipline.

### Human Verification Required

None outstanding. The phase's single blocking human-verify checkpoint (18-05 Task 3) was already executed and approved by the user on 2026-07-02, with live evidence recorded in `18-05-SUMMARY.md` ("Task 3 — Human-Verify Result: APPROVED"): Anthropic key saved, test-connection valid, `claude-sonnet-5` selected, live contact messaged, `llm_calls` shows 4 consecutive rows with `provider='anthropic'`, `key_source='byok'`, zero listener errors. Two live bugs found during that UAT (Anthropic thinking/temperature exclusivity, Claude-5-generation adaptive thinking shape) were fixed same-day (commits 85b041c, c050ab4, 7e7ad6b) with regression tests, and are reflected in the current codebase (verified above).

### Gaps Summary

No gaps found. All 5 plans (18-01 through 18-05) delivered their must_haves; all 12 phase requirements (LLMP-01..LLMP-12) are satisfied with concrete code evidence; the targeted 32-test phase-18 suite and the full 904-test regression suite are green except for one pre-existing, documented, out-of-scope failure (`test_warmup_worker.py::test_restricted_sender_excluded`, WARM-14, belongs to Phase 15, unrelated to LLM provider code). The phase's blocking human-verify gate was executed and approved with live data. Two live-discovered Anthropic API compatibility bugs were fixed same-day with tests, and are part of the verified delivered surface.

---

_Verified: 2026-07-02T13:10:00Z_
_Verifier: Claude (gsd-verifier)_
