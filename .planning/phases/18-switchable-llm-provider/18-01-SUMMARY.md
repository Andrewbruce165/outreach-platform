---
phase: 18-switchable-llm-provider
plan: 01
subsystem: infra
tags: [anthropic, openai, llm, postgres, migration, pytest, sqlalchemy, fernet]

# Dependency graph
requires:
  - phase: 05-inbox-analytics
    provides: llm_calls table + log_llm_call never-raise logger (extended here with provider/key_source)
  - phase: 15-warmup
    provides: warmup_settings per-workspace table precedent (mirrored for llm_settings)
provides:
  - anthropic SDK dependency declared (requirements.txt)
  - migration 044 — llm_settings per-workspace table (PK workspace_id, D-01) + llm_calls.provider/key_source (D-07)
  - LLMSettings ORM class (server_default on every NOT NULL col, Pitfall 5) + LLMCall provider/key_source columns
  - 7 Wave-0 RED test files covering LLMP-01/02/04/05/06/07/08/09/10/11/12 (behavioural RED; isolation guard GREEN)
  - conftest exists-guarded migration 044 apply (CHECK-constraint path in test DB)
affects: [18-02-provider-adapter, 18-03-settings-api, 18-04-wire-answerer-warmup-logger, 18-05-frontend]

# Tech tracking
tech-stack:
  added: [anthropic>=0.69,<1.0]
  patterns:
    - "Per-workspace settings table keyed PK=workspace_id, absence of row = platform default (mirrors warmup_settings 038)"
    - "VARCHAR+CHECK with DO$$ duplicate_object guard instead of PG enum (ALTER TYPE ADD VALUE can't run in a transaction)"
    - "server_default on every NOT NULL ORM column so create_all builds test schema WITH DB defaults (mig 040/042 drift fix)"
    - "Deferred in-body imports in RED test scaffold keep --collect-only clean before target modules exist"

key-files:
  created:
    - migrations/044_llm_settings.sql
    - tests/test_llm_capabilities.py
    - tests/test_llm_fallback.py
    - tests/test_llm_provider.py
    - tests/test_llm_settings_api.py
    - tests/test_llm_models_filter.py
    - tests/test_llm_logger_provider.py
    - tests/test_llm_isolation.py
  modified:
    - requirements.txt
    - app/models/__init__.py
    - tests/conftest.py
    - tests/test_ai_engine_empty_retry.py

key-decisions:
  - "llm_settings is a dedicated table (PK workspace_id), not columns on workspaces — cleaner masked-key read + default-absent = platform default (D-01/D-02)"
  - "provider/api_key_status are VARCHAR+CHECK, not PG enum — same reason campaigns.status is (ALTER TYPE ADD VALUE forbidden in transaction)"
  - "Anthropic role-alternation coalescing is a first-class RED test (the debounce case produces consecutive same-role turns; Anthropic 400s on non-alternating roles)"
  - "test_llm_isolation is a GREEN preservation guard (not RED) — asserts Whisper/embeddings STAY on the platform singleton so a later plan can't regress D-12"

patterns-established:
  - "Wave-0 RED scaffold: fully-asserting behavioural tests with deferred imports, one failing test per downstream requirement to turn green"
  - "ORM+migration co-mirror discipline: every NOT NULL col has both SQL DEFAULT and ORM server_default"

requirements-completed: [LLMP-01, LLMP-04, LLMP-06, LLMP-07, LLMP-08, LLMP-09, LLMP-10, LLMP-11, LLMP-12]

# Metrics
duration: 6min
completed: 2026-07-02
---

# Phase 18 Plan 01: Infra Migration and Test Scaffold Summary

**anthropic SDK + idempotent migration 044 (per-workspace `llm_settings` PK=workspace_id + `llm_calls.provider/key_source`), the mirrored `LLMSettings` ORM class, and 7 Wave-0 RED test files (behavioural RED, D-12 isolation guard GREEN) so every downstream Phase 18 plan has a failing test to green — including the Anthropic role-alternation case.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-02T11:01:16Z
- **Completed:** 2026-07-02T11:07:50Z
- **Tasks:** 3
- **Files modified:** 12 (4 created migration/models path + 7 test files created + 1 test modified; 4 modified total)

## Accomplishments
- `anthropic>=0.69,<1.0` added to requirements.txt (existing `openai>=1.40.0,<2.0.0` pin untouched).
- Migration 044 creates `llm_settings` (workspace-level PK per D-01, Fernet-encrypted BYO key per D-04, absence-of-row = platform default per D-02) + `llm_calls.provider`/`key_source` (D-07) — fully idempotent (`CREATE TABLE IF NOT EXISTS`, `DO $$ ... EXCEPTION duplicate_object $$` CHECK guards, `ADD COLUMN IF NOT EXISTS`), no PG enum.
- `LLMSettings` ORM class mirrors the migration exactly with `server_default=` on every NOT NULL column (Pitfall 5, mig 040/042 drift); `LLMCall` gained nullable `provider` + `key_source`; imported `Float` for `DOUBLE PRECISION` temperature.
- 7 RED test files land and collect cleanly (896 collected, 0 errors across the full suite via test-overlay). Behavioural tests are RED (target `app.services.llm.*` modules absent, `log_llm_call` provider kwarg absent); the LLMP-12 isolation guard is GREEN (Whisper + KB embeddings still on the platform singleton).
- conftest exists-guarded migration 044 apply so the ephemeral test DB exercises the same CHECK-constraint path as prod.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add anthropic SDK + migration 044 (llm_settings table + llm_calls columns)** - `b32bb6e` (feat)
2. **Task 2: ORM mirror — LLMSettings class + LLMCall provider/key_source columns** - `1bb39f2` (feat)
3. **Task 3: Wave-0 RED test scaffold (7 new files) + update empty-retry test note** - `509f2e6` (test)

**Plan metadata:** _final docs commit (this SUMMARY + STATE + ROADMAP)_

## Files Created/Modified
- `migrations/044_llm_settings.sql` - Idempotent per-workspace `llm_settings` table + `llm_calls.provider/key_source` columns; VARCHAR+CHECK guards; cites D-01/D-02/D-04/D-05/D-06/D-07.
- `app/models/__init__.py` - `LLMSettings` ORM class (server_default on all NOT NULL cols); `LLMCall.provider`/`key_source`; `Float` import.
- `requirements.txt` - `anthropic>=0.69,<1.0`.
- `tests/test_llm_capabilities.py` - LLMP-09/10: reasoning gate, temperature gating, max_tokens clamp floor ≥4000, effort→budget < max_tokens.
- `tests/test_llm_fallback.py` - LLMP-06: `is_key_level_error` taxonomy (key-level True; transient 429/5xx/conn False).
- `tests/test_llm_provider.py` - LLMP-11: OpenAI + Anthropic native param translation, role-alternation coalescing, response normalization.
- `tests/test_llm_settings_api.py` - LLMP-01/02/04/05: default-off, encrypted+masked, test-connection, workspace isolation.
- `tests/test_llm_models_filter.py` - LLMP-08: family/capability filter both providers.
- `tests/test_llm_logger_provider.py` - LLMP-07: provider + key_source columns persisted.
- `tests/test_llm_isolation.py` - LLMP-12: Whisper + KB embeddings pinned to platform singleton (GREEN preservation guard).
- `tests/conftest.py` - Exists-guarded migration 044 apply.
- `tests/test_ai_engine_empty_retry.py` - One-line note: will be updated in 18-04 to patch the adapter path (not rewritten here).

## Decisions Made
- **Dedicated `llm_settings` table over `workspaces` columns** (D-01/D-02): cleaner masked-key read pattern, and absence-of-row cleanly = platform default. Mirrors `warmup_settings` (038).
- **VARCHAR+CHECK, not PG enum**: `ALTER TYPE ADD VALUE` cannot run inside a transaction (the auto-applier wraps migrations); same precedent as `campaigns.status`.
- **Anthropic role-alternation as a first-class RED test**: `get_conversation_history` + the 3–5 min debounce can produce two inbound (user) turns in a row; Anthropic 400s on non-alternating roles (RESEARCH line 153), so `test_anthropic_coalesces_consecutive_same_role` locks in the merge-with-`\n\n` behaviour for 18-02.
- **`test_llm_isolation` is a GREEN preservation guard, not RED**: D-12 requires Whisper + embeddings to STAY on the platform OpenAI singleton; the guard passes today and will turn RED if a later plan routes them through the per-workspace factory.

## Deviations from Plan

None - plan executed exactly as written.

(One intra-task correction: the plan's isolation-test sketch referenced a `TelegramAIEngine` class name; the actual engine class is `AIEngine` (ai_engine.py:783). Corrected the `inspect.getsource` target to `ai_engine.AIEngine.transcribe_audio` so the D-12 guard passes GREEN as intended — this is a name-fix within Task 3's own scaffold, not a plan deviation.)

## Issues Encountered
None. All three task verifications passed; the full suite collects with 0 errors (896 tests) and the new behavioural tests fail for the right reason (missing target modules), while the isolation guard passes.

## User Setup Required
None - no external service configuration required. (Runtime deploy note for later plans: the `anthropic` SDK add requires rebuilding BOTH `api` and `listener` containers — the answerer runs in the listener. Not needed for this plan; tests run via the test-overlay which builds the api image.)

## Next Phase Readiness
- Wave-0 blocker cleared: the table, ORM mirror, SDK declaration, and all RED test files exist. 18-02 (provider adapter + resolution), 18-03 (settings API), 18-04 (wire answerer/warmup/logger) each have concrete failing tests to green.
- No blockers.

---
*Phase: 18-switchable-llm-provider*
*Completed: 2026-07-02*

## Self-Check: PASSED

- All 9 created files verified present on disk (migration + 7 test files + SUMMARY).
- All 3 task commits verified in git history (b32bb6e, 1bb39f2, 509f2e6).
