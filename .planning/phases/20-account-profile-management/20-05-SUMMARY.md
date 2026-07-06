---
phase: 20-account-profile-management
plan: 05
subsystem: ui
tags: [react, tanstack-start, typescript, openapi, lovable, telethon, cross-repo, human-verify]

# Dependency graph
requires:
  - phase: 20-account-profile-management (plan 02)
    provides: "PATCH /profile + GET /username-check + guardrail semantics (D-08 hard-block / D-09 advisory) + onboarding cache"
  - phase: 20-account-profile-management (plan 03)
    provides: "photo upload/delete/serve + resync endpoints (D-08/D-11/D-12)"
  - phase: 20-account-profile-management (plan 04)
    provides: "POST /2fa password path + two-request recovery-email confirm flow (D-03/D-04/D-05)"
  - phase: 10-pool-visibility
    provides: "accounts.tsx FleetTable/SenderRow + restriction badges the enriched row builds on"
provides:
  - "Regenerated lovable-handoff/openapi.json + types/api.ts carrying all 7 Phase-20 paths + SenderResponse profile fields (tg_username/tg_bio/has_photo/profile_field_changed_at)"
  - "Enriched accounts screen (sibling repo): avatar photo + @username row, Изменить профиль / Обновить профиль kebab, two-section profile modal (identity + 2FA), two-step recovery-email flow, frequency guardrails"
  - "Card/tile grid (SenderCard) replacing FleetTable rows, grouped by role (Sender → Checker) then priority tier (needs-reauth → active → limited/frozen/paused)"
  - "resync now composes live first_name/last_name from Telegram get_me() into sender.name (backend gap-fix)"
  - "Human sign-off on the visual contract + the live 2FA recovery-email round-trip (the manual-only items from 20-VALIDATION)"
affects: [account-profile-frontend, accounts-bulk-edit-future]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-repo commit isolation: openapi.json/types → backend repo (Andrewbruce165/outreach-platform); accounts.tsx → sibling (AGS-Venture-Lab/aimly-tg-outreach); staged explicit files per repo, never git add -A"
    - "Offline handoff regen via app.openapi() in the test container (18-05/19-05 precedent) — no un-gated prod api rebuild for the spec dump"
    - "Two-section profile modal with per-section scoped primary CTAs (no single generic Save) — Section A identity + Section B 2FA/recovery-email"
    - "resync mirrors the PATCH /profile name-composition convention (Sender has a single name column, no separate first/last) — compose first_name+last_name → sender.name"
    - "Card grid grouped two levels (role, then priority tier) with needs-reauth always surfaced on top regardless of status"

key-files:
  created: []
  modified:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - /root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx
    - app/routers/senders.py
    - app/services/telegram.py
    - tests/test_account_profile.py

key-decisions:
  - "resync composes live first_name/last_name from get_me() into the single sender.name column (mirrors PATCH /profile) — Sender has no separate first/last columns (gap-closure round 1)"
  - "Profile modal redesigned into bordered .profile-section blocks with the Role (Sender/Checker) selector moved to its own block after the photo field and before the save footer (gap-closure round 1)"
  - "FleetTable replaced by a grouped SenderCard grid; two-level grouping (role, then needs-reauth > active > everything else) (gap-closure round 1)"
  - "Cross-repo commit isolation preserved throughout all 4 substantive commits (2 backend, 2 sibling)"
  - "Avatar-photo staleness after resync left as a known minor gap (not blocking) — user confirmed name/bio/username refresh correctly and approved"
  - "Bulk/mass account editing scoped OUT of Phase 20 by agreement — to be captured as a separate backlog item"

patterns-established:
  - "Pattern: gap-closure fixes for a human-verify plan are committed with a fix(NN-NN-gap) / feat(...) message and land on top of the task commits without reopening the plan structure"
  - "Pattern: a raised issue the user does NOT insist on before approving is documented as a known minor gap, not treated as unresolved-blocking"

requirements-completed: [PROF-09]

# Metrics
duration: multi-day (human-verify gate + 2 gap-closure rounds; 2026-07-04 → 2026-07-06)
completed: 2026-07-06
---

# Phase 20 Plan 05: Frontend and Handoff Summary

**Account-profile management surfaced end-to-end: regenerated Lovable handoff contract (all 7 Phase-20 paths + profile fields), enriched accounts screen (avatar+@username row, Изменить/Обновить профиль kebab, two-section identity+2FA modal with two-step recovery-email, frequency guardrails), then a table→card grid redesign and a resync name-composition fix across two gap-closure rounds — human-verified and approved, closing PROF-09 and Phase 20.**

## Performance

- **Duration:** multi-day (dominated by the blocking human-verify gate + two gap-closure rounds; code work per task/round was minutes)
- **Started:** 2026-07-04 (Task 1 immediately after 20-04)
- **Completed:** 2026-07-06 (user typed "approved")
- **Tasks:** 3 (2 auto + 1 blocking human-verify)
- **Files modified:** 6 across 2 repos (2 handoff files + 1 frontend file + 3 backend files from the resync gap-fix)

## Accomplishments
- **Task 1 — handoff regen (backend repo):** `lovable-handoff/openapi.json` + `types/api.ts` regenerated so the contract exposes every Phase-20 path (`/senders/{slug}/profile`, `/username-check`, `/photo` [GET/POST/DELETE], `/resync`, `/2fa`, `/2fa/recovery-email`[/confirm]) and the `SenderResponse` profile fields (`tg_username`/`tg_bio`/`has_photo`/`profile_field_changed_at`).
- **Task 2 — enriched accounts screen (sibling repo):** avatar photo (initials fallback) + `@username` row, kebab `Изменить профиль` (edit modal) / `Обновить профиль` (resync), two-section profile modal (Section A identity with one scoped `Сохранить профиль` + Section B 2FA password + two-step recovery-email each with its own scoped CTA), and the client-side frequency guardrails (username/photo <1h HARD block with live countdown; name/bio + warmup/<7-day WARN-only).
- **Task 3 — human-verify gate:** backend deployed + frontend live at `https://aimly.agsventurelab.com`; user ran the 8-step visual + live 2FA recovery-email round-trip. Reached the gate, went through two gap-closure rounds, and signed off with "approved".
- **PROF-09 closed; Phase 20 complete (5/5 plans).**

## Task Commits

Each task was committed atomically (cross-repo isolation preserved — 2 backend, 2 sibling):

1. **Task 1: Regenerate handoff openapi+types** — `1128ee8` (docs, **backend** repo `Andrewbruce165/outreach-platform`) — `lovable-handoff/openapi.json` + `types/api.ts`
2. **Task 2: Enriched row + kebab + profile modal + guardrails** — `55c5c64` (feat, **sibling** repo `AGS-Venture-Lab/aimly-tg-outreach`, pushed) — `src/routes/_authenticated/accounts.tsx`
3. **Task 3: Human verification** — no commit (blocking human-verify checkpoint; verification only)

**Gap-closure round 1** (both deployed/pushed):
4. **resync refreshes first_name/last_name from Telegram** — `ed3960b` (fix, **backend** repo) — `app/routers/senders.py`, `app/services/telegram.py`, `tests/test_account_profile.py` (added `test_resync_updates_name`)
5. **Profile modal redesign + table→card grid with role/status grouping** — `1373bf6` (feat, **sibling** repo, pushed to origin/main) — `src/routes/_authenticated/accounts.tsx` (+ aimly.css utility classes)

**Gap-closure round 2:** no commit — the only issue raised (avatar photo not visually refreshing after resync) was investigated and left as a known minor gap; the user confirmed name/bio/username DO refresh and approved without requiring it.

**Plan metadata:** this SUMMARY + STATE + ROADMAP + REQUIREMENTS docs commit (backend repo).

_Total: **4 substantive commits across 2 repos** — backend `1128ee8` (handoff regen) + `ed3960b` (resync name gap-fix); sibling `55c5c64` (initial UI) + `1373bf6` (redesign gap-fix) — plus this plan's final docs metadata commit. Round-2 produced no code change._

## Files Created/Modified
- `lovable-handoff/openapi.json` (backend) — regenerated contract, +1132 lines, carries all 7 Phase-20 paths.
- `lovable-handoff/types/api.ts` (backend) — regenerated TS types, +758 lines, includes `tg_username`/`tg_bio`/`has_photo`/`profile_field_changed_at`.
- `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx` (sibling) — enriched row + kebab + two-section modal + guardrails (Task 2), then modal redesign + card grid (gap-closure round 1).
- `app/routers/senders.py` (backend) — resync composes `first_name`/`last_name` from `get_me()` into `sender.name` (gap-closure round 1).
- `app/services/telegram.py` (backend) — `resync_sender_profile()` returns the live name parts for composition (gap-closure round 1).
- `tests/test_account_profile.py` (backend) — added `test_resync_updates_name` (gap-closure round 1).

## Decisions Made
- **resync name composition (round 1):** `resync_profile()` / `resync_sender_profile()` now compose live `first_name`/`last_name` from Telegram's `get_me()` into the single `sender.name` column. The `Sender` model has no separate first/last columns, so this mirrors the composition convention already used by `PATCH /profile`. Deployed via `docker compose up -d --build api` (migration 049 already present, no schema change).
- **Profile modal redesign (round 1):** replaced the cramped single-flow modal with bordered `.profile-section` blocks (Профиль / Безопасность 2FA); the Role (Sender/Checker) selector moved to its own block after the photo field and before the save footer (previously mixed in confusingly). Also fixed a pre-existing bug where the "Фамилия" field always initialized blank (now best-effort split from `sender.name`).
- **Accounts page redesign (round 1):** `FleetTable`/`SenderRow` replaced with a grouped `SenderCard` grid (`.tile-grid`), dead `<table>` scaffolding removed. Two-level grouping: by role (Sender first, then Checker), then by priority tier (needs-reauth always on top regardless of status → active → limited/frozen/paused). New aimly.css utilities: `.tile-grid`, `.profile-section*`, `.text-clamp-2`, `.acct-card*`. `tsc --noEmit` and eslint both exit 0.
- **Cross-repo isolation held throughout:** openapi.json/types committed only to the backend repo; accounts.tsx committed only to the sibling repo; each commit staged explicit files, never `git add -A`.

## Deviations from Plan

The plan's two auto tasks executed as written and hit the blocking human-verify gate as designed. The two gap-closure rounds below are the human-verify feedback loop the plan explicitly provisions for (`<resume-signal>`: "describe the issues … to drive gap-closure"), not unplanned scope changes.

### Gap-closure round 1 (user-driven, after first test)
- **Issue 1 (UI):** profile modal too cramped, no visual section separation, Role selector confusingly mixed with profile fields. **Fix:** modal redesigned into bordered sections with the Role selector in its own block (commit `1373bf6`, sibling).
- **Issue 2 (bug):** `Обновить профиль` (resync) didn't pull the real Telegram profile name into the name field. **Fix:** resync composes live `first_name`/`last_name` into `sender.name` (commit `ed3960b`, backend; test `test_resync_updates_name`).
- **Issue 3 (UX):** accounts page requested to be more convenient → table replaced with a card grid grouped by role then status/priority (commit `1373bf6`, sibling).

### Gap-closure round 2 (user-driven, after second test)
- **Issue raised (not fixed):** avatar photo did not visually refresh after resync. Root cause found (see Known Gaps). The user tested and confirmed name/bio/username DO refresh correctly ("вроде все ок работает"), did not require the avatar fix as a blocker, and proceeded to approve. **Left as a known minor gap — not implemented in this plan.**

---

**Total deviations:** 0 unplanned. Two gap-closure rounds handled through the plan's provisioned human-verify feedback loop (2 commits in round 1; 0 in round 2).
**Impact on plan:** Necessary corrections surfaced by live verification. No scope creep — all changes are within PROF-09's "enriched accounts surface" scope. Mass/bulk editing was explicitly kept OUT of scope (see Out of Scope).

## Issues Encountered
- **Handoff regen commit ordering:** the openapi/types regen (`1128ee8`) landed as `docs(20-05)` right after 20-04's docs commit; verified it touched only the two handoff files. No conflict with concurrent quick-task openapi regens (`c1p`/`fcq`) that landed later on separate lines.

## Known Gaps (minor, not blocking)
- **Avatar photo staleness after resync.** `AccountAvatar`'s photo-fetch `useEffect` depends on `profile_field_changed_at.photo`, a stamp resync intentionally never sets (resync is a read-only op with no cooldown), so the `<img>` goes stale after resync even though `tg_photo` bytes update server-side. Separately, three call-sites invalidate `queryKey: ["sender-photo", slug]` but no `useQuery` anywhere uses that key, making those invalidations dead code. **Raised in round 2, NOT fixed** — the user confirmed name/bio/username refresh correctly and approved without requiring it. Captured here for a future minor UI touch-up (e.g. bump a photo cache-buster on resync success, or wire the dead invalidation key to a real query).

## Out of Scope (by agreement, not deferred work)
- **Mass / bulk account editing** (select multiple accounts, batch-change photo/name/description/username) — requested by the user during round 2 and explicitly scoped OUT of Phase 20 by agreement. It is NOT a Phase-20 gap or TODO; it will be captured separately as a new backlog/roadmap item (that capture is a separate task, not part of this finalization).

## User Setup Required
None — no external service configuration required. Backend (resync gap-fix) already deployed via `docker compose up -d --build api`; sibling frontend commits pushed to `origin/main`. Migration 049 (from 20-01) was already applied; no new migration in this plan.

## Next Phase Readiness
- **Phase 20 complete (5/5 plans); PROF-01..09 all closed.** The account-profile management surface is live end-to-end and human-verified, including the live 2FA recovery-email round-trip that automated tests cannot cover.
- Handoff contract (openapi.json + types) is current for the Phase-20 endpoints — future Lovable regens should build on it.
- Two follow-on items exist outside this plan: (1) the avatar-staleness minor UI gap above, and (2) the out-of-scope bulk-edit feature request to be captured as a new backlog item.

---
*Phase: 20-account-profile-management*
*Completed: 2026-07-06*

## Self-Check: PASSED
- Handoff files present in backend repo: `lovable-handoff/openapi.json`, `lovable-handoff/types/api.ts` (commit `1128ee8`).
- Frontend file present in sibling repo: `src/routes/_authenticated/accounts.tsx` (commits `55c5c64`, `1373bf6`).
- Backend gap-fix files present: `app/routers/senders.py`, `app/services/telegram.py`, `tests/test_account_profile.py` (commit `ed3960b`).
- All 4 commit hashes verified to exist (`1128ee8`, `ed3960b` in backend; `55c5c64`, `1373bf6` in sibling).
