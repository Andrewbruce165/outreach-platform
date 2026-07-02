---
phase: 18-switchable-llm-provider
plan: 05
type: execute
wave: 4
depends_on: ["18-03", "18-04"]
files_modified:
  - lovable-handoff/openapi.json
  - lovable-handoff/types/api.ts
  - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/settings.tsx
  - /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts
autonomous: false
requirements: [LLMP-03, LLMP-05, LLMP-08, LLMP-09, LLMP-10, LLMP-11]
must_haves:
  truths:
    - "The Settings page has an AI/LLM section: provider select, model select (live list), key input, knobs (temperature/reasoning-effort/max-tokens), Test connection button"
    - "Switching is blocked in the UI until a key is entered (mirrors the D-03 backend gate)"
    - "Model knobs show only when the selected model supports them and display the green-corridor recommended ranges"
    - "openapi.json + types/api.ts include the new llm-settings endpoints"
  artifacts:
    - path: "lovable-handoff/openapi.json"
      provides: "regenerated spec with /workspace/llm-settings paths"
      contains: "llm-settings"
    - path: "/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/settings.tsx"
      provides: "AI/LLM settings section wired to the new endpoints"
      contains: "llm-settings"
  key_links:
    - from: "settings.tsx"
      to: "/api/v1/workspace/llm-settings"
      via: "GET/PATCH + test-connection + models fetch"
      pattern: "llm-settings"
    - from: "lovable-handoff/types/api.ts"
      to: "lovable-handoff/openapi.json"
      via: "openapi-typescript regen"
      pattern: "llm-settings|LLMSettings"
---

<objective>
Regenerate the Lovable handoff bundle (openapi.json + types) with the new llm-settings endpoints, and build the AI/LLM section in the sibling frontend Settings page: provider/model selects (live model list), masked key input + Test connection, capability-gated knobs with green-corridor hints, and the D-03 switch-requires-key gate. Human-verify the end-to-end switch.

Purpose: Delivers the user-facing "switch LLM in the UI" (the whole point of the phase). Cross-repo: backend openapi lands in tg-outreach, the React screen in the sibling aimly-tg-outreach repo.
Output: regenerated handoff, Settings AI/LLM section, error-codes entries; human UAT confirming a switch to Claude answers in chat.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/18-switchable-llm-provider/18-CONTEXT.md

<interfaces>
<!-- Handoff regen flow + sibling-repo conventions. -->

scripts/export-handoff.sh — regenerates lovable-handoff/openapi.json + types/api.ts from a RUNNING api container.
Prereq: the api container must be rebuilt FIRST so the new llm_settings router is live:
  docker compose up -d --build api
  bash scripts/export-handoff.sh
It hard-fails if the scraped openapi title is not the Outreach/aimly project (sanity guard).

Sibling frontend repo: /root/apps/aimly/aimly-tg-outreach (origin AGS-Venture-Lab/aimly-tg-outreach — a DIFFERENT git remote than tg-outreach).
- Settings route: src/routes/_authenticated/settings.tsx
- Error-code map: src/lib/error-codes.ts (human-readable messages keyed by backend `code`)
- Data fetching: TanStack Query (useQuery/useMutation + invalidateQueries), shadcn components — mirror the existing warmup/campaign patterns in the repo.

Backend endpoints delivered by 18-03 (drive the UI from these):
- GET    /api/v1/workspace/llm-settings            -> {provider, model, api_key_prefix, api_key_status, temperature, reasoning_effort, max_tokens}
- PATCH  /api/v1/workspace/llm-settings            body {provider?, model?, api_key?, temperature?, reasoning_effort?, max_tokens?}
- POST   /api/v1/workspace/llm-settings/test-connection  -> {status:'valid'|'invalid', detail?}
- GET    /api/v1/workspace/llm-settings/models?provider=openai|anthropic  -> {models:[...], note?}

Green corridor (from 18-02 capabilities): reasoning models max_tokens floor >=4000, ceiling ~32000; temperature 0-2 (OpenAI non-reasoning) / 0-1 (Claude); reasoning_effort minimal|low|medium|high.
Capability gating (D-09): temperature knob shown only when model is non-reasoning-OpenAI or Claude; reasoning_effort shown only for reasoning models / Claude.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Rebuild api + regenerate handoff bundle (openapi.json + types)</name>
  <files>lovable-handoff/openapi.json, lovable-handoff/types/api.ts</files>
  <read_first>
    - scripts/export-handoff.sh (the regen flow + the api-must-be-rebuilt-first note)
    - lovable-handoff/openapi.json (current — to diff after regen)
  </read_first>
  <action>
    Rebuild the api container so the 18-03 llm_settings router is live, then run the handoff export:
    ```bash
    cd /root/apps/aimly/tg-outreach
    docker compose up -d --build api
    bash scripts/export-handoff.sh
    ```
    Confirm `lovable-handoff/openapi.json` now contains the `/api/v1/workspace/llm-settings` paths and `lovable-handoff/types/api.ts` was regenerated. Do NOT hand-edit the openapi.json — it is generated from `app.openapi()`. If the export script fails on the UI-SPEC drift check for the new endpoints, that is expected drift for a new feature — note it in the summary; do not silence the check by editing the spec by hand.
  </action>
  <verify>
    <automated>grep -q "llm-settings" lovable-handoff/openapi.json && grep -q "llm-settings" lovable-handoff/types/api.ts && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `lovable-handoff/openapi.json` contains `/api/v1/workspace/llm-settings` (and `/test-connection`, `/models`)
    - `lovable-handoff/types/api.ts` regenerated and contains the llm-settings paths/types
    - openapi.json was produced by the export script (not hand-edited) — its `info.title` still passes the Outreach/aimly sanity guard
  </acceptance_criteria>
  <done>Handoff bundle regenerated from the live api; llm-settings endpoints present in spec + types.</done>
</task>

<task type="auto">
  <name>Task 2: Build the AI/LLM Settings section in the sibling frontend + error codes</name>
  <files>/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/settings.tsx, /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts</files>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/settings.tsx (full file — existing sections, query/mutation patterns, shadcn components in use)
    - /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts (the human-readable code map to extend)
    - lovable-handoff/types/api.ts (the regenerated types for the new endpoints)
    - .planning/phases/18-switchable-llm-provider/18-CONTEXT.md D-08/D-09/D-10 (live model list, capability-gated knobs, green corridor)
  </read_first>
  <action>
    In the sibling repo `/root/apps/aimly/aimly-tg-outreach`, add an "AI / LLM" section to `src/routes/_authenticated/settings.tsx` (a new card/section in the existing Settings page — placement is discretion, matching the repo's section style). Wire it to the 18-03 endpoints via TanStack Query, mirroring the existing sections' useQuery/useMutation + invalidateQueries + sonner-toast conventions:
    - **Provider select**: OpenAI | Anthropic (Claude).
    - **API key input** (password field): on save, PATCH `{api_key}`. Show the masked `api_key_prefix` + `api_key_status` badge (unset / valid / invalid). D-03 GATE: disable the model select / provider switch save until a key is entered or already stored (mirror the backend KEY_REQUIRED behaviour so the user never hits a raw 400).
    - **Test connection button**: POST test-connection; show a spinner then a green "valid" / red "invalid" state from the response; refetch settings after.
    - **Model select**: on provider change (with a valid/entered key), GET `/models?provider=...` and populate the dropdown from the live filtered list; if `note` is present (list unavailable), show a soft hint and let the user type/pick a fallback.
    - **Knobs** (capability-gated, D-09): temperature slider (shown only for non-reasoning OpenAI models + Claude; range 0-2 OpenAI / 0-1 Claude), reasoning-effort select minimal|low|medium|high (shown only for reasoning models / Claude), max-tokens number input. Show the **green corridor** (D-10): for reasoning models enforce/hint a minimum of 4000 and a ceiling ~32000 with an inline note "рекомендованный диапазон" — mirror the send-limits green-corridor idea. The backend hard-clamps regardless, but the UI must not let the user set a value below the reasoning floor without a visible warning.
    - PATCH persists provider/model/temperature/reasoning_effort/max_tokens; invalidate the settings query on success.

    In `src/lib/error-codes.ts`, add human-readable messages for the new backend codes: `KEY_REQUIRED` ("Введите API-ключ, чтобы переключить провайдера или модель"), and any test-connection/JWT codes surfaced. Keep the existing array/object shape of the file.

    Do NOT touch the tg-outreach backend in this task. Commit the sibling-repo changes to the sibling repo (AGS-Venture-Lab/aimly-tg-outreach) separately from tg-outreach (see MEMORY: cross-repo commit discipline — never `git add -A`, stage specific files).
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && npx tsc --noEmit 2>&1 | tail -5; grep -q "llm-settings" src/routes/_authenticated/settings.tsx && grep -q "KEY_REQUIRED" src/lib/error-codes.ts && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - `settings.tsx` contains the string `llm-settings` (fetches the endpoints) and a provider select + key input + Test connection + model select + knobs
    - The model/provider save is gated on a key (D-03) — grep `KEY_REQUIRED` handling or a disabled-until-key control
    - `src/lib/error-codes.ts` contains `KEY_REQUIRED`
    - `npx tsc --noEmit` in the sibling repo exits 0 (no type errors)
  </acceptance_criteria>
  <done>Settings AI/LLM section wired to the new endpoints with capability-gated knobs, green-corridor hints, D-03 key gate, and Test connection; tsc clean.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 3: Human-verify end-to-end LLM switch (OpenAI ↔ Claude)</name>
  <action>Deploy backend (api + listener) and frontend, then perform the human-verification steps below. This gate confirms a runtime provider switch (OpenAI to Claude) answers in chat with correct provider/key_source logging and D-12 isolation intact. No code changes in this task — verification only.</action>
  <what-built>
    Backend: llm_settings API (GET/PATCH/test-connection/models) + provider adapter routing the answerer + warmup through the chosen provider (18-01..18-04). Frontend: AI/LLM section in Settings with provider/model selects, masked key + Test connection, capability-gated knobs + green corridor.
  </what-built>
  <how-to-verify>
    1. Deploy backend: `cd /root/apps/aimly/tg-outreach && docker compose up -d --build api && docker compose up -d --build listener` (BOTH — the answerer runs in the listener, Pitfall 7).
    2. Deploy frontend (sibling repo, Cloudflare) per the repo's deploy flow.
    3. In the UI Settings → AI/LLM: enter a real Anthropic (Claude) key → click Test connection → expect a green "valid".
    4. Open the model select → expect only chat-capable Claude models (no embeddings/whisper/tts).
    5. Pick a Claude model, set reasoning-effort + max-tokens (try to set max-tokens below 4000 on a reasoning model → expect a green-corridor warning), Save.
    6. Message a test contact from a running campaign → confirm the AI replies.
    7. In the DB (or inbox LLM-log): confirm the new `llm_calls` row shows `provider='anthropic'`, `key_source='byok'`. Query:
       `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SELECT provider, key_source, model, created_at FROM llm_calls ORDER BY created_at DESC LIMIT 3;"`
    8. (Optional D-06) Enter a garbage key, save, message a contact → dialog still gets a reply (fallback to platform OpenAI) and the key shows `invalid` in Settings; the fallback llm_calls row shows `key_source='fallback'`.
    9. Confirm voice-message transcription (if used) and KB search still work (they stay on the platform OpenAI key, D-12).
  </how-to-verify>
  <resume-signal>Type "approved" or describe issues (e.g. model list wrong, knobs not gating, no reply, wrong provider in llm_calls).</resume-signal>
</task>

</tasks>

<verification>
- `grep -q "llm-settings" lovable-handoff/openapi.json` and in types/api.ts
- Sibling `npx tsc --noEmit` clean; settings.tsx references llm-settings; error-codes has KEY_REQUIRED
- Human UAT: switch to Claude → reply arrives → llm_calls shows provider='anthropic' key_source='byok'; garbage key → fallback reply + key_source='fallback' + invalid badge; Whisper/KB unaffected
</verification>

<success_criteria>
- Handoff bundle regenerated with llm-settings endpoints
- Frontend AI/LLM section delivers the runtime switch with capability-gated knobs + green corridor + D-03 gate
- Human-verified end-to-end switch (OpenAI ↔ Claude) with correct provider/key_source logging and D-12 isolation intact
</success_criteria>

<output>
After completion, create `.planning/phases/18-switchable-llm-provider/18-05-SUMMARY.md`
</output>
