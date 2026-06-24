---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/conftest.py
  - tests/test_migration_031.py
  - tests/test_listener_response_speed.py
  - tests/test_ai_engine.py
  - .planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-VALIDATION.md
autonomous: true
requirements: [FLD-01, FLD-02, FLD-03, FLD-04, FLD-05, FLD-06, MIG-01, MIG-02, MIG-03, PMT-01, PMT-02, PMT-03, PMT-04, PMT-05, PMT-06, PMT-07, RT-01, D-10]
must_haves:
  truths:
    - "conftest applies migrations through 031 so new columns exist in the test DB"
    - "RED test files exist for migration 031, listener response_speed, and prompt assembly"
    - "Tests are skip/xfail-guarded so the suite stays green until the feature lands"
  artifacts:
    - path: "tests/test_migration_031.py"
      provides: "MIG-01/02/03 + FLD-01..06 integration assertions (RED)"
    - path: "tests/test_listener_response_speed.py"
      provides: "RT-01 delay-by-enum assertions (RED)"
    - path: "tests/test_ai_engine.py"
      provides: "PMT-01..07 golden-prompt assertions (RED, extended)"
  key_links:
    - from: "tests/conftest.py"
      to: "migrations/028..031"
      via: "hardcoded migration list"
      pattern: "031_phase11"
---

<objective>
Lay the Wave-0 test scaffold for Phase 11 so every downstream requirement (migration, prompt assembly, response-speed runtime) has an automated verify BEFORE implementation lands. This is a refactor phase whose behavioral core is "no duplicate instructions in the system prompt" — that must be asserted deterministically.

Purpose: Nyquist compliance — no implementation task ships without a test it makes pass. Also fixes the conftest migration-list gap (RESEARCH Pitfall 3) that would otherwise make EVERY downstream integration test fail with UndefinedColumn.
Output: conftest fix + 3 RED test files (skip-guarded so the suite stays green).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-RESEARCH.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-VALIDATION.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-PATTERNS.md

<interfaces>
<!-- Grounded from live code 2026-06-24 (not the line numbers in RESEARCH/PATTERNS, which drifted). -->

Migration numbering CORRECTION: migration slot `030` is ALREADY TAKEN by Phase 10
(migrations/030_sender_restriction_events.sql, committed). Phase 11 MUST use `031_*.sql`.
All RESEARCH/PATTERNS references to "030" for THIS phase mean "031".

conftest migration list (tests/conftest.py) currently ends at:
    "027_folders_workspace_name_unique.sql",
It is a HARDCODED tuple — it does NOT glob. Missing: 028, 029, 030, 031.

build_system_prompt is at app/services/ai_engine.py:559 ; current <tone> assembly
reads voice_baseline (:618), tone JSONB sliders, tone_of_voice (:632); static
_PROMPT_DIALOGUE_GOAL at :402 rendered as <dialogue_goal> at :650.

listener.py: DEBOUNCE_MIN=20.0 (:134), DEBOUNCE_MAX=180.0 (:135), MAX_BUFFER_TIME=300.0 (:136);
schedule_ai_response at :209 ; delay calc at :230:
    delay = min(random.uniform(self.DEBOUNCE_MIN, self.DEBOUNCE_MAX), self.MAX_BUFFER_TIME - buffer_age)
context dict built at :848 (keys ai_context_id, contact_name, ...).

Target prompt block order (BRIEF §7 → tags): <role> → <company> → <product> → <tone>
→ <task_audience> → <dialogue_flow> → <arguments_facts> → <rules> → <signals>/<tools> → <message_style>.
</interfaces>
</context>

<threat_model>
ASVS L1 surface for this plan: test-infra only, no production code path touched.
- T1 (test DB leaks to prod): mitigated by mandatory test-overlay command; tests are written to be run ONLY via `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Task actions never invoke pytest without the overlay; conftest guard (tests/conftest.py:46-77) blocks non-test DSN.
- T2 (false-green): RED tests must FAIL meaningfully (not error on import) before downstream waves — verified by running them and observing skip/xfail, not pass.
No new input surfaces, no auth, no data migration in this plan.
</threat_model>

<tasks>

<task type="auto">
  <name>Task 1: Extend conftest migration list to 031 + widen test_agent_factory</name>
  <read_first>
    - tests/conftest.py:127-164 (the hardcoded migration tuple AND the post-migration explicit-default block — note lines 157-159 run a multi-line `ALTER TABLE ai_contexts ALTER COLUMN tone SET DEFAULT '{"formal":0,...}'::jsonb`)
    - migrations/028_sender_restriction.sql, migrations/029_campaign_pause_reason.sql, migrations/030_sender_restriction_events.sql (the three uncovered migrations)
    - 11-PATTERNS.md "Tests" section (Pitfall 3 — hardcoded list does NOT glob)
    - 11-VALIDATION.md (references the OLD filename test_migration_030.py in ~6 places — must be retargeted to test_migration_031.py)
  </read_first>
  <action>
    COMMIT SAFETY (D-10): commit ONLY the files this plan names, BY NAME (--files tests/conftest.py tests/test_migration_031.py tests/test_listener_response_speed.py tests/test_ai_engine.py .planning/.../11-VALIDATION.md). NEVER `git add -A` / `git add .` — Phase 10 work runs in parallel.

    (a) Migration tuple: In tests/conftest.py append four entries to the migration tuple after "027_folders_workspace_name_unique.sql": "028_sender_restriction.sql", "029_campaign_pause_reason.sql", "030_sender_restriction_events.sql", "031_phase11_field_split.sql" (the 031 filename must match the file Plan 11-02 creates). Because 031 does not exist yet, guard its read with a conditional: only append it to the executed list if (PROJECT_ROOT / "migrations" / "031_phase11_field_split.sql").exists(), so this plan's conftest change is green now and auto-activates when 11-02 lands.

    (b) REMOVE the stale `tone` default: In the post-migration explicit-default block (tests/conftest.py ~157-164) DELETE the statement `ALTER TABLE ai_contexts ALTER COLUMN tone SET DEFAULT '{"formal": 0, "warm": 0, "brief": 0}'::jsonb;` (it spans conftest lines ~158-159: `ALTER TABLE ai_contexts ALTER COLUMN tone` / `SET DEFAULT ...`). Migration 031 (Plan 11-02) DROPS the `tone` column, so once 031 applies this ALTER would raise "column tone does not exist" and crash the ENTIRE integration suite from Wave 2 onward. Leave the other ALTERs in that block (max_message_length, mirror_language, allow_emoji, auto_pause_scope) intact — those columns survive 031.

    (c) Fixture widening: Locate the test_agent_factory / test_context fixture(s) that currently set voice_baseline/tone and add optional kwargs tone_preset, response_speed, response_delay_seconds defaulting to None so downstream tests can build new-era agents without rewriting the fixture. Do NOT remove voice_baseline from the factory yet (column still exists until 11-02's migration).

    (d) Retarget 11-VALIDATION.md filenames: 11-VALIDATION.md still references the OLD filename `tests/test_migration_030.py` in ~6 places (quick-run command ~line 23, sampling ~line 33, the per-task verification map ~lines 44-47, the Wave 0 checklist ~line 75, and the sign-off ~line 96). Replace EVERY occurrence of `test_migration_030` with `test_migration_031` (sed -i 's/test_migration_030/test_migration_031/g' on the file is fine). After this, `grep -n test_migration_030 11-VALIDATION.md` MUST return nothing.

    (e) Flip wave_0 flag: In 11-VALIDATION.md frontmatter set `wave_0_complete: true` (this plan IS Wave 0 — its completion is what makes the scaffold ready).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - tests/conftest.py migration tuple contains "028_sender_restriction.sql", "029_campaign_pause_reason.sql", "030_sender_restriction_events.sql"
    - 031 is appended only behind an `.exists()` guard (string "031_phase11_field_split.sql" present in conftest.py)
    - conftest post-migration `ALTER COLUMN tone` default statement removed: `grep -n "ALTER COLUMN tone" tests/conftest.py` returns nothing (the statement spans two lines; the `ALTER COLUMN tone` token on line ~158 is the anchor to delete)
    - test_agent_factory (or equivalent fixture) accepts tone_preset / response_speed / response_delay_seconds kwargs
    - 11-VALIDATION.md retargeted: `grep -n test_migration_030 11-VALIDATION.md` returns nothing (all references now test_migration_031)
    - 11-VALIDATION.md frontmatter `wave_0_complete: true`
    - `--collect-only` exits 0 (no collection errors from the conftest change)
    - commit used --files with named paths only; no `git add -A`/`git add .` (D-10)
  </acceptance_criteria>
  <done>conftest applies 028-030 today and auto-applies 031 once it exists; fixture supports new tone/speed fields.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: RED test_migration_031.py (MIG-01/02/03 + FLD-01..06)</name>
  <read_first>
    - tests/conftest.py (asyncpg_conn fixture used by integration tests; how migrations are applied)
    - migrations/018_phase5_1.sql (enum-VARCHAR+CHECK + COALESCE-backfill-before-drop pattern the migration under test will follow)
    - 11-RESEARCH.md §"Phase Requirements → Test Map" and §"Validation Architecture"
  </read_first>
  <behavior>
    - test_new_columns: after migration 031, ai_contexts has columns tone_preset (varchar, CHECK in Friendly/Professional/Direct/Casual), response_speed (varchar, CHECK in instant/human/slow/manual), response_delay_seconds (integer); campaigns has dialogue_flow (jsonb default '[]'), arguments_facts (text), campaign_rules (text) — queried via information_schema.columns / pg_constraint.
    - test_tone_preset_backfill (MIG-01): seed an ai_contexts row with voice_baseline='Professional' BEFORE 031, run 031, assert tone_preset='Professional' and column voice_baseline no longer exists.
    - test_legacy_tone_dropped (MIG-02): assert columns tone and tone_of_voice no longer exist on ai_contexts after 031.
    - test_lead_hint_merge (MIG-03): seed campaign with success_criteria='X' and lead_trigger_hint=NULL → after 031 lead_trigger_hint contains 'X'; seed second campaign with both set → lead_trigger_hint retains existing hint AND includes success_criteria (concat/COALESCE, no data loss); assert success_criteria column dropped.
  </behavior>
  <action>
    Create tests/test_migration_031.py as pytest-asyncio integration tests against the ephemeral test DB. Because migration 031 does not exist yet, mark the module (or each test) with a skip-if-missing guard: `pytestmark = pytest.mark.skipif(not (PROJECT_ROOT/"migrations"/"031_phase11_field_split.sql").exists(), reason="031 not yet implemented")` so the suite stays green now and the tests activate in Wave 2. Assertions must be concrete (exact column names, exact CHECK value lists, exact backfilled values) — no "looks correct". Use information_schema for existence checks and direct SELECT for backfill values. Do NOT implement the migration here.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_migration_031.py -q 2>&1 | tail -8</automated>
  </verify>
  <acceptance_criteria>
    - tests/test_migration_031.py defines test_new_columns, test_tone_preset_backfill, test_legacy_tone_dropped, test_lead_hint_merge
    - Module is skip-guarded on absence of 031_phase11_field_split.sql → pytest run reports SKIPPED (not error, not pass) while 031 is absent
    - CHECK-value lists asserted verbatim: ('Friendly','Professional','Direct','Casual') and ('instant','human','slow','manual')
  </acceptance_criteria>
  <done>Migration behavior is pinned by tests that go green only when a correct 031 lands.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: RED test_listener_response_speed.py (RT-01) + extend test_ai_engine.py (PMT-01..07)</name>
  <read_first>
    - app/services/listener.py:134-136 (DEBOUNCE_MIN/MAX/MAX_BUFFER_TIME), :209-233 (schedule_ai_response delay calc), :848 (context dict build)
    - app/services/ai_engine.py:559-660 (build_system_prompt + current <tone>/<dialogue_goal> blocks), :397-408 (_PROMPT_PRODUCT_GUARD, _PROMPT_DIALOGUE_GOAL)
    - tests/test_ai_engine.py (existing golden-prompt / build_system_prompt tests to mirror style)
    - 11-VALIDATION.md §"Behavioral Core — как доказать «нет дубля»"
  </read_first>
  <behavior>
    - RT-01 (test_listener_response_speed.py): given a context dict with response_speed='manual' and response_delay_seconds=42 → computed delay == 42 (capped by MAX_BUFFER_TIME guard); response_speed='instant' → delay <= ~2s; response_speed='human' (or absent, default) → delay within current DEBOUNCE_MIN..DEBOUNCE_MAX range; the MAX_BUFFER_TIME - buffer_age cap is still applied for every mode.
    - PMT-01 test_prompt_block_order: full agent+campaign context → assert prompt.index("<role>") < index("<company>") < index("<product>") < index("<tone>") < index("<task_audience>") < index("<dialogue_flow>") < index("<arguments_facts>") < index("<rules>") < index("<message_style>").
    - PMT-02 test_tone_single_source: tone_preset='Friendly' plus residual voice_baseline in context → prompt contains the preset line and does NOT contain "Baseline persona" or "Tone calibration".
    - PMT-03 test_dialogue_flow_render: dialogue_flow=[{title,instruction}×2] → prompt has numbered "1." and "2." stages and does NOT contain the old static _PROMPT_DIALOGUE_GOAL text ("move through three steps").
    - PMT-04 test_arguments_facts_guard: arguments_facts set → block present AND an anti-hallucination guard line present.
    - PMT-05 test_rules_dedup_no_duplicate (behavioral core): agent rules "Не давить." + campaign_rules "Не давить.\nОтвечать кратко." → prompt.count("Не давить") == 1 AND "Отвечать кратко" present.
    - PMT-06 test_task_source_campaign: primary_goal/audience_hints render in <task_audience>; who_is_agent identity text does not carry a task/goal sentence.
    - PMT-07 test_brief_excluded: a raw brief string is never an input to build_system_prompt (assert function signature/contract takes structured context only).
  </behavior>
  <action>
    Create tests/test_listener_response_speed.py and add the seven PMT tests to tests/test_ai_engine.py. Skip-guard the new assertions that depend on not-yet-implemented behavior so the current suite stays green: use pytest.mark.xfail(reason="Phase 11 prompt rewrite pending", strict=False) on the PMT order/source tests, and skipif on the response_speed test if schedule_ai_response does not yet branch on response_speed (detect via inspecting that the function reads context.get("response_speed")). Build contexts with the existing test_ai_engine fixtures/builders. Assertions concrete (index ordering, exact substring counts, exact delay equality). Do NOT modify ai_engine.py or listener.py here.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine.py tests/test_listener_response_speed.py -q 2>&1 | tail -10</automated>
  </verify>
  <acceptance_criteria>
    - tests/test_listener_response_speed.py exists with manual/instant/human delay assertions including the MAX_BUFFER_TIME cap assertion
    - tests/test_ai_engine.py gains test_prompt_block_order, test_tone_single_source, test_dialogue_flow_render, test_arguments_facts_guard, test_rules_dedup_no_duplicate, test_task_source_campaign, test_brief_excluded
    - New behavior-dependent tests are xfail/skip-guarded → full pytest run on current code reports XFAIL/SKIP (not unexpected pass, not error)
    - test_rules_dedup_no_duplicate asserts `prompt.count("Не давить") == 1` literally
  </acceptance_criteria>
  <done>RT-01 + PMT-01..07 are pinned as RED/xfail tests ready to flip green in Wave 3.</done>
</task>

</tasks>

<verification>
- conftest migration tuple covers 028-031 (031 behind .exists() guard).
- Full suite via test-overlay is GREEN (new tests SKIP/XFAIL, none unexpectedly pass or error).
- Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` exits 0.
</verification>

<success_criteria>
- Every Phase 11 requirement (FLD/MIG/PMT/RT) has a named test that is RED-by-skip today and will be flipped green by 11-02/11-03.
- The conftest UndefinedColumn trap (Pitfall 3) is closed for 028/029/030/031.
- No production code (app/) modified.
</success_criteria>

<output>
After completion, create `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-01-SUMMARY.md`
</output>
