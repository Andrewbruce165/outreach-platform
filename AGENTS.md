# AGENTS.md — Lovable Build Instructions for aimly

This is a Lovable React/Vite/Tailwind/shadcn project. It talks to a Python/FastAPI backend at `${BACKEND_URL}` via JWT-bearer auth (Supabase magic link).

## Setup prerequisite (Pitfall 3)

Pin Supabase JWT algorithm to HS256:

1. Open Supabase Dashboard → Settings → API → JWT Settings
2. Set Algorithm = HS256
3. Copy JWT Secret into backend `SUPABASE_JWT_SECRET` env var

The backend `app/utils/auth.py` is HS256-only in v1 (see file's Pitfall 3 comment). Asymmetric / JWKS support is deferred to v2.

## Rules

1. Never invent backend types. Import all `/api/v1/*` request/response types from `@/types/api.ts` (generated from backend OpenAPI). If a type is missing, the build is broken — flag it, do not guess.
2. All forms use react-hook-form + zod. Zod schemas in `src/lib/validators/*.ts`.
3. All data fetching uses TanStack Query. Cache keys = `['<resource>', ...params]`. `refetchInterval` only where UI-SPEC §5 says so (inbox = 10s, dashboard = 30s).
4. Design tokens come from `src/styles/aimly.css` (ingest `design-source/project/styles.css` verbatim). Do not redefine tokens.
5. No new motion libraries. CSS keyframes (`pulse-ring`, `shimmer`) only. Always wrap in `@media (prefers-reduced-motion: reduce) { animation: none }`.
6. Rate limits 4/20/150 are not configurable in v1 (Pitfall 9). Never offer a UI control to change them. Hard backend constraint per CLAUDE.md.
7. AI accent `--ai-purple #8774e1` is reserved. Only on `<live-dot>`, `<ai-shimmer>`, thought-trace, AI co-pilot panel, launch overlay, AI suggestion chips.
8. Brand "aimly" is lowercase, no exclamation marks.
9. No `console.log` in committed code. Use `import.meta.env.DEV` guards if needed.
10. All telemetry events go through `track(event, props)` from `src/lib/telemetry.ts` — POST to `/api/v1/telemetry/events`. Use `navigator.sendBeacon` on `pagehide` for reliability (Pitfall 4).

## Funnel "Engaged" stage definition (Pitfall 5)

Backend definition (LOCKED in code): `engaged = COUNT(DISTINCT conversation_id WHERE inbound_message_count >= 2 AND status NOT IN ('lead','handoff','finished','bot_ignored'))`. Frontend renders the value the backend returns — do NOT recompute.

## Endpoint path notes (reconciled from UI-SPEC)

See `reconciliation.md` for the full table. Key deltas vs UI-SPEC §5:

- Onboarding QR: `POST /api/v1/onboarding/qr-start` (not `/qr`), poll `GET /qr-status/{session_id}` (not `/sessions/{id}`)
- Contacts CSV: `POST /api/v1/contacts/import/preview` then `POST /api/v1/contacts/import` (not `/csv-preview` / `/csv-apply`)
- Contact recheck: `POST /api/v1/contacts/recheck` (not `/check-contacts`)
- Senders: `{slug}` path param everywhere (not `{id}`)
- Folder children: `GET /api/v1/contacts?folder_id={id}` (not `/folders/{id}/contacts`)

## Inbox build (UI-INBX-01 — 3-pane, Phase 5 shipped — no backend gap)

The inbox screen (UI-SPEC §5.7, screen-build-order.md step 10) builds the 3-pane layout (list / thread / thought trace) entirely against endpoints shipped in Phase 5. No new backend work was required for the inbox in Phase 05.1 — `/api/v1/conversations`, `/api/v1/conversations/{id}/disable-ai`, `/api/v1/conversations/{id}/send`, `/api/v1/conversations/{id}/llm-calls` are all live. Poll list at 10s, thread+trace at 15s, messages at 10s.

## Build order

See `screen-build-order.md`. Build Auth (§5.1) first. Then Settings (so Sign Out works), Onboarding (§5.2), Accounts (§5.10), Contacts (§5.9), Agents (§5.8), Campaigns list (§5.4), Campaign builder (§5.5), Campaign detail (§5.6), Inbox (§5.7), Dashboard (§5.3 last — depends on all other data).

## Out of scope (do not build)

- Push notifications
- Mobile-native shell
- WebSocket inbox (10s poll is fine — UI-SPEC §0)
- Multi-user invitations (v2)
- Dark theme (v2 — leave toggle stub, only Light wired)
- ⌘K command palette (v2 — disabled stub with tooltip)
- HMAC webhook signing
- LLM-driven campaign auto-fill (v1 stub returns seeded defaults)
- GDPR data export portal (v1.1 — mailto link placeholder OK)

## Per-screen verification

Before considering any screen done:

- Lighthouse accessibility >= 90
- Reduced-motion CSS guard present
- All icon-only buttons have `aria-label`
- Empty states have icon + heading + body + CTA (4-element formula UI-SPEC §6.5)
- Error envelope `{code, message}` mapped to `error-codes.md` copy
- 401 redirects to `/login` with toast "Your session expired. Sign in again."
- Telemetry events from `telemetry-events.md` fire on documented triggers
