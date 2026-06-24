# Phase 11: Agent/Campaign Field Split & Prompt Assembly - Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 11 (8 backend, 3 frontend/contract)
**Analogs found:** 11 / 11 (all in-repo — this is a refactor phase, every pattern already exists)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `migrations/030_*.sql` | migration | transform (DDL + data-migrate) | `migrations/018_phase5_1.sql` + `016_phase4.sql` | exact |
| `app/models/__init__.py` (AIContext, Campaign) | model | CRUD | same file (existing 05.1 v2 cols `voice_baseline`/`tone`/`max_message_length`; `campaigns.tools` JSONB) | exact |
| `app/schemas/__init__.py` (AgentCreate/Update, CampaignCreate/Update) | schema | request-response | same file (`voice_baseline: Literal[...]`, `ToneSpec`, `QAPair`) | exact |
| `app/routers/agents.py` | controller | CRUD | same file (`_agent_to_response` :56, PATCH field block :280-318) | exact |
| `app/routers/campaigns.py` | controller | CRUD | `app/routers/agents.py` (mirror field-handling) + same file | exact |
| `app/services/ai_engine.py::build_system_prompt` | service | transform | same function (:559-713 block-conditional assembly) | exact |
| `app/services/ai_engine.py::get_context_for_conversation` / `get_context` | service | request-response (DB read) | same functions (:147 SELECT, :466 cached SELECT) | exact |
| `app/services/listener.py::schedule_ai_response` | service | event-driven | same function (:208-233 debounce delay calc) + context build :847 | exact |
| `tests/test_ai_engine.py` / `test_migration_030.py` / `test_listener_response_speed.py` | test | unit/integration | `tests/conftest.py` migration list :127-150 | role-match |
| `lovable-handoff/openapi.json` + `types/api.ts` | config | build-artifact | `scripts/export-handoff.sh` (regen flow) | exact |
| `src/routes/_authenticated/campaigns.new.tsx` (+ `agents.tsx`, `EditCampaignModal.tsx`) | component | request-response (form) | same file (STEPS :45, form-state hooks :103-130, autoFillMut :154) | exact |

---

## Pattern Assignments

### `migrations/030_*.sql` (migration, transform — DDL + data-migrate)

**Analog:** `migrations/018_phase5_1.sql` (enum+CHECK pattern), `migrations/016_phase4.sql` (JSONB DEFAULT, COALESCE backfill)

**Enum-as-VARCHAR+CHECK pattern** (`018_phase5_1.sql:31-34`) — copy verbatim shape for new `tone_preset` and `response_speed`. **Never SQLEnum** (`ALTER TYPE ADD VALUE` cannot run in a transaction — see `016_phase4.sql:9-11`):
```sql
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS voice_baseline VARCHAR(20);
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_voice_baseline_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_voice_baseline_check
    CHECK (voice_baseline IS NULL OR voice_baseline IN ('Professional','Friendly','Playful'));
```

**JSONB column with DEFAULT** (`016_phase4.sql:37`) — analog for `campaigns.dialogue_flow JSONB DEFAULT '[]'::jsonb`:
```sql
tools                JSONB NOT NULL DEFAULT '[]'::jsonb,
```

**COALESCE backfill-before-drop pattern** (`018_phase5_1.sql:62-66`) — analog for both data migrations (`voice_baseline`→`tone_preset` MIG-01; `success_criteria`→`lead_trigger_hint` MIG-03). Note the `WHERE ... IS NULL` idempotency guard so re-run is a no-op:
```sql
UPDATE campaigns SET webhook_url = COALESCE(
    webhook_url, lead_webhook_url, handoff_webhook_url, finish_webhook_url
) WHERE webhook_url IS NULL
  AND (lead_webhook_url IS NOT NULL OR ...);
-- Do NOT drop the source col before the UPDATE (Pitfall 4)
```

**Transaction wrapper + revival-of-dropped-column note** (`018_phase5_1.sql:11`, `:42-45`) — `response_delay_seconds` was dropped in 015, re-ADD with `IF NOT EXISTS` exactly like `auto_pause_triggers` was revived:
```sql
BEGIN;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS auto_pause_triggers TEXT[];
...
COMMIT;
```

**Strict operator order for `030_*.sql` (Pitfall 4):** ADD `tone_preset` → UPDATE from `voice_baseline` → DROP CONSTRAINT `..._voice_baseline_check` → DROP COLUMN `voice_baseline`/`tone`/`tone_of_voice`. Same shape for `success_criteria`.

---

### `app/models/__init__.py` — AIContext (:184-219), Campaign (:497+) (model, CRUD)

**Analog:** same file. New ORM columns mirror the existing 05.1 v2 column declarations.

**VARCHAR enum column** (`:204`) — analog for `tone_preset`, `response_speed`:
```python
voice_baseline = Column(String(20), nullable=True)
```

**Int column with server_default** (`:207`) — analog for re-added `response_delay_seconds`:
```python
max_message_length = Column(Integer, nullable=True, server_default="280")
```

**JSONB column** (`:206`, `:197`) — analog for `campaigns.dialogue_flow`:
```python
tone = Column(JSONB, nullable=True)
faq = Column(JSONB, default={})
```

**Drop-comment convention** (`:216-219`) — when removing `voice_baseline`/`tone`/`tone_of_voice`, leave a tombstone comment in the same style as the existing `# NB: response_delay_seconds, is_active dropped (Phase 3 D-01).`

---

### `app/schemas/__init__.py` — AgentCreate/Update (:451-494), CampaignCreate/Update (:604-682) (schema, request-response)

**Analog:** same file.

**Literal enum validation** (`:465`) — analog for `tone_preset` and `response_speed`. Use `Literal[...]`, not custom validators (Don't-Hand-Roll):
```python
voice_baseline: Optional[Literal["Professional", "Friendly", "Playful"]] = None
```

**Structured JSONB element as nested model** (`:445-448`, `ToneSpec :438-442`) — analog for `dialogue_flow: list[DialogueStage]`. Define a `DialogueStage(BaseModel)` with `title`/`instruction` constr fields:
```python
class QAPair(BaseModel):
    q: constr(min_length=1, max_length=2000)
    a: constr(min_length=1, max_length=4000)
```

**Partial-PATCH convention** (`AgentUpdate :476+`, `CampaignUpdate :649+`) — all new fields `Optional[...] = None`; mirror new fields into BOTH Create and Update bodies. `success_criteria` (`:632`, `:678`) is being merged away — keep it accepted only if needed for back-compat, otherwise remove from both.

**`from_attributes` config** (`CampaignCreate :606`) — keep `model_config = ConfigDict(from_attributes=True)`.

---

### `app/routers/agents.py` & `campaigns.py` (controller, CRUD)

**Analog:** `app/routers/agents.py` — response serialiser (`:56-82`) and PATCH field block (`:280-318`).

**Response pass-through** (`_agent_to_response :65-78`) — add new fields straight from columns, same one-line-per-field style:
```python
voice_baseline=agent.voice_baseline,
tone=agent.tone,
max_message_length=agent.max_message_length,
```

**Partial-PATCH `if payload.X is not None:` block** (`:301-318`) — copy this exact idiom for each new field. JSONB/nested gets `.model_dump()` (Pitfall 7 full-replacement, not merge):
```python
if payload.voice_baseline is not None:
    agent.voice_baseline = payload.voice_baseline
if payload.tone is not None:
    agent.tone = payload.tone.model_dump()
```
For `campaigns.dialogue_flow` (list of nested): `campaign.dialogue_flow = [s.model_dump() for s in payload.dialogue_flow]` — mirror `qa_pairs` handling (`:313-314`).

**Cache invalidation on PATCH** — after committing agent edits, call `AIEngine.invalidate_context` (`ai_engine.py:521`) so `tone_preset`/`response_speed` changes apply before TTL. Confirm existing PATCH already does this; new fields ride the same call.

---

### `app/services/ai_engine.py::build_system_prompt` (:559-713) (service, transform)

**Analog:** the same function — this is a re-order + dedup of the existing block-conditional skeleton, NOT a rewrite from scratch.

**Block-conditional skeleton to preserve** (`:597-713`) — keep the `blocks: list[str]` + conditional-append + `"\n\n".join(blocks)` shape; change ONLY the order (BRIEF §7) and the source of each block:
```python
blocks: list[str] = []
blocks.append("<role>\n" + "\n\n".join(role_lines) + "\n</role>")
if company_knowledge:
    blocks.append(f"<company>\n{company_knowledge}\n</company>")
if knowledge_base:
    blocks.append(f"<product>\n{knowledge_base}\n\n{_PROMPT_PRODUCT_GUARD}\n</product>")
# ...
return "\n\n".join(blocks)
```

**Target order (BRIEF §7 → tags):** `<role>` (ИДЕНТИЧНОСТЬ) → `<company>` → `<product>` → `<tone>` → `<task_audience>` (ЗАДАЧА+КОМУ ПИШЕМ) → `<dialogue_flow>` (ХОД РАЗГОВОРА) → `<arguments_facts>` → (БАЗА ЗНАНИЙ, deferred — skip) → `<rules>` (ПРАВИЛА, deduped) → `<signals>`/`<tools>` → `<message_style>` (ФОРМАТ ОТВЕТА).

**`<tone>` block — DELETE and replace (PMT-02).** Current 3-source assembly (`:614-635`, reads `voice_baseline`+`tone_spec`+`tone_of_voice`) is removed entirely. Replace with single-source `tone_preset` → 1-2 lines (RESEARCH provides the `_TONE_LINES` dict). Tone must NOT reappear in `<rules>` (D-03).

**`_PROMPT_PRODUCT_GUARD` (:397-400)** — exact analog for the new `[АРГУМЕНТЫ И ФАКТЫ]` anti-hallucination guard (PMT-04, D-12). Reuse the proven "strictly from this block / don't fill in details that aren't here" phrasing pattern:
```python
_PROMPT_PRODUCT_GUARD = (
    "Answer product questions strictly from this block. Don't fill in details "
    "that aren't here."
)
```
Build a `_PROMPT_FACTS_GUARD` constant in the same module-level constant style (`:389-457`) and append it inside the `<arguments_facts>` block exactly as `<product>` appends `_PROMPT_PRODUCT_GUARD` (`:611`).

**`_PROMPT_DIALOGUE_GOAL` (:402-408) → REMOVE (PMT-03).** Static 3-step goal is replaced by per-campaign `dialogue_flow` render. Numbered-stage rendering from the JSONB array (RESEARCH supplies the `enumerate(dialogue_flow, start=1)` loop). The `<dialogue_goal>` append at `:649-651` is deleted.

**`<rules>` dedup (PMT-05).** Current `<rules>` (`:637-638`) renders agent rules only. New version concatenates agent `rules` + campaign `campaign_rules` through a line-level dedup helper (`dict.fromkeys` / seen-set, RESEARCH supplies `_dedup_rules`). Agent rules first, campaign rules second (Pitfall 5 — preserve order, exact-match only).

**Module-level constant style** (`:385-457`) — any new guard/text constant goes here as `_PROMPT_*`, not inline.

---

### `app/services/ai_engine.py::get_context_for_conversation` (:147-268) & `get_context` (:466-519) (service, DB read)

**Analog:** same functions.

**SELECT → dict mapping** (`:174-261`) — add `a.tone_preset`, `a.response_speed`, `a.response_delay_seconds` (agent) and `c.dialogue_flow`, `c.arguments_facts`, `c.campaign_rules` (campaign) to the SELECT, then map into the `context` dict (`:216-245`) and the `context["campaign"]` sub-dict (`:248-261`) following the existing one-line-per-field style:
```python
context = {
    ...
    "voice_baseline": row.voice_baseline or "",   # → becomes "tone_preset": row.tone_preset or ""
    "max_message_length": row.max_message_length or 280,
}
context["campaign"] = {
    ...
    "lead_trigger_hint": row.lead_trigger_hint,    # add dialogue_flow / arguments_facts / campaign_rules here
}
```

**Remove tone COALESCE.** The `<tone>` COALESCE/multi-column reads (`:186-191`, mapped `:227-231`: `tone_of_voice`/`voice_baseline`/`tone_spec`) collapse to a single `a.tone_preset` SELECT column. Drop the now-dead keys from the dict.

**`get_context` cached read (:486-498)** — if `tone_preset`/`response_speed` are needed by the cheap cached path (listener pulls agent speed from here per RESEARCH Open-Q1), widen this SELECT too. Note the ordinal-position warning in the comment (`:483-485`).

---

### `app/services/listener.py::schedule_ai_response` (:208-233) + context build (:847-854) (service, event-driven)

**Analog:** same function.

**Delay calculation point (RT-01)** (`:229`) — this single line is where `response_speed` wires in. The `MAX_BUFFER_TIME` guard (`:223`, `:229`) MUST be preserved:
```python
delay = min(random.uniform(self.DEBOUNCE_MIN, self.DEBOUNCE_MAX), self.MAX_BUFFER_TIME - buffer_age)
```
Replace with a branch on `context["response_speed"]`: `instant`→~0-2s, `human`→current `DEBOUNCE_MIN/MAX` (default, back-compat), `slow`→larger range, `manual`→`response_delay_seconds`. **DEBOUNCE_* class constants (`:133-135`) stay as the `human` default — do NOT touch `queue.py` constants (Pitfall 1 — different subsystem).**

**Context-dict augmentation** (`:847-854`) — add `response_speed`/`response_delay_seconds` to the `context` dict built in `handle_incoming_message`, sourced from the agent (`ai_context_id` is already in scope at `:825`). Reuse `AIEngine.get_context` (cached TTL 60s) rather than a new SELECT:
```python
context = {
    "ai_context_id": ai_context_id,
    "contact_name": name,
    ...   # add: "response_speed", "response_delay_seconds"
}
```

---

### Tests (test, unit/integration)

**Analog:** `tests/conftest.py` migration list (`:127-150`).

**Hardcoded migration list (Pitfall 3)** — conftest does NOT glob; the list ends at `027`. MUST add `028_sender_restriction_events.sql`, `029_campaign_pause_reason.sql`, `030_*.sql` to the tuple (`:127-150`), else `UndefinedColumn` in test DB:
```python
for filename in (
    "012_workspace.sql",
    ...
    "027_folders_workspace_name_unique.sql",
    # ADD: 028, 029, 030
):
```
**Post-migration explicit-default block** (`:157-163`) — when adding columns with DEFAULT that ORM `create_all` also creates, set defaults explicitly here (same reason as the existing `tone`/`max_message_length` block).

New test files (`test_migration_030.py`, `test_listener_response_speed.py`, extended `test_ai_engine.py`) follow existing pytest-asyncio conventions. Golden-prompt order assertions: `assert prompt.index("<role>") < prompt.index("<tone>") < ...` (RESEARCH §Validation).

---

### `lovable-handoff/openapi.json` + `types/api.ts` (config, build-artifact)

**Analog / Don't-Hand-Roll:** `scripts/export-handoff.sh` (regen flow, UI-FLD-03). Never hand-edit `types/api.ts` — run the script after schema changes (openapi-typescript@7). Regen AFTER backend schema changes, BEFORE frontend work (Pitfall 6 ordering).

---

### Frontend wizard — `campaigns.new.tsx` (+ `agents.tsx`, `EditCampaignModal.tsx`) (component, form)

**Analog:** `campaigns.new.tsx` — `STEPS` array (`:45-53`), per-field `useState` hooks (`:103-130`), `autoFillMut` (`:154-169`).

**Step config** (`:45-73`) — `brief` and `agent` steps change (BRIEF §6); Senders/Audience/Schedule/Integrations/Review unchanged. `STEP_TITLES`/`STEP_SUBS` updated for renamed labels.

**Form-state hook per field** (`:103-130`) — add `useState` for new fields (`tonePreset`, `responseSpeed`, `responseDelaySeconds`, `dialogueFlow`, `argumentsFacts`, `campaignRules`) following the existing one-hook-per-field style. Rename `audienceHints` label to "Кому пишем" (column unchanged, D-13).

**Dialogue-flow stage editor (D-05, UI-FLD-02)** — native React state array + move-up/down + add/remove buttons. NO drag-n-drop library (Don't-Hand-Roll — 3-5 elements). Pattern: array `useState<DialogueStage[]>`, index-based splice for reorder.

**Auto-fill mutation** (`:154-169`) — `autoFillMut` continues to fill structural state fields only; raw brief text never sent to prompt (D-15). New structural fields can be added to its `onSuccess` setters.

---

## Shared Patterns

### Idempotent migration
**Source:** `migrations/018_phase5_1.sql`, `016_phase4.sql`
**Apply to:** `030_*.sql`
- `BEGIN;`/`COMMIT;` wrapper, `ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS` before re-ADD, `DROP COLUMN IF EXISTS`, `WHERE ... IS NULL` guards on backfill UPDATEs. Enum = VARCHAR+CHECK (never SQLEnum). Fail-fast applier (`app/database.py::_apply_migrations`, lexical order, advisory-lock) — file must be safe to re-run.

### Anti-hallucination guard
**Source:** `app/services/ai_engine.py:397-400` (`_PROMPT_PRODUCT_GUARD`)
**Apply to:** new `[АРГУМЕНТЫ И ФАКТЫ]` block (PMT-04). Reuse "strictly from this block / don't fill in details that aren't here" formulation as a new `_PROMPT_*` module constant.

### Partial-PATCH field handling
**Source:** `app/routers/agents.py:280-318`
**Apply to:** all new fields in `agents.py` and `campaigns.py` PATCH handlers. `if payload.X is not None:`; nested/list → `.model_dump()` (full replace, Pitfall 7).

### Literal-enum validation
**Source:** `app/schemas/__init__.py:465` (`voice_baseline: Literal[...]`)
**Apply to:** `tone_preset`, `response_speed`, `primary_goal` — Pydantic `Literal[...]`, auto 422. CHECK constraint in DB mirrors the Literal values.

### Block-conditional prompt assembly
**Source:** `app/services/ai_engine.py:597-713`
**Apply to:** the `build_system_prompt` rewrite — keep the skeleton, change order + source per block, append `"\n\n".join(blocks)`.

---

## No Analog Found

None. Every required pattern exists in the codebase. This is a refactor/re-order phase — the risk is over-engineering, not missing precedent (RESEARCH §Don't-Hand-Roll: "Phase 11 is rearranging and deduping existing bricks").

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| — | — | — | All 11 files have exact in-repo analogs |

---

## Metadata

**Analog search scope:** `migrations/`, `app/models/`, `app/schemas/`, `app/routers/`, `app/services/`, `tests/`, `scripts/`, frontend `src/routes/_authenticated/`
**Files scanned:** ai_engine.py (build_system_prompt + 2 SELECTs + `_PROMPT_*` constants), listener.py (debounce + context build), models, schemas, agents/campaigns routers, conftest, migrations 016/018, campaigns.new.tsx
**Pattern extraction date:** 2026-06-24
