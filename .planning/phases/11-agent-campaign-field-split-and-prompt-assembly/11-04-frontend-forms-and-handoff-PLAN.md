---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: 04
type: execute
wave: 4
depends_on: ["11-02", "11-03"]
files_modified:
  - lovable-handoff/openapi.json
  - lovable-handoff/openapi-types.ts
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx
  - .planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-VALIDATION.md
autonomous: false
requirements: [UI-FLD-01, UI-FLD-02, UI-FLD-03, D-05, D-07, D-08, D-09, D-10, D-13, D-15]
must_haves:
  truths:
    - "The Agent form shows a single Тон select (4 options), a Скорость ответа control, and no tone sliders / free-text tone"
    - "The Campaign form has a working Ход разговора stage editor (add/remove/reorder)"
    - "Renamed labels: Audience hints -> Кому пишем; lead-signal field absorbs the old Success criteria"
    - "openapi.json + generated types reflect the new schema and the frontend type-checks clean"
  artifacts:
    - path: "lovable-handoff/openapi.json"
      provides: "Synced API contract with new fields"
      contains: "dialogue_flow"
    - path: "/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx"
      provides: "Rebuilt Agent form (tone_preset, response_speed, removed tone sliders)"
    - path: "/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx"
      provides: "Rebuilt Campaign wizard step + stage editor"
  key_links:
    - from: "campaigns.new.tsx stage editor"
      to: "POST /campaigns dialogue_flow"
      via: "array state -> request body"
      pattern: "dialogue_flow"
    - from: "scripts/export-handoff.sh"
      to: "lovable-handoff/openapi.json"
      via: "regen after schema change"
      pattern: "openapi"
---

<objective>
Rebuild the Agent and Campaign wizard forms in the frontend repo to match the new field split, sync the API contract, and verify the end-to-end flow with a human. This is the cross-repo, user-facing half of Phase 11 (D-08).

Purpose: The schema/prompt changes (11-02/11-03) are invisible to the client until the wizard exposes them. Close the loop so a user can set tone via one preset, author dialogue stages, and enter campaign facts/rules.
Output: synced openapi.json + types, rebuilt forms, human-verified flow.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-CONTEXT.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-UI-SPEC.md
@.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-PATTERNS.md
@scripts/export-handoff.sh

<interfaces>
<!-- Grounded from live repos 2026-06-24. -->

CROSS-REPO (D-08): backend = /root/apps/aimly/tg-outreach (origin Andrewbruce165/outreach-platform);
frontend = /root/apps/aimly/aimly-tg-outreach (origin AGS-Venture-Lab/aimly-tg-outreach).
lovable-handoff/ physically lives in the BACKEND repo but feeds the frontend.

COMMIT SAFETY (D-10): commit ONLY this phase's files BY NAME (--files ...) in BOTH repos.
NEVER git add -A / git add . — Phase 10 work runs in parallel. STATE.md edits surgical.

Frontend routes that exist (verified): agents.tsx, campaigns.new.tsx, campaigns.$id.tsx,
campaigns.index.tsx. NOTE: there is NO src/components/EditCampaignModal.tsx — campaign editing
lives in campaigns.$id.tsx. The executor must find the actual edit surface, not assume the
research's filename.

Handoff regen: scripts/export-handoff.sh (openapi-typescript@7). Run AFTER backend schema
changes (11-02), BEFORE frontend edits (Pitfall 6 ordering). Never hand-edit generated types.

UI-SPEC contract (11-UI-SPEC.md) is authoritative for design:
  - aimly kit classes: .field/.field__label/.field__hint/.input/.textarea/.select/.toggle/.btn/.card/.pill
  - Tone select replaces voice_baseline select; options Friendly/Professional/Direct/Casual
  - Response-speed: .select (4 opts) + conditional .input number (Задержка, сек) when manual; default human
  - Stage editor: numbered badge + .input(title) + .textarea(instruction) + remove X; ghost "+ Добавить стадию";
    up/down chevrons; NO drag-n-drop lib; 3-5 guidance soft cap ~7; aria-labels on icon-only buttons
  - Remove tone sliders + tone free-text from Agent form (deleted, not hidden)
  - Relabel audience_hints -> "Кому пишем"; lead-signal field carries migrated success_criteria; remove separate Success criteria field
  - "Используемые базы знаний" multi-select: OMIT in Phase 11 (no backend column)
  - Russian copy table in UI-SPEC §Copywriting; empty-state copy for stage editor

dialogue_flow JSON shape: [{ "title": string, "instruction": string }] ; instruction required;
empty-instruction stages dropped on save (not sent).
</interfaces>
</context>

<threat_model>
ASVS L1 surface for this plan:
- T1 Client-side enum/shape mismatch sending invalid data: mitigated because the backend enforces Literal enums + DialogueStage caps (11-02) returning 422; the frontend additionally constrains tone/speed to fixed selects and drops empty-instruction stages before submit, so malformed dialogue_flow is not sent. Generated types (UI-FLD-03) keep the client in sync with server validation.
- T2 Stale contract / type drift (hand-edited types diverge from server): mitigated by regenerating openapi.json + types via scripts/export-handoff.sh and never hand-editing generated files (Don't-Hand-Roll). Task 1.
- T3 Cross-repo commit contamination (D-10): mitigated by committing only named files in each repo, never git add -A; Lovable parallel commits handled by rebase-on-origin/main before push (Pitfall 6). Tasks 2-3.
- T4 No secrets in frontend: response-speed/tone/facts are non-secret UI fields; no API keys introduced. The auto-fill button calls the existing endpoint with the existing auth; no new credential surface.
The free-text fields (arguments_facts, campaign_rules) are user input rendered server-side with guards (11-03); the frontend surfaces a hint mirroring the guard but does not itself sanitize — the trust boundary is the backend, which is correct.
</threat_model>

<tasks>

<task type="auto">
  <name>Task 1: Regenerate openapi.json + types from updated backend schemas</name>
  <read_first>
    - scripts/export-handoff.sh (regen flow + which openapi/types files it writes)
    - lovable-handoff/openapi.json (current contract — to diff after regen)
    - 11-PATTERNS.md §"lovable-handoff/openapi.json + types/api.ts" (regen AFTER backend schema, never hand-edit)
  </read_first>
  <files>lovable-handoff/openapi.json, lovable-handoff/openapi-types.ts</files>
  <action>
    Run scripts/export-handoff.sh (in the backend repo, with api importable) so it regenerates lovable-handoff/openapi.json and the generated TypeScript types from the 11-02 schemas. Diff the openapi.json to confirm: tone_preset / response_speed / response_delay_seconds appear on the agent schema; dialogue_flow / arguments_facts / campaign_rules appear on the campaign schema; voice_baseline / tone_of_voice / tone / success_criteria are gone. Do NOT hand-edit the generated types — if the diff is wrong, fix the Pydantic schema in 11-02 and rerun. This task runs in the BACKEND repo; commit only the regenerated handoff files by name (D-10).
  </action>
  <verify>
    <automated>grep -q "dialogue_flow" lovable-handoff/openapi.json && grep -q "tone_preset" lovable-handoff/openapi.json && ! grep -q "success_criteria" lovable-handoff/openapi.json && echo HANDOFF_OK</automated>
  </verify>
  <acceptance_criteria>
    - lovable-handoff/openapi.json contains "dialogue_flow", "tone_preset", "response_speed", "arguments_facts", "campaign_rules"
    - lovable-handoff/openapi.json does NOT contain "voice_baseline", "tone_of_voice", "success_criteria"
    - Generated TS types file was produced by the script (not hand-edited)
    - grep verify prints HANDOFF_OK
  </acceptance_criteria>
  <done>The API contract the frontend builds against reflects the field split.</done>
</task>

<task type="auto">
  <name>Task 2: Rebuild Agent form (tone preset + response speed; remove tone sliders/free-text)</name>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx (current agent form — field hooks, voice_baseline select, tone sliders, tone_of_voice)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx (agent step of the wizard, STEPS array, per-field useState, autoFillMut)
    - 11-UI-SPEC.md §"Agent form" + §"Response-speed control" + §"Copywriting Contract"
  </read_first>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx, /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx</files>
  <action>
    In agents.tsx (and the wizard agent step in campaigns.new.tsx) replace the voice_baseline select with a Тон .select offering exactly Friendly / Professional / Direct / Casual bound to tone_preset. DELETE the tone JSONB sliders (formal/warm/brief) and the tone_of_voice free-text control entirely (not hidden) — remove their state hooks and request-body fields. Add a Скорость ответа .select (Мгновенно=instant / Как человек=human default / Медленно=slow / Вручную=manual); when manual is selected reveal a conditional .input number ("Задержка, сек", min 0) bound to response_delay_seconds with the hint copy from UI-SPEC. Update the Идентичность field placeholder to "Имя, роль, характер, манера речи." (no task/goal wording, D-13) and the Жёсткие правила placeholder to "Только запреты и стоп-темы." (no tone, D-03); add the tone-field hint "Единый тон агента. Не дублируйте тон в жёстких правилах." Keep Макс. длина. Reuse the aimly kit classes (.field/.input/.select/.textarea) matching neighbouring fields; all emphasis weight 600; tg-blue only per UI-SPEC accent list. Wire new fields into create + update request bodies using the regenerated types. This task runs in the FRONTEND repo.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && bun run tsc 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - agents.tsx renders a tone_preset select with the 4 options and NO formal/warm/brief sliders and NO tone_of_voice input (grep shows those removed)
    - response_speed select present; manual selection reveals a response_delay_seconds number input
    - Identity placeholder has no task/goal wording; rules placeholder has no tone wording; tone-field hint present
    - `bun run tsc` in the frontend repo is clean (exit 0)
  </acceptance_criteria>
  <done>Agent form exposes the single tone source and response-speed; legacy tone controls deleted.</done>
</task>

<task type="auto">
  <name>Task 3: Rebuild Campaign form with the Ход разговора stage editor + relabels</name>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx (campaign-side fields of the wizard, STEPS, autoFillMut, success_criteria/leadHint usage)
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx (campaign edit surface — the real one; NO EditCampaignModal exists)
    - 11-UI-SPEC.md §"Campaign form" + §"Stage editor" + §"Arguments & facts guard hint" + §"Brief auto-fill" + §"Copywriting Contract"
  </read_first>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx, /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx</files>
  <action>
    In campaigns.new.tsx and campaigns.$id.tsx: relabel the audience_hints field to "Кому пишем" (column unchanged, D-13). Build the Ход разговора stage editor: a React useState<DialogueStage[]> array; each card = numbered badge + .input (title) + .textarea (instruction) + remove icon-button (X, aria-label "Удалить стадию"); a ghost "+ Добавить стадию" button below; per-card up/down chevron icon-buttons (aria-label "Переместить вверх"/"вниз", first card up disabled, last card down disabled) reordering via index splice; NO drag-n-drop library. Empty state shows the UI-SPEC empty heading/body copy. On save, drop stages with empty instruction and send dialogue_flow as the array of {title, instruction}. Add Аргументы и факты .textarea (arguments_facts) with the hint "ИИ использует только эти факты и не выдумывает остальное." Add Правила кампании .textarea (campaign_rules). Merge the lead-signal field: the existing leadHint field now also carries the migrated success_criteria — relabel to «Сигнал „Лид"» and REMOVE the separate Success criteria field; repoint autoFillMut so it writes the lead-signal hint (not success_criteria) per D-13/D-15. Do NOT render a "Используемые базы знаний" multi-select (omit, no backend column). Reuse aimly kit classes/tokens; stage card surface --surface-2 inside --border, radius --r-md; lucide icons Plus/X/ChevronUp/ChevronDown. The agent picker stays a plain select (no override UI, D-07). This task runs in the FRONTEND repo.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && bun run tsc 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - campaigns.new.tsx has a stage editor with add/remove/up-down controls bound to a DialogueStage[] state and sends dialogue_flow on save (empty-instruction stages dropped)
    - audience_hints label is "Кому пишем"; arguments_facts and campaign_rules textareas present with correct hints/labels
    - Separate "Success criteria" field removed; lead-signal field relabeled «Сигнал „Лид"»; autoFillMut targets the lead-signal hint
    - No "Используемые базы знаний" control rendered; agent field is a plain select (no override UI)
    - icon-only buttons have aria-labels; first-up/last-down disabled
    - `bun run tsc` clean (exit 0)
  </acceptance_criteria>
  <done>Campaign form drives dialogue_flow + facts + rules; merged/renamed fields per D-13.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 4: Human UAT of the rebuilt wizard end-to-end</name>
  <read_first>
    - 11-UI-SPEC.md §"Interaction Contracts" + §"Copywriting Contract"
    - 11-VALIDATION.md §"Manual-Only Verifications"
  </read_first>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx, /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx, /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx</files>
  <action>
    Pause for the user to manually exercise the rebuilt Agent + Campaign wizard against a running frontend+backend. Present the how-to-verify steps below, then wait for the resume signal. Do not proceed to commit/SUMMARY until the user types "approved". If the user reports issues, loop back to Task 2/Task 3 to fix them.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && bun run tsc 2>&1 | tail -3</automated>
  </verify>
  <done>User has confirmed (typed "approved") that forms render, the stage editor adds/removes/reorders, renamed/merged fields are correct, and values round-trip on save+reopen.</done>
  <what-built>
    Rebuilt Agent + Campaign wizard forms wired to the new backend (tone_preset, response_speed,
    dialogue_flow stage editor, arguments_facts, campaign_rules), synced openapi.json/types.
    Backend prompt assembly already verified by automated golden-prompt tests (11-03).
  </what-built>
  <how-to-verify>
    1. Start/refresh the frontend; open the campaign wizard.
    2. Agent step: confirm exactly one Тон select (4 options), no sliders/free-text tone; pick Скорость ответа = Вручную and confirm the seconds input appears.
    3. Campaign step: add 3 stages in Ход разговора, reorder one up and one down, remove one; confirm numbering updates and first-up/last-down are disabled at the ends.
    4. Fill Аргументы и факты and Правила кампании; confirm "Кому пишем" label and the single «Сигнал „Лид"» field (no separate Success criteria).
    5. Save the campaign, then reopen it (campaigns.$id) and confirm all values persisted (round-trip).
    6. Optional: trigger a real AI reply and confirm the configured response speed delay feels right.
  </how-to-verify>
  <resume-signal>Type "approved" or describe issues (fields missing, stage editor broken, values not persisting, labels wrong).</resume-signal>
</task>

</tasks>

<verification>
- openapi.json + generated types reflect the field split (Task 1 grep).
- Frontend `bun run tsc` clean after both form rebuilds.
- Human UAT confirms forms render, stage editor works, values round-trip.
- Commits in BOTH repos use --files with named paths only (D-10); no git add -A.
</verification>

<success_criteria>
- Agent form: single tone preset + response-speed control; legacy tone controls removed (UI-FLD-01).
- Campaign form: working dialogue_flow stage editor + arguments_facts + campaign_rules; relabels per D-13 (UI-FLD-02).
- openapi.json/types synced via script, no hand-edits (UI-FLD-03).
- End-to-end flow human-verified.
</success_criteria>

<output>
After completion (UAT approved):
1. Set `nyquist_compliant: true` in `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-VALIDATION.md` frontmatter — by this point every Phase 11 requirement (FLD/MIG/PMT/RT/UI-FLD) has a landed test or human-verified UAT, so the validation contract is satisfied.
2. Create `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-04-SUMMARY.md`.
3. Commit ONLY named files in BOTH repos via `--files` (D-10); never `git add -A`.
</output>
