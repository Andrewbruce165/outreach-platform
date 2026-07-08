---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 02
subsystem: api
tags: [telethon, telegram, inbox, edit-message, delete-messages, send-file, download-media, error-mapping]

# Dependency graph
requires:
  - phase: 05-inbox-analytics
    provides: "conversations router + send_message_by_telegram_id client-per-op skeleton this plan clones"
  - phase: 24-campaign-first-message-file-attachment
    provides: "migration 055 messages.message_type/file_name/mime_type/size_bytes columns the router (23-03/23-05) writes into"
provides:
  - "_resolve_peer_by_telegram_id: shared cache→get_dialogs(200)→retry peer-resolve helper"
  - "edit_message_by_telegram_id: no-pre-gate edit with MessageEditTimeExpired/NotModified/AuthorRequired/IdInvalid mapping"
  - "delete_message_by_telegram_id: revoke=True delete-for-everyone, silent no-op treated as success"
  - "send_file_by_telegram_id: force_document=False auto-media + caption overflow, router owns temp file"
  - "download_media_by_telegram_id: lazy media download by (peer, message_id) not file.id, MEDIA_UNAVAILABLE"
affects: [23-03, 23-05, conversations-router, inbox-ui]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "client-per-op skeleton cloned from send_message_by_telegram_id (get_client → op → disconnect in finally)"
    - "shared _resolve_peer_by_telegram_id ladder reused by all four inbox methods"
    - "structured {success, error:{code,message}} return dicts (no exceptions leak to router)"

key-files:
  created: []
  modified:
    - "app/services/telegram.py — 1 helper + 4 inbox mutation methods + 4 telethon.errors imports"

key-decisions:
  - "Written to the CANONICAL (main) get_client signature (sender_id + fingerprint) because the worktree base is stale pre-Phase-21; orchestrator cherry-picks onto main where get_client has those params."
  - "edit does NOT pre-gate the server-controlled edit window — catch MessageEditTimeExpiredError instead."
  - "MessageNotModifiedError → idempotent success no-op (mirrors set_username/UsernameNotModifiedError)."
  - "delete: reaching completion IS success (Telethon silently no-ops stale/own deletes); failure reserved for flood/frozen/session errors."
  - "send_file uses force_document=False (auto-media, D-11); router owns temp-file lifecycle (D-14) so no os.unlink and no URL download here."
  - "download by (peer, telegram_message_id) via get_messages(ids=...) + download_media(file=bytes); never the deprecated bot-API file id (Pitfall 6)."

patterns-established:
  - "Inbox Telethon methods return structured dicts; router owns gates/ordering/DB/temp-files and is fully testable against mocks."

requirements-completed: [INBM-01, INBM-02, INBM-03, INBM-05, INBM-06]

# Metrics
duration: 7min
completed: 2026-07-08
---

# Phase 23 Plan 02: Telegram Service Inbox Mutation Methods Summary

**Four mockable Telethon inbox methods (edit / delete-revoke / send-file auto-media / lazy download) plus a shared peer-resolve helper, each client-per-op with structured error dicts, added to `app/services/telegram.py`.**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-07-08T08:20:14Z
- **Completed:** 2026-07-08T08:27:09Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- `_resolve_peer_by_telegram_id` extracts the proven cache→get_dialogs(200)→retry ladder as one shared helper.
- `edit_message_by_telegram_id` attempts the edit with no client-side window pre-gate and maps the four Telethon edit error classes to `MESSAGE_EDIT_TOO_OLD` / `MESSAGE_NOT_EDITABLE`, with `MessageNotModifiedError` as an idempotent success no-op.
- `delete_message_by_telegram_id` passes `revoke=True` and treats Telethon's silent no-op (stale/own message) as success; only flood/frozen/session errors fail.
- `send_file_by_telegram_id` sends with `force_document=False` (auto-media, D-11) + a >1024-char caption overflow follow-up, without owning the temp file or downloading from a URL.
- `download_media_by_telegram_id` downloads lazily via `get_messages(peer, ids=...)` → `download_media(file=bytes)`, returning `MEDIA_UNAVAILABLE` when the message/media is gone, never using the deprecated bot-API file id.
- All five additions verified present on the `telegram_service` global via the test-overlay import check; all acceptance greps pass (`force_document=False`, `revoke=True`, `get_messages(peer, ids=...)`, `download_media(file=bytes)`, `MEDIA_UNAVAILABLE`, no `.file.id`, no `httpx`/`os.unlink` in send_file method).

## Task Commits

Each task was committed atomically:

1. **Task 1: peer-resolve helper + edit + delete** - `ffcb10f` (feat)
2. **Task 2: send_file + download_media** - `988f3ca` (feat)

_Note: the `.file.id` docstring token in Task 2 was reworded in the same Task-2 commit to keep automated verifiers unambiguous._

## Files Created/Modified
- `app/services/telegram.py` - Added 4 `telethon.errors` imports (MessageNotModified/MessageEditTimeExpired/MessageAuthorRequired/MessageIdInvalid), `_resolve_peer_by_telegram_id` helper, and 4 inbox mutation methods after `send_message_by_telegram_id`.

## Decisions Made
- **Canonical signature over stale-base signature.** The worktree executor branched from a stale base (pre-Phase-21): its local `get_client`/`send_message_by_telegram_id` lack the `sender_id` + `fingerprint` params. The plan's `<interfaces>` block specifies the canonical (main) skeleton WITH `sender_id` + `fingerprint`. New methods were written to the canonical signature (calling `get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)`) so they align once the orchestrator cherry-picks onto main. The `hasattr` verify passes regardless (signature mismatch would only surface at call time, and only against the stale base — never against main).
- Followed the plan's error-code mapping and structured-dict contract exactly.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Verify command adapted for worktree/prod container-name conflict + missing env**
- **Found during:** Task 1 (automated verify)
- **Issue:** The plan's verify command starts the `db` container, but its fixed `container_name: outreach-platform-db` conflicts with the running prod db; and a bare `python -c` import fails config `Settings()` validation (blank `TELEGRAM_API_ID`).
- **Fix:** Ran the identical import+hasattr check with `--no-deps` (module import needs no DB) and dummy env vars (`-e TELEGRAM_API_ID=1 …`). Verification semantics unchanged.
- **Files modified:** none (test invocation only)
- **Verification:** `ok all 5` printed.
- **Committed in:** n/a (no code change)

---

**Total deviations:** 1 (blocking — verify invocation only, no source change)
**Impact on plan:** None on delivered code. All four methods + helper implemented exactly per spec.

## Issues Encountered
- **Stale worktree base (documented, not resolved here).** This parallel worktree is checked out from a pre-Phase-20 base: its `.planning/` has no phase-20/21/23/24 directories and its `STATE.md`/`ROADMAP.md` are far behind main (Phase 24). Consequently this executor did NOT run `gsd-tools state advance-plan / update-progress / record-metric` or `roadmap update-plan-progress` — those commands mutate the stale worktree `STATE.md`/`ROADMAP.md`, and cherry-picking such a diff onto main's much newer copies would corrupt them (see project memory `feedback-worktree-executor-stale-base-cherry-pick.md`). **The orchestrator must run the STATE.md/ROADMAP.md updates against main after cherry-picking this plan's commits.** The SUMMARY.md and the `telegram.py` code commits are clean and cherry-pick safely.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- 23-03 and 23-05 (endpoint plans) can consume the four stable method signatures with no Telethon knowledge; router owns gates/ordering/DB writes/temp-file lifecycle.
- No DB/schema dependency in this plan (Wave 1, ran parallel with 23-01). The `messages` media columns needed by the routers were bridged by Phase 24's migration 055.

---
*Phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui*
*Completed: 2026-07-08*

## Self-Check: PASSED

- SUMMARY.md present
- Task commits ffcb10f, 988f3ca present in git log
