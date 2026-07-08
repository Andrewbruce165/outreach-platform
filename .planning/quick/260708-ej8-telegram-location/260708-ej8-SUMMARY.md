---
phase: quick-260708-ej8-telegram-location
plan: 01
subsystem: senders
tags: [senders, location, dial-code, frontend, openapi]
requires: []
provides:
  - "SenderResponse.location (derived country/region from phone dial code)"
  - "app/utils/location.phone_location()"
affects:
  - "GET /api/v1/senders"
  - "GET /api/v1/senders/{slug}"
  - "frontend account card + profile modal (sibling repo)"
tech-stack:
  added: []
  patterns:
    - "pure-Python dial-code lookup (no phonenumbers dependency, D-01)"
    - "derived/computed response field (no DB column, no migration)"
    - "longest-prefix match with +7 Russia/Kazakhstan sub-rule"
key-files:
  created:
    - app/utils/location.py
    - tests/test_location.py
  modified:
    - app/schemas/__init__.py
    - app/routers/senders.py
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - "(sibling) src/routes/_authenticated/accounts.tsx"
    - "(sibling) docs/openapi.json"
    - "(sibling) src/types-openapi.json"
    - "(sibling) src/types/api.ts"
decisions:
  - "D-01: custom dial-code table, NOT the phonenumbers library (matches phone.py policy, CLAUDE.md)"
  - "Unknown code -> stable 'Unknown' string; None reserved strictly for malformed/empty phone"
  - "+7 sub-rule: national digit 6/7 -> Kazakhstan, else Russia (approximate heuristic)"
  - "location is computed at response time (no DB column, no migration)"
metrics:
  duration: ~7min
  completed: 2026-07-08
---

# Quick Task 260708-ej8: Telegram Account Location Summary

Added a derived `location` field to `SenderResponse` — country/region computed on the fly from the account phone's dial code via a small pure-Python lookup table — and surfaced it as a `Location:` line in the frontend account card and profile modal. No new dependency, no DB migration.

## What Was Built

- **`app/utils/location.py`** — `phone_location(phone)` does longest-prefix matching over ~50 dial codes (CIS bias + broad international). Ambiguous `+7` resolves via a sub-rule (national digit 6/7 → Kazakhstan, else Russia). `+1` → "US/Canada" (NANP). Unmatched code → `"Unknown"`; malformed/empty/no-leading-`+` → `None` (never crashes).
- **`tests/test_location.py`** — 29 parametrized cases: RU/KZ (+7 both branches), UA, BY, US/Canada, UK, CIS neighbours, broad international, longest-prefix correctness (998>9, 380>3), unknown-code fallback, malformed/empty/None → None.
- **`SenderResponse.location: Optional[str]`** — wired via `_sender_to_response()` (`location=phone_location(sender.phone)`), the single builder feeding both `GET /senders` and `GET /senders/{slug}`.
- **Handoff regen** — `lovable-handoff/openapi.json` + `types/api.ts` regenerated via `export-handoff.sh` after rebuilding the api container. UI-SPEC drift check passed (39 endpoints).
- **Frontend (sibling repo)** — conditional `Location: {sender.location}` line under the phone in the account card and in the profile detail modal (hidden when null); `location` field added to `src/types/api.ts`, `docs/openapi.json`, `src/types-openapi.json` (targeted add, no full regen to avoid clobbering pre-existing per-file drift).

## Verification

- `pytest tests/test_location.py` → 29 passed (via test-overlay).
- `pytest tests/test_location.py tests/test_phone_normalization.py` → 57 passed.
- `jq .components.schemas.SenderResponse.properties.location lovable-handoff/openapi.json` → non-null nullable-string schema.
- Live api `/openapi.json` served from the rebuilt container exposes `SenderResponse.location`.
- Sibling `tsc --noEmit` → exit 0, no errors.

## Deviations from Plan

None — plan executed exactly as written. One tooling adjustment: `bunx` is unavailable in this environment, so the sibling type-check ran via `./node_modules/.bin/tsc --noEmit` (equivalent). A targeted text edit replaced the initial `jq` insertion into `src/types-openapi.json` because `jq` reformatted the file's compact JSON (63-line churn); the text edit keeps the diff to +11 clean lines.

## Notes

- The sibling repo had an unrelated `src/routes/_authenticated/inbox.tsx` modification appear mid-session (external/parallel process). It was left untouched and NOT staged — only the 4 task files were committed there.
- No `phonenumbers` dependency, no DB migration (location is purely computed).

## Commits

Backend (tg-outreach — `Andrewbruce165/outreach-platform`):
- `944cf3f` test(260708-ej8): add failing tests for phone_location dial-code lookup
- `eba5913` feat(260708-ej8): add phone_location dial-code lookup module
- `e3cfd75` feat(260708-ej8): expose derived location on SenderResponse
- `002d861` chore(260708-ej8): regenerate handoff openapi with SenderResponse.location

Frontend (sibling — `AGS-Venture-Lab/aimly-tg-outreach`):
- `c627024` feat(accounts): show account Location line derived from phone

## Self-Check: PASSED

- app/utils/location.py — FOUND
- tests/test_location.py — FOUND
- lovable-handoff/openapi.json SenderResponse.location — FOUND
- commits 944cf3f / eba5913 / e3cfd75 / 002d861 — FOUND in main
- sibling commit c627024 — FOUND in aimly-tg-outreach
