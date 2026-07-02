---
phase: 18-switchable-llm-provider
plan: 03
subsystem: api
tags: [llm, anthropic, openai, settings-api, fernet, models-filter, byok]

# Dependency graph
requires:
  - phase: 18-switchable-llm-provider (plan 01)
    provides: migration 044 + LLMSettings ORM (PK workspace_id), RED tests test_llm_settings_api / test_llm_models_filter
  - phase: 18-switchable-llm-provider (plan 02)
    provides: app/services/llm/ adapter package (capabilities/resolve/providers), encrypt_api_key/decrypt_api_key Fernet aliases, is_key_level_error
  - phase: 01-multitenancy
    provides: AuthCtx/auth_dep + workspace-scope + _require_jwt pattern (mirrored from workspace.py)
provides:
  - app/routers/llm_settings.py — workspace-scoped GET (masked) / PATCH (encrypt + D-03 gate) / test-connection (D-05) / models (D-08)
  - app/services/llm/models_filter.py — pure filter_models(model_ids, provider=) chat-with-tools family filter
  - capabilities.filter_chat_models(provider, model_ids) alias (the name the RED tests + router import)
  - resolve.probe_key(provider, key) + resolve.list_model_ids(provider, key) — cheap SDK probes, zero logging
  - main.py registration of llm_settings.router
affects: [18-04-wire-answerer-warmup-logger, 18-05-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Workspace-scoped settings router mirrors workspace.py: auth_dep + _require_jwt on mutations + WHERE workspace_id == ctx.workspace_id cross-tenant guard (D-01)"
    - "Masked-key read: response schema carries api_key_prefix (prefix+last4) ONLY — the LLMSettingsResponse has no plaintext key field; PATCH resets api_key_status='unset' so a new key must be re-tested"
    - "Upsert the single per-workspace row via INSERT ... ON CONFLICT (workspace_id) DO UPDATE (D-01 one-config-per-workspace)"
    - "test-connection probe is a monkeypatch seam: router calls resolve.probe_key(provider, key); success => valid, any exception => invalid (key-level detail vs probe-failed via is_key_level_error), never leaks the key in detail"
    - "Live /models soft-fails to {models:[], note} on any provider error so a /v1/models outage never 500s the Settings page (test-connection is the authoritative validity signal)"

key-files:
  created:
    - app/routers/llm_settings.py
    - app/services/llm/models_filter.py
  modified:
    - app/services/llm/capabilities.py
    - app/services/llm/resolve.py
    - app/main.py

key-decisions:
  - "Model filter lives at capabilities.filter_chat_models(provider, model_ids) (the RED-test import name) as a thin alias over the canonical models_filter.filter_models(model_ids, provider=) — one implementation, both names satisfied (plan artifact demanded models_filter.py + def filter_models)"
  - "GET default returns model=None (NOT settings.openai_model) — the RED test asserts body['model'] is None; the UI shows 'no model chosen, using platform default' rather than pre-selecting the platform model"
  - "test-connection accepts a body-only api_key (no row required) and persists the valid/invalid outcome ONLY when a row exists (a body-only probe has nothing to flip) — matches the RED test which never pre-creates a row"
  - "probe_key + list_model_ids added to resolve.py (not the router) so they are a clean monkeypatch seam and inherit resolve.py's zero-logging discipline — the decrypted BYO key can never reach app logs"

patterns-established:
  - "Provider-neutral settings surface: one router, no per-provider endpoint duplication; provider is a column/query param, adapters build the concrete SDK client"

requirements-completed: [LLMP-01, LLMP-02, LLMP-03, LLMP-04, LLMP-05, LLMP-08]

# Metrics
duration: 4min
completed: 2026-07-02
---

# Phase 18 Plan 03: Settings API, Model Listing and Test Connection Summary

**The workspace-scoped LLM settings API (`app/routers/llm_settings.py`): masked GET (D-02 default-off), encrypting PATCH with the D-03 key-mandatory switch gate (Fernet via `encrypt_api_key`, D-04), a `test-connection` probe that flips `api_key_status` valid/invalid through the monkeypatchable `resolve.probe_key` (D-05), and a live family-filtered `/models` list that soft-fails to empty+note (D-08) — plus a pure `models_filter.filter_models` (aliased as `capabilities.filter_chat_models` for the RED tests) and `resolve.probe_key`/`list_model_ids` SDK probes — registered in `main.py`, turning `test_llm_settings_api.py` + `test_llm_models_filter.py` GREEN.**

## Performance

- **Duration:** 4 min
- **Started:** 2026-07-02T11:23:17Z
- **Completed:** 2026-07-02T11:27:35Z
- **Tasks:** 2
- **Files modified:** 5 (2 created — router + models_filter; 3 modified — capabilities, resolve, main.py)

## Accomplishments
- `app/services/llm/models_filter.py`: pure `filter_models(model_ids, provider=)` — keeps chat-with-tools families (gpt-4o/gpt-4.x/gpt-4-x/gpt-5/o1/o3/o4 for OpenAI, claude-* for Anthropic), drops embeddings/whisper/tts/dall-e/realtime/audio/transcribe/image/moderation/search/instruct. `gpt-4o-realtime-preview`/`gpt-4o-transcribe` match the keep-prefix but are caught by the drop-set as intended (D-08).
- `capabilities.filter_chat_models(provider, model_ids)`: thin alias over `filter_models` (lazy import, no circular risk) — the exact name the 18-01 RED test imports.
- `app/routers/llm_settings.py` (4 endpoints under `/api/v1/workspace/llm-settings`, all `ctx.workspace_id`-scoped, D-01):
  - `GET` — masked read; absent row => `{provider:'openai', model:null, api_key_status:'unset'}` (D-02); present row maps columns, exposes `api_key_prefix` only (never decrypts into the response, D-04).
  - `PATCH` (JWT) — upserts THE single workspace row (`ON CONFLICT (workspace_id) DO UPDATE`); D-03 gate 400 `KEY_REQUIRED` when switching provider/model without a stored or body key; new key => `encrypt_api_key` + `_mask` (prefix+last4) + `api_key_status='unset'`; defensive `clamp_max_tokens` when model+max_tokens both present.
  - `POST /test-connection` (JWT) — resolves body-override-or-stored-decrypted key, `await resolve.probe_key(...)`; success => `valid`, any exception => `invalid` (detail = 'key-level error' vs 'probe failed' via `is_key_level_error`, never the key); persists the outcome when a row exists (D-05).
  - `GET /models?provider=` — decrypts the stored key (OpenAI can fall back to the platform key so the picker works pre-BYOK), `resolve.list_model_ids` → `filter_chat_models`, returns filtered ids; any provider error => `{models:[], note:'model list unavailable'}` 200 soft-fail (D-08).
- `resolve.py`: `probe_key(provider, key)` (cheapest auth check via `client.models.list()`) + `list_model_ids(provider, key)` (raw ids for the filter) — both zero-logging so the decrypted BYO key can never leak.
- `main.py`: `llm_settings` added to the router import block (after `knowledge_bases`) + `app.include_router(llm_settings.router)`.

## Task Commits

Each task was committed atomically:

1. **Task 1: server-side model-list family/capability filter (D-08)** - `3325ba5` (feat)
2. **Task 2: workspace-scoped LLM settings API — GET/PATCH/test-connection/models** - `c89d463` (feat)

**Plan metadata:** _final docs commit (this SUMMARY + STATE + ROADMAP)_

_TDD note: the RED test scaffold pre-existed from 18-01 (509f2e6); this plan is the GREEN half, so no separate per-task test commit was needed._

## Files Created/Modified
- `app/services/llm/models_filter.py` - pure `filter_models(model_ids, provider=)` chat-with-tools family filter (D-08).
- `app/services/llm/capabilities.py` - added `filter_chat_models(provider, model_ids)` alias.
- `app/services/llm/resolve.py` - added `probe_key` + `list_model_ids` async SDK probes (zero logging).
- `app/routers/llm_settings.py` - workspace-scoped GET/PATCH/test-connection/models router.
- `app/main.py` - registered `llm_settings.router`.

## Decisions Made
- **Filter name reconciliation (tests = source of truth).** The plan artifact demanded `app/services/llm/models_filter.py` with `def filter_models`; the committed 18-01 RED test imports `capabilities.filter_chat_models(provider, model_ids)`. Both are satisfied: `filter_models` is the canonical implementation in `models_filter.py` (artifact contract) and `filter_chat_models` is a thin positional alias in `capabilities.py` (test contract). One implementation, no duplication.
- **GET default `model=None`.** The RED test asserts `body['model'] is None`, so the default read does not pre-select `settings.openai_model` — the row-absent state cleanly signals "platform default, no explicit model chosen." (resolve_llm_config in 18-02 still resolves the actual platform model at call time; that path is unchanged.)
- **test-connection is a `resolve.probe_key` seam.** The RED test monkeypatches `app.services.llm.resolve.probe_key`, so the router calls it by module attribute (`llm_resolve.probe_key`) rather than importing the symbol — this keeps the monkeypatch effective. Body-only probes (no row) report their result without persisting.
- **`/models` OpenAI platform-key fallback.** Before a BYO key is saved, OpenAI `/models` lists against the platform `OPENAI_API_KEY` so the picker is usable in the default-off state; Anthropic (BYOK-only, D-03) still requires a saved key.

## Deviations from Plan

### Reconciled with committed RED tests (not scope changes)

**1. [Plan-vs-test naming skew] Model filter symbol name/location**
- **Found during:** Task 1.
- **Issue:** The plan's action sketch created `models_filter.filter_models(model_ids, provider=)`; the RED test imports `capabilities.filter_chat_models(provider, model_ids)` (different name, different module, positional-first arg order).
- **Fix:** Implemented `filter_models` in `models_filter.py` (satisfies the plan artifact grep `def filter_models` + `contains models_filter`) and added the `filter_chat_models` alias in `capabilities.py` (satisfies the test import). Semantics identical.
- **Files modified:** `app/services/llm/models_filter.py`, `app/services/llm/capabilities.py`.
- **Committed in:** `3325ba5`.

**2. [Plan-vs-test seam] test-connection probe entry point**
- **Found during:** Task 2.
- **Issue:** The plan sketched a local `_probe(provider, key)` helper; the RED test monkeypatches `app.services.llm.resolve.probe_key`.
- **Fix:** Added `probe_key` (and `list_model_ids` for /models) to `resolve.py`; the router calls them via the module attribute so the monkeypatch takes effect.
- **Files modified:** `app/services/llm/resolve.py`, `app/routers/llm_settings.py`.
- **Committed in:** `c89d463`.

**Total deviations:** 2 plan-vs-test reconciliations, no auto-fixed bugs, no scope creep. Every decision the plan encoded (D-01 workspace scope, D-02 default-off, D-03 key gate, D-04 encrypt+mask, D-05 probe, D-08 filter) is preserved.

## Issues Encountered
None. Both task verifications passed first run (models filter 2/2, settings API 4/4); the combined Phase 18 LLM suite (settings + filter + capabilities + provider + fallback + isolation guard) is 18/18 GREEN with no regression.

## User Setup Required
None for this plan (tests run via the test-overlay). The BYO Anthropic/OpenAI key is entered by the user in the Settings UI (18-05); D-03 makes the Claude path BYOK-only.

**Runtime deploy note (for 18-04/18-05 deploy):** the settings router lives in the `api` container — a `docker compose up -d --build api` picks it up. The answerer wiring (18-04) additionally needs the `listener` rebuilt so the anthropic SDK is present there too.

## Next Phase Readiness
- The settings surface the UI drives is complete: masked GET, encrypting PATCH with the D-03 gate, test-connection probe, filtered live model list. 18-04 (wire answerer/warmup/logger) can call `resolve_llm_config` → `get_provider(config).complete(...)`; 18-05 (frontend) can build the Settings → AI/LLM page against these four endpoints.
- No blockers. No PROTECTED queue constant or empirical rate-limit touched; no ai_engine/warmup/listener changes.
- The untracked `igor_base_registered_50.csv` (checker ops data) was intentionally left uncommitted per the plan's parallel-agent commit discipline.

---
*Phase: 18-switchable-llm-provider*
*Completed: 2026-07-02*

## Self-Check: PASSED

- Both created files verified present on disk (`app/routers/llm_settings.py`, `app/services/llm/models_filter.py`) + the SUMMARY.
- Both task commits verified in git history (`3325ba5`, `c89d463`).
- Target tests GREEN via test-overlay: `test_llm_settings_api.py` (4) + `test_llm_models_filter.py` (2) = 6/6; combined Phase 18 LLM suite 18/18 with no regression.
