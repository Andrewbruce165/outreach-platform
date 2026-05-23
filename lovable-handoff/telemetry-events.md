# Telemetry events (17)

All events go through `track(event, props)` from `src/lib/telemetry.ts` → POST `/api/v1/telemetry/events`. Use `navigator.sendBeacon` on `pagehide` (Pitfall 4).

## magic_link_requested
Fired on: login form submit (UI-SPEC §5.1 "Send magic link" CTA)
Props: `{ method: 'magic_link' }`

## signup_completed
Fired on: first successful /auth/callback (workspace lazy-created)
Props: `{ workspace_id: string, is_first_session: true }`
Use: T0 for Core Value KPI

## sender_added
Fired on: onboarding step 4 success
Props: `{ sender_id: string, method: 'phone' | 'qr' }`

## contacts_imported
Fired on: CSV import stage 4 (done)
Props: `{ folder_id: string, created: number, updated: number, skipped: number }`

## csv_import_completed
Alias of contacts_imported (kept for explicit naming per UI-SPEC §9)
Props: same as contacts_imported

## agent_created
Fired on: POST /api/v1/agents 201
Props: `{ agent_id: string }`

## campaign_created
Fired on: POST /api/v1/campaigns 201 (final step)
Props: `{ campaign_id: string, sender_count: number, has_signals: boolean }`

## campaign_launched
Fired on: POST /api/v1/campaigns/{id}/start 200
Props: `{ campaign_id, agent_id, folder_id, sender_count, custom_tool_count }`
Use: T1 for Core Value KPI

## campaign_paused
Fired on: POST /api/v1/campaigns/{id}/pause 200
Props: `{ campaign_id }`

## campaign_resumed
Fired on: POST /api/v1/campaigns/{id}/resume 200
Props: `{ campaign_id }`

## conversation_taken_over_by_human
Fired on: POST /disable-ai OR first manual /send OR safety auto-pause match
Props: `{ conversation_id, trigger: 'manual_toggle' | 'manual_send' | 'auto_pause' }`

## llm_trace_opened
Fired on: "Show LLM trace" button click (UI-SPEC §5.7 Inbox topbar)
Props: `{ conversation_id }`

## workspace_api_key_created
Fired on: POST /api/v1/workspace/api-keys 201
Props: `{ key_id, name }`

## settings_changed
Fired on: PATCH /api/v1/workspace OR theme toggle (debounced 5s)
Props: `{ tab: 'workspace' | 'profile' | 'appearance' }`

## agent_voice_changed
Fired on: PATCH on Voice tab Save (UI-SPEC §5.8)
Props: `{ agent_id, voice_baseline: 'Professional' | 'Friendly' | 'Playful' }`

## custom_tool_added
Fired on: ToolEditorModal Save (new tool added to campaign)
Props: `{ campaign_id, tool_name, parameter_count }`

## dashboard_viewed
Fired on: Dashboard mount (debounced 1/session — `sessionStorage` guard)
Props: `{}`

# Core Value KPI computation

`time_to_first_campaign_seconds = MIN(campaign_launched.server_ts) - MIN(signup_completed.server_ts)` per workspace.

Target: < 600 seconds (10 minutes) for 80% of new users. Query via `GET /api/v1/telemetry/core-value`.
