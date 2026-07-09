# UI-SPEC path reconciliation

UI-SPEC §5 was authored by reading the design screens; backend has shipped router paths used by Phase 5 tests (94 tests). Where UI-SPEC and backend disagree, we reconcile **toward backend** (it is the shipped truth; renaming would break tests).

The frontend uses generated `types/api.ts` for shape contracts, so screen behavior is unchanged — only path strings reconciled.

## §5.2 Onboarding — QR endpoints

- UI-SPEC said: `POST /api/v1/onboarding/qr` + poll `GET /onboarding/sessions/{id}`
- Backend ships: `POST /api/v1/onboarding/qr-start` + poll `GET /onboarding/qr-status/{session_id}`
- Reason: Phase 2 shipped under qr-start/qr-status; renaming breaks 94 tests
- Action: UI-SPEC §5.2 patched (see UI-SPEC.md §13 Changelog 2026-05-23)

## §5.9 Contacts — CSV import preview/apply

- UI-SPEC said: `POST /api/v1/contacts/csv-preview` + `POST /api/v1/contacts/csv-apply`
- Backend ships: `POST /api/v1/contacts/import/preview` + `POST /api/v1/contacts/import`
- Reason: Phase 2 Plan 04 shipped under import/preview + import
- Action: UI-SPEC §5.9 patched

## §5.9 Contacts — recheck

- UI-SPEC said: `POST /api/v1/check-contacts`
- Backend ships: `POST /api/v1/contacts/recheck`
- Reason: Recheck endpoint is part of contacts router, not a top-level resource
- Action: UI-SPEC §5.9 patched

## §5.9 Contacts — folder children

- UI-SPEC said: `GET /api/v1/folders/{id}/contacts`
- Backend ships: `GET /api/v1/contacts?folder_id={id}&limit=&offset=`
- Reason: Backend treats contacts as a top-level collection with folder filter
- Action: UI-SPEC §5.9 patched

## §5.10 Senders — path param

- UI-SPEC said: `{id}` (UUID)
- Backend ships: `{slug}` (human-readable)
- Reason: Slug is the natural identifier for senders (per onboarding flow); UUIDs are internal
- Action: UI-SPEC §5.10 patched — all `{id}` in §5.10 sender paths replaced with `{slug}`

## §5.10 Senders — re-auth has no dedicated endpoint

- UI-SPEC initially listed: `POST /api/v1/senders/{slug}/reauth`
- Backend ships: no `/reauth` endpoint
- Reason: re-auth (session revoked) reuses the existing onboarding flow — phone → SMS code → success — against the same slug. Backend writes the new encrypted session to the existing sender row via `POST /onboarding/verify-code`. A dedicated `/reauth` endpoint would duplicate logic for zero behavior gain.
- Action: UI-SPEC §5.10 Backend cell patched (2026-05-23). The frontend "Re-auth" row action opens `<OnboardingFlow>` pre-filled with the sender's phone; success path identical to first-time onboarding.

## UI-CONT-01 closure note

The CSV import preview/apply + recheck reconciliation above (three §5.9 path patches) closes requirement UI-CONT-01 — the contacts screen now builds against `/contacts/import/preview`, `/contacts/import`, `/contacts/recheck` (the shipped backend paths), so Lovable can wire the 4-stage CSV import modal with confidence that types and URLs match.

## UI-INBX-01 closure note

The inbox screen (UI-SPEC §5.7) requires no backend reconciliation — Phase 5 already shipped `/api/v1/conversations`, `/api/v1/conversations/{id}/disable-ai`, `/api/v1/conversations/{id}/send`, `/api/v1/conversations/{id}/llm-calls`, and the surrounding lifecycle (enable-ai, PATCH, DELETE, messages). UI-INBX-01 is closed by virtue of the 3-pane inbox build (screen-build-order.md step 10) targeting the live Phase 5 endpoints directly, plus the AGENTS.md "Inbox build" section that documents the lack of backend gap.
