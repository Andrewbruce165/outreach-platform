---
phase: 18-switchable-llm-provider
plan: 05
subsystem: frontend
tags: [llm, settings-ui, handoff, openapi, anthropic, openai, byok, green-corridor, cross-repo]

# Dependency graph
requires:
  - phase: 18-switchable-llm-provider (plan 03)
    provides: llm_settings API (GET/PATCH/test-connection/models) + error code KEY_REQUIRED + masked-key response shape
  - phase: 18-switchable-llm-provider (plan 04)
    provides: runtime switch wired through answerer/warmup/logger (provider + key_source + D-06 fallback)
  - phase: 05.1-lovable-ui-v1
    provides: lovable-handoff export flow (scripts/export-handoff.sh) + settings.tsx tabbed page + error-codes.ts map
provides:
  - lovable-handoff/openapi.json + types/api.ts regenerated with /workspace/llm-settings (+ /test-connection, /models)
  - frontend AI/LLM Settings tab (sibling repo) wired to the four llm-settings endpoints
  - src/lib/error-codes.ts KEY_REQUIRED + CONNECTION_INVALID human-readable messages
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Handoff regenerated OFFLINE from app.openapi() via the ephemeral test container (docker compose -f ... -f docker-compose.test.yml run --rm --no-deps api) rather than the export script's prod-api rebuild — respects the no-prod-deploy rule while still sourcing the spec from current code (mirrors Phase 16 precedent). UI-SPEC drift check run separately and passes 39/39."
    - "Client-side capability gate mirrors app/services/llm/capabilities: isOpenAiReasoning (gpt-5*/o1/o3/o4*) drives temperature/reasoning-effort visibility; Claude always shows both (D-09). Green corridor (D-10) warns-not-blocks below the reasoning floor (4000); backend hard-clamps regardless."
    - "D-03 UI gate: model select + config card are hidden/disabled until a key is stored (api_key_status valid|invalid) OR just typed — surfacing KEY_REQUIRED text so the user never hits a raw 400."

key-files:
  created:
    - .planning/phases/18-switchable-llm-provider/18-05-SUMMARY.md
  modified:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/settings.tsx (SIBLING repo)
    - /root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts (SIBLING repo)

key-decisions:
  - "Regenerated the handoff offline (app.openapi() in the test container) instead of the export script's prod-api rebuild — the running prod api is a shared production service and the project rules forbid rebuilding it without explicit user action; Phase 16 established the offline-openapi precedent. Output is identical to what the script would produce (same title guard, same UI-SPEC check)."
  - "Client-side reasoning detection duplicates the backend prefix rule (gpt-5*/o1/o3/o4*) as the single UI source; Claude is treated as reasoning-capable for knob visibility (D-09 extended thinking) but not gated by the OpenAI prefix."
  - "Green corridor warns (toast + inline) rather than blocking sub-floor max_tokens — the backend clamp is authoritative (D-10); the UI's job is to make the recommended range visible, not to reimplement enforcement."

patterns-established:
  - "Cross-repo commit discipline: openapi/types → tg-outreach (Andrewbruce165/outreach-platform); React screen → sibling aimly-tg-outreach (AGS-Venture-Lab), each staged file-by-file, never git add -A."

requirements-completed: [LLMP-03, LLMP-05, LLMP-08, LLMP-09, LLMP-10, LLMP-11]

# Metrics
duration: 5min
completed: 2026-07-02
---

# Phase 18 Plan 05: OpenAPI Handoff and Frontend Settings Summary

**The user-facing LLM switch is built: the Lovable handoff bundle (openapi.json + types/api.ts) was regenerated with the `/workspace/llm-settings` (+ `/test-connection`, `/models`) endpoints, and the sibling frontend gained an "AI / LLM" Settings tab — provider select (OpenAI/Anthropic), masked BYO-key input with a status badge + Test connection, a live provider-filtered model list with a soft-note manual fallback, and capability-gated knobs (temperature / reasoning-effort / max-tokens) with the D-10 green corridor and the D-03 switch-requires-key gate — with the end-to-end runtime switch left for a blocking human-verify checkpoint (Task 3).**

## Performance
- **Duration:** ~5 min (automatable tasks; human-verify pending)
- **Started:** 2026-07-02T11:56Z
- **Completed (automatable):** 2026-07-02T12:00Z
- **Tasks:** 2 of 3 executed (Task 3 is a blocking human-verify checkpoint — see below)
- **Files modified:** 4 (2 backend-repo, 2 sibling-repo)

## Accomplishments
- **Task 1 — Handoff regen:** `lovable-handoff/openapi.json` + `types/api.ts` now carry the three llm-settings paths and the `LLMSettingsResponse`/`LLMSettingsUpdate`/`ModelListResponse`/`TestConnectionRequest`/`TestConnectionResponse` schemas. Generated offline from `app.openapi()` (test container, fresh build with the 18-03/18-04 routers) — NOT hand-edited; title passes the Outreach/aimly sanity guard; UI-SPEC drift check 39/39.
- **Task 2 — Frontend AI/LLM section (sibling repo):** new `AiLlmTab` in `settings.tsx`:
  - Provider select (OpenAI | Anthropic); switching resets the model (provider-specific list).
  - Password key input + `KeyStatusBadge` (unset/valid/invalid) + masked `api_key_prefix`; "Сохранить ключ" PATCHes `{provider, api_key}`.
  - "Проверить соединение" → POST test-connection (spinner → green/red toast → refetch).
  - Live `/models?provider=` list (enabled for OpenAI always, Anthropic once a key exists — D-03/D-08); `note` → soft hint + manual model-id input fallback; stored model stays selectable if the live list omits it.
  - Capability-gated knobs (D-09): temperature slider (non-reasoning OpenAI + Claude, range 0-2 / 0-1), reasoning-effort select minimal|low|medium|high (reasoning models + Claude), max-tokens number input.
  - Green corridor (D-10): reasoning floor 4000 / ceiling 32000 with inline "рекомендованный диапазон" hint + a warn-not-block toast when max-tokens is below the floor.
  - D-03 gate: the Model + config cards are gated behind a stored-or-entered key, surfacing the `KEY_REQUIRED` message instead of a raw 400.
  - `error-codes.ts`: added `KEY_REQUIRED` + `CONNECTION_INVALID`.
- `npx tsc --noEmit` in the sibling repo exits 0.

## Task Commits
1. **Task 1: regenerate handoff bundle with llm-settings endpoints** — `c48203d` (feat) [tg-outreach]
2. **Task 2: AI/LLM Settings section + error codes** — `60343bb` (feat) [SIBLING aimly-tg-outreach]

**Plan metadata:** _final docs commit (this SUMMARY + STATE + ROADMAP)_ [tg-outreach]

## Files Created/Modified
- `lovable-handoff/openapi.json` — regenerated offline; +llm-settings paths/schemas.
- `lovable-handoff/types/api.ts` — regenerated via openapi-typescript@7.
- `src/routes/_authenticated/settings.tsx` (sibling) — new AI/LLM tab + AiLlmTab + KeyStatusBadge; imports errorMessageFromEnvelope.
- `src/lib/error-codes.ts` (sibling) — KEY_REQUIRED + CONNECTION_INVALID.

## Decisions Made
- **Offline handoff regen (no prod-api rebuild).** The export script rebuilds/boots the prod `api` to scrape `/openapi.json`; the running prod container did NOT have the 18-03/18-04 routers (image predates them) and the project rules forbid rebuilding the shared prod api without explicit user action. Resolved by generating `app.openapi()` inside the ephemeral test container (fresh build with all current routers), then running the same UI-SPEC drift check + openapi-typescript locally. This is the Phase 16 precedent ("openapi regenerated offline via app.openapi()"). The eventual prod deploy of api+listener is part of the Task 3 human-verify checkpoint, under user control.
- **Client-side capability gate mirrors the backend.** `isOpenAiReasoning` duplicates `capabilities.is_reasoning_model` (gpt-5*/o1/o3/o4*); Claude shows both knobs (D-09). The backend remains the single enforcement authority (clamp_max_tokens), the UI only surfaces the recommended range (D-10).

## Deviations from Plan
### Auto-fixed / adjusted
**1. [Rule 3 - Blocking] Handoff regen path — offline instead of prod-api rebuild**
- **Found during:** Task 1.
- **Issue:** The plan's Task 1 action runs `docker compose up -d --build api` then `scripts/export-handoff.sh`, which rebuilds/boots the SHARED PRODUCTION api. The project rules (and CLAUDE.md network topology) forbid rebuilding the prod api without explicit user action; the running prod container lacked the 18-03/18-04 llm_settings router, so a plain export would have produced a spec without llm-settings.
- **Fix:** Generated `openapi.json` from `app.openapi()` inside the ephemeral test container (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --no-deps api`), which builds fresh from current source and DOES contain all three llm-settings paths. Ran `scripts/check-uispec-endpoints.py` (39/39 OK) and `openapi-typescript@7` locally — the same downstream steps the script performs. No prod api touched.
- **Files modified:** `lovable-handoff/openapi.json`, `lovable-handoff/types/api.ts`.
- **Commit:** `c48203d`.

**Total deviations:** 1 (Rule-3 blocking, resolved via the established offline precedent). No scope creep; every UI decision the plan encoded (D-03 gate, D-08 live list, D-09 gated knobs, D-10 green corridor) is delivered.

## Issues Encountered
- The running prod `api` container image predates 18-03/18-04 (it does not serve `/api/v1/workspace/llm-settings`). This is expected — 18-04's SUMMARY explicitly notes Phase 18 was NOT deployed. The prod api+listener deploy is the first step of the Task 3 human-verify checkpoint.

## User Setup Required — BLOCKING HUMAN-VERIFY (Task 3)
Task 3 is a `checkpoint:human-verify` (gate=blocking) and is NOT auto-approvable (config `auto_advance: false`). The end-to-end runtime switch requires a real Anthropic key, a live campaign contact, and inspection of the `llm_calls` table — all human actions. Steps:
1. Deploy backend: `cd /root/apps/aimly/tg-outreach && docker compose up -d --build api && docker compose up -d --build listener` (BOTH — the answerer runs in the listener; the anthropic SDK must be in the listener image).
2. Deploy the sibling frontend (Cloudflare, per that repo's flow).
3. Settings → AI / LLM: enter a real Anthropic key → Test connection → expect green "валиден".
4. Open the model select → expect only chat-capable Claude models (no embeddings/whisper/tts).
5. Pick a Claude model, set reasoning-effort + max-tokens (try < 4000 on a reasoning model → expect the green-corridor warning), Save.
6. Message a test contact from a running campaign → confirm the AI replies.
7. Verify: `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "SELECT provider, key_source, model, created_at FROM llm_calls ORDER BY created_at DESC LIMIT 3;"` → newest row `provider='anthropic'`, `key_source='byok'`.
8. (Optional D-06) Garbage key → dialog still replies (fallback to platform OpenAI), key shows `invalid`, fallback row `key_source='fallback'`.
9. Confirm Whisper transcription + KB search still work (platform OpenAI key, D-12).

**Resume signal:** "approved", or describe issues (model list wrong, knobs not gating, no reply, wrong provider in llm_calls).

## Next Phase Readiness
- All automatable 18-05 work is committed across both repos. The phase is functionally complete pending the human UAT of the live switch (Task 3). No blockers introduced; no empirical constant or protected queue path touched; no prod service rebuilt.

---
*Phase: 18-switchable-llm-provider*
*Completed (automatable tasks): 2026-07-02*

## Self-Check: PASSED

- Created/modified files verified present on disk (openapi.json, types/api.ts, sibling settings.tsx + error-codes.ts) + this SUMMARY.
- Both task commits verified in their respective repos (c48203d in tg-outreach, 60343bb in aimly-tg-outreach).
- Automated verification: `grep llm-settings` OK in openapi.json (7) + types/api.ts (3) + settings.tsx (8); `KEY_REQUIRED` present in error-codes.ts; sibling `npx tsc --noEmit` exit 0; UI-SPEC drift 39/39.
- Task 3 (blocking human-verify) intentionally NOT executed — awaiting user deploy + UAT (auto_advance: false).
