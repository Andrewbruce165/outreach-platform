# Phase 18: Switchable LLM Provider in UI - Research

**Researched:** 2026-07-02
**Domain:** Multi-provider LLM abstraction (OpenAI + Anthropic), per-workspace configuration, encrypted BYO API keys
**Confidence:** HIGH (SDK/API facts verified against live PyPI + official docs; code seams verified against actual source)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Уровень настройки**
- **D-01:** Provider/model choice lives at **workspace-level** (Settings). All agents and campaigns of the workspace use one setting. Per-agent override is NOT in this phase (deferred).
- **D-02:** Default state (workspace configured nothing) = current behaviour: platform OpenAI key + `settings.openai_model`. Nothing breaks for existing workspaces.

**API keys**
- **D-03:** Own API key is **mandatory** for explicit provider/model choice: without a key, switching is unavailable, workspace stays on the platform default. Platform does not pay for switched clients' tokens.
- **D-04:** Key stored **encrypted** — reuse the Fernet helper (`app/services/encryption.py`, same as session strings). Key is never returned in full in API responses (mask/prefix only) and never written to logs.
- **D-05:** **Test connection** button on key entry — cheap probe to the chosen provider; result visible immediately in UI.
- **D-06:** Runtime key errors (401 / invalid / quota): **fallback to the platform OpenAI default** (`OPENAI_API_KEY` + `settings.openai_model`) — dialogs don't stop; key flagged invalid in UI (a key-status flag in DB). Rationale: ghosted-contact was already an incident (2026-07-02); a live dialog matters more than billing purity.
- **D-07:** `llm_logger` writes the key/model source on every call (`platform` / `byok` / `fallback`) + the actual provider/model — for analytics and future cost-billing (from seed BYOK-01).

**Model choice & settings in UI**
- **D-08:** Model list is **live from the provider API** by the client's key (`/v1/models` for OpenAI, `/v1/models` for Anthropic), **server-side filtered** to chat-with-tools-compatible: no embeddings/whisper/tts/dall-e/realtime/deprecated. Filter is on the backend (whitelist family patterns: gpt-4o*/gpt-5*/o*/claude-*).
- **D-09:** Partial model settings in UI: **temperature**, **reasoning effort**, **max tokens (response budget)**. Temperature shown only for models that accept it (OpenAI reasoning models reject it). Reasoning effort — only for reasoning models; for Claude maps to extended thinking (mapping detail — Claude's discretion).
- **D-10:** Guard against dangerous values: **hard clamp on the backend + "green corridor" (recommended ranges) in UI**. In particular, the lower bound of max tokens for reasoning models (≥4000) — lesson of the 2026-07-02 incident. Impossible to break the prod answerer by a setting.

**Switch boundaries**
- **D-11:** Through the chosen model/key go: **the chat AI answerer** (all `ai_engine.generate_response` calls, including tool-handling and second-pass) AND **warmup chat** (single tone everywhere).
- **D-12:** **Whisper transcription of voice messages AND KB embeddings (ingest + search) ALWAYS stay on the platform OpenAI key** regardless of provider choice — Anthropic has no such APIs. Choosing Claude does not break voice or KB.

### Claude's Discretion
- Placement of the section in Settings UI (separate "AI / LLM" tab or a section in existing settings)
- Storage schema (columns on `workspaces` vs a separate LLM-settings table) — accounting for the ORM default-vs-server_default drift lesson (memory: migrations 040/042)
- Exact mapping of reasoning_effort ↔ Claude extended thinking budget
- Concrete clamp ranges and "green corridor" values per-model
- Provider abstraction in `ai_engine` (adapter / client factory) and adding the `anthropic` SDK to requirements
- Caching of the live model list (TTL), behaviour when `/models` is unavailable

### Deferred Ideas (OUT OF SCOPE)
- **Per-agent model override** — workspace default + per-agent override (v2)
- **BYOK for Whisper/embeddings** (all service calls through the client key) — rejected boundary; revisit on enterprise request for full BYOK
- **Other providers** (OpenRouter, Google, local models) — adapter architecture should permit; in this phase only Claude + OpenAI
- **Cost-billing based on key_source from llm_logger** — business logic beyond this phase; D-07 lays down the data
- **Update PROJECT.md**: Out-of-Scope line "Own OpenAI key per workspace (BYOK-01)" is subsumed by this phase — remove at phase transition
</user_constraints>

<phase_requirements>
## Phase Requirements

No formal `REQ-ID`s exist yet for Phase 18 (REQUIREMENTS.md maps up to SRLD-09 / Phase 17). Scope is derived from CONTEXT.md decisions D-01..D-12. The planner should DERIVE requirement IDs (suggested prefix `LLMP-01..NN`, "Switchable LLM Provider") during `/gsd:plan-phase 18` and append them to REQUIREMENTS.md, mirroring how Phases 12–17 derived their IDs. Suggested derivation for coverage-gate purposes:

| Suggested ID | Behaviour | Decisions | Research Support |
|--------------|-----------|-----------|------------------|
| LLMP-01 | Per-workspace LLM settings row (provider/model/knobs/encrypted key/key-status) | D-01, D-04 | § Storage Pattern (new `llm_settings` table, mirrors `warmup_settings`) |
| LLMP-02 | Default-off resolution: no row → platform OpenAI + `settings.openai_model` (byte-identical to today) | D-02 | § Resolution Order; `warmup_settings` default-off precedent |
| LLMP-03 | Own key mandatory to switch; switching blocked without a valid key | D-03 | § Test Connection Probe (key required before selection) |
| LLMP-04 | Fernet-encrypted key at rest; masked in responses; never logged | D-04 | § encryption.py reuse; § Security |
| LLMP-05 | Test-connection probe per provider (cheap verifiable call) | D-05 | § Test Connection Probe |
| LLMP-06 | Runtime key-error fallback to platform default + flag key invalid | D-06 | § Error Taxonomy for Fallback |
| LLMP-07 | `llm_logger` records provider + model + key_source | D-07 | § llm_logger extension |
| LLMP-08 | Live model list per provider, server-side family/capability filter | D-08 | § Model Listing (OpenAI + Anthropic) |
| LLMP-09 | Temperature / reasoning-effort / max-tokens knobs, capability-gated | D-09 | § Provider Capability Matrix |
| LLMP-10 | Backend hard-clamp + green corridor (reasoning max-tokens ≥4000) | D-10 | § Clamp & Green Corridor |
| LLMP-11 | Answerer + warmup route through the chosen provider/model/knobs | D-11 | § Provider Adapter; § Warmup |
| LLMP-12 | Whisper + KB embeddings pinned to platform OpenAI regardless of choice | D-12 | § Embedding/Whisper Isolation |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

These are LOCKED directives; research recommendations never contradict them.

- **Ask before non-trivial code** — objaсни→confirm→code (except typos/renames/docstrings).
- **Async everywhere** — all DB via async/await + AsyncSession; all outbound HTTP via `httpx.AsyncClient`; never `requests`, never `time.sleep()`, never `print()` (use `logging`).
- **Migrations: raw SQL only** in `migrations/`, numbered `NNN_short_name.sql`, **auto-applied** at API start via `app/database.py::_apply_migrations` (advisory lock, lexical order). **Must be idempotent** (`IF NOT EXISTS`, `DO $$ … EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`). Fail-fast: bad migration → API won't start. **Never Alembic** (`alembic` is in requirements.txt but the auto-applier is raw-SQL; do not introduce Alembic revisions).
- **Next migration number is 044** (latest on disk is `043_kb_chunks_fts_index.sql`).
- **ORM `default=` vs `server_default=` drift** (memory, mig 040/042): `create_all` recreates tables WITHOUT DB defaults for `default=`-only columns → raw-SQL INSERT omitting them → NotNullViolation. For any new NOT NULL column: set BOTH the ORM `server_default=` AND the migration `DEFAULT`, and provide an explicit INSERT value.
- **Security**: sessions encrypted, `API_KEY` never in logs. Same discipline extends to LLM API keys (D-04).
- **Queue/FloodWait/rate-limit constants**: DO NOT touch (`queue.py` empirical intervals). Phase 18 touches only the LLM call path — never the send-queue.
- **Tests only via test-overlay**: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. NEVER `docker compose run --rm api pytest` (conftest guard fires; historical prod DROP SCHEMA incident). NEVER `docker compose down -v` (wipes prod volume).
- **Communication in Russian; code/commits in English.**
- **Deploy**: `docker compose up -d --build api` + `docker compose up -d --build listener` (restart does NOT pick up code changes; the listener is a separate container and must be rebuilt too — the answerer runs in the listener).

## Summary

Phase 18 makes the LLM provider/model a **per-workspace runtime setting** instead of the hardcoded env `OPENAI_MODEL` (gpt-5-mini, a reasoning model). The work is fundamentally a **provider-adapter refactor of a single choke point** (`ai_engine._build_completion_params` + the three `chat.completions.create` call sites + warmup's own `AsyncOpenAI`) plus a **workspace settings table**, a **model-listing/test-connection API**, and **UI** in the sibling Lovable repo.

The critical architectural facts: OpenAI (SDK `openai>=1.40,<2.0`, currently 1.x — do NOT bump to 2.x, it has breaking changes and the project pin forbids it) uses `chat.completions.create` with `messages` (system as a role), `tools`/`tool_calls`, `max_completion_tokens`, and reasoning models reject `temperature`. Anthropic (SDK `anthropic` 0.115.1, verified on PyPI 2026-07-01) uses `client.messages.create` with a **top-level `system` param** (not a message role), **required `max_tokens`**, `tools` with `input_schema` (JSON Schema) and `tool_use`/`tool_result` **content blocks** (not OpenAI's `tool_calls`/role:"tool"), temperature range **0.0–1.0** (vs OpenAI 0.0–2.0), and either **`thinking={type:"enabled", budget_tokens:N}`** (older models: Opus 4.5, Haiku 4.5, earlier Claude 4) or a **`reasoning_effort`/`effort` capability + adaptive thinking** (newest models: Fable 5, Opus 4.8/4.7, Sonnet 5, which do NOT support manual `budget_tokens`). The two APIs' message/tool shapes differ enough that a **thin adapter that translates the internal message+tool representation into each provider's native shape** is the right abstraction — do NOT try to make Anthropic mimic OpenAI's exact object graph.

The existing code is favourable: `generate_response` already receives `AsyncSession` + `conversation_id` and calls `get_context_for_conversation` (which JOINs to the workspace), so **workspace LLM settings can be loaded on the same path with zero signature churn to the listener**. Temperature is currently **never passed** (code relies on model defaults), so adding it is net-new and must be capability-gated. The D-06 fallback must be surgical: trigger ONLY on key-level errors (OpenAI `AuthenticationError`/401, `PermissionDeniedError`/403, `RateLimitError` **with code `insufficient_quota`**; Anthropic `AuthenticationError`/401, `PermissionDeniedError`/403, `billing_error`/402) — NOT on transient 429 rate-limits or 5xx (those should retry/degrade as today, not silently swap providers).

**Primary recommendation:** Add `anthropic>=0.69,<1.0` to requirements; build a `app/services/llm/` provider-adapter layer (a `LLMProvider` protocol with `OpenAIProvider` + `AnthropicProvider`, each translating the internal `messages`+`tools` representation to native and normalising the response to `{text, tool_calls[], finish_reason, usage}`); load per-workspace config in `generate_response` via a new `llm_settings` table (mirror `warmup_settings` migration 038); gate knobs on a per-model capability map (temperature/reasoning-effort/max-tokens); hard-clamp on the backend (reasoning max-tokens ≥4000 per D-10); keep Whisper + embeddings on the module-level platform `AsyncOpenAI` singleton (D-12); fall back to platform OpenAI only on key-level errors (D-06).

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `anthropic` | `>=0.69,<1.0` (latest 0.115.1) | Anthropic Messages API async client (`AsyncAnthropic`) | Official SDK; async client mirrors `AsyncOpenAI` ergonomics; typed exceptions |
| `openai` | `>=1.40.0,<2.0.0` (KEEP pin) | Existing OpenAI async client | Already in `requirements.txt`; the `<2.0` pin is intentional — 2.x is a breaking major |

**Version verification (performed 2026-07-02):**
- `pip index versions anthropic` → latest **0.115.1** (released 2026-07-01). Pin a conservative floor + `<1.0` cap to avoid a future breaking 1.0.
- `pip index versions openai` → latest **2.44.0**, but the project pins `<2.0.0` (currently resolves to 1.109.x). **Do NOT relax this pin** — the 1.x→2.x jump has breaking client changes; all existing code (`chat.completions.create`, `RateLimitError`, `APIStatusError`) assumes 1.x.

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `cryptography` (Fernet) | 42.0.0 (present) | Encrypt the BYO API key at rest | D-04 — reuse `app/services/encryption.py`; add `encrypt_api_key`/`decrypt_api_key` aliases (or reuse `encrypt_session`/`decrypt_session` verbatim — same Fernet, same `ENCRYPTION_KEY`) |
| `httpx` | 0.26.0 (present) | Both SDKs use httpx under the hood; also usable for raw `/v1/models` if a probe is preferred outside the SDK | Model listing / test connection |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Two provider SDKs + adapter | LiteLLM / one unified gateway | Adds a heavy dependency + its own model-name mapping quirks; hides provider-native features (extended thinking display, capabilities endpoint); the adapter is ~200 lines and keeps the two native paths explicit. **Rejected** — CONTEXT.md D-Discretion says "adapter/factory", and only 2 providers are in scope. |
| New `llm_settings` table | Columns on `workspaces` | Columns bloat the hot `workspaces` row and complicate the masked-key read pattern; a dedicated table (PK = `workspace_id`, mirrors `warmup_settings`) is cleaner and default-absent = platform default (D-02). **Recommended: separate table.** |
| Raw `/v1/models` via httpx | SDK `client.models.list()` | Both work. SDK path reuses auth/retry; Anthropic SDK `models.list()` returns the rich `capabilities` object. **Recommend SDK path** for both (auth + typed errors free), fall back to httpx only if needed. |

**Installation:**
```bash
# add to requirements.txt:
# anthropic>=0.69,<1.0
docker compose up -d --build api
docker compose up -d --build listener   # answerer runs in the listener — MUST rebuild too
```

## Architecture Patterns

### Recommended Project Structure
```
app/services/
├── ai_engine.py          # keeps generate_response; delegates LLM call to provider adapter
├── warmup.py             # replaces self._openai with the same workspace-aware factory (D-11)
├── llm_logger.py         # + provider, key_source, actual_model columns (D-07)
├── encryption.py         # reuse Fernet for API keys (D-04)
└── llm/                  # NEW provider-adapter package
    ├── __init__.py       # get_provider(workspace_llm_config) factory
    ├── base.py           # LLMProvider protocol + normalized types (LLMResult, ToolCall)
    ├── openai_provider.py    # translates internal → chat.completions; normalizes response
    ├── anthropic_provider.py # translates internal → messages.create; normalizes response
    ├── capabilities.py   # per-model capability map + clamp/green-corridor helpers (D-09/D-10)
    └── resolve.py        # load llm_settings for workspace, decrypt key, decide platform/byok/fallback
```

### Pattern 1: Provider adapter with a normalized internal representation
**What:** `generate_response` builds ONE internal representation (system prompt string + `messages` list + `tools` list in the current OpenAI-ish shape) and hands it to a `LLMProvider.complete(...)` that returns a normalized `LLMResult{ text, tool_calls: list[ToolCall], finish_reason, usage, raw }`. Each provider translates in/out.
**When to use:** Always — this is the phase's core abstraction (D-Discretion "adapter/factory").
**Translation map (verified against both official docs):**

| Concept | OpenAI (chat.completions) | Anthropic (messages) |
|---------|---------------------------|----------------------|
| System prompt | `messages[0] = {role:"system", content}` | top-level `system=` param (NOT a message) |
| User/assistant turns | `{role, content}` | `{role, content}` (roles must alternate) |
| Max output tokens | `max_completion_tokens` (reasoning) / `max_tokens` (chat) | `max_tokens` (**required**, always) |
| Tools | `tools=[{type:"function", function:{name, description, parameters(JSON Schema)}}]` | `tools=[{name, description, input_schema(JSON Schema)}]` |
| Tool call out | `message.tool_calls[].{id, function.name, function.arguments(str JSON)}` | content block `{type:"tool_use", id, name, input(dict)}`, `stop_reason:"tool_use"` |
| Tool result back | `{role:"tool", tool_call_id, content}` | user message content block `{type:"tool_result", tool_use_id, content}` |
| Assistant reply text | `message.content` | content blocks `{type:"text", text}` (concatenate text blocks) |
| Finish reason | `finish_reason` (`stop`/`length`/`tool_calls`) | `stop_reason` (`end_turn`/`max_tokens`/`tool_use`) |
| Temperature | 0.0–2.0; **rejected by reasoning models** | 0.0–1.0 |
| Reasoning control | `reasoning_effort` (`minimal`/`low`/`medium`/`high`) on gpt-5*/o* | `thinking={type:"enabled", budget_tokens:N}` (older) OR `reasoning_effort`/adaptive (newer) |
| Usage | `usage.{prompt_tokens, completion_tokens, total_tokens}` | `usage.{input_tokens, output_tokens}` |

**Example (Anthropic native call the adapter must emit):**
```python
# Source: https://platform.claude.com/docs/en/api/messages (verified 2026-07-02)
from anthropic import AsyncAnthropic
client = AsyncAnthropic(api_key=byok_or_platform_key)
resp = await client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=4000,                     # REQUIRED, always
    system=system_prompt,                # top-level, not a message role
    messages=[{"role": "user", "content": "<user_message>...</user_message>"}],
    tools=[{"name": "mark_as_lead", "description": "...",
            "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}],
    # temperature=0.7,                    # 0.0–1.0 only
    # thinking={"type": "enabled", "budget_tokens": 2000},  # older models only; budget < max_tokens
)
# resp.content is a list of blocks; iterate: text = "".join(b.text for b in resp.content if b.type=="text")
# tool_use blocks: [b for b in resp.content if b.type=="tool_use"] -> {id, name, input}
# resp.stop_reason, resp.usage.input_tokens/output_tokens
```

### Pattern 2: Workspace config resolution on the existing context path
**What:** `generate_response` already calls `get_context_for_conversation(conversation_id, session)` which JOINs `conversations → campaigns → ai_contexts` and exposes `workspace_id`. Add a `resolve_llm_config(workspace_id, session)` (or fold into the same SELECT) that reads `llm_settings`; absent row → `PLATFORM_DEFAULT` (D-02). No listener/`generate_response` signature change needed — thread the resolved config into `_build_completion_params`/the provider factory internally.
**Why it fits:** zero churn to the listener call site (`ai_engine.generate_response(session=..., conversation_id=..., ...)` at `listener.py:336`).

### Pattern 3: Provider-native call sites, not per-call `if provider`
**What:** The three OpenAI call sites in `ai_engine.py` (initial `~1424`, empty-retry `~1478`, second-pass tool-summarise `~1652`) each do `await client.chat.completions.create(**params)`. Replace the raw client with `provider.complete(messages, tools=..., retry=...)` so all three route through the adapter. The empty-content-guard logic (finish_reason=="length" retry) generalises: OpenAI `finish_reason=="length"` ↔ Anthropic `stop_reason=="max_tokens"`.

### Anti-Patterns to Avoid
- **Making Anthropic emulate OpenAI's object graph** (`.choices[0].message.tool_calls`): the SDKs return different shapes. Normalise into a plain `LLMResult` dataclass instead of duck-typing across SDKs.
- **Passing `temperature` unconditionally**: reasoning models (gpt-5*/o*) return `400 unsupported_value`. Gate on capability (see § Provider Capability Matrix). Temperature is currently NOT passed at all — adding it blindly reintroduces a 400.
- **Reusing another provider's `max_completion_tokens` key for Anthropic**: Anthropic uses `max_tokens` (required); OpenAI reasoning uses `max_completion_tokens`. The adapter owns this mapping.
- **Fallback on transient errors**: D-06 fallback is for KEY-level errors only. A 429 rate-limit or 5xx must NOT flip the workspace to platform key (that would mask load problems and leak client traffic onto the platform bill). Fallback triggers = invalid-key/permission/quota only.
- **Logging the key**: never log the API key or put it in `llm_calls.prompt`. Mask to prefix+last4 in responses.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| API-key encryption | Custom crypto | `app/services/encryption.py` Fernet (`ENCRYPTION_KEY`) | Already vetted for session strings (D-04); one key, one code path |
| Model listing | Hardcoded model dropdown | Live `/v1/models` per provider (D-08) + server-side family filter | Models churn monthly; hardcoding goes stale, and D-08 mandates live |
| Anthropic HTTP/retry/auth | Raw httpx + manual headers | `AsyncAnthropic` SDK (`x-api-key`, `anthropic-version`, retries) | SDK handles the `anthropic-version` header, retries, typed errors |
| Reasoning-effort→thinking math | Ad-hoc numbers | A small documented mapping table (see § reasoning mapping) | One place to tune; testable pure function |
| Per-provider error classification | String-match error messages | Catch typed SDK exception classes (see § Error Taxonomy) | Docs explicitly say "catch typed classes, don't string-match" |

**Key insight:** The two SDKs already solve auth, retries, versioning, and error typing. The only genuinely new logic is (a) the translation adapter, (b) the capability/clamp map, and (c) the workspace-config resolution + fallback decision. Keep those three small and pure/testable.

## Common Pitfalls

### Pitfall 1: Anthropic manual `thinking` unsupported on newest models
**What goes wrong:** Sending `thinking={type:"enabled", budget_tokens:N}` to Claude Fable 5 / Opus 4.8 / 4.7 / Sonnet 5 fails or is deprecated — those models use **adaptive thinking** (`thinking.type:"adaptive"` or the `effort` capability), not manual budgets. `budget_tokens` manual thinking is supported on Opus 4.5, Haiku 4.5, and earlier Claude 4.
**Why it happens:** Anthropic moved reasoning control from manual budgets to an adaptive/effort model on 5-series.
**How to avoid:** Read the model's `capabilities.thinking.types` + `capabilities.effort` from `/v1/models` (Anthropic returns these). If `effort.supported`, prefer mapping reasoning_effort→`reasoning_effort`/effort param; else if `thinking.types.enabled.supported`, use manual `budget_tokens`; else omit reasoning controls. Store the capability snapshot so the adapter picks the right knob.
**Warning signs:** 400 `invalid_request_error` mentioning thinking; empty thinking blocks.

### Pitfall 2: `budget_tokens` must be < `max_tokens`
**What goes wrong:** For manual thinking, `budget_tokens >= max_tokens` → 400.
**How to avoid:** When mapping reasoning_effort→budget, clamp `budget_tokens = min(mapped, max_tokens - 512)`. Combined with D-10's reasoning max-tokens ≥4000 floor, budgets stay valid.

### Pitfall 3: OpenAI reasoning models reject temperature (and top_p, etc.)
**What goes wrong:** `temperature` on gpt-5*/o* → `400 invalid_request_error` code `unsupported_value` ("Only the default (1) value is supported").
**How to avoid:** `_is_reasoning_model()` already exists (`ai_engine.py:61`) — extend the capability map so temperature is only sent to non-reasoning OpenAI models and to Anthropic (0–1). UI hides the slider (D-09) and the backend never sends it.
**Warning signs:** the exact incident that motivated this phase's clamp discipline — silent 400s dropping replies.

### Pitfall 4: The reasoning empty-response incident (2026-07-02) is the reason for D-10
**What goes wrong:** gpt-5-mini spent its whole `max_completion_tokens` budget on hidden reasoning → `content=''`, `finish_reason=="length"`, no error → contact ghosted. Fixed by generous budgets (4000/6000) + reasoning_effort caps + logged retry.
**How to avoid:** The clamp (D-10) MUST enforce reasoning max-tokens ≥4000 on the write path so a client cannot set a small budget and reproduce this. The existing empty-guard/retry in `generate_response` must be preserved and generalised across providers (Anthropic `stop_reason=="max_tokens"` is the analog). Reference: memory `project-ai-answerer-runs-gpt5-mini-reasoning.md`.

### Pitfall 5: ORM `default=` vs `server_default=` drift on the new table
**What goes wrong:** A new NOT NULL column with only ORM `default=` (no DB `server_default`) → `create_all` builds the test schema without a DB default → raw-SQL INSERT omitting it → NotNullViolation (hit in mig 040/042).
**How to avoid:** For every NOT NULL column on `llm_settings`, set BOTH the migration `DEFAULT` and the ORM `server_default=`, and mirror the ORM class so `create_all` (test DB) matches. Reference: memory `project-orm-default-vs-server-default-drift.md`.

### Pitfall 6: Fallback must not leak client traffic on transient errors
**What goes wrong:** Treating a 429 rate-limit as a "bad key" and swapping to the platform key → platform pays for the client's overflow, and the real cause (client hitting their own rate limit) is masked.
**How to avoid:** Fallback (D-06) triggers ONLY on: OpenAI `AuthenticationError`(401), `PermissionDeniedError`(403), `RateLimitError` **iff** `e.code == "insufficient_quota"`; Anthropic `AuthenticationError`(401), `PermissionDeniedError`(403), `APIStatusError` with 402/`billing_error`. Transient `RateLimitError` (no insufficient_quota) and 5xx → existing degrade-to-None behaviour (reply not sent this turn), NOT a provider swap.

### Pitfall 7: The listener is a separate container
**What goes wrong:** The AI answerer runs in `outreach-platform-listener`, not the API container. Deploying only `--build api` leaves the answerer on old code.
**How to avoid:** Deploy BOTH `docker compose up -d --build api` AND `--build listener`. Warmup runs in the API container (lifespan worker), the answerer in the listener — both call the LLM, both must pick up the adapter.

## Code Examples

### Loading the platform singleton vs a per-workspace client
```python
# Source: app/services/ai_engine.py:41 (current) — the platform singleton stays,
# used for Whisper (transcribe_audio, ai_engine.py:1734) + KB embeddings (D-12).
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))  # platform, embeddings/whisper ONLY

# NEW: adapter builds a per-call client from resolved workspace config.
# Source: app/services/llm/resolve.py (new)
def build_client(cfg: LLMConfig):
    if cfg.provider == "anthropic":
        return AsyncAnthropic(api_key=cfg.decrypted_key)   # byok
    return AsyncOpenAI(api_key=cfg.decrypted_key or os.environ["OPENAI_API_KEY"])  # byok or platform default
```

### OpenAI reasoning params (existing, to move into the adapter)
```python
# Source: app/services/ai_engine.py:71-95 (current _build_completion_params)
# Reasoning models split max_completion_tokens between hidden reasoning + output.
params = {"model": model, "messages": messages,
          "max_completion_tokens": budget}          # 4000 first, 6000 retry (D-10 floor)
if is_reasoning_model(model):
    params["reasoning_effort"] = effort             # 'low' first, 'minimal' retry
# temperature ONLY for non-reasoning OpenAI models (else 400 unsupported_value)
if not is_reasoning_model(model) and temperature is not None:
    params["temperature"] = temperature             # 0.0–2.0
```

### Detecting a fallback-worthy error (both providers)
```python
# Source: OpenAI error-codes docs + Anthropic errors docs (verified 2026-07-02)
import openai, anthropic
def is_key_level_error(e) -> bool:
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return True
    if isinstance(e, openai.RateLimitError) and getattr(e, "code", None) == "insufficient_quota":
        return True
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    if isinstance(e, anthropic.APIStatusError) and e.status_code == 402:  # billing_error
        return True
    return False
```

### reasoning_effort ↔ Claude thinking mapping (discretion — recommended table)
```python
# For Claude models that support MANUAL thinking (Opus 4.5, Haiku 4.5, earlier Claude 4):
EFFORT_TO_BUDGET = {"minimal": 0, "low": 2000, "medium": 8000, "high": 16000}
# budget=0 => omit thinking entirely (thinking disabled).
# Always clamp: budget = min(budget, max_tokens - 512)  (Pitfall 2).
# For Claude models with the `effort` capability (Fable 5 / Opus 4.8+/Sonnet 5):
#   pass reasoning_effort straight through (low/medium/high) or thinking={"type":"adaptive"}.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded `OPENAI_MODEL` env, one platform key | Per-workspace provider/model/knobs + BYO key | This phase | Runtime switch, no redeploy (specifics §3) |
| Anthropic manual `thinking.budget_tokens` | Adaptive thinking + `effort`/`reasoning_effort` on 5-series | 2025→2026 model line | Capability-gate reasoning control per model |
| OpenAI `max_tokens` on chat | `max_completion_tokens` on reasoning models; `max_tokens` rejected on reasoning | gpt-5/o-series | Adapter must pick the right key per model family |
| `openai` 1.x | `openai` 2.x exists (2.44.0) | 2025 | **Do NOT adopt** — project pins `<2.0`; 1.x assumed everywhere |

**Deprecated/outdated:**
- Anthropic manual extended thinking (`thinking={type:"enabled", budget_tokens}`) is **deprecated on Opus 4.6 / Sonnet 4.6 and unsupported on the 5-series** (Fable 5, Mythos 5, Opus 4.8/4.7, Sonnet 5) — those use adaptive thinking. Still supported on Opus 4.5 / Haiku 4.5 / earlier Claude 4. Gate by the model's `capabilities` from `/v1/models`.
- OpenAI docs increasingly push the **Responses API** over `chat.completions`; this phase should **stay on `chat.completions`** (the entire codebase + tool-dispatch logic is built on it; migrating to Responses is out of scope and risky).

## Storage Pattern (Discretion — recommended)

**Recommendation: a new `llm_settings` table, PK = `workspace_id`, mirroring `warmup_settings` (migration 038).** Absence of a row = platform default (D-02), exactly like warmup's default-off pattern.

```sql
-- migrations/044_llm_settings.sql  (idempotent, auto-applied; next free number is 044)
BEGIN;
CREATE TABLE IF NOT EXISTS llm_settings (
    workspace_id        UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL DEFAULT 'openai',    -- 'openai' | 'anthropic'
    model               TEXT,                              -- NULL => platform settings.openai_model (D-02)
    api_key_encrypted   TEXT,                              -- Fernet ciphertext; NULL => platform key (D-03)
    api_key_prefix      TEXT,                              -- masked display (prefix + last4) — never the full key
    api_key_status      TEXT NOT NULL DEFAULT 'unset',     -- 'unset' | 'valid' | 'invalid' (D-05/D-06)
    temperature         DOUBLE PRECISION,                  -- NULL => provider default; gated by capability (D-09)
    reasoning_effort    TEXT,                              -- NULL | 'minimal'|'low'|'medium'|'high'
    max_tokens          INTEGER,                           -- NULL => code default; clamp ≥4000 for reasoning (D-10)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMIT;
```
- **CHECK constraints** for `provider IN ('openai','anthropic')` and `api_key_status IN (...)` should use the migration-028 pattern (`DO $$ ... EXCEPTION duplicate_object $$`) so the applier re-runs safely (migration `033_campaign_max_new_dialogs.sql` and `028_sender_restriction.sql` are the precedents — note ALTER TYPE ADD VALUE cannot run in a transaction, so use VARCHAR+CHECK not a PG enum, same as `campaigns.status`).
- **ORM mirror** (`app/models/__init__.py`): every NOT NULL column gets `server_default=` matching the SQL DEFAULT (Pitfall 5). Nullable knob columns (`model`, `temperature`, etc.) are fine as plain nullable.
- **Never store the key plaintext**; `api_key_prefix` is the only thing ever returned to the UI.

## llm_logger Extension (D-07)
`log_llm_call` (`app/services/llm_logger.py`) currently takes `model` and writes to `llm_calls`. Add parameters `provider: str` and `key_source: str` (`'platform'|'byok'|'fallback'`) and persist them (migration adds `llm_calls.provider TEXT`, `llm_calls.key_source TEXT` — nullable, no backfill needed; combine into the 044 migration or a sibling 045). The response-normalisation for `usage` differs (OpenAI `completion_tokens` vs Anthropic `output_tokens`) — the adapter normalises before logging so the existing token columns stay meaningful. Keep the never-raise contract and the no-prompt-in-logs guard.

## Provider Capability Matrix (D-09 gating)
| Model family | temperature | reasoning control | max-token param |
|--------------|-------------|-------------------|-----------------|
| OpenAI gpt-4o* / gpt-4* | yes (0–2) | none | `max_tokens` (or `max_completion_tokens`) |
| OpenAI gpt-5* / o1/o3/o4* | **NO** (400) | `reasoning_effort` (minimal/low/medium/high) | `max_completion_tokens` (max_tokens rejected) |
| Anthropic Claude (older: Opus4.5/Haiku4.5/earlier C4) | yes (0–1) | manual `thinking.budget_tokens` (< max_tokens) | `max_tokens` (required) |
| Anthropic Claude (5-series/4.6) | yes (0–1) | adaptive / `reasoning_effort`/`effort` | `max_tokens` (required) |

**Source for gating:** OpenAI has NO capability metadata in `/v1/models` (only id/created/owned_by) → gate by ID prefix via the existing `_is_reasoning_model`. Anthropic `/v1/models` DOES return `capabilities.{thinking, effort, structured_outputs}` → gate by reading those. Cache both (TTL, discretion — recommend 1h in-process, mirror the JWKS cache pattern in `auth.py`).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `anthropic` PyPI package | Anthropic provider path | ✗ (not installed) | latest 0.115.1 | Must add to requirements.txt + rebuild; no fallback (blocks Claude path) |
| `openai` package | OpenAI path (existing) | ✓ | 1.x (pinned `<2.0`) | — |
| `cryptography` Fernet | API-key encryption | ✓ | 42.0.0 | — |
| `ENCRYPTION_KEY` env | Fernet key derivation | ✓ (used for sessions) | — | — |
| `OPENAI_API_KEY` env | Platform default + Whisper + embeddings (D-12) | ✓ | — | — |
| `ANTHROPIC_API_KEY` env | NOT needed — Claude is BYOK only (D-03) | n/a | — | Platform never holds a Claude key |
| Outbound HTTPS to api.anthropic.com | Anthropic calls + test-connection | ✓ (server has outbound; senders use Decodo proxy but LLM calls do not) | — | — |

**Missing dependencies with no fallback:** `anthropic` package — must be added to `requirements.txt` and the container rebuilt (both api + listener). This is a normal add, not a blocker, but the plan's first wave should include it.
**Missing dependencies with fallback:** none.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.23+ |
| Config file | `pyproject.toml` / `pytest.ini` (asyncio mode auto) + `tests/conftest.py` |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_provider.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

**CRITICAL:** ONLY via the test-overlay (ephemeral `db-test` in tmpfs). NEVER `docker compose run --rm api pytest` (conftest guard fires; prod DROP SCHEMA history). Full baseline is GREEN (~865 tests as of 2026-07-02).

### Test seams (from actual code)
- **Adapter is a pure translation function** → unit-test in/out translation with NO network: assert OpenAI-shape → native-OpenAI-params and internal-shape → native-Anthropic-params (system hoisted to top-level, tools → input_schema, tool_result content blocks). This is the single highest-value seam.
- **LLM client is patchable**: existing tests do `patch.object(ai_engine.client.chat.completions, "create", new=AsyncMock(side_effect=[...]))` (see `tests/test_ai_engine_empty_retry.py:98`). Mirror for the Anthropic path: `patch` the adapter's client `messages.create` with an `AsyncMock` returning a fake `Message` (content blocks + stop_reason + usage).
- **Capability/clamp helpers are pure** → unit-test `is_reasoning_model`, temperature-gating, `max_tokens` clamp (reasoning floor ≥4000, D-10), and `effort→budget` mapping (budget < max_tokens, Pitfall 2) with plain assertions, no DB.
- **Config resolution + fallback decision** → unit-test `is_key_level_error()` classification per exception type (401/403/insufficient_quota → True; transient 429/5xx → False) and `resolve_llm_config` (absent row → platform default; row with byok key → byok; error → fallback flips `api_key_status='invalid'`).
- **Settings API** → `async_client` + `es256_supabase_jwt`/`test_workspace` fixtures (conftest): assert GET returns masked key (never full), PATCH stores encrypted, Test-connection endpoint returns valid/invalid, workspace isolation on read/write.

### Phase Requirements → Test Map
| Req | Behaviour | Test Type | Automated Command | File Exists? |
|-----|-----------|-----------|-------------------|-------------|
| LLMP-01/02 | llm_settings table + default-off resolution | integration | `pytest tests/test_llm_settings_api.py -x` | ❌ Wave 0 |
| LLMP-04 | key encrypted at rest, masked in response, absent from logs | integration + grep guard | `pytest tests/test_llm_settings_api.py -x` | ❌ Wave 0 |
| LLMP-05 | test-connection probe valid/invalid | integration (mocked client) | `pytest tests/test_llm_settings_api.py::test_test_connection -x` | ❌ Wave 0 |
| LLMP-06 | fallback only on key-level errors | unit | `pytest tests/test_llm_fallback.py -x` | ❌ Wave 0 |
| LLMP-07 | logger records provider + key_source | integration | `pytest tests/test_llm_logger_provider.py -x` | ❌ Wave 0 |
| LLMP-08 | model list family/capability filter | unit | `pytest tests/test_llm_models_filter.py -x` | ❌ Wave 0 |
| LLMP-09/10 | capability gating + clamp (reasoning ≥4000) | unit | `pytest tests/test_llm_capabilities.py -x` | ❌ Wave 0 |
| LLMP-11 | adapter translation OpenAI + Anthropic; answerer + warmup route through it | unit + integration | `pytest tests/test_llm_provider.py tests/test_ai_engine.py -x` | partial (`test_ai_engine*` exist) |
| LLMP-12 | Whisper + embeddings stay on platform OpenAI | grep/introspection guard | `pytest tests/test_llm_isolation.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_llm_*.py -x` (the new provider suite, < 30s, no network)
- **Per wave merge:** full suite via test-overlay
- **Phase gate:** full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_llm_provider.py` — adapter translation both directions (LLMP-11)
- [ ] `tests/test_llm_capabilities.py` — capability gating + clamp/green-corridor (LLMP-09/10)
- [ ] `tests/test_llm_fallback.py` — `is_key_level_error` taxonomy (LLMP-06)
- [ ] `tests/test_llm_settings_api.py` — settings CRUD + masking + test-connection (LLMP-01/02/04/05)
- [ ] `tests/test_llm_models_filter.py` — server-side model filter (LLMP-08)
- [ ] `tests/test_llm_logger_provider.py` — provider/key_source columns (LLMP-07)
- [ ] `tests/test_llm_isolation.py` — Whisper/embeddings still use platform singleton (LLMP-12)
- [ ] Framework install: add `anthropic>=0.69,<1.0` to `requirements.txt`, rebuild api + listener before running tests
- [ ] Existing `test_ai_engine_empty_retry.py` must be updated so the empty-guard/retry works through the adapter for both providers

## Open Questions

1. **Which Claude models to offer by default / recommend?**
   - What we know: `/v1/models` is live-filtered; family whitelist `claude-*`. Live models include claude-sonnet-4-5, claude-opus-4-x, claude-haiku-4-5, plus 5-series.
   - What's unclear: which model to preselect / show in the green corridor per D-10.
   - Recommendation: don't hardcode a default Claude model; let the user pick from the live list. If a "recommended" hint is wanted, pick a mid-cost Sonnet-class model at plan time and mark it as a UI hint only.

2. **Reasoning-effort UI for Claude 5-series (adaptive) vs older (manual budget).**
   - What we know: capability endpoint distinguishes `effort` vs `thinking.enabled`.
   - What's unclear: whether the UI should expose the same 4-level effort selector uniformly.
   - Recommendation: expose a single `reasoning_effort` (minimal/low/medium/high) knob; the adapter maps it to native (`reasoning_effort` for OpenAI + effort-capable Claude; `budget_tokens` for manual-thinking Claude). One UI knob, provider-specific translation.

3. **Model-list cache staleness / `/models` unavailable (D-Discretion).**
   - What we know: both endpoints exist; both can time out or 401 on a bad key.
   - Recommendation: 1h in-process TTL cache keyed by (workspace_id, provider); on `/models` failure during model selection, surface a soft error in the UI and fall back to a small static hint list, but NEVER auto-select. Test-connection (D-05) is the authoritative key-validity signal, decoupled from the list.

## Sources

### Primary (HIGH confidence)
- Anthropic Messages API — https://platform.claude.com/docs/en/api/messages — request shape (top-level `system`, required `max_tokens`, tools `input_schema`, `tool_use`/`tool_result` blocks), temperature 0–1, `client.messages.create`
- Anthropic Models List — https://platform.claude.com/docs/en/api/models-list — `/v1/models` shape, `capabilities.{thinking, effort, structured_outputs}`, `x-api-key` + `anthropic-version` headers
- Anthropic Extended Thinking — https://platform.claude.com/docs/en/docs/build-with-claude/extended-thinking — `thinking={type:"enabled", budget_tokens}`, budget < max_tokens, model support matrix (5-series adaptive), temperature not restricted
- Anthropic Errors — https://platform.claude.com/docs/en/api/errors — 401 auth / 402 billing / 403 permission / 429 rate / typed SDK exceptions
- PyPI `anthropic` — latest 0.115.1 (2026-07-01), verified via `pip index versions anthropic`
- PyPI `openai` — latest 2.44.0; project pin `<2.0` verified via `pip index versions openai`
- Actual source (verified this session): `app/services/ai_engine.py` (:41 client singleton, :61 `_is_reasoning_model`, :71 `_build_completion_params`, :1424/:1478/:1652 call sites, :1734 Whisper), `app/services/warmup.py` (:99 own `AsyncOpenAI`, :658 chat.completions), `app/services/encryption.py` (Fernet), `app/services/llm_logger.py`, `app/config.py` (:54 `openai_model`), `app/routers/workspace.py` (settings endpoint pattern), `app/models/__init__.py` (Workspace), `migrations/038_warmup_settings.sql` (per-workspace table precedent), `tests/conftest.py` + `tests/test_ai_engine_empty_retry.py` (mock/patch seam)

### Secondary (MEDIUM confidence)
- OpenAI `/v1/models` response shape (`{object:"list", data:[{id, object, created, owned_by}]}`) — https://platform.openai.com/docs/api-reference/models/list (via WebSearch; official page 403s WebFetch but shape corroborated by multiple sources)
- OpenAI reasoning models reject temperature (`400 unsupported_value`, only default 1) — https://community.openai.com/t/temperature-in-gpt-5-models/1337133 + Microsoft Foundry reasoning docs + LibreChat issue #10737
- OpenAI `insufficient_quota` surfaces as `RateLimitError`(429) distinct from transient 429 — https://developers.openai.com/api/docs/guides/error-codes + OpenAI Help Center 5955604

### Tertiary (LOW confidence — flagged for validation)
- Exact `EFFORT_TO_BUDGET` numbers are a recommendation, not from docs — tune during planning/testing.
- Which specific Claude model IDs are live at plan time — resolve against the live `/v1/models` when a test key is available.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — SDK versions verified on PyPI; both API shapes verified against official docs
- Architecture / adapter translation: HIGH — translation map built from official request/response docs + verified code seams
- Provider capability gating: HIGH (OpenAI temperature rejection + Anthropic capabilities endpoint both verified); reasoning-mapping numbers LOW (tunable)
- Pitfalls: HIGH — grounded in the 2026-07-02 empty-response incident (memory), the ORM-drift lesson (memory), and official constraint docs
- Storage / migration: HIGH — mirrors the existing `warmup_settings` (038) + `campaigns.status` VARCHAR+CHECK precedents

**Research date:** 2026-07-02
**Valid until:** 2026-08-01 (30 days) — model IDs and Anthropic thinking/effort semantics are fast-moving; re-verify `/v1/models` capabilities and SDK versions if planning slips past this window
