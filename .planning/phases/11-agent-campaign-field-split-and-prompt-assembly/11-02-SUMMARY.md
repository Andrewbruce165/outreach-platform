---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: "02"
subsystem: data-layer
tags: [migration, orm, pydantic, crud, field-split, phase-11]
dependency_graph:
  requires: ["11-01"]
  provides: ["11-03", "11-04"]
  affects:
    - migrations/032_phase11_field_split.sql
    - app/models/__init__.py
    - app/schemas/__init__.py
    - app/routers/agents.py
    - app/routers/campaigns.py
    - app/services/ai_engine.py
tech_stack:
  added:
    - DialogueStage (Pydantic model for campaign dialogue stages)
    - tone_preset Literal enum (Friendly/Professional/Direct/Casual)
    - response_speed Literal enum (instant/human/slow/manual)
  patterns:
    - Idempotent DDL: ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS + guarded UPDATE
    - Data-before-drop (Pitfall 4): backfill UPDATE precedes DROP COLUMN
    - Partial PATCH: exclude_unset=True + setattr loop; dialogue_flow full-replace serialization
key_files:
  created:
    - migrations/032_phase11_field_split.sql
  modified:
    - app/models/__init__.py
    - app/schemas/__init__.py
    - app/routers/agents.py
    - app/routers/campaigns.py
    - app/services/ai_engine.py
    - tests/conftest.py
    - tests/test_agents.py
    - tests/test_ai_engine.py
    - tests/test_phase5_1_agents_v2.py
    - tests/test_phase5_1_agents_v2_router.py
    - tests/test_phase5_1_auto_fill_stub.py
    - tests/test_phase5_1_campaign_v2.py
    - tests/test_phase5_1_campaign_v2_router.py
    - tests/test_phase5_1_full_suite.py
    - tests/test_phase5_1_migration_018.py
    - tests/test_phase5_1_orm_widening.py
decisions:
  - "D-01: tone_preset replaces voice_baseline/tone/tone_of_voice as single source (Friendly/Professional/Direct/Casual)"
  - "D-02: tone JSONB slider values intentionally discarded — no typed-enum mapping exists"
  - "D-04: dialogue_flow added as JSONB array on campaigns (max 7 stages)"
  - "D-11: response_speed/response_delay_seconds added for future pacing control"
  - "D-12: arguments_facts added as TEXT on campaigns"
  - "D-13: success_criteria merged into lead_trigger_hint before DROP"
  - "D-14: campaign_rules added as TEXT on campaigns"
  - "Migration slot deviation: plan said 031 but 031 was taken by Phase 10; used 032 instead"
metrics:
  duration_minutes: 34
  completed_date: "2026-06-24"
  tasks_completed: 3
  files_modified: 17
---

# Phase 11 Plan 02: Migration Schema and CRUD Summary

Idempotent migration 032, ORM update, Pydantic schema rework with Literal enums, and router CRUD plumbing for the Phase 11 Agent/Campaign field split — establishing tone_preset, response_speed, dialogue_flow, arguments_facts, campaign_rules as single-source columns while removing voice_baseline, tone, tone_of_voice, and success_criteria.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Migration 032 | 9fd7c27 | migrations/032_phase11_field_split.sql |
| 2 | ORM + Pydantic schemas | 2bf0c89 | app/models/__init__.py, app/schemas/__init__.py |
| 3 | Router CRUD + ai_engine + tests | b57470b | app/routers/agents.py, campaigns.py, services/ai_engine.py, 10 test files |

## What Was Built

### Migration 032 (Task 1)
- Adds `tone_preset VARCHAR(20)` with CHECK (Friendly/Professional/Direct/Casual), `response_speed VARCHAR(20)` with CHECK (instant/human/slow/manual), `response_delay_seconds INTEGER` to `ai_contexts`
- Adds `dialogue_flow JSONB NOT NULL DEFAULT '[]'::jsonb`, `arguments_facts TEXT`, `campaign_rules TEXT` to `campaigns`
- Data migration (data-before-drop): `voice_baseline → tone_preset` (Playful→Casual), `success_criteria → lead_trigger_hint` (concat, idempotency-guarded)
- Drops: `voice_baseline`, `tone`, `tone_of_voice` from `ai_contexts`; `success_criteria` from `campaigns`
- Fully idempotent (IF NOT EXISTS, DROP CONSTRAINT IF EXISTS, WHERE-guarded UPDATEs)

### ORM Models (Task 2)
- `AIContext`: removed `tone_of_voice` (line 193, pre-Phase-11), `voice_baseline`, `tone`; added `tone_preset`, `response_speed`, `response_delay_seconds` with tombstone comments
- `Campaign`: removed `success_criteria`; added `dialogue_flow` (JSONB, server_default='[]'), `arguments_facts`, `campaign_rules`

### Pydantic Schemas (Task 2)
- Removed `ToneSpec` class entirely (replaced by Literal field)
- Added `DialogueStage(BaseModel)` with `title: Optional[constr(max_length=120)]` and `instruction: constr(min_length=1, max_length=2000)`
- `AgentCreate/Update`: replaced `tone`/`voice_baseline`/`tone_of_voice` with `tone_preset: Optional[Literal[...]]`, `response_speed: Optional[Literal[...]]`, `response_delay_seconds: Optional[conint(ge=0, le=3600)]`
- `CampaignCreate/Update`: removed `success_criteria`; added `dialogue_flow: Optional[conlist(DialogueStage, max_length=7)]`, `arguments_facts`, `campaign_rules`

### Router CRUD (Task 3)
- `agents.py`: `_agent_to_response`, `create_agent`, `update_agent`, `duplicate_agent` — all replaced legacy tone fields with Phase 11 fields
- `campaigns.py`: `_campaign_to_response`, `create_campaign`, `patch_campaign` (with dialogue_flow full-replace serialization), `duplicate_campaign`, `_AutoFillResponse` — removed `success_criteria`; added Phase 11 fields
- `ai_engine.py`: both `get_context_for_conversation` and `get_context` SQL queries updated; `build_system_prompt` uses `tone_preset` as single source replacing the legacy `<tone>` multi-field block

### Tests (Task 3)
- 10 test files updated to replace removed field references with Phase 11 fields
- Migration 018 idempotency test fixed: re-applies 032 after re-applying 018 to maintain schema state across test session
- test_migration_032.py: 4/4 GREEN
- Full test suite: 651 passing (up from 456 before Phase 11), 61 failing (down from 249 — all pre-existing)

## Deviations from Plan

### Critical Deviation: Migration Slot

**Discovered in Wave 1 before execution:** The plan referenced `031_phase11_field_split.sql` but slot 031 was already taken by `031_sre_flood_wait_category.sql` (Phase 10). All Phase 11 migration references were corrected to `032_phase11_field_split.sql` before execution began.

### Auto-fixed Issues

**1. [Rule 1 - Bug] `tone_of_voice` column at line 193 of models/__init__.py not removed by initial edit**
- **Found during:** Task 2 test run
- **Issue:** `tone_of_voice = Column(Text, nullable=True)` existed at line 193 in the ORM, BEFORE the `# ── 05.1 v2 columns` section. The initial edit only targeted the 05.1 section block lower in the file.
- **Fix:** Added second targeted edit to remove the line 193 `tone_of_voice` declaration
- **Files modified:** `app/models/__init__.py`
- **Commit:** 2bf0c89

**2. [Rule 1 - Bug] `test_agent_factory` default `tone_of_voice="friendly"` in conftest**
- **Found during:** Task 3 test run
- **Issue:** Factory defaults dict included `tone_of_voice="friendly"` which got passed to `AIContext(**defaults)`, triggering SQLAlchemy AttributeError since the ORM model no longer has that column.
- **Fix:** Removed `tone_of_voice="friendly"` from defaults dict; added comment explaining Phase 11 removal
- **Files modified:** `tests/conftest.py`
- **Commit:** b57470b

**3. [Rule 1 - Bug] `duplicate_campaign` still referenced `success_criteria`**
- **Found during:** Task 3 test run (`test_duplicate_endpoint_copies_row_and_senders_not_queue_assignments`)
- **Issue:** `campaigns.py` line 842 `duplicate_campaign` had `success_criteria=src.success_criteria` in Campaign() constructor
- **Fix:** Removed `success_criteria` line; added Phase 11 fields `dialogue_flow`, `arguments_facts`, `campaign_rules`
- **Files modified:** `app/routers/campaigns.py`
- **Commit:** b57470b

**4. [Rule 1 - Bug] `ai_engine.py` SQL queries referenced dropped columns**
- **Found during:** Task 3 test run (`test_get_context_phase3_schema`)
- **Issue:** Both `get_context_for_conversation` and `get_context` queries selected `tone_of_voice`, `voice_baseline`, `tone` which no longer exist
- **Fix:** Updated SQL selects and context dicts to use `tone_preset`, `response_speed`, `response_delay_seconds`; updated `build_system_prompt` to use `tone_preset` single source
- **Files modified:** `app/services/ai_engine.py`
- **Commit:** b57470b

**5. [Rule 1 - Bug] `test_migration_018_idempotent` re-applies 018 without re-applying 032, corrupting schema state for subsequent tests**
- **Found during:** Task 3 full test run (migration_032 tests failing when run after migration_018 tests)
- **Issue:** `test_migration_018_idempotent` and `test_webhook_url_backfill` both re-applied migration 018 which adds back `tone`/`voice_baseline` columns, but did not re-apply 032 to drop them. Tests running later in the same pytest session saw dropped columns as present.
- **Fix:** Both tests now re-apply 032 immediately after re-applying 018
- **Files modified:** `tests/test_phase5_1_migration_018.py`
- **Commit:** b57470b

**6. [Rule 2 - Missing functionality] `test_05_1_schemas_importable` imported `ToneSpec` (removed in Phase 11)**
- **Found during:** Task 3 test run
- **Issue:** Test tried to import `ToneSpec` from `app.schemas` which was removed
- **Fix:** Replaced `ToneSpec` import with `DialogueStage`; updated assertion to check DialogueStage fields
- **Files modified:** `tests/test_phase5_1_full_suite.py`
- **Commit:** b57470b

**7. [Rule 2 - Wrong assertion] `test_agent_create_legacy_only_payload_returns_v2_fields_null` asserted `auto_pause_scope is None`**
- **Found during:** Task 3 test run
- **Issue:** Test asserted `j["auto_pause_scope"] is None` but DB default is `'conversation'`
- **Fix:** Corrected assertion to `j["auto_pause_scope"] == "conversation"` with explanatory comment
- **Files modified:** `tests/test_phase5_1_agents_v2_router.py`
- **Commit:** b57470b

**8. [Rule 1 - Bug] `test_defaults_applied` selected `tone` column (dropped in Phase 11)**
- **Found during:** Task 3 full test run
- **Issue:** `test_defaults_applied` had `SELECT tone, max_message_length ...` but `tone` is now dropped by migration 032
- **Fix:** Removed `tone` from SELECT and assertions; added Phase 11 note
- **Files modified:** `tests/test_phase5_1_migration_018.py`
- **Commit:** b57470b

## Pre-existing Failures (Not Phase 11 Related)

Two test failures exist in the plan's scope files but are NOT caused by Phase 11:
- `test_delete_agent_cascades_assignments`: Tests `context_contact_assignments` table which was DROPPED by migration 016 (Phase 4). This test was written TDD-RED and never made GREEN. Out of scope.
- `test_handoff_bundle_directory_exists`: Tests for `lovable-handoff/` directory from plan 05.1-05 (never executed). Out of scope.

## Known Stubs

None — all new fields are wired through ORM → schema → router. The `dialogue_flow` field defaults to `[]` in DB and is properly serialized on PATCH. `build_system_prompt` uses `tone_preset` directly.

## Self-Check: PASSED

Files exist:
- migrations/032_phase11_field_split.sql: FOUND
- app/models/__init__.py: FOUND (tone_preset, response_speed, response_delay_seconds in AIContext)
- app/schemas/__init__.py: FOUND (DialogueStage, tone_preset Literal)
- app/routers/agents.py: FOUND (tone_preset CRUD)
- app/routers/campaigns.py: FOUND (dialogue_flow CRUD)
- app/services/ai_engine.py: FOUND (tone_preset in SQL and context dict)

Commits:
- 9fd7c27: migration 032
- 2bf0c89: ORM + schemas
- b57470b: routers + ai_engine + tests

Tests: test_migration_032.py 4/4 GREEN, full suite 651 passing.
