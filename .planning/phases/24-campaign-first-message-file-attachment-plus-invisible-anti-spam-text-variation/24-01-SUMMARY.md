---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 01
subsystem: api
tags: [variation, anti-spam, zero-width, unicode, stdlib, pure-function, telegram]

# Dependency graph
requires:
  - phase: none
    provides: pure Wave-1 primitive with zero dependencies
provides:
  - "app/services/variation.py — pure stdlib vary(text)->str + strip_invisible(s)->str"
  - "byte-unique-per-send invisible text variation defeating naive byte-exact bulk-dedup (D-11 defense-in-depth)"
  - "invisibility invariant strip_invisible(vary(x)) == x for Latin/Cyrillic/emoji/URL/@mention/markdown"
affects: [24-06 worker-variation-and-blob-delivery, queue.py send path]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure stateless variation function (no DB/I/O/network) — worker-cheap, unit-testable in isolation"
    - "Codepoint hygiene: every invisible codepoint expressed via chr(0xXXXX), never raw glyphs or backslash-u"
    - "Conservative TLD-allowlist bare-domain protection so plain words with dots (т.е.) are not over-protected"

key-files:
  created:
    - app/services/variation.py
    - tests/test_variation.py
  modified: []

key-decisions:
  - "Insertion alphabet {U+200B,U+200C,U+2060}; space-jitter {U+00A0,U+202F}; U+200D (emoji joiner) never emitted (D-09)"
  - "Insert only between two adjacent letters + a protected-index set (URL/bare-domain/email/@/#) — inherently skips markdown/digits/emoji (D-09 safe-spans)"
  - "Density ~10-20% of eligible gaps with a hard cap of 20 insertions per message (D-15)"
  - "strip_invisible is the exact inverse but deliberately preserves U+200D so pre-existing emoji joiners survive"

patterns-established:
  - "Pattern: variation.vary() called on a LOCAL COPY at send time only — never written back to DB (Pitfall 3, enforced by consumer 24-06)"
  - "Pattern: pure-module tests import inside the test body so collection stays green during RED"

requirements-completed: [D-09, D-10, D-11, D-15, D-16]

# Metrics
duration: 13min
completed: 2026-07-07
---

# Phase 24 Plan 01: Variation Pure Module Summary

**Pure stdlib `vary(text)`/`strip_invisible(s)` that makes each Telegram campaign opener byte-unique via zero-width codepoints while staying visually identical — never emitting the U+200D emoji joiner and never splicing inside URLs, bare domains, emails, @mentions, #hashtags, digit runs or emoji graphemes.**

## Performance

- **Duration:** 13 min
- **Started:** 2026-07-07T15:06:46Z
- **Completed:** 2026-07-07T15:20:03Z
- **Tasks:** 2 (TDD: RED → GREEN)
- **Files modified:** 2 (both created)

## Accomplishments
- `app/services/variation.py`: pure `vary()` + `strip_invisible()`, stdlib-only (`re`, `random`), ~135 lines, zero third-party deps.
- Zero-width insertion between adjacent letters only, protected-span index set for URL/bare-domain/email/@/# (conservative TLD allowlist), density ~10–20% capped at 20 (D-15), occasional NBSP/narrow-NBSP space-jitter (D-10).
- U+200D never emitted; `strip_invisible` is the exact inverse and preserves emoji joiners.
- 16 pure-function tests (invisibility roundtrip, byte-uniqueness, safe-spans incl. bare domain `agsventurelab.com`, no-ZWJ, density cap, emoji-family integrity) — all GREEN.

## Task Commits

Each task was committed atomically (TDD):

1. **Task 1: RED — invisibility/uniqueness/safe-span/density tests** - `18693c1` (test)
2. **Task 2: GREEN — implement vary() + strip_invisible()** - `ef5a8b9` (feat)

_Executed in an isolated worktree branch `worktree-agent-a8241f7f75c3ce4d7`; commits carried to main by the orchestrator's wave merge._

## Files Created/Modified
- `app/services/variation.py` - pure `vary()`/`strip_invisible()` invisible anti-spam variation primitive.
- `tests/test_variation.py` - 16 pure-function tests (no DB fixtures), codepoints via `chr(0xXXXX)` only.

## Decisions Made
None beyond the plan — followed the specified codepoint set, insertion rules, density corridor and inverse-strip contract exactly.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Test-overlay container-name clash: the worktree's compose project tried to recreate the prod-named `outreach-platform-db` container. Resolved by starting only the ephemeral `db-test` and running `run --rm --no-deps api pytest`.
- Worktree lacks a `.env` for compose interpolation (empty `TELEGRAM_API_ID` → pydantic `ValidationError` at import). Resolved by passing `--env-file /root/apps/aimly/tg-outreach/.env`. Neither issue is a code defect.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `from app.services.variation import vary` is ready for Plan 24-06 (worker send path). Consumer MUST vary a local copy only and never persist the varied string (Pitfall 3).
- No blockers.

## Self-Check: PASSED
- `app/services/variation.py` exists on branch (commit `ef5a8b9`).
- `tests/test_variation.py` exists on branch (commit `18693c1`).
- Both commits present in `git log` of `worktree-agent-a8241f7f75c3ce4d7`.
- 16/16 tests GREEN via test-overlay; zero raw invisible glyphs in either file; U+200D absent from implementation.

---
*Phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation*
*Completed: 2026-07-07*
