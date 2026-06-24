---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: 02
type: execute
wave: 2
depends_on: ["11-01"]
files_modified:
  - migrations/031_phase11_field_split.sql
  - app/models/__init__.py
  - app/schemas/__init__.py
  - app/routers/agents.py
  - app/routers/campaigns.py
autonomous: true
requirements: [FLD-01, FLD-02, FLD-03, FLD-04, FLD-05, FLD-06, MIG-01, MIG-02, MIG-03, D-01, D-02, D-04, D-10, D-11, D-12, D-13, D-14]
must_haves:
  truths:
    - "Agent can store a single tone source (tone_preset) and a response-speed setting"
    - "Campaign can store dialogue_flow stages, arguments_facts, and campaign_rules"
    - "Existing voice_baseline values map to tone_preset and existing success_criteria merges into lead_trigger_hint with no data loss"
    - "Dropped legacy tone columns and success_criteria no longer exist"
    - "Agent/Campaign create+update API accept and return the new fields with enum validation"
  artifacts:
    - path: "migrations/031_phase11_field_split.sql"
      provides: "Idempotent DDL + data-migration for the field split"
      contains: "tone_preset"
    - path: "app/models/__init__.py"
      provides: "AIContext + Campaign ORM columns for new fields"
    - path: "app/schemas/__init__.py"
      provides: "AgentCreate/Update + CampaignCreate/Update + DialogueStage with Literal enums"
    - path: "app/routers/agents.py"
      provides: "tone_preset/response_speed/response_delay_seconds CRUD plumbing"
    - path: "app/routers/campaigns.py"
      provides: "dialogue_flow/arguments_facts/campaign_rules CRUD plumbing"
  key_links:
    - from: "migrations/031_phase11_field_split.sql"
      to: "ai_contexts.voice_baseline"
      via: "UPDATE backfill before DROP"
      pattern: "tone_preset = CASE voice_baseline"
    - from: "app/schemas/__init__.py"
      to: "DialogueStage"
      via: "dialogue_flow: list[DialogueStage]"
      pattern: "dialogue_flow"
---

<objective>
Land the data layer for the Agent/Campaign field split: one idempotent migration (031) that adds the new typed columns, best-effort data-migrates the old tone source and success_criteria, and drops the three superseded tone columns + success_criteria. Mirror the new fields into ORM models, Pydantic schemas (with enum validation), and the agent/campaign router CRUD handlers.

Purpose: Establish a single source of truth per field at the schema level so the prompt rewrite (11-03) and frontend (11-04) build against a stable contract.
Output: migration 031, updated models/schemas/routers. Flips test_migration_031.py green.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md

COMMIT SAFETY (D-10): commit ONLY this plan's named files via `--files <paths>`. NEVER `git add -A` / `git add .` — Phase 10 work runs in parallel and must not be swept in.
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-CONTEXT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-RESEARCH.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-PATTERNS.md
@migrations/018_phase5_1.sql
@migrations/016_phase4.sql

<interfaces>
<!-- Grounded from live code 2026-06-24. -->

MIGRATION NUMBER: use `031_phase11_field_split.sql` (030 is taken by Phase 10).

Current ai_contexts ORM (app/models/__init__.py:184+):
  tone_of_voice = Column(Text)        # :193  -> DROP target
  voice_baseline = Column(String(20)) # :204  -> DROP target (after backfill)
  tone = Column(JSONB)                # :206  -> DROP target
  max_message_length = Column(Integer, server_default="280")  # :207 (keep)
  drop-NB comment at :216

Current Campaign ORM (app/models/__init__.py:497+):
  tools = Column(JSONB, server_default text "'[]'::jsonb")  # :535 (analog for dialogue_flow)
  audience_hints = Column(Text)       # :538 (KEEP - D-13 rename is label-only)
  primary_goal = Column(String(20))   # :539 (keep)
  success_criteria = Column(Text)     # :540 -> DROP target (after merge into lead_trigger_hint)
  lead_trigger_hint already exists on campaigns (merge target)

Schemas (app/schemas/__init__.py):
  ToneSpec:438 ; AgentCreate:451 (voice_baseline Literal:465, tone_of_voice:457)
  AgentUpdate:476 ; CampaignCreate:604 (audience_hints:630, primary_goal:631, success_criteria:632)
  CampaignUpdate:649 ; Response schemas ~:508/:714 also reference these fields.
  Literal-enum precedent: voice_baseline: Optional[Literal["Professional","Friendly","Playful"]]

Routers: agents.py (_agent_to_response serialiser + PATCH "if payload.X is not None" block);
campaigns.py mirrors the same idiom.

CHECK-constraint naming precedent: ai_contexts_voice_baseline_check (018_phase5_1.sql).
ALTER TYPE ADD VALUE cannot run in a transaction -> use VARCHAR(20)+CHECK, never SQLEnum (016 AUDIT Q6).
</interfaces>
</context>

<threat_model>
ASVS L1 surface for this plan:
- T1 Input validation on new enums (tone_preset, response_speed): mitigated by BOTH Pydantic Literal[...] (auto 422) AND DB CHECK constraints mirroring the same value lists (defence in depth). Task 1 + Task 2.
- T2 dialogue_flow JSONB shape/size abuse (oversized array, non-dict elements, huge instruction strings): mitigated by DialogueStage nested model with constr length caps on title/instruction and a soft cap on list length (<=7 elements) validated by Pydantic. Task 2.
- T3 response_delay_seconds out-of-range (negative / absurdly large -> delayed-worker DoS): mitigated by Pydantic ge=0, le=3600 so manual delay cannot exceed an hour. Task 2.
- T4 Data-migration safety (best-effort drops, D-02/D-13): mitigated by strict backfill-BEFORE-drop operator order (Pitfall 4), WHERE ... IS NULL idempotency guards, optional backup-comment of dropped values; success_criteria merged (not lost) into lead_trigger_hint. Task 1.
- T5 Migration fail-fast blocking api start: mitigated by full idempotency (IF NOT EXISTS / DROP ... IF EXISTS), tested on ephemeral DB before deploy. Task 1.
Free-text fields (arguments_facts, campaign_rules) are persisted verbatim here; prompt-injection handling of their CONTENT is addressed in 11-03 (guards), not in this plan.
</threat_model>

<tasks>

<task type="auto">
  <name>Task 1: Migration 031 — add columns, backfill, drop legacy (idempotent)</name>
  <read_first>
    - migrations/018_phase5_1.sql (enum VARCHAR+CHECK shape :31-37; COALESCE backfill-before-drop :44-50; transaction wrapper + revived-column note)
    - migrations/016_phase4.sql (JSONB DEFAULT '[]'::jsonb column :37; why SQLEnum is forbidden :9-11)
    - app/models/__init__.py:184-219 (AIContext cols), :497-540 (Campaign cols) — confirm exact existing column names being dropped/merged
    - 11-RESEARCH.md §"Architecture Patterns" Pattern 3 + §"Common Pitfalls" Pitfall 4
  </read_first>
  <action>
    Create migrations/031_phase11_field_split.sql wrapped in BEGIN;/COMMIT;. ADD COLUMN IF NOT EXISTS to ai_contexts: tone_preset VARCHAR(20), response_speed VARCHAR(20), response_delay_seconds INTEGER. ADD COLUMN IF NOT EXISTS to campaigns: dialogue_flow JSONB NOT NULL DEFAULT '[]'::jsonb, arguments_facts TEXT, campaign_rules TEXT. For each enum DROP CONSTRAINT IF EXISTS then ADD CONSTRAINT: ai_contexts_tone_preset_check CHECK (tone_preset IS NULL OR tone_preset IN ('Friendly','Professional','Direct','Casual')); ai_contexts_response_speed_check CHECK (response_speed IS NULL OR response_speed IN ('instant','human','slow','manual')). DATA-MIGRATE BEFORE DROP (strict order, Pitfall 4): UPDATE ai_contexts SET tone_preset = CASE voice_baseline WHEN 'Professional' THEN 'Professional' WHEN 'Friendly' THEN 'Friendly' WHEN 'Playful' THEN 'Casual' ELSE tone_preset END WHERE tone_preset IS NULL AND voice_baseline IS NOT NULL. Merge success_criteria into lead_trigger_hint without losing either: when lead_trigger_hint is NULL/empty set it to success_criteria; when both present concat (existing hint, newline, success_criteria); guard with WHERE success_criteria IS NOT NULL AND success_criteria <> '' AND (lead_trigger_hint IS NULL OR position(success_criteria in lead_trigger_hint)=0) so a re-run does not double-append. THEN drop: ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_voice_baseline_check; DROP COLUMN IF EXISTS voice_baseline; DROP COLUMN IF EXISTS tone; DROP COLUMN IF EXISTS tone_of_voice; ALTER TABLE campaigns DROP COLUMN IF EXISTS success_criteria. Precede the drops with a SQL comment noting slider/free-text tone values are intentionally discarded (D-02). For existing rows leave response_speed NULL (treated as 'human' default downstream). Every ADD uses IF NOT EXISTS, every DROP uses IF EXISTS, every UPDATE is idempotency-guarded so re-apply on drift is a no-op.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_migration_031.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - migrations/031_phase11_field_split.sql contains tone_preset VARCHAR(20), response_speed VARCHAR(20), response_delay_seconds INTEGER, dialogue_flow JSONB NOT NULL DEFAULT '[]'::jsonb, arguments_facts TEXT, campaign_rules TEXT
    - CHECK lists verbatim ('Friendly','Professional','Direct','Casual') and ('instant','human','slow','manual')
    - voice_baseline->tone_preset UPDATE appears BEFORE DROP COLUMN IF EXISTS voice_baseline (source-order assertion)
    - success_criteria->lead_trigger_hint UPDATE appears BEFORE DROP COLUMN IF EXISTS success_criteria
    - tests/test_migration_031.py passes (all four tests green)
    - File idempotent: ADD IF NOT EXISTS, DROP IF EXISTS, guarded backfills
  </acceptance_criteria>
  <done>New columns exist, legacy tone + success_criteria gone, no data lost; migration re-runnable.</done>
</task>

<task type="auto">
  <name>Task 2: ORM models + Pydantic schemas for new fields</name>
  <read_first>
    - app/models/__init__.py:184-219 (AIContext) and :497-540 (Campaign) — column declaration style, drop-comment convention :216
    - app/schemas/__init__.py:438-466 (ToneSpec, AgentCreate, voice_baseline Literal), :476-516 (AgentUpdate + Response), :604-682 (CampaignCreate/Update), :714-716 (CampaignResponse fields)
    - 11-PATTERNS.md "models" + "schemas" sections (Literal-enum, nested-model JSONB, partial-PATCH conventions)
  </read_first>
  <action>
    ORM (app/models/__init__.py): on AIContext add tone_preset = Column(String(20), nullable=True), response_speed = Column(String(20), nullable=True), response_delay_seconds = Column(Integer, nullable=True); REMOVE the voice_baseline, tone, tone_of_voice column declarations and replace the existing drop-NB comment with a tombstone note in the same style (voice_baseline/tone/tone_of_voice dropped Phase 11 D-01; tone_preset single source). On Campaign add dialogue_flow = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")), arguments_facts = Column(Text, nullable=True), campaign_rules = Column(Text, nullable=True); REMOVE the success_criteria column declaration with a tombstone note (merged into lead_trigger_hint Phase 11 D-13). KEEP audience_hints (D-13 rename is label-only).
    Schemas (app/schemas/__init__.py): add DialogueStage(BaseModel) with title: Optional[constr(max_length=120)] = None and instruction: constr(min_length=1, max_length=2000). Add to AgentCreate AND AgentUpdate: tone_preset: Optional[Literal["Friendly","Professional","Direct","Casual"]] = None, response_speed: Optional[Literal["instant","human","slow","manual"]] = None, response_delay_seconds: Optional[conint(ge=0, le=3600)] = None; REMOVE tone_of_voice, voice_baseline and any ToneSpec/tone slider field from AgentCreate/AgentUpdate and the agent Response schema. Add to CampaignCreate AND CampaignUpdate: dialogue_flow: Optional[conlist(DialogueStage, max_length=7)] = None, arguments_facts: Optional[str] = None, campaign_rules: Optional[str] = None; REMOVE success_criteria from CampaignCreate/CampaignUpdate and the campaign Response schema; add the three new campaign fields plus tone_preset/response_speed/response_delay_seconds to the relevant Response schemas so GET returns them. Preserve model_config = ConfigDict(from_attributes=True).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "import app.models, app.schemas; from app.schemas import DialogueStage, AgentCreate, CampaignCreate; print('import OK')"</automated>
  </verify>
  <acceptance_criteria>
    - app/models AIContext has tone_preset, response_speed, response_delay_seconds and NO voice_baseline/tone/tone_of_voice
    - app/models Campaign has dialogue_flow, arguments_facts, campaign_rules and NO success_criteria; audience_hints retained
    - app/schemas defines DialogueStage; AgentCreate/Update carry tone_preset (Literal 4 values), response_speed (Literal 4 values), response_delay_seconds (conint ge=0,le=3600)
    - CampaignCreate/Update carry dialogue_flow (conlist max_length=7), arguments_facts, campaign_rules; success_criteria removed from request schemas
    - import of app.models + app.schemas succeeds (no NameError / leftover references)
  </acceptance_criteria>
  <done>ORM and schema contracts reflect the field split with enum + size validation.</done>
</task>

<task type="auto">
  <name>Task 3: Router CRUD plumbing for agents.py + campaigns.py</name>
  <read_first>
    - app/routers/agents.py (_agent_to_response serialiser + PATCH "if payload.X is not None" block + create handler field assignment)
    - app/routers/campaigns.py (campaign create + PATCH field block + _campaign_to_response serialiser)
    - 11-PATTERNS.md §"app/routers/agents.py & campaigns.py" (response pass-through + partial-PATCH idiom + cache invalidation on PATCH)
  </read_first>
  <action>
    agents.py: in the create handler set agent.tone_preset / agent.response_speed / agent.response_delay_seconds from the payload; remove any assignment of voice_baseline / tone / tone_of_voice. In the PATCH handler add `if payload.tone_preset is not None: agent.tone_preset = payload.tone_preset` and the same idiom for response_speed and response_delay_seconds; remove the now-dead voice_baseline/tone/tone_of_voice PATCH branches. In _agent_to_response add tone_preset=agent.tone_preset, response_speed=agent.response_speed, response_delay_seconds=agent.response_delay_seconds and drop the removed-field lines. Ensure the existing AIEngine.invalidate_context call after a successful agent PATCH still fires so tone_preset/response_speed changes apply before the 60s cache TTL (new fields ride the same call — no second call needed).
    campaigns.py: in the create handler set campaign.dialogue_flow = [s.model_dump() for s in payload.dialogue_flow] when provided (full-replace, mirror tools/qa_pairs handling), campaign.arguments_facts, campaign.campaign_rules. In the PATCH handler add `if payload.dialogue_flow is not None: campaign.dialogue_flow = [s.model_dump() for s in payload.dialogue_flow]` plus `if payload.arguments_facts is not None:` and `if payload.campaign_rules is not None:` branches; remove the success_criteria branch. In _campaign_to_response add dialogue_flow=campaign.dialogue_flow, arguments_facts=campaign.arguments_facts, campaign_rules=campaign.campaign_rules and drop success_criteria. If the campaign auto-fill handler reads/writes success_criteria, repoint it to lead_trigger_hint (D-13/D-15 — the auto-fill structural target moves to the lead-signal hint).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_agents.py tests/test_campaigns.py -q</automated>
  </verify>
  <acceptance_criteria>
    - agents.py create + PATCH + _agent_to_response reference tone_preset/response_speed/response_delay_seconds and contain NO voice_baseline/tone/tone_of_voice references (grep returns 0)
    - campaigns.py create + PATCH + _campaign_to_response reference dialogue_flow/arguments_facts/campaign_rules and contain NO success_criteria references (grep returns 0)
    - dialogue_flow PATCH uses [s.model_dump() for s in payload.dialogue_flow] (full replace, not merge)
    - existing agent/campaign router test suites pass via test-overlay (adapt assertions for removed/added fields if the existing tests reference dropped fields)
  </acceptance_criteria>
  <done>Agent/Campaign API round-trips the new fields with enum validation; legacy field references gone.</done>
</task>

</tasks>

<verification>
- Migration 031 applies idempotently on ephemeral DB; test_migration_031.py green.
- app.models + app.schemas import cleanly with no dangling references to dropped fields.
- Agent/Campaign router suites green via test-overlay.
- Full suite: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` — no new red (PMT/RT tests still xfail until 11-03).
</verification>

<success_criteria>
- tone_preset is the only tone column on ai_contexts; voice_baseline/tone/tone_of_voice dropped (FLD-01, MIG-01/02).
- response_speed + response_delay_seconds re-added (FLD-02/03).
- dialogue_flow / arguments_facts / campaign_rules exist on campaigns (FLD-04/05/06).
- success_criteria merged into lead_trigger_hint with no loss, then dropped (MIG-03).
- API create/update/get expose new fields with 422 on invalid enum.
</success_criteria>

<output>
After completion, create `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-02-SUMMARY.md`
</output>
