---
phase: quick-260710-afg
plan: 01
subsystem: frontend-inbox
tags: [inbox, senders, ui, traffic-light]
requires: []
provides:
  - "deriveSenderHealth(sender) shared helper: 3-state (green/yellow/red) health collapse"
  - "Inbox chat-list sender line ('via @slug — phone') colored by sender account health"
affects:
  - frontend/src/routes/_authenticated/inbox.tsx
tech-stack:
  added: []
  patterns: ["3-state traffic-light collapse mirroring accounts.tsx status/auth semantics"]
key-files:
  created:
    - frontend/src/lib/sender-health.ts
  modified:
    - frontend/src/routes/_authenticated/inbox.tsx
decisions:
  - "No backend/migration change — inbox already fetches the workspace senders list and passes it to ConvList; sender resolved client-side by slug."
  - "Re-auth (auth_status !== 'ok') takes precedence over status, exactly like accounts.tsx priorityTier."
metrics:
  duration: ~3min
  completed: 2026-07-10
---

# Quick Task 260710-afg: Inbox Chat-List Color-Coded Sender Status Summary

Color-coded the "via @nickname — phone" sender line in the inbox conversation list by the sender account's health (green = active, yellow = spam-limited/paused/warmup, red = re-auth-needed/frozen/error), reusing the exact `auth_status`/`status` semantics the TG Accounts page already uses, via a new shared `deriveSenderHealth` helper. No backend, schema, or migration change.

## What Was Built

### Task 1 — `frontend/src/lib/sender-health.ts` (new)
`deriveSenderHealth(sender): SenderHealthInfo` collapses a `SenderResponse` into a 3-state traffic light:
- **red** — `auth_status !== "ok"` (session expired, takes precedence) OR `status` `frozen`/`error`
- **yellow** — `status` `limited` (spam-limited), `paused` (отлёжка), or `warmup`
- **green** — `status` `active`
- unknown/future status → yellow fallback (never crashes)

Returns `{ health, color (CSS var --success/--warning/--danger), label (RU tooltip) }`. Exports `deriveSenderHealth` + `SenderHealth`. Single source for the 3-color collapse, reusable by other surfaces.

### Task 2 — `frontend/src/routes/_authenticated/inbox.tsx`
- Imported `deriveSenderHealth`.
- In `ConvList`, added a memoized `senderBySlug` `Map` (built once per `senders` change) for O(1) per-row lookup.
- Replaced the muted sender-line `<div>` with a health-colored version: looks the sender up by `c.sender_slug`, colors the line by `deriveSenderHealth(sender).color`, sets `fontWeight` 600 for non-green (attention) states, adds a `title` tooltip, and appends the sender phone (`via @slug — phone`). Unknown slug falls back to the prior muted style and slug-only text. No other part of the row touched.

## Verification

- `bun run build` (Docker `oven/bun:1` stage — host has no bun) succeeded: `✓ built in 17.60s`, prerender OK, no new TypeScript/Vite errors.
- Only the two intended frontend files changed (`git status`), no backend/schema/migration touched.
- Visual cross-check against TG Accounts page is a manual step to run after `./deploy-frontend.sh`.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Commits

- `d136485` feat(260710-afg): add deriveSenderHealth 3-state traffic-light helper
- `5f61de0` feat(260710-afg): color inbox chat-list sender line by account health

## Self-Check: PASSED

- FOUND: frontend/src/lib/sender-health.ts
- FOUND: frontend/src/routes/_authenticated/inbox.tsx
- FOUND: .planning/.../260710-afg-SUMMARY.md
- FOUND commits: d136485, 5f61de0
