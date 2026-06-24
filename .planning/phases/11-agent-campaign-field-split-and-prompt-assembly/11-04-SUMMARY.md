---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: "04"
subsystem: frontend-forms-and-handoff
tags: [frontend, forms, openapi, stage-editor, cross-repo, phase-11, ui-fld]
dependency_graph:
  requires: ["11-02", "11-03"]
  provides: ["UAT-pending"]
  affects:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/agents.tsx
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx
    - /root/apps/aimly/aimly-tg-outreach/src/components/EditCampaignModal.tsx
tech_stack:
  added:
    - DialogueStage local interface (matches backend schema)
    - InlineStageEditor component in EditCampaignModal (no dnd lib)
    - StageEditor component in campaigns.new.tsx
  patterns:
    - React useState array for dialogue_flow (add/remove/splice reorder)
    - Conditional field reveal (response_speed === "manual" → delay input)
    - Pre-save filter: drop stages with empty instruction
key_files:
  created: []
  modified:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - src/routes/_authenticated/agents.tsx (frontend repo)
    - src/routes/_authenticated/campaigns.new.tsx (frontend repo)
    - src/routes/_authenticated/campaigns.$id.tsx (frontend repo)
    - src/components/EditCampaignModal.tsx (frontend repo)
decisions:
  - "D-01: tone_preset select (4 options) replaces voice_baseline + tone sliders + tone_of_voice; all 3 old controls deleted not hidden"
  - "D-07: agent picker stays plain select in campaign wizard; no per-campaign override UI"
  - "D-10: commits made with named files only in both repos; never git add -A"
  - "D-13: audience_hints relabeled Кому пишем; success_criteria merged into lead_trigger_hint as Сигнал Лид"
metrics:
  duration_minutes: 45
  completed_date: "2026-06-24"
  tasks_completed: 3
  files_modified: 6
---

# Phase 11 Plan 04: Frontend Forms and Handoff Summary

Pre-UAT autonomous tasks complete. Rebuilt Agent form (tone_preset + response_speed), Campaign wizard (dialogue_flow stage editor, arguments_facts, campaign_rules, renamed/merged fields), synced openapi.json + TS types. Awaiting human UAT (Task 4).

## Tasks Completed

| Task | Name | Repo | Commit | Key Files |
|------|------|------|--------|-----------|
| 1 | Regenerate openapi.json + types from updated backend | backend | 1e938b0 | lovable-handoff/openapi.json, lovable-handoff/types/api.ts |
| 2 | Rebuild Agent form (tone preset + response speed) | frontend | 3e3e291 | agents.tsx, src/types/api.ts |
| 3 | Rebuild Campaign form with stage editor + relabels | frontend | b77872c | campaigns.new.tsx, campaigns.$id.tsx, EditCampaignModal.tsx |

## What Was Built

### Task 1: openapi.json + types regenerated (backend repo)

Rebuilt API container to pick up Phase 11 Wave 2 schema changes, then ran `scripts/export-handoff.sh`. Verified:
- New fields present: `tone_preset`, `response_speed`, `response_delay_seconds`, `dialogue_flow`, `arguments_facts`, `campaign_rules`
- Old fields absent: `voice_baseline`, `tone_of_voice` (from agent schemas); `success_criteria` removed from campaign schemas
- Generated `lovable-handoff/types/api.ts` via openapi-typescript@7 (never hand-edited)
- Copied generated types to frontend repo `src/types/api.ts`

Verification: `grep -q "dialogue_flow" ... && ! grep -q "voice_baseline" ... && echo HANDOFF_OK` → HANDOFF_OK

### Task 2: Agent form rebuilt (frontend repo)

`agents.tsx` — `AgentEditor` component:
- `voice_baseline` select replaced by `tone_preset` .select with 4 options: Friendly / Professional / Direct / Casual (D-01)
- Tone JSONB sliders (formal/warm/brief) and `tone_of_voice` free-text DELETED (not hidden)
- `response_speed` .select added: Мгновенно / Как человек (default) / Медленно / Вручную (D-11)
- Conditional `response_delay_seconds` number input revealed only when `response_speed === "manual"`
- Tone hint added: "Единый тон агента. Не дублируйте тон в жёстких правилах."
- Identity placeholder updated: "Имя, роль, характер, манера речи." (no task/goal wording, D-13)
- Rules placeholder updated: "Только запреты и стоп-темы." (no tone, D-03)
- Agent list table: "Voice" column now shows `tone_preset` (was `voice_baseline`)

### Task 3: Campaign forms rebuilt (frontend repo)

**campaigns.new.tsx — AgentStep component:**
- `StageEditor` component added: React `useState<DialogueStage[]>` array
  - Each card: numbered badge + .input (title) + .textarea (instruction) + remove X button
  - Up/Down chevron buttons with aria-labels; first-card up and last-card down disabled
  - Empty state shows guidance copy per UI-SPEC
  - Ghost "+ Добавить стадию" button (disabled at 7 stages max)
  - On save: stages with empty instruction dropped before sending to backend
- `audience_hints` relabeled "Кому пишем" (D-13, column unchanged)
- `arguments_facts` textarea added with anti-hallucination hint (D-12)
- `campaign_rules` textarea added (D-14)
- `success_criteria` field REMOVED; `leadHint` relabeled «Сигнал "Лид"» (D-13)
- `autoFillMut` now reads `lead_trigger_hint` from auto-fill response (D-15)
- `canNext` and `allValid` updated: removed `successCriteria` check
- buildPayload: `dialogue_flow` filtered and sent; `arguments_facts` and `campaign_rules` added

**campaigns.$id.tsx — Overview section:**
- "Audience" row relabeled "Кому пишем"
- "Success criteria" row removed
- "Аргументы и факты" and "Правила кампании" rows added

**EditCampaignModal.tsx — edit surface:**
- `InlineStageEditor` component added (self-contained, same logic as StageEditor)
- "Audience hints" field relabeled "Кому пишем"
- "Success criteria" field removed
- Lead trigger hint label changed to «Сигнал "Лид"»
- `arguments_facts` and `campaign_rules` textareas added
- `dialogue_flow` state initialized from campaign data with round-trip save
- `diff` logic updated: `dialogue_flow`, `arguments_facts`, `campaign_rules` tracked for change detection

## Deviations from Plan

### 1. [Rule 3 - Blocking] API container needed rebuild before openapi regen

**Found during:** Task 1
**Issue:** The running API container was stale (pre-Phase-11 code). Running `scripts/export-handoff.sh` fetched the old schema — `voice_baseline` still present, `tone_preset` absent.
**Fix:** Ran `docker compose up -d --build api` to rebuild the container with Phase 11 Wave 2 code, waited for readiness, then re-ran the export script.
**Files modified:** none (infrastructure only)
**Impact:** Added ~2 min to Task 1; no code changes.

### 2. [Rule 2 - Missing] EditCampaignModal also needed campaign form updates

**Found during:** Task 3
**Issue:** The plan named `campaigns.$id.tsx` as the edit surface but EditCampaignModal (imported from campaigns.$id.tsx) also renders the full campaign form and had `success_criteria`, no new fields, and old labels.
**Fix:** Updated EditCampaignModal.tsx with `InlineStageEditor`, new fields, and relabels. The plan acknowledged "NO EditCampaignModal exists" as a research finding — this is incorrect; it does exist and is the edit surface. Applied Rule 2 (missing critical functionality for correctness).
**Files modified:** `src/components/EditCampaignModal.tsx`
**Commit:** b77872c

## Known Stubs

None — all new fields are wired through create, update, and display surfaces. `dialogue_flow` round-trips via edit modal. `arguments_facts` and `campaign_rules` display in campaign detail view.

## Self-Check

Files exist:
- lovable-handoff/openapi.json: FOUND
- lovable-handoff/types/api.ts: FOUND

Commits:
- backend: 1e938b0 (feat(11-04): regenerate openapi.json): FOUND
- frontend: 3e3e291 (feat(11-04): rebuild Agent form): FOUND
- frontend: b77872c (feat(11-04): rebuild Campaign forms): FOUND

## Self-Check: PASSED
