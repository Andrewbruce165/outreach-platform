# Screen build order (11 screens)

Build screens sequentially. Smoke-test each against staging backend before starting the next.

## 1. Auth (UI-SPEC §5.1) — `/login`, `/auth/callback`
Endpoints: Supabase SDK `signInWithOtp`, then POST `/api/v1/auth/me`.
Build prompt: "Implement single-card magic-link login per UI-SPEC §5.1 — input + 'Send magic link' button + post-submit checkmark state. On /auth/callback, POST /api/v1/auth/me to validate JWT and resolve workspace_id, then redirect based on senders.length."

## 2. Settings (§5.11) — `/settings`
Endpoints: GET/PATCH /api/v1/workspace, GET/POST/DELETE /api/v1/workspace/api-keys.
Build prompt: "Implement 5 tabs (Workspace / API keys / Members / Profile / Appearance) per UI-SPEC §5.11. Members tab is v2 placeholder. Appearance theme is Light only in v1 (Dark stubbed)."

## 3. Onboarding (§5.2) — `/onboarding`
Endpoints: POST /onboarding/{start, verify-code, verify-2fa, qr-start}, GET /onboarding/qr-status/{session_id}.
Build prompt: "Implement 3-step wizard (phone → code → success) plus QR tab. Use exact path names from reconciliation.md."

## 4. TG accounts (§5.10) — `/accounts`
Endpoints: GET /senders, /senders/{slug}, PATCH /senders/{slug}, POST /senders/{slug}/{pause,resume,assign-proxy}, DELETE /senders/{slug}, plus onboarding endpoints from step 3.
Build prompt: "Fleet table + 4-step onboarding modal per UI-SPEC §5.10. Use {slug} not {id}. Show health donut, corridor bar, warm-up sparkline."

## 5. Contacts (§5.9) — `/contacts`
Endpoints: GET /folders, POST/PATCH/DELETE /folders/{id}, GET /contacts?folder_id={id}, POST /contacts/import/preview, POST /contacts/import, POST /contacts/recheck.
Build prompt: "2-pane (folders sidebar + folder detail) per UI-SPEC §5.9. CSV import is 4-stage modal (upload → mapping → importing → done)."

## 6. Agents (§5.8) — `/agents`, `/agents/{id}`
Endpoints: GET/POST/PATCH/DELETE /api/v1/agents, POST /api/v1/agents/{id}/duplicate.
Build prompt: "Agents list (auto-fill grid) + 4-tab editor (Context / Voice / FAQ-Knowledge / Safety) per UI-SPEC §5.8. Use all 12 v2 columns from types/api.ts AgentResponse."

## 7. Campaigns list (§5.4) — `/campaigns`
Endpoints: GET /api/v1/campaigns, POST /campaigns/{id}/{start,pause,resume,stop,duplicate}, DELETE /campaigns/{id}.
Build prompt: "Tabs + table with inline funnel per UI-SPEC §5.4. Row actions menu uses /stop not /finish."

## 8. Campaign builder (§5.5) — `/campaigns/new`
Endpoints: POST /api/v1/campaigns (draft on Continue), PATCH /campaigns/{id} (subsequent steps), POST /campaigns/{id}/start (Launch), POST /api/v1/campaigns/auto-fill (AI co-pilot button — v1 stub).
Build prompt: "7-step wizard per UI-SPEC §5.5 with launch overlay. tools field shape: `[{id, name, description, parameters: [{name, type, description, required}]}]` (see types/api.ts ToolSpec — webhook_url is Optional/deprecated)."

## 9. Campaign detail (§5.6) — `/campaigns/{id}`
Endpoints: GET /campaigns/{id}, GET /api/v1/analytics/campaigns/{id}, GET /api/v1/analytics/funnel?scope=campaign&id={id}, GET /api/v1/analytics/llm?scope=campaign&id={id}, GET /api/v1/conversations?campaign_id={id}, GET /api/v1/conversations/{id}/llm-calls, POST /campaigns/{id}/{pause,resume,stop,duplicate}, PATCH /campaigns/{id}.
Build prompt: "5 tabs (Overview / Conversations / Senders / LLM trace / Settings). Settings is 2-column (overrides ↔ integrations) per UI-SPEC §5.6."

## 10. Inbox (§5.7) — `/inbox`, `/inbox/{conversation_id}` (UI-INBX-01 — Phase 5 already shipped backend)
Endpoints: GET /api/v1/conversations (with filters), GET /conversations/{id}, GET /conversations/{id}/messages, GET /conversations/{id}/llm-calls, POST /conversations/{id}/{enable-ai,disable-ai,send}, PATCH /conversations/{id}, DELETE /conversations/{id}.
Build prompt: "3-pane (list / thread / thought trace) per UI-SPEC §5.7. Trace toggleable. 10s poll cadence. D-04 auto-takeover warning on composer. All endpoints shipped in Phase 5 — no backend gap; the 3-pane build is a pure frontend wiring task against the live `/conversations` + `/disable-ai` + `/send` surface."

## 11. Dashboard (§5.3) — `/`
Endpoints: GET /api/v1/analytics/workspace, GET /api/v1/analytics/funnel?scope=workspace, GET /api/v1/senders, GET /api/v1/conversations?status=active&limit=20.
Build prompt: "KPI row + full-width Sankey funnel + AccountHealthCard + CampaignPerformance + Activity feed per UI-SPEC §5.3. 30s auto-refresh; pauses on window blur. Funnel uses 5-stage data from /analytics/funnel."
