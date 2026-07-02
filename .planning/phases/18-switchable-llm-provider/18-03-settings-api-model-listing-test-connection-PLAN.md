---
phase: 18-switchable-llm-provider
plan: 03
type: execute
wave: 2
depends_on: ["18-01"]
files_modified:
  - app/routers/llm_settings.py
  - app/services/llm/models_filter.py
  - app/main.py
autonomous: true
requirements: [LLMP-01, LLMP-02, LLMP-03, LLMP-04, LLMP-05, LLMP-08]
user_setup:
  - service: anthropic
    why: "BYO Claude API key (D-03) — Claude path is BYOK-only; platform holds no Anthropic key"
    env_vars: []
    dashboard_config:
      - task: "User enters their own Anthropic or OpenAI API key in Settings → AI/LLM (via UI, plan 18-05)"
        location: "Workspace Settings UI"
must_haves:
  truths:
    - "GET llm-settings returns the workspace config with the key MASKED (prefix+last4), never the full key"
    - "PATCH llm-settings stores the api key Fernet-encrypted and blocks switching without a key (D-03)"
    - "Test-connection probes the chosen provider and reports valid/invalid, flipping api_key_status"
    - "GET models returns a live, family-filtered list (chat-with-tools only) per provider"
    - "A workspace cannot read or write another workspace's llm-settings"
  artifacts:
    - path: "app/routers/llm_settings.py"
      provides: "workspace-scoped GET/PATCH settings + test-connection + models endpoints"
      exports: ["router"]
      contains: "llm-settings"
    - path: "app/services/llm/models_filter.py"
      provides: "server-side family/capability filter for /v1/models"
      contains: "def filter_models"
  key_links:
    - from: "app/routers/llm_settings.py"
      to: "app/services/encryption.py"
      via: "encrypt_api_key on PATCH"
      pattern: "encrypt_api_key"
    - from: "app/main.py"
      to: "app/routers/llm_settings.py"
      via: "include_router"
      pattern: "llm_settings.router"
---

<objective>
Add the workspace-scoped LLM settings API: GET (masked), PATCH (encrypt + validate D-03 key-mandatory), test-connection probe (D-05), and live model listing with server-side family filter (D-08). Register the router in main.py.

Purpose: This is the surface the Settings UI (18-05) drives. Runs in parallel with the adapter plan (18-02) — no shared files (adapter package vs router + a distinct models_filter module).
Output: `app/routers/llm_settings.py`, `app/services/llm/models_filter.py`, main.py registration. Turns `tests/test_llm_settings_api.py` + `tests/test_llm_models_filter.py` GREEN.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/18-switchable-llm-provider/18-CONTEXT.md
@.planning/phases/18-switchable-llm-provider/18-RESEARCH.md

<interfaces>
<!-- Existing router + registration patterns to mirror exactly. -->

app/routers/workspace.py (auth + workspace-scope pattern — same AuthCtx/auth_dep, same cross-tenant WHERE workspace_id):
```python
from app.utils.auth import AuthCtx, auth_dep
router = APIRouter(prefix="/api/v1", tags=["workspace"])

@router.get("/workspace", response_model=WorkspaceResponse)
async def get_workspace(ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workspace).where(Workspace.id == ctx.workspace_id))
    ...
def _require_jwt(ctx: AuthCtx) -> None:
    if ctx.source != "jwt":
        raise HTTPException(status_code=403, detail={"code":"JWT_REQUIRED", ...})
```

app/main.py registration (lines 20-36 import block, 184-198 include_router block):
```python
from app.routers import (agents, analytics, ..., warmup, workspace)
...
app.include_router(warmup.router)
app.include_router(knowledge_bases.router)
```

app/models/__init__.py::LLMSettings (from 18-01) — columns:
workspace_id (PK), provider, model, api_key_encrypted, api_key_prefix, api_key_status, temperature, reasoning_effort, max_tokens, created_at, updated_at

Masking: `api_key_prefix` is the ONLY key material ever returned. Compute on PATCH as e.g. `f"{key[:6]}...{key[-4:]}"` (never the whole key).

OpenAI /v1/models shape: {object:"list", data:[{id, object, created, owned_by}]} — no capability metadata.
Anthropic /v1/models: returns capabilities.{thinking, effort, structured_outputs}. Use SDK client.models.list() for both (auth + typed errors free).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: models_filter.py — server-side family/capability filter (D-08)</name>
  <read_first>
    - tests/test_llm_models_filter.py (the RED assertions: which ids kept/dropped)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Model Listing (D-08) + § Provider Capability Matrix (family whitelist gpt-4o*/gpt-5*/o*/claude-*)
  </read_first>
  <behavior>
    - filter_models(['gpt-4o','gpt-5-mini','text-embedding-3-small','whisper-1','dall-e-3','o3-mini','gpt-4o-realtime-preview','gpt-4o-transcribe','tts-1'], provider='openai') == kept: gpt-4o, gpt-5-mini, o3-mini (drops embeddings/whisper/dall-e/realtime/tts/transcribe)
    - filter_models(['claude-sonnet-4-5','claude-opus-4-5','claude-haiku-4-5','some-non-claude'], provider='anthropic') keeps the claude-* ids, drops non-claude
  </behavior>
  <action>
    Create `app/services/llm/models_filter.py` — pure function, no I/O:
    ```python
    import re

    # D-08: chat-with-tools families only. Exclude embeddings/whisper/tts/dall-e/realtime/transcribe/deprecated.
    _OPENAI_KEEP = re.compile(r"^(gpt-4o|gpt-4\.|gpt-4-|gpt-5|o1|o3|o4)", re.I)
    _OPENAI_DROP = re.compile(r"(embedding|whisper|tts|dall-e|dalle|realtime|audio|transcribe|image|moderation|search|instruct)", re.I)
    _ANTHROPIC_KEEP = re.compile(r"^claude-", re.I)

    def filter_models(model_ids: list[str], *, provider: str) -> list[str]:
        out = []
        for mid in model_ids:
            if provider == "anthropic":
                if _ANTHROPIC_KEEP.match(mid):
                    out.append(mid)
            else:  # openai
                if _OPENAI_KEEP.match(mid) and not _OPENAI_DROP.search(mid):
                    out.append(mid)
        return out
    ```
    Note: `gpt-4o-realtime-preview` matches `_OPENAI_KEEP` (starts gpt-4o) but is caught by `_OPENAI_DROP` (realtime) — so it drops. Same for `gpt-4o-transcribe`. Verify against the test ids.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_models_filter.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/llm/models_filter.py` contains `def filter_models`
    - Filter keeps `gpt-4o`, `gpt-5-mini`, `o3-mini`; drops `text-embedding-3-small`, `whisper-1`, `dall-e-3`, `gpt-4o-realtime-preview`, `gpt-4o-transcribe`, `tts-1`
    - Filter keeps `claude-*` and drops non-claude for provider='anthropic'
    - `tests/test_llm_models_filter.py` passes (exit 0)
  </acceptance_criteria>
  <done>Pure server-side model filter drops non-chat/non-tools families; filter test GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: llm_settings router — GET (masked) / PATCH (encrypt + D-03 gate) / test-connection / models</name>
  <read_first>
    - app/routers/workspace.py (full file — auth_dep, _require_jwt, cross-tenant WHERE, response schema style, HTTPException code/message shape)
    - app/services/encryption.py (encrypt_api_key/decrypt_api_key — from 18-02; if 18-02 not merged yet, use encrypt_session/decrypt_session which exist unconditionally)
    - app/models/__init__.py::LLMSettings (from 18-01)
    - app/config.py line 54 (settings.openai_model — the default returned when no row)
    - tests/test_llm_settings_api.py (RED assertions: default-off GET, masked PATCH, test_test_connection, workspace isolation)
    - .planning/phases/18-switchable-llm-provider/18-RESEARCH.md § Test Connection Probe + § Storage Pattern + § Model Listing
  </read_first>
  <behavior>
    - GET /api/v1/workspace/llm-settings, no row: returns {provider:'openai', model: settings.openai_model (or null), api_key_status:'unset', api_key_prefix: null, temperature:null, reasoning_effort:null, max_tokens:null}. Never a full key.
    - PATCH with {provider:'anthropic', model:'claude-sonnet-4-5', api_key:'sk-ant-...'}: upserts the row, stores api_key_encrypted (Fernet), api_key_prefix masked, api_key_status stays 'unset' until test-connection sets 'valid'. Returns masked body.
    - PATCH selecting a non-openai provider or a non-default model WITHOUT any stored key AND without an api_key in the body -> 400 KEY_REQUIRED (D-03: switching needs a key).
    - POST /test-connection: builds the provider client (mocked in tests), does a cheap probe (models.list or 1-token completion); success -> set api_key_status='valid', return {status:'valid'}; key-level error -> set api_key_status='invalid', return {status:'invalid'}.
    - GET /models?provider=...: uses the stored/decrypted key (or body key) to list models via SDK, runs filter_models, returns the filtered ids. /models failure -> soft 200 with empty list + a note (never 500-crash the settings page).
    - All endpoints: cross-tenant guard WHERE workspace_id == ctx.workspace_id; JWT-only for PATCH/test-connection (mirror _require_jwt).
  </behavior>
  <action>
    Create `app/routers/llm_settings.py` with `router = APIRouter(prefix="/api/v1", tags=["llm-settings"])` and endpoints on `/workspace/llm-settings`:
    - Pydantic schemas: `LLMSettingsResponse` (provider, model, api_key_prefix, api_key_status, temperature, reasoning_effort, max_tokens — NO full key field ever), `LLMSettingsUpdate` (all optional: provider, model, api_key, temperature, reasoning_effort, max_tokens), `TestConnectionResponse` (status: 'valid'|'invalid', detail: Optional[str]), `ModelListResponse` (models: list[str], note: Optional[str]).
    - `_get_or_none(db, workspace_id)` helper: SELECT LLMSettings WHERE workspace_id.
    - `GET /workspace/llm-settings` (auth_dep): row absent -> response with defaults (provider 'openai', model settings.openai_model, status 'unset'). Present -> map columns, `api_key_prefix` only (NEVER decrypt into the response).
    - `PATCH /workspace/llm-settings` (auth_dep + _require_jwt): upsert. D-03 gate: if the effective provider != 'openai' OR the effective model differs from the platform default AND there is neither a stored `api_key_encrypted` nor an `api_key` in the body -> raise HTTPException 400 `{"code":"KEY_REQUIRED","message":"An API key is required to switch provider/model"}`. When `api_key` present: `api_key_encrypted = encrypt_api_key(body.api_key)`, `api_key_prefix = f"{body.api_key[:6]}...{body.api_key[-4:]}"`, `api_key_status='unset'` (must re-test). Persist knob columns; clamp is enforced at call time (18-02) but ALSO clamp max_tokens here defensively via `app.services.llm.capabilities.clamp_max_tokens` when both model+max_tokens present. Return masked response.
    - `POST /workspace/llm-settings/test-connection` (auth_dep + _require_jwt): resolve the key (body override or stored decrypted). Build the provider client (`app.services.llm.get_provider` / a small local `_probe(provider, key)` that calls `models.list()`), await a cheap probe. On success: UPDATE api_key_status='valid', return {status:'valid'}. On `is_key_level_error`: UPDATE api_key_status='invalid', return {status:'invalid', detail:...}. On transient/other error: return {status:'invalid', detail:'probe failed'} WITHOUT flipping to invalid permanently is acceptable — but at minimum never leak the key in `detail`.
    - `GET /workspace/llm-settings/models` (auth_dep, query `provider`): decrypt stored key (or 400 KEY_REQUIRED if none), call the provider SDK `models.list()`, extract ids, `filter_models(ids, provider=provider)`, return. On provider error: return `ModelListResponse(models=[], note="model list unavailable")` with 200 (D-Discretion soft-fail; test-connection is the authoritative validity signal).
    - NEVER log the api key; never put it in any HTTPException detail.

    Register in `app/main.py`: add `llm_settings` to the `from app.routers import (...)` block (alphabetical near `knowledge_bases`) and `app.include_router(llm_settings.router)  # Phase 18 — switchable LLM provider settings` next to the warmup/knowledge_bases lines.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_settings_api.py -x -q 2>&1 | tail -8</automated>
  </verify>
  <acceptance_criteria>
    - `app/routers/llm_settings.py` contains `router = APIRouter(` and endpoints `/workspace/llm-settings`, `/workspace/llm-settings/test-connection`, `/workspace/llm-settings/models`
    - PATCH uses `encrypt_api_key` (or `encrypt_session`) — grep the file for `encrypt_`
    - No endpoint returns the full key: the response schema `LLMSettingsResponse` has NO field named `api_key` (only `api_key_prefix`) — grep confirms absence of a plaintext key field
    - D-03 gate present: grep `KEY_REQUIRED`
    - `app/main.py` contains `llm_settings` in the import block and `app.include_router(llm_settings.router)`
    - `tests/test_llm_settings_api.py` passes (exit 0), including `test_test_connection` and `test_workspace_isolation`
  </acceptance_criteria>
  <done>Workspace-scoped settings API: masked GET, encrypting PATCH with D-03 key gate, test-connection probe, filtered live model list; registered in main.py; settings-API tests GREEN.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_settings_api.py tests/test_llm_models_filter.py -x` — GREEN
- API never returns the full key (grep response schema for absence of plaintext key field)
- main.py registers the router
- Cross-tenant isolation asserted by test_workspace_isolation
</verification>

<success_criteria>
- llm_settings router + models_filter + main.py registration
- test_llm_settings_api + test_llm_models_filter GREEN
- Key masked in every response; D-03 key-mandatory gate enforced; no key in logs/details
</success_criteria>

<output>
After completion, create `.planning/phases/18-switchable-llm-provider/18-03-SUMMARY.md`
</output>
