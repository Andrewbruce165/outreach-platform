---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 03
subsystem: telegram
tags: [telethon, send_file, auto-media, blob, file-attachment]

# Dependency graph
requires:
  - phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
    provides: send_file contact-resolve ladder + error taxonomy (USER_IS_BLOCKED, PRIVACY_RESTRICTED)
provides:
  - "send_file blob source (file_bytes) that skips the httpx URL download"
  - "force_document passthrough enabling Telethon auto-media (photo->photo) via original file extension"
  - "1024-caption overflow branch reused verbatim for the blob path"
  - "no-source guard returning structured SEND_FAILED"
affects: [24-06 worker blob+auto-media call site]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Optional dual source (file_bytes | file_url) with blob-wins precedence, network skipped on blob path"
    - "Default-preserving signature extension (force_document=True, file_url optional) → existing callers byte-identical"

key-files:
  created:
    - tests/test_send_file_blob.py
  modified:
    - app/services/telegram.py

key-decisions:
  - "D-08: file_bytes writes straight to a NamedTemporaryFile; the httpx URL download path is preserved, not deleted"
  - "D-06: force_document defaults to True (today's behavior); the load-bearing temp-file suffix (os.path.splitext(file_name)[1]) drives auto-media when force_document=False"
  - "D-07: caption >1024 chars reuses the existing overflow branch — file without caption + full text as a follow-up message"
  - "Backwards-compat: current queue.py worker call (file_url=..., no new args) is byte-identical to today"

patterns-established:
  - "Blob-vs-URL source branch keeps the shared temp-suffix write so both paths feed identical auto-media logic"

requirements-completed: [D-06, D-07, D-08]

# Metrics
duration: ~15min
completed: 2026-07-07
---

# Phase 24 Plan 03: send_file blob source + auto-media Summary

**`send_file` now accepts a DB-blob source and a `force_document` flag so the campaign opener can deliver a photo as a photo — with the URL path and today's document-default behavior fully preserved.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-07T15:10Z
- **Completed:** 2026-07-07T15:18Z
- **Tasks:** 2
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments
- Extended `TelegramService.send_file` with `file_bytes` (D-08) and `force_document` (D-06), both defaulted so any existing caller is byte-identical to today.
- Blob path skips the network entirely; the shared temp-file suffix write is what makes Telethon auto-media work when `force_document=False`.
- 1024-char caption overflow (D-07) and the full error taxonomy are untouched.

## Task Commits

Each task was committed atomically (TDD):

1. **Task 1: RED — Telethon-mocked send_file tests** - `24c307d` (test)
2. **Task 2: GREEN — implement file_bytes + force_document passthrough** - `5641755` (feat)

## Files Created/Modified
- `tests/test_send_file_blob.py` - 5 Telethon-mocked unit tests: blob auto-media (suffix + force_document=False), URL default force_document=True, 1024 overflow follow-up, no-source guard, not-registered path.
- `app/services/telegram.py` - `send_file` signature (`file_url` now optional, `file_bytes` + `force_document` added), no-source guard, blob-vs-URL source branch, `force_document=force_document` passthrough (hardcoded `True` removed).

## Verification
- `pytest tests/test_send_file_blob.py -x` → 5 passed.
- `pytest -k send_file -q` → 5 passed, 0 regressions (no other send_file-named tests exist).
- Existing caller `app/services/queue.py:882` uses keyword args with no `file_bytes`/`force_document` → confirmed byte-identical to today.

## Deviations from Plan
**Base correction (not a plan deviation, but recorded):** the worktree was spawned from a stale base (commit 92bd54b, right after Phase 19) that predated the Phase 21 `telegram.py` changes and the Phase 24 planning directory. The worktree branch was reset onto the current `main` (ddbb09e) at startup so the edit lands on the true current `send_file` (line 910) and cherry-picks cleanly. No plan logic changed.

Otherwise: None — plan executed exactly as written.

## Notes for Downstream (24-06)
The worker call site to wire is `app/services/queue.py:882`. Call
`send_file(file_bytes=<blob>, file_name=att.file_name, force_document=False, ...)`
to send an attachment as auto-media.

## Self-Check: PASSED
- `app/services/telegram.py` — FOUND (file_bytes/force_document params + passthrough verified via grep)
- `tests/test_send_file_blob.py` — FOUND
- Commit `24c307d` — FOUND
- Commit `5641755` — FOUND
