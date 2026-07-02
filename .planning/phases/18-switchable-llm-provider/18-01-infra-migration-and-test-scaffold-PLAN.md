---
phase: 18-switchable-llm-provider
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - requirements.txt
  - migrations/044_llm_settings.sql
  - app/models/__init__.py
  - tests/test_llm_capabilities.py
  - tests/test_llm_fallback.py
  - tests/test_llm_provider.py
  - tests/test_llm_settings_api.py
  - tests/test_llm_models_filter.py
  - tests/test_llm_logger_provider.py
  - tests/test_llm_isolation.py
autonomous: true
requirements: [LLMP-01, LLMP-04, LLMP-06, LLMP-07, LLMP-08, LLMP-09, LLMP-10, LLMP-11, LLMP-12]
must_haves:
  truths:
    - "anthropic SDK is importable in the api and listener containers"
    - "llm_settings table exists with one-row-per-workspace shape (PK workspace_id) — workspace-level setting scope per D-01"
    - "llm_calls has provider and key_source columns for D-07 logging"
    - "The full test suite collects with 0 errors after the new RED test files are added"
  artifacts:
    - path: "migrations/044_llm_settings.sql"
      provides: "llm_settings table + llm_calls.provider/key_source columns (idempotent)"
      contains: "CREATE TABLE IF NOT EXISTS llm_settings"
    - path: "app/models/__init__.py"
      provides: "LLMSettings ORM mirror + LLMCall provider/key_source columns"
      contains: "class LLMSettings"
    - path: "requirements.txt"
      provides: "anthropic SDK dependency"
      contains: "anthropic>=0.69,<1.0"
    - path: "tests/test_llm_capabilities.py"
      provides: "RED capability-gating + clamp test scaffold"
  key_links:
    - from: "app/models/__init__.py::LLMSettings"
      to: "migrations/044_llm_settings.sql"
      via: "server_default matches SQL DEFAULT on every NOT NULL column"
      pattern: "server_default"
---

<objective>
Lay the Phase 18 foundation: add the `anthropic` SDK, create the `llm_settings` per-workspace table + `llm_calls` provider/key_source columns (migration 044, idempotent, auto-applied), mirror the ORM, and drop the Wave-0 RED test scaffold so every downstream plan has a failing test to turn green.

Purpose: All downstream plans (adapter, settings API, wiring) depend on the table, the ORM mirror, the SDK being installed, and the RED test files existing. This plan is the sole Wave-0 blocker.
Output: `anthropic` in requirements; migration 044 + ORM `LLMSettings`; `llm_calls.provider`/`key_source`; 7 RED test files; updated `tests/test_ai_engine_empty_retry.py` note.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/18-switchable-llm-provider/18-CONTEXT.md
@.planning/phases/18-switchable-llm-provider/18-RESEARCH.md
@.planning/phases/18-switchable-llm-provider/18-VALIDATION.md

<interfaces>
<!-- Existing patterns the executor MUST mirror — extracted from codebase, no exploration needed. -->

migrations/038_warmup_settings.sql (per-workspace table precedent — copy the header/idempotency style):
```sql
BEGIN;
CREATE TABLE IF NOT EXISTS warmup_settings (
    workspace_id   UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    ...
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMIT;
```

app/models/__init__.py::WarmupSettings (ORM mirror precedent — every NOT NULL col has server_default):
```python
class WarmupSettings(Base):
    __tablename__ = "warmup_settings"
    workspace_id  = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    enabled       = Column(Boolean, nullable=False, server_default=text("false"))
    topics        = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    system_prompt = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

app/models/__init__.py::LLMCall (columns to extend — add provider + key_source):
```python
class LLMCall(Base):
    __tablename__ = "llm_calls"
    ...
    model = Column(String(50), nullable=False)
    prompt = Column(JSONB, nullable=False)
    ...
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

CHECK-constraint idempotency pattern (migration 028 style — ALTER TYPE ADD VALUE cannot run in a transaction, so use VARCHAR+CHECK not a PG enum):
```sql
DO $$ BEGIN
  ALTER TABLE llm_settings ADD CONSTRAINT llm_settings_provider_chk CHECK (provider IN ('openai','anthropic'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add anthropic SDK + migration 044 (llm_settings table + llm_calls columns)</name>
  <read_first>
    - requirements.txt (see current openai pin `openai>=1.40.0,<2.0.0` — do NOT touch it)
    - migrations/038_warmup_settings.sql (idempotency + header style to copy)
    - migrations/028_sender_restriction.sql (CHECK-constraint DO $$ ... EXCEPTION duplicate_object pattern)
    - CLAUDE.md section "Auto-applier миграций" (raw SQL, idempotent, fail-fast, next number 044)
  </read_first>
  <action>
    Add to `requirements.txt` on its own line (do NOT change the existing `openai>=1.40.0,<2.0.0` pin):
    ```
    anthropic>=0.69,<1.0
    ```

    Create `migrations/044_llm_settings.sql` — idempotent, wrapped `BEGIN; ... COMMIT;`, with a header comment block modelled on 038's header (state: per-workspace LLM settings, absence of row = platform default D-02, auto-applied, fail-fast). The table is keyed `PRIMARY KEY (workspace_id)` — one row per workspace — which implements the workspace-level setting scope (D-01: provider/model choice lives at the workspace level; no per-agent override this phase). Exact content:
    ```sql
    BEGIN;

    -- Per-workspace LLM provider/model/knobs + encrypted BYO API key (Phase 18).
    -- D-01: setting scope is workspace-level (PK = workspace_id, one row per workspace; no per-agent override).
    -- Absence of a row = platform default (D-02): platform OPENAI_API_KEY + settings.openai_model.
    -- api_key stored Fernet-encrypted (D-04); only api_key_prefix ever returned to UI.
    -- Idempotent: CREATE TABLE IF NOT EXISTS + DO$$ duplicate_object CHECK guards.

    CREATE TABLE IF NOT EXISTS llm_settings (
        workspace_id        UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
        provider            TEXT NOT NULL DEFAULT 'openai',
        model               TEXT,
        api_key_encrypted   TEXT,
        api_key_prefix      TEXT,
        api_key_status      TEXT NOT NULL DEFAULT 'unset',
        temperature         DOUBLE PRECISION,
        reasoning_effort    TEXT,
        max_tokens          INTEGER,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    DO $$ BEGIN
      ALTER TABLE llm_settings ADD CONSTRAINT llm_settings_provider_chk
        CHECK (provider IN ('openai','anthropic'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;

    DO $$ BEGIN
      ALTER TABLE llm_settings ADD CONSTRAINT llm_settings_key_status_chk
        CHECK (api_key_status IN ('unset','valid','invalid'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;

    -- D-07: llm_logger records provider + key_source per call. Nullable, no backfill.
    ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS provider   TEXT;
    ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS key_source TEXT;

    COMMIT;
    ```
    Do NOT use a PG enum type (ALTER TYPE ADD VALUE cannot run in a transaction — same reason `campaigns.status` is VARCHAR+CHECK).
  </action>
  <verify>
    <automated>grep -q "anthropic>=0.69,<1.0" requirements.txt && grep -q "CREATE TABLE IF NOT EXISTS llm_settings" migrations/044_llm_settings.sql && grep -q "ADD COLUMN IF NOT EXISTS provider" migrations/044_llm_settings.sql && grep -q "D-01" migrations/044_llm_settings.sql && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `requirements.txt` contains `anthropic>=0.69,<1.0`
    - `requirements.txt` still contains `openai>=1.40.0,<2.0.0` (unchanged)
    - `migrations/044_llm_settings.sql` contains `CREATE TABLE IF NOT EXISTS llm_settings`
    - `migrations/044_llm_settings.sql` header cites `D-01` (workspace-level scope, PK workspace_id) — literal "D-01" present for decision-coverage tracing
    - `migrations/044_llm_settings.sql` contains `api_key_encrypted   TEXT` and `api_key_prefix      TEXT` and `api_key_status      TEXT NOT NULL DEFAULT 'unset'`
    - `migrations/044_llm_settings.sql` contains `ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS provider   TEXT` and `key_source TEXT`
    - `migrations/044_llm_settings.sql` contains `EXCEPTION WHEN duplicate_object THEN NULL` (idempotent CHECK)
    - No `CREATE TYPE` / `ALTER TYPE ADD VALUE` anywhere in the migration
  </acceptance_criteria>
  <done>anthropic SDK added; migration 044 creates llm_settings (workspace-level PK per D-01) + adds llm_calls.provider/key_source, all idempotent.</done>
</task>

<task type="auto">
  <name>Task 2: ORM mirror — LLMSettings class + LLMCall provider/key_source columns</name>
  <read_first>
    - app/models/__init__.py lines 399-422 (WarmupSettings — the mirror pattern to copy: server_default on every NOT NULL col)
    - app/models/__init__.py lines 785-829 (LLMCall — where to add the two new columns)
    - .planning/notes memory lesson: ORM `default=` vs `server_default=` drift (mig 040/042). Every NOT NULL column MUST have `server_default=` so create_all builds the test schema WITH the DB default.
  </read_first>
  <action>
    In `app/models/__init__.py`, add a new `LLMSettings` ORM class mirroring the migration 044 table exactly. Place it near `WarmupSettings` (after line ~422). Every NOT NULL column MUST carry `server_default=` matching the SQL DEFAULT (Pitfall 5 — mig 040/042 drift). Nullable knob columns are plain nullable. Use the imports already in the file (`Column`, `UUID`, `ForeignKey`, `Text`, `Float`/`Double`, `Integer`, `DateTime`, `func`, `text`):
    ```python
    class LLMSettings(Base):
        """Per-workspace LLM provider/model/knobs + encrypted BYO key (Phase 18).

        D-01: setting scope is workspace-level — one row per workspace (PK workspace_id),
        no per-agent override this phase. Absence of a row = platform default (D-02):
        platform OPENAI_API_KEY + settings.openai_model. `api_key_encrypted`
        is Fernet ciphertext (D-04); `api_key_prefix` (prefix+last4) is the ONLY
        key material ever returned to the UI. `api_key_status` tracks validity
        (D-05/D-06). Knob columns are nullable => provider/code default.
        """
        __tablename__ = "llm_settings"

        workspace_id      = Column(UUID(as_uuid=True),
                                   ForeignKey("workspaces.id", ondelete="CASCADE"),
                                   primary_key=True)
        provider          = Column(Text, nullable=False, server_default=text("'openai'"))
        model             = Column(Text, nullable=True)
        api_key_encrypted = Column(Text, nullable=True)
        api_key_prefix    = Column(Text, nullable=True)
        api_key_status    = Column(Text, nullable=False, server_default=text("'unset'"))
        temperature       = Column(Float, nullable=True)
        reasoning_effort  = Column(Text, nullable=True)
        max_tokens        = Column(Integer, nullable=True)
        created_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
        updated_at        = Column(DateTime(timezone=True), server_default=func.now(),
                                   onupdate=func.now(), nullable=False)
    ```
    (If `Float` is not imported, add it to the sqlalchemy import line — `DOUBLE PRECISION` maps to SQLAlchemy `Float`.)

    Then extend the existing `LLMCall` class (line ~785): add two nullable columns after `error`:
    ```python
        provider   = Column(Text, nullable=True)   # D-07: 'openai'|'anthropic'
        key_source = Column(Text, nullable=True)   # D-07: 'platform'|'byok'|'fallback'
    ```
  </action>
  <verify>
    <automated>grep -q "class LLMSettings" app/models/__init__.py && grep -q "server_default=text(\"'openai'\")" app/models/__init__.py && grep -q "key_source = Column" app/models/__init__.py && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `app/models/__init__.py` contains `class LLMSettings(Base):`
    - `LLMSettings` has `__tablename__ = "llm_settings"` and PK `workspace_id` (D-01 workspace-level scope, cited in the class docstring)
    - `LLMSettings.provider` has `server_default=text("'openai'")` and `nullable=False`
    - `LLMSettings.api_key_status` has `server_default=text("'unset'")` and `nullable=False`
    - `LLMSettings` has nullable `api_key_encrypted`, `api_key_prefix`, `model`, `temperature`, `reasoning_effort`, `max_tokens`
    - `LLMCall` gained `provider = Column(Text, nullable=True)` and `key_source = Column(Text, nullable=True)`
  </acceptance_criteria>
  <done>LLMSettings ORM class mirrors migration 044 with server_default on every NOT NULL column; LLMCall carries provider + key_source.</done>
</task>

<task type="auto">
  <name>Task 3: Wave-0 RED test scaffold (7 new files) + update empty-retry test note</name>
  <read_first>
    - tests/conftest.py (fixtures: `async_client`, `test_workspace`, JWT fixtures — reuse verbatim; also the DSN guard)
    - tests/test_ai_engine_empty_retry.py (patch seam: `patch.object(ai_engine.client.chat.completions, "create", new=AsyncMock(...))`)
    - .planning/phases/18-switchable-llm-provider/18-VALIDATION.md (Wave 0 Requirements list + Per-Task Verification Map — the exact 7 files and what each asserts)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Validation Architecture (test seams: adapter pure translation, capability/clamp pure, is_key_level_error taxonomy, settings API masking) + line 153 (Anthropic "roles must alternate")
    - app/services/ai_engine.py lines 859-889 (get_conversation_history — proves consecutive same-role turns are produced by debounce; the Anthropic alternation test targets this real case)
  </read_first>
  <action>
    Create 7 RED test files. Each MUST import inside the test body (deferred import) so `--collect-only` stays clean even before the app modules exist (mirror the Phase 13/17 scaffold pattern). Each file gets fully-asserting tests that FAIL now (module not yet built) and pass once the corresponding plan lands. Target modules: `app.services.llm.capabilities`, `app.services.llm.resolve`, `app.services.llm.base`, `app.services.llm.openai_provider`, `app.services.llm.anthropic_provider`, and the settings router. Files + minimum test coverage:

    1. `tests/test_llm_capabilities.py` (LLMP-09/10, unit, no DB):
       - `test_reasoning_model_gate`: `is_reasoning_model("gpt-5-mini")` True, `is_reasoning_model("gpt-4o-mini")` False, `is_reasoning_model("claude-sonnet-4-5")` False (Claude reasoning gated separately).
       - `test_temperature_gated_off_for_openai_reasoning`: capability map / gate says temperature NOT allowed for gpt-5*, allowed for gpt-4o* and claude-*.
       - `test_max_tokens_clamp_reasoning_floor`: clamp of `max_tokens=500` on a reasoning model returns `>=4000` (D-10 floor); clamp of `max_tokens=200000` returns a sane ceiling.
       - `test_effort_to_budget_below_max_tokens`: effort→budget mapping returns `budget < max_tokens` for every effort level (Pitfall 2), and `minimal` → 0 (omit thinking).

    2. `tests/test_llm_fallback.py` (LLMP-06, unit, no DB):
       - `test_key_level_errors_true`: `is_key_level_error()` True for openai.AuthenticationError, openai.PermissionDeniedError, openai.RateLimitError with `code=='insufficient_quota'`, anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic APIStatusError status_code 402.
       - `test_transient_errors_false`: False for a plain openai.RateLimitError (no insufficient_quota), a 500 APIStatusError, APIConnectionError. (Construct exceptions minimally; if SDK ctor is awkward, use a small fake object exposing `.code`/`.status_code`.)

    3. `tests/test_llm_provider.py` (LLMP-11, unit, no network):
       - `test_openai_adapter_builds_native_params`: internal {system, messages, tools} → OpenAI params has `messages[0].role=='system'`, tools shaped `{type:'function', function:{name,...}}`, `max_completion_tokens` present for a reasoning model.
       - `test_anthropic_adapter_builds_native_params`: same internal input → Anthropic params has top-level `system=` (NOT a system message), `max_tokens` present (required), tools shaped `{name, description, input_schema}` (NO `type:'function'` wrapper), and NO temperature key when temperature is None.
       - `test_anthropic_coalesces_consecutive_same_role`: internal messages = `[{role:'user',content:'a'},{role:'user',content:'b'},{role:'assistant',content:'c'},{role:'user',content:'d'}]` (the debounce case: 2 inbound in a row from get_conversation_history). Assert the params `messages` handed to `messages.create` STRICTLY ALTERNATE user/assistant (no two consecutive entries share a role), the two leading user turns are merged into one whose content contains BOTH `'a'` and `'b'` joined by `"\n\n"`, and the final list is `user, assistant, user` (length 3). This is the RED test the checker requires — Anthropic 400s on non-alternating roles (RESEARCH line 153). Capture the params via a mocked `AsyncAnthropic.messages.create` (AsyncMock) and inspect `call_args.kwargs["messages"]`.
       - `test_anthropic_normalizes_response`: given a fake Anthropic Message (content = [text block + tool_use block], stop_reason='tool_use', usage input/output tokens) the adapter returns a normalized `LLMResult` with `.text` (concatenated text blocks), `.tool_calls` (list with name+arguments), `.finish_reason`, `.usage`.

    4. `tests/test_llm_settings_api.py` (LLMP-01/02/04/05, integration, uses async_client + test_workspace):
       - `test_get_settings_default_off`: GET `/api/v1/workspace/llm-settings` with no row returns provider default and `api_key_status=='unset'`, `model` null.
       - `test_patch_stores_encrypted_and_masks`: PATCH with `{provider, model, api_key}` returns 200; response body NEVER contains the full key (only masked prefix); a DB read shows `api_key_encrypted` != plaintext key.
       - `test_test_connection` (the id referenced in VALIDATION): POST `/api/v1/workspace/llm-settings/test-connection` with a mocked provider client returns `{status:'valid'}` for a good key and `{status:'invalid'}` for a key-level error.
       - `test_workspace_isolation`: workspace A cannot read workspace B's llm-settings (D-01 workspace-level scope enforced).

    5. `tests/test_llm_models_filter.py` (LLMP-08, unit):
       - `test_openai_family_filter`: filter of a raw id list `['gpt-4o','gpt-5-mini','text-embedding-3-small','whisper-1','dall-e-3','o3-mini','gpt-4o-realtime-preview']` keeps `gpt-4o`, `gpt-5-mini`, `o3-mini` and drops embeddings/whisper/dall-e/realtime.
       - `test_anthropic_family_filter`: keeps `claude-*` ids, drops anything non-claude.

    6. `tests/test_llm_logger_provider.py` (LLMP-07, integration):
       - `test_log_records_provider_and_key_source`: `log_llm_call(..., provider='anthropic', key_source='byok', ...)` writes a llm_calls row with `provider=='anthropic'` and `key_source=='byok'`.

    7. `tests/test_llm_isolation.py` (LLMP-12, grep/introspection guard, no network):
       - `test_whisper_uses_platform_singleton`: assert `ai_engine.transcribe_audio` source references the module-level `client` (platform AsyncOpenAI) — read `inspect.getsource(ai_engine.transcribe_audio)` and assert it does NOT build a per-workspace client and does NOT call the provider factory.
       - `test_kb_embeddings_use_platform_singleton`: assert `app/services/kb_ingest.py` and `app/services/kb_search.py` reference `ai_engine.client` (the platform singleton) for embeddings, not the new `app.services.llm` factory — grep the source text.

    Also add a one-line comment at the top of `tests/test_ai_engine_empty_retry.py` noting it will be updated in plan 18-04 to patch the adapter path (do NOT rewrite it here — just the note, so the scaffold stays collect-clean).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_capabilities.py tests/test_llm_fallback.py tests/test_llm_provider.py tests/test_llm_settings_api.py tests/test_llm_models_filter.py tests/test_llm_logger_provider.py tests/test_llm_isolation.py --collect-only -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - All 7 files exist under `tests/` with the exact names above
    - `--collect-only` on all 7 files exits 0 with 0 collection errors (deferred imports keep it clean)
    - `tests/test_llm_capabilities.py` contains `def test_max_tokens_clamp_reasoning_floor`
    - `tests/test_llm_fallback.py` contains `def test_key_level_errors_true` and `def test_transient_errors_false`
    - `tests/test_llm_provider.py` contains `def test_anthropic_adapter_builds_native_params`
    - `tests/test_llm_provider.py` contains `def test_anthropic_coalesces_consecutive_same_role` asserting two consecutive user turns produce a strictly alternating Anthropic message list (contents merged with `"\n\n"`)
    - `tests/test_llm_settings_api.py` contains `def test_test_connection`
    - `tests/test_llm_isolation.py` contains a test asserting Whisper/embeddings use the platform singleton
    - When run (not collect-only) the new behavioural tests FAIL (RED) because target modules do not yet exist — this is expected
  </acceptance_criteria>
  <done>7 RED test files land, collect cleanly, and fail on behaviour (target modules absent) — every downstream plan has a failing test to green, including the Anthropic role-alternation case.</done>
</task>

</tasks>

<verification>
- `grep -q "anthropic>=0.69,<1.0" requirements.txt` succeeds
- Migration 044 is idempotent (contains IF NOT EXISTS + duplicate_object guards) and cites D-01 (workspace-level scope)
- `class LLMSettings` exists with server_default on all NOT NULL columns; `LLMCall` has provider + key_source
- `tests/test_llm_provider.py` includes the Anthropic role-alternation RED case (`test_anthropic_coalesces_consecutive_same_role`)
- Full suite still COLLECTS with 0 errors via test-overlay: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q 2>&1 | tail -3`
- New behavioural tests are RED (target modules not built yet) — correct for Wave 0
</verification>

<success_criteria>
- anthropic SDK declared; migration 044 + ORM mirror create the llm_settings table (workspace-level PK, D-01) and llm_calls columns
- 7 RED test files exist, collect clean, fail on behaviour (incl. Anthropic alternation case)
- No PROTECTED constant (queue intervals) touched; no PG enum introduced; openai pin unchanged
</success_criteria>

<output>
After completion, create `.planning/phases/18-switchable-llm-provider/18-01-SUMMARY.md`
</output>
</output>
